import json
import logging
from aiohttp import web

logger = logging.getLogger(__name__)

async def _parse_body(request):
    try:
        return await request.json()
    except Exception:
        return None

def _get_username(request) -> str:
    cookies = request.cookies
    return cookies.get("username", "")

def _set_login_cookie(response, username: str):
    response.set_cookie("username", username, max_age=86400 * 7, path="/")

def _clear_login_cookie(response):
    response.del_cookie("username", path="/")


async def handle_login(request):
    from config import USERS
    body = await _parse_body(request)
    if not body:
        return web.json_response({"success": False, "error": "请求体必须为JSON格式"}, status=400)
    username = body.get("username", "").strip()
    password = body.get("password", "").strip()
    if not username or not password:
        return web.json_response({"success": False, "error": "用户名和密码不能为空"}, status=400)
    if username not in USERS or USERS[username] != password:
        return web.json_response({"success": False, "error": "用户名或密码错误"}, status=401)
    resp = web.json_response({"success": True, "data": {"username": username}})
    _set_login_cookie(resp, username)
    return resp


async def handle_logout(request):
    resp = web.json_response({"success": True, "message": "已退出"})
    _clear_login_cookie(resp)
    return resp


async def handle_me(request):
    from config import USERS
    username = _get_username(request)
    if not username:
        return web.json_response({"success": False, "error": "未登录"}, status=401)
    return web.json_response({"success": True, "data": {"username": username}})