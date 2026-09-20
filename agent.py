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

import asyncio
import json
import os
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
from langchain_core.messages import BaseMessage, AIMessage, ToolMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from config import AVAILABLE_MODELS
from tools import TOOLS, mec_llm_diagnose_device
from llm_gateway import get_chat_model, invoke_messages, switch_model as gateway_switch_model
from request_router import route_request
from prompt_config import load_agent_system_prompt
from response_fallback import build_deterministic_fallback, ensure_non_empty_response


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
# Model switching (request-scoped through the unified gateway)
# ──────────────────────────────────────────────

def switch_model(model_id: str) -> bool:
    ok = gateway_switch_model(model_id)
    if ok:
        cfg = AVAILABLE_MODELS[model_id]
        logger.info("🔄 切换模型: %s (%s)", model_id, cfg.get("label", ""))
    return ok


def _get_llm():
    return get_chat_model(timeout=45, max_tokens=4096)


def _select_agent_tools(route: str):
    """Return only conversational/query/action tools.

    Device/project diagnosis is a deterministic workflow, not an LLM Tool.
    SSH command execution is never exposed to the model.
    """
    by_name = {getattr(t, "name", ""): t for t in TOOLS}
    common = ["resolve_mec_device", "resolve_mec_project", "help_info", "memory"]
    route_names = {
        "device_diagnosis": common + [
            "mec_device_info", "query_mec_device_from_db", "query_mec_abnormal",
        ],
        "device_info": common + [
            "mec_device_info", "query_mec_device_from_db",
        ],
        "project_diagnosis": common + [
            "query_mec_project_from_db", "query_mec_abnormal", "feishu_analyze_logs",
        ],
        "mec_query": common + [
            "query_mec_abnormal", "query_mec_device_from_db",
            "query_mec_project_from_db", "query_mec_event_records",
            "query_mec_event_image", "query_mec_project_event_stats",
            "mec_device_info",
        ],
        "server_query": common + [
            "query_server_traffic_flow", "query_server_events",
            "query_server_event_stats", "query_server_device_metrics",
            "query_server_traffic_pattern", "query_server_analysis_report",
        ],
        "repair": common + [
            "mec_device_info", "query_mec_device_from_db", "mec_repair_device",
        ],
        "report": common + [
            "feishu_analyze_logs", "feishu_fetch_report",
        ],
        "general": common + [
            "query_mec_abnormal", "query_mec_device_from_db",
            "query_mec_project_from_db", "mec_device_info",
            "feishu_analyze_logs", "feishu_fetch_report",
            "query_mec_event_records", "query_mec_project_event_stats",
            "query_server_traffic_flow", "query_server_events",
            "query_server_event_stats", "query_server_device_metrics",
            "query_server_traffic_pattern", "query_server_analysis_report",
            "mec_repair_device", "push_to_dingtalk",
            "generate_improvement_report",
        ],
    }
    names = route_names.get(route, route_names["general"])
    return [by_name[n] for n in dict.fromkeys(names) if n in by_name]
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
    request_project: Optional[str]
    request_ip: Optional[str]
    request_model: Optional[str]
    route_hint: Optional[str]
    deep_analysis_done: bool
    diagnosis_workflow_done: bool


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
# Deterministic request/result routing
# ──────────────────────────────────────────────

def route_request_node(state: AgentState) -> dict:
    messages = state.get("messages", [])
    last_user = next(
        (m.content for m in reversed(messages) if isinstance(m, HumanMessage)),
        "",
    )
    hint = route_request(last_user)
    return {
        "route_hint": hint.get("route", "general"),
        "deep_analysis_done": False,
        "diagnosis_workflow_done": False,
    }


async def post_tool_router_node(state: AgentState) -> dict:
    """Use the single shared diagnosis Result Router."""
    if state.get("deep_analysis_done"):
        return {}

    messages = state.get("messages", [])
    last_tool = next(
        (
            m for m in reversed(messages)
            if isinstance(m, ToolMessage) and getattr(m, "name", "") == "mec_diagnose_device"
        ),
        None,
    )
    if not last_tool:
        return {}

    from diagnosis_router import parse_result, route_device_result
    result = parse_result(last_tool.content)
    if result.get("type") != "diagnose_device_result":
        return {}

    model_id = state.get("request_model") or ""
    if model_id:
        from llm_gateway import switch_model
        switch_model(model_id)

    routed = route_device_result(
        result,
        deep_analysis_invoke=lambda ip, project: mec_llm_diagnose_device.invoke({
            "ip": ip, "project": project
        }),
    )

    if routed.get("deep_analysis") or routed.get("deep_analysis_error"):
        return {
            "messages": [
                SystemMessage(content="【确定性深度诊断结果】\\n" + json.dumps(
                    routed, ensure_ascii=False
                ))
            ],
            "deep_analysis_done": True,
        }

    return {"deep_analysis_done": True}

