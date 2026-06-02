import json
import asyncio
import time
import logging
from aiohttp import web

logger = logging.getLogger(__name__)

_agent = None
_agent_checkpointer_ctx = None
_agent_lock = asyncio.Lock()
_agent_init_time_since_init = [None]
_active_runs: dict[str, asyncio.Task] = {}  # session_id -> Task

_STOP_KEYWORDS = ["/stop", "停止", "取消", "终止", "停下"]
_MAX_MSG_CHARS = 40000


async def get_agent():
    global _agent, _agent_checkpointer_ctx, _agent_init_time_since_init
    if _agent is not None:
        return _agent
    async with _agent_lock:
        if _agent is None:
            logger.info("🖤 首次初始化 LangGraph Agent (约需 20-40秒)...")
            t0 = time.time()
            from agent import build_agent_async
            _agent, _agent_checkpointer_ctx = await build_agent_async()
            init_time = time.time() - t0
            _agent_init_time_since_init[0] = init_time
            logger.info(f"✅ LangGraph Agent 初始化完成 (用时 {init_time:.1f}秒)")
    return _agent


def _fix_table_alignment(text: str) -> str:
    import re
    lines = text.split('\n')
    result = []
    in_table = False
    table_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('|') and stripped.endswith('|'):
            table_lines.append(stripped)
            in_table = True
            continue
        if in_table:
            result.extend(_normalize_table(table_lines))
            table_lines = []
            in_table = False
        result.append(line)
    if in_table:
        result.extend(_normalize_table(table_lines))
    return '\n'.join(result)


def _normalize_table(rows):
    if not rows:
        return []
    parsed = []
    for row in rows:
        cells = [c.strip() for c in row.split('|')]
        if cells and cells[0] == '':
            cells = cells[1:]
        if cells and cells[-1] == '':
            cells = cells[:-1]
        parsed.append(cells)
    if not parsed:
        return rows
    max_cols = max(len(c) for c in parsed)
    sep_idx = -1
    for i, cells in enumerate(parsed):
        import re
        if all(re.match(r'^-+\s*$', c) for c in cells):
            sep_idx = i
            break
    if sep_idx == -1 and len(parsed) >= 1:
        parsed.insert(1, [])
        sep_idx = 1
    for i in range(len(parsed)):
        while len(parsed[i]) < max_cols:
            parsed[i].append('')
        if i == sep_idx:
            parsed[i] = ['---'] * max_cols
    result = []
    for cells in parsed:
        result.append('| ' + ' | '.join(cells) + ' |')
    return result


def _extract_agent_reply(state: dict) -> str:
    messages = state.get("messages", [])
    for msg in reversed(messages):
        if hasattr(msg, 'content') and msg.content and getattr(msg, 'type', '') == 'ai':
            return msg.content
    return ""


def _count_msg_chars(messages: list) -> int:
    total = 0
    for m in messages:
        c = getattr(m, 'content', '') or ''
        total += len(c) if isinstance(c, str) else sum(len(s) for s in c) if isinstance(c, list) else 0
    return total


def _trim_messages_for_llm(messages: list, max_chars: int = _MAX_MSG_CHARS) -> list:
    total = _count_msg_chars(messages)
    if total <= max_chars:
        return messages
    from langchain_core.messages import ToolMessage, HumanMessage, AIMessage
    result = list(messages)
    for i in range(len(result)):
        m = result[i]
        if not isinstance(m, ToolMessage):
            continue
        content = str(getattr(m, 'content', '') or '')
        if len(content) > 200:
            result[i] = ToolMessage(content=content[:200] + '...(截断)', tool_call_id=m.tool_call_id, name=m.name)
    return result


