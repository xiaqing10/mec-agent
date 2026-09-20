import json
import asyncio
import time
import logging
import uuid
from aiohttp import web

logger = logging.getLogger(__name__)

_agent = None
_agent_checkpointer_ctx = None
_agent_lock = asyncio.Lock()
_agent_init_time_since_init = [None]
_active_runs: dict[str, dict] = {}  # session_id -> {task, request_id}
_session_locks: dict[str, asyncio.Lock] = {}
_MAX_SESSION_LOCKS = 1000

def _get_session_lock(session_id: str) -> asyncio.Lock:
    lock = _session_locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _session_locks[session_id] = lock
    return lock

_STOP_KEYWORDS = ["/stop", "停止", "取消", "终止", "停下"]


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


def _json_safe(value):
    """Convert LangChain/runtime objects into SSE-safe JSON values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    # LangChain BaseMessage (HumanMessage/AIMessage/ToolMessage, etc.).
    if hasattr(value, "content") and hasattr(value, "type"):
        return {
            "type": getattr(value, "type", type(value).__name__),
            "content": _json_safe(getattr(value, "content", "")),
        }
    if hasattr(value, "model_dump"):
        try:
            return _json_safe(value.model_dump())
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            return _json_safe(value.dict())
        except Exception:
            pass
    return str(value)


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


def _state_values(state):
    """Normalize LangGraph dict-like state and StateSnapshot to a plain mapping."""
    if state is None:
        return {}
    if isinstance(state, dict):
        return state
    values = getattr(state, "values", None)
    if isinstance(values, dict):
        return values
    # Some LangGraph versions expose a Mapping-like snapshot without dict inheritance.
    try:
        return dict(state)
    except (TypeError, ValueError):
        return {}


def _extract_agent_reply(state) -> str:
    state_values = _state_values(state)
    messages = state_values.get("messages", [])
    for msg in reversed(messages):
        if getattr(msg, 'type', '') != 'ai':
            continue
        from response_fallback import content_to_text
        content = content_to_text(getattr(msg, 'content', ''))
        if content:
            return content
    return ""


async def handle_chat(request):
    body = await _parse_body(request)
    if not body:
        return web.json_response({"success": False, "error": "请求体必须为JSON格式"}, status=400)

    user_message = body.get("message", "").strip()
    session_id = str(body.get("session_id") or "").strip()
    if not user_message:
        return web.json_response({"success": False, "error": "message字段不能为空"}, status=400)
    if not session_id:
        return web.json_response({"success": False, "error": "session_id不能为空，禁止使用公共default会话"}, status=400)

    model_id = body.get("model", "")
    if model_id:
        from agent import switch_model
        if switch_model(model_id):
            logger.info("会话 %s 切换模型: %s", session_id, model_id)

    from config import set_current_user_id
    username = _get_username(request)
    if username:
        set_current_user_id(username)

    lock = _get_session_lock(session_id)
    acquired = False
    try:
        try:
            await asyncio.wait_for(lock.acquire(), timeout=0.15)
        except asyncio.TimeoutError:
            return web.json_response({"success": False, "error": "当前会话正在处理中，请稍后重试。"}, status=409)
        acquired = True
        from langchain_core.messages import HumanMessage
        agent = await get_agent()
        config = {"configurable": {"thread_id": session_id}, "recursion_limit": 50}

        from agent import extract_explicit_request_context
        req_project, req_ip = extract_explicit_request_context(user_message)
        final_state = await agent.ainvoke(
            {"messages": [HumanMessage(content=user_message)],
             "request_project": req_project or None,
             "request_ip": req_ip or None,
             "request_model": model_id or None},
            config
        )
        from response_fallback import build_deterministic_fallback
        reply = _fix_table_alignment(_extract_agent_reply(final_state) or build_deterministic_fallback(
            _state_values(final_state).get("messages", [])))

        username = _get_username(request) or session_id
        final_values = _state_values(final_state)
        intent = final_values.get("conversation_intent", "")
        pending = final_values.get("pending_feedback", False)
        auto_correctness = final_values.get("auto_correctness")
        if intent:
            try:
                from feedback_store import create_feedback_record
                tool_msgs = [m for m in final_values.get("messages", []) if hasattr(m, 'type') and m.type == 'tool']
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
        logger.error("❌ LangGraph执行失败: %s | 类型=%s", str(e), type(e).__name__, exc_info=True)
        from response_fallback import build_deterministic_fallback
        fallback = build_deterministic_fallback(
            _state_values(final_state).get("messages", []) if "final_state" in locals() else [],
            errors=[f"{type(e).__name__}: {str(e)}"],
        )
        # Keep the transport successful so the UI always has a user-visible reply.
        return web.json_response({
            "success": True,
            "action": "chat",
            "data": {"reply": fallback, "degraded": True},
            "session_id": session_id,
            "pending_feedback": False,
            "error": f"处理失败: {str(e)}",
        }, status=200)
    finally:
        if acquired:
            lock.release()
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
    session_id = str(body.get("session_id") or "").strip()
    if not user_message:
        return web.json_response({"success": False, "error": "message字段不能为空"}, status=400)
    if not session_id:
        return web.json_response({"success": False, "error": "session_id不能为空，禁止使用公共default会话"}, status=400)

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

    request_id = uuid.uuid4().hex
    _disconnected = False

    async def _send(event_type: str, data: dict):
        nonlocal _disconnected
        payload = _json_safe(dict(data))
        payload.setdefault("request_id", request_id)
        text = f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        try:
            await response.write(text.encode('utf-8'))
        except (ConnectionResetError, ConnectionAbortedError, RuntimeError):
            _disconnected = True
            logger.info("客户端断开: request_id=%s session=%s", request_id, session_id)

    current_task = asyncio.current_task()

    is_stop = any(kw in user_message for kw in _STOP_KEYWORDS)
    if is_stop:
        run_info = _active_runs.get(session_id)
        old_task = run_info.get("task") if run_info else None
        if old_task and not old_task.done():
            logger.info("🛑 用户取消会话 %s 的进行中任务 request_id=%s", session_id, run_info.get("request_id", ""))
            old_task.cancel()
        await _send("done", {"status": "stopped"})
        return response

    lock = _get_session_lock(session_id)
    acquired = False

    try:
        # The lock is the source of truth for per-session serialization.
        # Do not publish _active_runs before the lock is actually owned:
        # otherwise a completed/cancelled request can leave a stale marker
        # that makes the next turn look permanently busy.
        try:
            await asyncio.wait_for(lock.acquire(), timeout=0.15)
        except asyncio.TimeoutError:
            await _send("error", {"message": "当前会话正在处理中，请稍后重试。"})
            await _send("done", {"status": "busy"})
            return response
        acquired = True
        _active_runs[session_id] = {"task": current_task, "request_id": request_id}
        from langchain_core.messages import HumanMessage

        if _agent is None:
            await _send("info", {"status": "initializing", "message": "首次使用正在初始化 Agent，约需 20-40 秒，请耐心等待..."})

        agent = await get_agent()

        if _agent_init_time_since_init[0] is not None:
            init_time = _agent_init_time_since_init[0]
            _agent_init_time_since_init[0] = None
            await _send("info", {"status": "initialized", "message": f"Agent 初始化完成 (用时 {init_time:.1f}秒)"})

        await _send("info", {"status": "started", "session_id": session_id})

        config = {"configurable": {"thread_id": session_id}, "recursion_limit": 50}

        current_tool = None
        tool_output_lines = []
        tool_called = False
        tool_actions = []
        stream_tool_names = []
        last_user_msg = user_message
        last_ai_msg = ""
        drain_task = None
        from tools import set_diag_progress_callback, reset_diag_progress_callback
        progress_list = []
        def _on_diag_progress(name, status, detail):
            progress_list.append({"name": name, "status": status, "detail": detail})
        progress_callback_token = set_diag_progress_callback(_on_diag_progress)

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

        from agent import extract_explicit_request_context
        req_project, req_ip = extract_explicit_request_context(user_message)
        async for event in agent.astream_events(
            {"messages": [HumanMessage(content=user_message)],
             "request_project": req_project or None,
             "request_ip": req_ip or None,
             "request_model": model_id or None},
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
                from response_fallback import content_to_text
                if hasattr(chunk, 'content'):
                    content = content_to_text(chunk.content)
                else:
                    content = content_to_text(chunk)
                if content:
                    last_ai_msg += content
                    await _send("token", {"content": content})

            elif kind == "on_chain_start" and name == "diagnosis_workflow":
                # Diagnosis is now a deterministic graph node rather than an
                # LLM-selected ToolNode call. Keep the existing progress UI.
                current_tool = "mec_diagnose_device"
                stream_tool_names.append(current_tool)
                tool_called = True
                await _send("tool_start", {"name": current_tool, "input": data.get("input", {})})
                drain_task = asyncio.ensure_future(_drain_progress_loop())

            elif kind == "on_chain_end" and name == "diagnosis_workflow":
                if drain_task is not None:
                    drain_task.cancel()
                    drain_task = None
                await _send("tool_end", {"name": current_tool or "mec_diagnose_device"})

            elif kind == "on_tool_start":
                current_tool = name
                if name and name not in stream_tool_names:
                    stream_tool_names.append(name)
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
                if name == "mec_repair_device":
                    try:
                        repair_data = json.loads(output_text)
                        if repair_data.get("status") == "pending_confirmation":
                            from repair_authorization import issue_repair_grant
                            grant = issue_repair_grant(
                                user_id=_get_username(request) or session_id,
                                session_id=session_id,
                                ip=repair_data.get("device_ip", ""),
                                action=repair_data.get("action", ""),
                                target=repair_data.get("target", ""),
                            )
                            repair_data.update(grant)
                            output_text = json.dumps(repair_data, ensure_ascii=False)
                    except (TypeError, json.JSONDecodeError) as exc:
                        logger.warning("修复方案解析失败，无法签发一次性授权: %s", exc)

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

        if not last_ai_msg.strip():
            # The finalizer node persists a deterministic AIMessage when model content is empty.
            # Read it back so the SSE client receives the exact same fallback as non-stream mode.
            final_state = await agent.aget_state(config)
            last_ai_msg = _extract_agent_reply(final_state)
            if not last_ai_msg.strip():
                from response_fallback import build_deterministic_fallback
                last_ai_msg = build_deterministic_fallback(
                    _state_values(final_state).get("messages", []),
                    tool_names=stream_tool_names,
                    errors=["模型未返回非空 content"],
                )
            logger.warning("astream_events 未产出可显示 AI 内容，补发确定性兜底回复")
            await _send("token", {"content": last_ai_msg})

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
        from response_fallback import build_deterministic_fallback
        err_msg = build_deterministic_fallback(
            [],
            tool_names=stream_tool_names if "stream_tool_names" in locals() else [],
            errors=[f"{type(e).__name__}: {str(e)}"],
        )
        # Keep this path deterministic: no second LLM call and no generic overwrite of tool context.
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
        try:
            if 'progress_callback_token' in locals():
                reset_diag_progress_callback(progress_callback_token)
        except Exception:
            pass
        if acquired:
            lock.release()
        current_run = _active_runs.get(session_id)
        if current_run and current_run.get("task") is current_task:
            _active_runs.pop(session_id, None)
        if acquired and not lock.locked():
            _session_locks.pop(session_id, None)

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

    try:
        result = await asyncio.to_thread(tool.invoke, params)
    except asyncio.CancelledError:
        raise
    if func_name == "mec_repair_device":
        try:
            repair_data = json.loads(result) if isinstance(result, str) else result
            if isinstance(repair_data, dict) and repair_data.get("status") == "pending_confirmation":
                from repair_authorization import issue_repair_grant
                session_id = body.get("session_id", "raw")
                user_id = _get_username(request) or session_id
                grant = issue_repair_grant(
                    user_id=user_id,
                    session_id=session_id,
                    ip=repair_data.get("device_ip", ""),
                    action=repair_data.get("action", ""),
                    target=repair_data.get("target", ""),
                )
                repair_data.update(grant)
                result = json.dumps(repair_data, ensure_ascii=False)
        except (TypeError, json.JSONDecodeError) as exc:
            logger.warning("Raw repair grant issuance failed: %s", exc)

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