# ──────────────────────────────────────────────
# Deterministic diagnosis workflow
# ──────────────────────────────────────────────
def diagnosis_workflow_node(state: AgentState) -> dict:
    """Execute device/project diagnosis without giving command selection to LLM."""
    route = state.get("route_hint", "general")
    if route not in {"device_diagnosis", "project_diagnosis"}:
        return {"diagnosis_workflow_done": True}

    from diagnose_mec.workflow import run_diagnosis
    messages = state.get("messages", [])
    last_user = next(
        (m.content for m in reversed(messages) if isinstance(m, HumanMessage)),
        "",
    )
    ip = state.get("request_ip") or state.get("last_ip") or ""
    project = state.get("request_project") or state.get("last_project") or ""

    if not ip:
        import re
        match = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", last_user)
        if match:
            ip = match.group(0)

    if not project:
        import re
        match = re.search(r"(?:项目|工程)[：:\\s]*([A-Za-z0-9_\\-\\u4e00-\\u9fff]{2,32})", last_user)
        if match:
            project = match.group(1)

    if route == "device_diagnosis" and not ip:
        # Pass the most likely explicit device token; mec_diagnose_device performs
        # deterministic DB resolution and rejects ambiguous names.
        match = re.search(r"(?:诊断|排查|检查)\\s+([A-Za-z0-9_\\-.\\u4e00-\\u9fff]{2,64})", last_user)
        if match:
            ip = match.group(1)

    result = run_diagnosis(route, ip=ip, project=project)
    tool_name = "mec_diagnose_device" if route == "device_diagnosis" else "mec_diagnose_project"
    return {
        "messages": [ToolMessage(
            content=json.dumps(result, ensure_ascii=False),
            name=tool_name,
            tool_call_id=f"deterministic_{tool_name}",
        )],
        "diagnosis_workflow_done": True,
    }

# ──────────────────────────────────────────────
# Agent node: LLM decides which tool to call or responds directly
# ──────────────────────────────────────────────
async def agent_node(state: AgentState) -> dict:
    """Call LLM with conversation history and bound tools."""
    messages = state["messages"]
    route_hint = state.get("route_hint", "general")
    selected_tools = _select_agent_tools(route_hint)

    # Base prompt lives outside the code so operators can tune it without editing agent logic.
    system_prompt = load_agent_system_prompt()

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

    if route_hint:
        system_prompt += f"\n\n本轮确定性路由提示：{route_hint}。请优先选择与该路由一致的工具；若当前用户请求与提示不一致，以当前请求的明确内容为准。"

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

    # Context-window trimming is a model-input view only. Never mutate the persisted
    # LangGraph message history: checkpoint state remains complete across turns.
    MAX_HISTORY = 20
    if len(messages) > MAX_HISTORY:
        logger.info("本轮模型输入裁剪: %d 条 → 最近 %d 条（不修改 checkpoint）", len(messages), MAX_HISTORY)
        messages = list(messages[-MAX_HISTORY:])
    all_messages = [("system", system_prompt)] + messages

    _t0 = time.time()
    msg_count = len(all_messages)
    msg_chars = sum(len(str(m)) for m in all_messages)
    user_label = f"用户={user_id}" if user_id else "用户=未知"
    logger.info(
        "🚀 [USER:%s] LLM invoke 开始 | 消息数=%d | 字符数=%d | 用户消息=%s",
        user_label, msg_count, msg_chars,
        (messages[-1].content[:80] if messages else "")
    )
    try:
        response = await asyncio.to_thread(invoke_messages,
            all_messages,
            with_tools=True,
            tools=selected_tools,
            model_id=state.get("request_model") or None,
            timeout=45,
            max_tokens=4096,
            retry=1,
        )
    except Exception as exc:
        logger.exception("❌ LLM gateway 失败 | 耗时=%.1fs", time.time() - _t0)
        response = AIMessage(content=_build_error_msg(
            str(exc), type(exc).__name__,
            getattr(exc, "status_code", None),
            ""
        ))

    _t1 = time.time()
    tool_calls = getattr(response, "tool_calls", None)
    if tool_calls:
        names = [tc.get("name", "?") for tc in tool_calls]
        logger.info("[TIMING] LLM invoke 完成 | 耗时=%.1fs | 工具=%s",
                    _t1 - _t0, names)
    else:
        content_preview = (
            response.content[:100]
            if hasattr(response, "content") and isinstance(response.content, str)
            else ""
        )
        logger.info("[TIMING] LLM invoke 完成 | 耗时=%.1fs | 直接回复=%s",
                    _t1 - _t0, content_preview[:60])

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
# Final-response guard: deterministic, no second LLM call
# ──────────────────────────────────────────────
def finalize_response_node(state: AgentState) -> dict:
    """Guarantee a non-empty final AI response using only current-turn facts."""
    messages = state.get("messages", [])
    fallback = ensure_non_empty_response(messages)
    if fallback is None:
        return {}
    return {"messages": [AIMessage(content=fallback)]}


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