async def handle_chat(request):
    body = await _parse_body(request)
    if not body:
        return web.json_response({"success": False, "error": "请求体必须为JSON格式"}, status=400)

    user_message = body.get("message", "").strip()
    session_id = body.get("session_id", "default")
    if not user_message:
        return web.json_response({"success": False, "error": "message字段不能为空"}, status=400)

    model_id = body.get("model", "")
    if model_id:
        from agent import switch_model
        if switch_model(model_id):
            logger.info("会话 %s 切换模型: %s", session_id, model_id)

    from config import set_current_user_id
    username = _get_username(request)
    if username:
        set_current_user_id(username)

    try:
        from langchain_core.messages import HumanMessage
        agent = await get_agent()
        config = {"configurable": {"thread_id": session_id}}

        state = await agent.aget_state(config)
        history = (state.values.get("messages", []) if state and state.values else [])
        if _count_msg_chars(history) > _MAX_MSG_CHARS:
            trimmed = _trim_messages_for_llm(history)
            logger.info("⏳ 截断上下文: %d 条(%d 字符) → %d 条(%d 字符)",
                        len(history), _count_msg_chars(history),
                        len(trimmed), _count_msg_chars(trimmed))
            await agent.update_state(config, {"messages": trimmed})

        final_state = await agent.ainvoke(
            {"messages": [HumanMessage(content=user_message)]},
            config
        )
        reply = _fix_table_alignment(_extract_agent_reply(final_state) or "处理完成，但未生成回复。")

        username = _get_username(request) or session_id
        intent = final_state.get("conversation_intent", "")
        pending = final_state.get("pending_feedback", False)
        auto_correctness = final_state.get("auto_correctness")
        if intent:
            try:
                from feedback_store import create_feedback_record
                tool_msgs = [m for m in final_state.get("messages", []) if hasattr(m, 'type') and m.type == 'tool']
                actions = [{"name": getattr(m, 'name', ''), "content": str(getattr(m, 'content', ''))[:100]} for m in tool_msgs[:10]]
                create_feedback_record(session_id, user_id=username, intent=intent, actions=actions, auto_correctness=auto_correctness)
            except Exception as e:
                logger.warning("Failed to save feedback: %s", e)

        return web.json_response({
            "success": True,
            "action": "chat",
            "data": {"reply": reply},
            "session_id": session_id,
            "pending_feedback": pending
        })
    except Exception as e:
        logger.error("❌ LangGraph执行失败: %s", e)
        return web.json_response({"success": False, "error": f"处理失败: {str(e)}"}, status=500)
    finally:
        try:
            username = _get_username(request)
            if username and not _is_trivial(user_message):
                from user_memory_store import extract_memories_from_conversation
                extract_memories_from_conversation(username, user_message, reply if 'reply' in dir() else "", intent if 'intent' in dir() else "")
        except Exception:
            pass


