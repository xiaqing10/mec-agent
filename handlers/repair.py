import json
import logging
from aiohttp import web

from tools.tool_repair import execute_repair

logger = logging.getLogger(__name__)


def _get_username(request) -> str:
    cookies = request.cookies
    return cookies.get("username", "")


async def _parse_body(request):
    try:
        return await request.json()
    except Exception:
        return None


async def handle_repair_execute(request):
    username = _get_username(request)
    if not username:
        return web.json_response({"success": False, "error": "未登录"}, status=401)

    body = await _parse_body(request)
    if not body:
        return web.json_response({"success": False, "error": "请求体必须为JSON格式"}, status=400)

    ip = body.get("ip", "")
    action = body.get("action", "")
    target = body.get("target", "")
    session_id = body.get("session_id", "default")
    repair_token = body.get("repair_token", "")

    if not ip or not action or not repair_token:
        return web.json_response(
            {"success": False, "error": "ip、action和repair_token为必填"},
            status=400,
        )

    from repair_authorization import consume_repair_grant
    repair_user_id = username or session_id
    ok, reason = consume_repair_grant(
        token=repair_token,
        user_id=repair_user_id,
        session_id=session_id,
        ip=ip,
        action=action,
        target=target,
    )
    if not ok:
        return web.json_response({"success": False, "error": reason}, status=403)

    logger.info(
        "Repair execute authorized: user=%s, session=%s, ip=%s, action=%s, target=%s",
        username, session_id, ip, action, target,
    )

    result = await __import__("asyncio").to_thread(execute_repair, ip, action, target)

    log_entry = {
        "user": username,
        "ip": ip,
        "action": action,
        "target": target,
        "success": result.get("success", False),
        "output": result.get("output", "")[:500],
    }
    logger.info("Repair result: %s", json.dumps(log_entry, ensure_ascii=False))

    return web.json_response(result)