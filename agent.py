#!/usr/bin/env python3
"""
LangGraph Agent for MEC diagnostic assistant.

Defines:
- AgentState: conversation state schema
- build_agent(): constructs the LangGraph StateGraph
- run_agent(): convenience function for running the agent

Architecture:
  Agent (LLM + tools) → ToolNode → Agent → ... → final response
  State (messages + last_ip/last_project) persisted via AsyncSqliteSaver (SQLite)
"""

import json
import sys
import time
import logging
from pathlib import Path
from typing import TypedDict, Annotated, Literal, Optional

logger = logging.getLogger(__name__)

SELF_AGENT_DIR = Path(__file__).parent
sys.path.insert(0, str(SELF_AGENT_DIR))

from langgraph.graph import StateGraph, END, add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_core.messages import BaseMessage, AIMessage, ToolMessage, HumanMessage
from langchain_openai import ChatOpenAI

from config import AVAILABLE_MODELS
from tools import TOOLS


# ──────────────────────────────────────────────
# Helper: build user-facing error message
# ──────────────────────────────────────────────
def _build_error_msg(error_str: str, error_type: str, status_code, api_code) -> str:
    """Classify the LLM error and return a human-readable message + debug info."""
    error_lower = error_str.lower()
    debug = f"[{error_type}] status={status_code} code={api_code}"

    if "401" in error_str or "unauthorized" in error_lower:
        return f"LLM API 认证失败（401），请检查 API Key 是否有效。{debug}"
    if status_code == 429 or "quota" in error_lower or "AccountQuotaExceeded" in error_lower:
        return f"LLM API 配额超限（429），请稍后再试。{debug}"
    if "timeout" in error_lower or "timed out" in error_lower:
        return f"LLM API 请求超时，请重试。{debug}"
    if "400" in error_str or "invalidparameter" in error_lower:
        return f"LLM 参数错误（400），工具参数格式可能不对。{debug}"
    if "expa terror" in error_lower or "xml" in error_lower:
        return f"LLM 返回格式异常（XML解析错误）。{debug}"
    if "context_length" in error_lower or "maximum context" in error_lower or "too long" in error_lower:
        return f"对话上下文过长，模型无法处理。{debug}"
    if "500" in error_str or "internal server error" in error_lower:
        return f"LLM 服务端错误（500），请重试或联系管理员。{debug}"
    if "502" in error_str:
        return f"LLM 网关错误（502），请重试。{debug}"
    if "503" in error_str:
        return f"LLM 服务暂时不可用（503），请稍后重试。{debug}"

    return f"LLM 请求异常，无法生成完整分析。{debug}"

# ──────────────────────────────────────────────
# Model switching (per-session via ContextVar)
# ──────────────────────────────────────────────
from contextvars import ContextVar

# Default model — first entry in AVAILABLE_MODELS
_DEFAULT_MODEL_ID = next(iter(AVAILABLE_MODELS))
_DEFAULT_MODEL_CFG = AVAILABLE_MODELS[_DEFAULT_MODEL_ID]

_current_model_id: ContextVar[str] = ContextVar("_current_model_id", default=_DEFAULT_MODEL_ID)
_current_api_key: ContextVar[str] = ContextVar("_current_api_key", default=_DEFAULT_MODEL_CFG["api_key"])
_current_base_url: ContextVar[str] = ContextVar("_current_base_url", default=_DEFAULT_MODEL_CFG["base_url"])


def switch_model(model_id: str) -> bool:
    """Switch LLM model for the current session (ContextVar-isolated).

    Returns True if switch was successful, False if model_id is unknown.
    Does NOT rebuild the LangGraph graph — _get_llm() will pick up the new config
    on next call.
    """
    cfg = AVAILABLE_MODELS.get(model_id)
    if not cfg:
        logger.warning("未知模型: %s，可用: %s", model_id, list(AVAILABLE_MODELS.keys()))
        return False
    global _llm, _llm_with_tools
    _current_model_id.set(model_id)
    _current_api_key.set(cfg["api_key"])
    _current_base_url.set(cfg["base_url"])
    _llm = None  # trigger rebuild on next _get_llm() call
    _llm_with_tools = None
    logger.info("🔄 切换模型: %s (%s)", model_id, cfg.get("label", ""))
    return True