async def handle_chat_stream(request):
    body = await _parse_body(request)
    if not body:
        return web.json_response({"success": False, "error": "请求体必须为JSON格式"}, status=400)

    user_message = body.get("message", "").strip()
    session_id = body.get("session_id", "default")
    if not user_message:
        return web.json_response({"success": False, "error": "message字段不能为空"}, status=400)

    model_id = body.get("model", "")
    if model_id:
        from agent import switch_model
        if switch_model(model_id):
            logger.info("会话 %s 切换模型: %s", session_id, model_id)

    from config import set_current_user_id
    username = _get_username(request)
    if username:
        set_current_user_id(username)

    response = web.StreamResponse(
        status=200,
        reason='OK',
        headers={
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',
        }
    )
    await response.prepare(request)

    async def _send(event_type: str, data: dict):
        text = f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        try:
            await response.write(text.encode('utf-8'))
        except (ConnectionResetError, ConnectionAbortedError, RuntimeError):
            pass

    current_task = asyncio.current_task()

    is_stop = any(kw in user_message for kw in _STOP_KEYWORDS)
    if is_stop:
        old_task = _active_runs.pop(session_id, None)
        if old_task and not old_task.done():
            logger.info("🛑 用户取消会话 %s 的进行中任务", session_id)
            old_task.cancel()
        await _send("done", {"status": "stopped"})
        return response

    _active_runs[session_id] = current_task

    try:
        from langchain_core.messages import HumanMessage

        if _agent is None:
            await _send("info", {"status": "initializing", "message": "首次使用正在初始化 Agent，约需 20-40 秒，请耐心等待..."})

        agent = await get_agent()

        if _agent_init_time_since_init[0] is not None:
            init_time = _agent_init_time_since_init[0]
            _agent_init_time_since_init[0] = None
            await _send("info", {"status": "initialized", "message": f"Agent 初始化完成 (用时 {init_time:.1f}秒)"})

        await _send("info", {"status": "started", "session_id": session_id})

        config = {"configurable": {"thread_id": session_id}}

        current_tool = None
        tool_output_lines = []
        tool_called = False
        tool_actions = []
        last_user_msg = user_message
        last_ai_msg = ""
        drain_task = None
        _disconnected = False

        from tools import set_diag_progress_callback
        progress_list = []
        def _on_diag_progress(name, status, detail):
            progress_list.append({"name": name, "status": status, "detail": detail})
        set_diag_progress_callback(_on_diag_progress)

        async def _drain_progress_loop():
            last_len = 0
            try:
                while True:
                    if len(progress_list) > last_len:
                        for item in progress_list[last_len:]:
                            await _send("diag_progress", item)
                        last_len = len(progress_list)
                    await asyncio.sleep(0.2)
            except asyncio.CancelledError:
                pass

        _stream_t0 = time.time()

        state = await agent.aget_state(config)
        history = (state.values.get("messages", []) if state and state.values else [])
        if _count_msg_chars(history) > _MAX_MSG_CHARS:
            trimmed = _trim_messages_for_llm(history)
            logger.info("⏳ 截断上下文: %d 条(%d 字符) → %d 条(%d 字符)",
                        len(history), _count_msg_chars(history),
                        len(trimmed), _count_msg_chars(trimmed))
            await agent.update_state(config, {"messages": trimmed})

        async for event in agent.astream_events(
            {"messages": [HumanMessage(content=user_message)]},
            config,
            version="v2"
        ):
            if _disconnected:
                logger.info("客户端已断开，停止流式输出")
                break
            kind = event.get("event", "")
            name = event.get("name", "")
            data = event.get("data") or {}

            if not hasattr(handle_chat_stream, '_first_event_logged'):
                handle_chat_stream._first_event_logged = True
                logger.info("[TIMING] SSE 首事件 | 耗时=%.1fs | kind=%s | name=%s",
                            time.time() - _stream_t0, kind, name)

            if kind == "on_chat_model_stream":
                chunk = data.get("chunk", "")
                if hasattr(chunk, 'content'):
                    content = chunk.content
                elif isinstance(chunk, str):
                    content = chunk
                else:
                    content = ""
                if content:
                    last_ai_msg += content
                    await _send("token", {"content": content})

            elif kind == "on_tool_start":
                current_tool = name
                tool_input = data.get("input", {})
                await _send("tool_start", {"name": name, "input": tool_input})
                if name == "mec_diagnose_device":
                    drain_task = asyncio.ensure_future(_drain_progress_loop())

            elif kind == "on_tool_end":
                tool_called = True
                if drain_task is not None:
                    drain_task.cancel()
                    drain_task = None
                output = data.get("output", "")
                await _send("tool_end", {"name": name})
                output_text = output.content if hasattr(output, 'content') else str(output)
                has_llm_output = output_text and output_text != "None"
                if name == "mec_diagnose_device":
                    try:
                        diag = json.loads(output_text)
                        if diag.get("type") == "diagnose_device_result":
                            logger.info("DEBUG diag_summary: ip=%s overall=%s dims=%d summary_len=%d",
                                        diag.get("ip"), diag.get("overall"),
                                        len(diag.get("dimensions", [])),
                                        len(diag.get("summary_for_llm", "")))
                            await _send("diag_summary", diag)
                            llm_output = diag.get("summary_for_llm", "")
                            if llm_output:
                                output_text = llm_output
                                await _send("tool_result", {"name": name, "output": llm_output[:8000]})
                                tool_actions.append({"name": name, "content": llm_output[:100]})
                            has_llm_output = False
                        else:
                            logger.warning("DEBUG diag_summary: json parsed but type=%s", diag.get("type"))
                    except (json.JSONDecodeError, TypeError) as e:
                        logger.warning("DEBUG diag_summary: json parse failed type=%s err=%s",
                                       type(e).__name__, str(e)[:100])
                        logger.warning("DEBUG diag_summary: raw output[:200] = %s", output_text[:200])
                if has_llm_output:
                    await _send("tool_result", {"name": name, "output": output_text[:8000]})
                    tool_actions.append({"name": name, "content": output_text[:100]})
                current_tool = None

        _stream_t1 = time.time()
        logger.info("[TIMING] SSE 流结束 | 总耗时=%.1fs", time.time() - _stream_t0)

        if not last_ai_msg:
            err_msg = "模型响应超时或异常，未能生成回复，请重试。若持续失败请联系管理员检查 API 状态。"
            logger.warning("astream_events 未产出任何 AI 消息，补发错误提示")
            await _send("token", {"content": err_msg})
            last_ai_msg = err_msg

        await _send("done", {"status": "complete"})

        trivial_patterns = ["好的", "谢谢", "ok", "嗯", "明白", "知道了", "再见", "bye"]
        is_trivial = any(p in last_user_msg.lower() for p in trivial_patterns)
        if not is_trivial:
            await _send("feedback_request", {
                "session_id": session_id,
                "summary": last_user_msg[:60],
                "intent": "",
            })

        from handlers.feedback_queue import enqueue_post_process
        asyncio.ensure_future(enqueue_post_process(
            session_id, _get_username(request), last_user_msg, last_ai_msg,
            tool_called, tool_actions, config, agent
        ))

    except asyncio.CancelledError:
        logger.info("会话 %s 被用户取消", session_id)
        try:
            await _send("done", {"status": "stopped"})
        except Exception:
            pass

    except Exception as e:
        logger.error("❌ SSE流失败: %s | 类型=%s", str(e), type(e).__name__, exc_info=True)
        err_msg = str(e)
        if "timed out" in err_msg.lower() or "timeout" in err_msg.lower():
            err_msg = "模型响应超时，请稍后重试。若持续失败请联系管理员检查 API 状态。"
        elif "quota" in err_msg.lower() or "429" in err_msg or "AccountQuotaExceeded" in err_msg:
            err_msg = "LLM API 配额超限，请稍后再试（每日 00:48 重置）。已执行的工具结果见上方。"
        elif "ExpatError" in err_msg or "xml" in err_msg.lower():
            err_msg = "模型返回异常（XML解析错误），请重试。"
        try:
            await _send("token", {"content": f"\n\n[错误] {err_msg}"})
        except Exception:
            pass
        finally:
            try:
                await _send("done", {"status": "error"})
            except Exception:
                pass

    finally:
        _active_runs.pop(session_id, None)

    return response


