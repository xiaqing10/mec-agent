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

    if not ip or not action:
        return web.json_response({"success": False, "error": "ip和action为必填"}, status=400)

    logger.info("Repair execute: user=%s, ip=%s, action=%s, target=%s", username, ip, action, target)

    result = execute_repair(ip, action, target)

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