# ──────────────────────────────────────────────
# State definition
# ──────────────────────────────────────────────
class AgentState(TypedDict):
    """Conversation state for the MEC diagnostic agent.

    - messages: chat history (managed by LangGraph's add_messages reducer)
    - last_ip: last device IP operated on (for context inheritance)
    - last_project: last project operated on
    - conversation_intent: LLM-extracted intent summary for this conversation turn
    - pending_feedback: whether to ask for user feedback after this turn
    """
    messages: Annotated[list, add_messages]
    last_ip: str
    last_project: str
    conversation_intent: Optional[str]
    pending_feedback: bool
    auto_correctness: Optional[int]


def _extract_context_from_messages(messages: list) -> tuple:
    """Extract last_ip and last_project from the most recent ToolMessage."""
    ip, project = "", ""
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage):
            try:
                data = json.loads(msg.content)
                if isinstance(data, dict):
                    if data.get("ip"):
                        ip = data["ip"]
                    if data.get("project"):
                        project = data["project"]
                    elif data.get("project_analysis"):
                        project = ""
                # Also check for ip in string content
                content_str = msg.content if isinstance(msg.content, str) else ""
                if not ip:
                    m = __import__('re').search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', content_str)
                    if m:
                        ip = m.group(1)
            except (json.JSONDecodeError, TypeError):
                # Tool returned plain text, try regex for IP
                content_str = msg.content if isinstance(msg.content, str) else ""
                if not ip:
                    m = __import__('re').search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', content_str)
                    if m:
                        ip = m.group(1)
            if ip or project:
                break
    return ip, project


# ──────────────────────────────────────────────
# LLM setup (lazy, avoid network calls at import time)
# ──────────────────────────────────────────────
_llm = None
_llm_with_tools = None

def _get_llm():
    global _llm, _llm_with_tools
    if _llm is None:
        model = _current_model_id.get()
        api_key = _current_api_key.get()
        base_url = _current_base_url.get()
        _llm = ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=0.1,
            max_retries=0,
            timeout=60,
        )
        _llm_with_tools = _llm.bind_tools(TOOLS)
    return _llm, _llm_with_tools


