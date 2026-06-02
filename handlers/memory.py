import json
import logging
from aiohttp import web

from user_memory_store import (
    get_user_memories, upsert_memory, delete_memory, update_memory,
    get_user_memory_summary, get_memory_char_usage, CHAR_LIMITS
)

logger = logging.getLogger(__name__)

VALID_FACT_TYPES = {"preference", "habit", "fact"}


def _get_username(request) -> str:
    cookies = request.cookies
    return cookies.get("username", "")


async def _parse_body(request):
    try:
        return await request.json()
    except Exception:
        return None


async def handle_memory_list(request):
    username = _get_username(request)
    if not username:
        return web.json_response({"success": False, "error": "未登录"}, status=401)
    try:
        memories = get_user_memories(username)
        return web.json_response({"success": True, "data": memories})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_memory_summary(request):
    username = _get_username(request)
    if not username:
        return web.json_response({"success": False, "error": "未登录"}, status=401)
    try:
        summary = get_user_memory_summary(username)
        fact_used, fact_limit = get_memory_char_usage(username, "fact")
        pref_used, pref_limit = get_memory_char_usage(username, "preference")
        habit_used, habit_limit = get_memory_char_usage(username, "habit")
        user_used = pref_used + habit_used
        user_limit = pref_limit + habit_limit
        return web.json_response({"success": True, "data": {
            "summary": summary,
            "fact_used": fact_used, "fact_limit": fact_limit,
            "fact_pct": int(fact_used / fact_limit * 100) if fact_limit else 0,
            "user_used": user_used, "user_limit": user_limit,
            "user_pct": int(user_used / user_limit * 100) if user_limit else 0,
        }})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_memory_create(request):
    username = _get_username(request)
    if not username:
        return web.json_response({"success": False, "error": "未登录"}, status=401)
    body = await _parse_body(request)
    if not body:
        return web.json_response({"success": False, "error": "请求体必须为JSON格式"}, status=400)
    fact_type = body.get("fact_type", "preference")
    if fact_type not in VALID_FACT_TYPES:
        return web.json_response({"success": False, "error": f"无效的 fact_type: {fact_type}，可选: {', '.join(VALID_FACT_TYPES)}"}, status=400)
    key = body.get("key", "").strip()
    value = body.get("value", "").strip()
    if not key or not value:
        return web.json_response({"success": False, "error": "key和value不能为空"}, status=400)
    try:
        upsert_memory(username, fact_type, key, value, source="manual", confidence=10)
        return web.json_response({"success": True, "message": "记忆已保存"})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_memory_update(request):
    username = _get_username(request)
    if not username:
        return web.json_response({"success": False, "error": "未登录"}, status=401)
    body = await _parse_body(request)
    if not body:
        return web.json_response({"success": False, "error": "请求体必须为JSON格式"}, status=400)
    memory_id = body.get("id")
    if not memory_id:
        return web.json_response({"success": False, "error": "缺少id"}, status=400)
    try:
        ok = update_memory(memory_id, key=body.get("key"), value=body.get("value"), user_id=username)
        if ok:
            return web.json_response({"success": True, "message": "记忆已更新"})
        return web.json_response({"success": False, "error": "记录不存在或不属于你"}, status=403)
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_memory_delete(request):
    username = _get_username(request)
    if not username:
        return web.json_response({"success": False, "error": "未登录"}, status=401)
    memory_id = request.match_info.get("id")
    if not memory_id:
        return web.json_response({"success": False, "error": "缺少id"}, status=400)
    try:
        memory_id = int(memory_id)
        ok = delete_memory(memory_id, user_id=username)
        if ok:
            return web.json_response({"success": True, "message": "记忆已删除"})
        return web.json_response({"success": False, "error": "记录不存在或不属于你"}, status=403)
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)