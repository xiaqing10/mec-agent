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
    _current_model_id.set(model_id)
    _current_api_key.set(cfg["api_key"])
    _current_base_url.set(cfg["base_url"])
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
    request_project: str
    request_ip: str


def extract_explicit_request_context(text: str) -> tuple[str, str]:
    """Extract only explicit project/IP references from the current user turn."""
    import re
    text = text or ""
    ip_match = re.search(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', text)
    ip = ip_match.group(0) if ip_match else ""
    project = ""
    patterns = [
        r'(?:项目|工程)[：:\s]*([A-Za-z0-9_\-\u4e00-\u9fff]{2,32})',
        r'([A-Za-z0-9_\-\u4e00-\u9fff]{2,32})项目',
        r'(?:切换到|切换至|改查|换到|换查)[：:\s]*([A-Za-z0-9_\-\u4e00-\u9fff]{2,32})',
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            candidate = m.group(1).strip()
            if candidate not in {"这个", "当前", "该项目", "一下", "状态"}:
                project = candidate
                break
    return project, ip


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
# Per-model immutable cache. ContextVar selects the model for this request;
# one request can never invalidate another request's LLM instance.
_llm_cache = {}
_llm_tools_cache = {}

def _get_llm():
    model = _current_model_id.get()
    if model not in _llm_cache:
        cfg = AVAILABLE_MODELS[model]
        _llm_cache[model] = ChatOpenAI(
            model=model,
            api_key=cfg["api_key"],
            base_url=cfg["base_url"],
            temperature=0.1,
            max_retries=0,
            timeout=45,
        )
        _llm_tools_cache[model] = _llm_cache[model].bind_tools(TOOLS)
    return _llm_cache[model], _llm_tools_cache[model]


# ──────────────────────────────────────────────
# Agent node: LLM decides which tool to call or responds directly
# ──────────────────────────────────────────────
def agent_node(state: AgentState) -> dict:
    """Call LLM with conversation history and bound tools."""
    messages = state["messages"]
    _, llm_with_tools = _get_llm()

    # Keep the system prompt compact. Tool schemas are the source of truth.
    system_prompt = """你是智慧交通/MEC运维智能体。首要原则：**先用工具获得事实，再回答；不要凭空猜设备、项目、状态或根因。**

## 1. 实体解析（最高优先级）
- 明确IP：直接使用该IP。
- 设备名、编号、简称、后缀或可能有多个匹配：先调用 `resolve_mec_device`，不要自行猜IP。
- 项目名、简称或可能有多个解释：先调用 `resolve_mec_project`，不要自行猜标准项目名。
- 工具返回多个候选时，不要选择“第一个”；要求用户补充项目或IP。
- 当前消息明确指定的项目/设备优先于历史上下文。
- 历史上下文只用于“这个设备/该项目/它/继续查”等明确省略指代；当前消息冲突时，以当前消息为准。

## 2. 数据源与任务路由
- MEC设备数据默认使用 `query_mec_*` / `mec_*` 工具。
- 只有用户明确提到“服务器、道路、雷达交通流、服务器事件”等服务器/交通场景时，才使用 `query_server_*` 工具。
- “服务器事件”和“MEC设备事件”是两套不同数据源，绝不能混用。
- 数据库已有状态/历史：优先数据库查询工具。
- 实时设备状态、SSH、容器、ROS、进程、日志或图片诊断：使用 `mec_diagnose_device`。
- 只要具体CPU/内存/磁盘/网络等指标：优先 `mec_device_info`。
- 只有标准工具无法覆盖的具体文件/日志/配置查询，才使用 `mec_ssh_exec`。
- 项目整体诊断：使用 `mec_diagnose_project`。

## 3. 诊断流程
- 单设备诊断优先 `mec_diagnose_device`，不要直接跳到LLM深度分析。
- 基础诊断返回结构化结果后，只在 `deep_analysis_recommended=true` 时调用 `mec_llm_diagnose_device`；不要自行猜测是否需要深度分析。
- “物理机SSH不可用”不等于“设备不可达”；以诊断工具最终的设备/容器可达性为准。
- 工具已经给出结构化诊断结果时，直接基于工具证据总结，不重新猜测。
- 根因与症状必须分开；例如“图片为0”不应自动当作根因。

## 4. 修复与副作用
- 不要主动执行修复。只有用户明确要求重启、恢复、修复、清理等操作时，才调用修复相关工具。
- `mec_repair_device` 只生成待确认方案；没有用户明确确认，不执行实际修复。
- `push_to_dingtalk`、写入记忆等有副作用的工具，只在用户明确要求或确有必要完成用户指令时使用。
- 记忆只保存用户明确表达的长期偏好/事实；不要因为一次查询项目或设备就保存成“常关注项目”。

## 5. 工具优先原则
- 能由确定性工具解决的歧义，不交给LLM猜。
- 同一个工具不要无意义重复调用；只有结果明确要求重试/补充信息时才重试。
- 不要把一个工具失败直接解释成整个设备失败；区分解析失败、认证失败、网络失败、物理机不可用、容器不可用和数据缺失。
- 不要伪造工具结果、IP、项目、时间或指标。
- 不要重复输出大段原始日志；重点总结结论、关键证据、影响和建议。

## 6. 回答格式
- 普通问答：直接回答。
- 诊断类：按“结论 → 关键证据 → 影响 → 建议”简洁汇总。
- 工具已提供前端结构化面板的数据，不要再次大段复制。
- 表格使用标准Markdown表格，不放进代码块。
- 信息不足时明确说明工具未获取到该信息，不要猜测。"""

    # Inject current real date so LLM doesn't use its training data cutoff date
    from datetime import datetime
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    system_prompt = f"当前真实时间：{now_str}\n\n{system_prompt}"

    # Request-scoped context takes precedence; legacy last_* is fallback only.
    ctx_ip = state.get("request_ip", "") or state.get("last_ip", "")
    ctx_project = state.get("request_project", "") or state.get("last_project", "")
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
    # 主动裁剪：保留最近 N 条消息，防止 token 溢出或内容安全过滤
    MAX_HISTORY = 20
    if len(messages) > MAX_HISTORY:
        logger.info("消息数=%d 超过上限%d，裁剪到最后%d条", len(messages), MAX_HISTORY, MAX_HISTORY)
        messages = messages[-MAX_HISTORY:]
    all_messages = [("system", system_prompt)] + messages

    _t0 = time.time()
    msg_count = len(all_messages)
    msg_chars = sum(len(str(m)) for m in all_messages)
    user_label = f"用户={user_id}" if user_id else "用户=未知"
    logger.info("🚀 [USER:%s] LLM invoke 开始 | 消息数=%d | 字符数=%d | 用户消息=%s",
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

        # 重试条件：超时/限流/400参数错误/内容安全
        error_lower = error_str.lower()
        should_retry = (
            "400" in error_str or "invalidparameter" in error_lower
            or "timeout" in error_lower or "timed out" in error_lower
            or status_code == 429 or "quota" in error_lower
            or status_code == 502 or status_code == 503
            or "sensitive" in error_lower
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
        updates["request_ip"] = ip
    if project:
        updates["last_project"] = project
        updates["request_project"] = project
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
    """Build and compile the LangGraph agent with in-memory checkpointer.

    Uses MemorySaver (no SQLite) — avoids AsyncSqliteSaver thread check errors
    introduced in langgraph-checkpoint-sqlite >= 3.0.
    Session history is kept in process memory per thread_id.
    """
    from langgraph.checkpoint.memory import MemorySaver
    memory = MemorySaver()
    graph = build_agent_with_checkpointer(memory)
    return graph, memory


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