# ──────────────────────────────────────────────
# Agent node: LLM decides which tool to call or responds directly
# ──────────────────────────────────────────────
def agent_node(state: AgentState) -> dict:
    """Call LLM with conversation history and bound tools."""
    messages = state["messages"]
    _, llm_with_tools = _get_llm()

    # Build system prompt with context
    system_prompt = """你是智慧交通垂域智能体，专注MEC边缘计算设备的日志分析和诊断维护，以及雷达交通数据的流量统计、事件分析和预测。

## 可用工具
### MEC设备诊断维护
- **mec_diagnose_device(ip/project)**: 单设备6维度SSH诊断（物理机/容器/进程/ROS/数据源/传感器）
- **mec_llm_diagnose_device(ip/project)**: SSH采集+LLM深度根因分析（mec_diagnose_device根因不明确时使用）
- **mec_device_info(ip, info_type)**: 查询设备详细指标（硬盘/内存/CPU/网络等）
- **query_mec_device_from_db(ip)**: 从数据库查询设备状态（无需SSH，设备离线时可用）
- **query_mec_project_from_db(project)**: 从数据库查询项目下所有设备汇总
- **mec_diagnose_project(project)**: 批量诊断项目下所有异常设备
- **feishu_analyze_logs(project)**: 解析飞书监控报告
- **feishu_llm_analyze_logs(project)**: LLM深度分析监控日志
- **feishu_fetch_report(project)**: 获取最新监控报告原文
- **query_mec_abnormal()**: 查询异常设备统计
- **query_mec_event_records(ip/project, date)**: 查询事件记录列表（时间/类型/车牌/车速）
- **query_mec_project_event_stats(project, date)**: 查询项目事件统计汇总
- **query_mec_event_image(event_id, ip)**: 抓取事件图片
- **mec_ssh_exec(ip, command, container, ros_env)**: 执行单条只读命令（细粒度场景使用）
- **mec_repair_device(ip, action, target)**: 生成修复方案（需用户确认后执行）
- **push_to_dingtalk(project, message)**: 推送消息到钉钉
- **memory(action, target, key, value)**: 管理用户记忆（偏好/习惯/事实）
- **help_info()**: 帮助信息
- **generate_improvement_report(days)**: 生成用户反馈改进报告。基于用户评价数据和LLM分析，给出Agent行为/工具/状态管理等维度的优化建议

### 交通数据分析（MongoDB数据，仅当用户明确提到"服务器"相关时使用，如"服务器流量""服务器事件""服务器雷达"等；用户不提"服务器"则默认查MEC设备数据）
- **query_server_traffic_flow(road_name, start_time, end_time, direction)**: 断面流量查询（车流量/平均速度/时间占有率/道路状态）
- **query_server_events(dev_no, start_time, end_time, event_type, limit, show_image)**: 雷达事件记录查询（事件类型/时间/设备/经纬度/图片，show_image="True"可显示图片）
- **query_server_event_stats(start_time, end_time, dev_no)**: 事件类型分布统计
- **query_server_device_metrics(dev_name, start_time, end_time)**: 设备运行健康指标（CPU/内存/磁盘/温度/告警）
- **query_server_traffic_pattern(start_time, end_time, road_name)**: 交通流时间序列分析（趋势/高峰/拥堵）
- **query_server_analysis_report(start_time, end_time, analysis_type)**: 交通数据分析报告（综合总结/异常识别/流量预测/事件-流量关联）

## 核心规则
1. **只读查询优先走数据库**（query_mec_device_from_db / query_mec_project_from_db），需要实时SSH诊断时用 mec_diagnose_device
2. **诊断标准流程**: mec_diagnose_device(6维度) → 若根因明确则修复建议；若根因不明确则 mec_llm_diagnose_device → 修复建议
3. **回复时不要重复输出工具已返回的原始数据**（前端已直接展示诊断面板和表格），只给总体结论、根因分析、影响范围、修复建议
4. **表格格式**: 标准markdown表格（| 开头，第二行分隔行 |---|），不用代码块包裹
5. **数据源优先级**: 数据库 > 飞书报告，除非用户明确要求从飞书获取
6. **诊断后发现问题时只调用一次 mec_repair_device** 生成修复方案，不重复调用
7. **闲聊/问候**直接友好回复，不调工具
8. **mec_ssh_exec** 仅在 mec_diagnose_device 和 mec_device_info 不覆盖的细粒度场景使用（如查看特定日志文件），且 ROS 命令需传 ros_env=True
9. **关键！工具选择规则**：以"query_server_"开头的工具查的是MongoDB中的"服务器"数据；以"query_mec_"或"mec_"开头的工具查的是MySQL中的MEC设备数据。"服务器事件"请用 query_server_events，"MEC设备事件"请用 query_mec_event_records，两者数据源完全不同，不要混淆

诊断维度说明：
- 物理机：SSH可达性、运行时间、硬盘占用率（/ 和 /data）
- 物理机离线：飞书报告中的物理机离线设备
- 容器：Docker运行状态、SSH连接
- 进程：supervisor进程状态、日志错误分析（驱动异常/ROS连接失败/OOM）
- ROS：roscore运行状态、topic频率
- 数据源：今日图片数量
- 传感器：摄像头和雷达在线率"""

    # Inject current real date so LLM doesn't use its training data cutoff date
    from datetime import datetime
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    system_prompt = f"当前真实时间：{now_str}\n\n{system_prompt}"

    # Add context from previous tool calls
    ctx_ip = state.get("last_ip", "") or _extract_context_from_messages(messages)[0]
    ctx_project = state.get("last_project", "") or _extract_context_from_messages(messages)[1]
    if ctx_ip or ctx_project:
        ctx_parts = []
        if ctx_ip:
            ctx_parts.append(f"最近操作设备IP: {ctx_ip}")
        if ctx_project:
            ctx_parts.append(f"最近操作项目: {ctx_project}")
        system_prompt += f"\n\n当前对话上下文：{'，'.join(ctx_parts)}"

    # Inject user memory
    from config import get_current_user_id
    from user_memory_store import get_user_memories
    user_id = get_current_user_id()
    if user_id:
        memories = get_user_memories(user_id)
        if memories:
            pref_items = [m for m in memories if m["fact_type"] == "preference"]
            habit_items = [m for m in memories if m["fact_type"] == "habit"]
            fact_items = [m for m in memories if m["fact_type"] == "fact"]
            mem_parts = []
            if pref_items:
                mem_parts.append("**用户偏好**（回复风格和关注范围）：\n" + "\n".join(f"- {m['value']}" for m in pref_items))
            if habit_items:
                mem_parts.append("**用户习惯**（常见操作模式，可据此预判意图）：\n" + "\n".join(f"- {m['value']}" for m in habit_items))
            if fact_items:
                mem_parts.append("**已知信息**（用户告知的背景事实）：\n" + "\n".join(f"- {m['value']}" for m in fact_items))
            if mem_parts:
                system_prompt += "\n\n## 关于当前用户\n" + "\n\n".join(mem_parts)

    # Insert system prompt as first message if not already there
    all_messages = [("system", system_prompt)] + messages

    _t0 = time.time()
    msg_count = len(all_messages)
    msg_chars = sum(len(str(m)) for m in all_messages)
    user_label = f"用户={user_id}" if user_id else "用户=未知"
    logger.warning("🚀 [TIMING] LLM invoke 开始 | %s | 消息数=%d | 字符数=%d | 用户消息=%s",
                   user_label, msg_count, msg_chars,
                   (messages[-1].content[:80] if messages else ''))
    try:
        response = llm_with_tools.invoke(all_messages)
    except Exception as e:
        import traceback
        error_str = str(e)
        _t1 = time.time()
        error_type = type(e).__name__
        tb_str = traceback.format_exc()

        # 提取关键信息：status_code、error code 等
        status_code = getattr(e, 'status_code', None) or getattr(e, 'http_status', None)
        api_code = getattr(e, 'code', None) or getattr(e, 'api_code', '')
        body_text = getattr(e, 'body', None) or getattr(e, 'message', '')

        logger.error("❌ LLM invoke 失败 | 耗时=%.1fs | 类型=%s | status=%s | api_code=%s\n  错误=%s\n  栈=%s",
                     _t1 - _t0, error_type, status_code, api_code,
                     error_str[:300], tb_str)

        # 重试条件：超时/限流/400参数错误
        error_lower = error_str.lower()
        should_retry = (
            "400" in error_str or "invalidparameter" in error_lower
            or "timeout" in error_lower or "timed out" in error_lower
            or status_code == 429 or "quota" in error_lower
            or status_code == 502 or status_code == 503
        )
        if should_retry:
            logger.info("Retrying LLM invoke with minimal messages...")
            trimmed = messages[-6:] if len(messages) > 6 else messages
            fallback_messages = [("system", system_prompt)] + trimmed
            _t2 = time.time()
            try:
                response = llm_with_tools.invoke(fallback_messages)
                logger.info("[TIMING] LLM retry 成功 | 耗时=%.1fs", time.time() - _t2)
            except Exception as e2:
                logger.error("❌ LLM retry 也失败 | 耗时=%.1fs | 类型=%s | 错误=%s\n%s",
                             time.time() - _t2, type(e2).__name__, str(e2)[:200], traceback.format_exc())
                from langchain_core.messages import AIMessage
                response = AIMessage(content=_build_error_msg(error_str, error_type, status_code, api_code))
        else:
            from langchain_core.messages import AIMessage
            response = AIMessage(content=_build_error_msg(error_str, error_type, status_code, api_code))
    _t1 = time.time()
    tool_calls = getattr(response, 'tool_calls', None)
    if tool_calls:
        names = [tc.get('name', '?') for tc in tool_calls]
        args = [tc.get('args', {}) for tc in tool_calls]
        logger.info("[TIMING] LLM invoke 完成 | 耗时=%.1fs | 工具=%s | 参数=%s", _t1 - _t0, names, args)
    else:
        content_preview = (response.content[:100] if hasattr(response, 'content') and response.content else '')
        logger.info("[TIMING] LLM invoke 完成 | 耗时=%.1fs | 直接回复=%s", _t1 - _t0, content_preview[:60])
    return {"messages": [response]}