def _agent_node_sync(state: AgentState) -> dict:
    """Compatibility wrapper for synchronous callers such as LangGraph Studio."""
    return asyncio.run(agent_node(state))


def _post_tool_router_node_sync(state: AgentState) -> dict:
    """Compatibility wrapper for synchronous graph invocation."""
    return asyncio.run(post_tool_router_node(state))


# ──────────────────────────────────────────────
# Build graph
# ──────────────────────────────────────────────
def build_agent():
    """Build and compile the LangGraph agent (sync version for LangGraph Studio)."""
    tool_node = ToolNode(TOOLS)

    graph = StateGraph(AgentState)

    graph.add_node("agent", _agent_node_sync)
    graph.add_node("diagnosis_workflow", diagnosis_workflow_node)
    graph.add_node("tools", tool_node)
    graph.add_node("route_request", route_request_node)
    graph.add_node("post_tool_router", _post_tool_router_node_sync)
    graph.add_node("update_context", update_context_node)
    graph.add_node("finalize_response", finalize_response_node)
    graph.add_node("feedback", feedback_node)

    graph.set_entry_point("route_request")
    graph.add_conditional_edges(
        "route_request",
        lambda state: "diagnosis_workflow" if state.get("route_hint") in {"device_diagnosis", "project_diagnosis"} else "agent",
        {"diagnosis_workflow": "diagnosis_workflow", "agent": "agent"},
    )
    graph.add_edge("diagnosis_workflow", "post_tool_router")

    graph.add_conditional_edges(
        "agent",
        should_continue,
        {
            "tools": "tools",
            "update_context": "update_context",
            "__end__": "update_context",
        }
    )

    graph.add_edge("tools", "post_tool_router")
    graph.add_edge("post_tool_router", "agent")
    graph.add_edge("update_context", "finalize_response")
    graph.add_edge("finalize_response", "feedback")
    graph.add_edge("feedback", END)

    return graph.compile()


async def build_agent_async():
    """Build the production agent with a persistent async SQLite checkpointer."""
    os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from config import CHECKPOINT_DB_PATH

    cm = AsyncSqliteSaver.from_conn_string(str(CHECKPOINT_DB_PATH))
    checkpointer = await cm.__aenter__()
    graph = build_agent_with_checkpointer(checkpointer)
    return graph, (checkpointer, cm)


def build_agent_with_checkpointer(memory):
    """Build and compile the LangGraph agent with a given checkpointer."""
    tool_node = ToolNode(TOOLS)
    graph = StateGraph(AgentState)

    graph.add_node("route_request", route_request_node)
    graph.add_node("diagnosis_workflow", diagnosis_workflow_node)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.add_node("post_tool_router", post_tool_router_node)
    graph.add_node("update_context", update_context_node)
    graph.add_node("finalize_response", finalize_response_node)
    graph.add_node("feedback", feedback_node)

    graph.set_entry_point("route_request")
    graph.add_conditional_edges(
        "route_request",
        lambda state: "diagnosis_workflow" if state.get("route_hint") in {"device_diagnosis", "project_diagnosis"} else "agent",
        {"diagnosis_workflow": "diagnosis_workflow", "agent": "agent"},
    )
    graph.add_edge("diagnosis_workflow", "post_tool_router")
    graph.add_conditional_edges(
        "agent",
        should_continue,
        {
            "tools": "tools",
            "update_context": "update_context",
            "__end__": "update_context",
        },
    )
    graph.add_edge("tools", "post_tool_router")
    graph.add_edge("post_tool_router", "agent")
    graph.add_edge("update_context", "finalize_response")
    graph.add_edge("finalize_response", "feedback")
    graph.add_edge("feedback", END)
    return graph.compile(checkpointer=memory)


# ──────────────────────────────────────────────
# Convenience: run agent and extract response
# ──────────────────────────────────────────────
def extract_agent_response(state: dict) -> str:
    """Extract a non-empty final response, using the deterministic fallback as a last resort."""
    messages = state.get("messages", [])
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            content = msg.content if isinstance(msg.content, str) else ""
            if content.strip():
                return content
            break
    return build_deterministic_fallback(messages)