async def handle_raw_diagnose(request):
    body = await _parse_body(request)
    if not body:
        return web.json_response({"success": False, "error": "请求体必须为JSON格式"}, status=400)

    ACTION_MAP = {
        "diagnose_device": ("mec_diagnose_device", False),
        "device_info": ("mec_device_info", True),
        "diagnose_project": ("mec_diagnose_project", False),
        "llm_diagnose": ("mec_llm_diagnose_device", True),
        "push": ("push_to_dingtalk", False),
        "analyze": ("feishu_analyze_logs", False),
        "llm_analyze": ("feishu_llm_analyze_logs", True),
        "query_abnormal": ("query_mec_abnormal", False),
        "fetch_report": ("feishu_fetch_report", True),
        "ssh_exec": ("mec_ssh_exec", True),
        "help": ("help_info", False),
    }

    action = body.get("action", "")
    params = body.get("parameters", {})

    if action not in ACTION_MAP:
        return web.json_response({"success": False, "error": f"未知操作: {action}"}, status=400)

    func_name, is_raw = ACTION_MAP[action]
    from tools import TOOLS
    tool = next((t for t in TOOLS if t.name == func_name), None)
    if not tool:
        return web.json_response({"success": False, "error": f"工具 {func_name} 未找到"}, status=500)

    result = tool.invoke(params)
    if is_raw:
        return web.json_response({"success": True, "action": action, "data": {"result": result}})
    try:
        parsed = json.loads(result) if isinstance(result, str) else result
        return web.json_response({"success": True, "action": action, "data": parsed})
    except (json.JSONDecodeError, TypeError):
        return web.json_response({"success": True, "action": action, "data": {"result": result}})


async def _parse_body(request):
    try:
        return await request.json()
    except Exception:
        return None


def _get_username(request) -> str:
    cookies = request.cookies
    return cookies.get("username", "")


def _is_trivial(text: str) -> bool:
    trivial_patterns = ["好的", "谢谢", "ok", "嗯", "明白", "知道了", "再见", "bye"]
    return any(p in text.lower() for p in trivial_patterns)