# ──────────────────────────────────────────────
# Post-tool node: update context from tool results
# ──────────────────────────────────────────────
def update_context_node(state: AgentState) -> dict:
    """After tool execution, update last_ip/last_project from the result."""
    ip, project = _extract_context_from_messages(state["messages"])
    updates = {}
    if ip:
        updates["last_ip"] = ip
    if project:
        updates["last_project"] = project
    return updates


# ──────────────────────────────────────────────
# Feedback node: log intent and mark for feedback
# ──────────────────────────────────────────────
def feedback_node(state: AgentState) -> dict:
    """Log conversation intent and mark for feedback for non-trivial turns."""
    messages = state["messages"]
    if not messages:
        return {"pending_feedback": False}

    # Find the user's last message and the last AI response
    last_user_msg = None
    last_ai_msg = None
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage) and last_user_msg is None:
            last_user_msg = msg.content
        if isinstance(msg, AIMessage) and last_ai_msg is None:
            last_ai_msg = msg.content
        if last_user_msg and last_ai_msg:
            break

    if not last_user_msg:
        return {"pending_feedback": False}

    # Check if any tools were called in this turn
    tool_called = any(isinstance(m, ToolMessage) for m in messages[-10:])

    # Determine if this is a substantive turn worth feedback
    trivial_patterns = ["好的", "谢谢", "ok", "嗯", "明白", "知道了", "再见", "bye"]
    is_trivial = any(p in last_user_msg.lower() for p in trivial_patterns) and not tool_called

    return {
        "conversation_intent": last_user_msg[:50],
        "pending_feedback": tool_called and not is_trivial,
        "auto_correctness": None,
    }


# ──────────────────────────────────────────────
# Conditional edge: continue to tools or end
# ──────────────────────────────────────────────
def should_continue(state: AgentState) -> Literal["tools", "update_context", "__end__"]:
    """Route: if LLM called a tool, go to tools; otherwise update context and end."""
    last_msg = state["messages"][-1] if state["messages"] else None
    if isinstance(last_msg, AIMessage) and last_msg.tool_calls:
        return "tools"
    return "update_context"


# ──────────────────────────────────────────────
# Build graph
# ──────────────────────────────────────────────
def build_agent():
    """Build and compile the LangGraph agent (sync version for LangGraph Studio)."""
    tool_node = ToolNode(TOOLS)

    graph = StateGraph(AgentState)

    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.add_node("update_context", update_context_node)
    graph.add_node("feedback", feedback_node)

    graph.set_entry_point("agent")

    graph.add_conditional_edges(
        "agent",
        should_continue,
        {
            "tools": "tools",
            "update_context": "update_context",
            "__end__": "update_context",
        }
    )

    graph.add_edge("tools", "agent")
    graph.add_edge("update_context", "feedback")
    graph.add_edge("feedback", END)

    return graph.compile()


async def build_agent_async():
    """Build and compile the LangGraph agent with async SQLite checkpointer.

    Returns (compiled_graph, context_manager) — the context_manager must be
    kept alive (not exited) for the database connection to stay open.
    Call `await context_manager.aclose()` on shutdown.
    """
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    ctx = AsyncSqliteSaver.from_conn_string(str(SELF_AGENT_DIR / "checkpoints.db"))
    memory = await ctx.__aenter__()
    graph = build_agent_with_checkpointer(memory)
    return graph, ctx


def build_agent_with_checkpointer(memory):
    """Build and compile the LangGraph agent with a given checkpointer."""
    tool_node = ToolNode(TOOLS)
    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.add_node("update_context", update_context_node)
    graph.add_node("feedback", feedback_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges(
        "agent",
        should_continue,
        {
            "tools": "tools",
            "update_context": "update_context",
            "__end__": "update_context",
        }
    )
    graph.add_edge("tools", "agent")
    graph.add_edge("update_context", "feedback")
    graph.add_edge("feedback", END)
    return graph.compile(checkpointer=memory)


# ──────────────────────────────────────────────
# Convenience: run agent and extract response
# ──────────────────────────────────────────────
def extract_agent_response(state: dict) -> str:
    """Extract the LLM's final text response from the agent state."""
    messages = state.get("messages", [])
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            return msg.content
        if isinstance(msg, ToolMessage):
            # If the last message was a tool result, the LLM might not have responded yet
            # This shouldn't happen in normal flow, but handle gracefully
            pass
    return "处理完成，但我未能生成回复。请再试一次。"
