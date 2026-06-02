import json
import logging
from aiohttp import web

from feedback_store import create_feedback_record, update_rating, get_feedback_stats, get_recent_feedback, update_feedback_by_id, delete_feedback_by_id, pin_feedback, unpin_feedback, get_pinned_feedback, get_user_conversation_summary

logger = logging.getLogger(__name__)


def _get_username(request) -> str:
    cookies = request.cookies
    return cookies.get("username", "")


async def _parse_body(request):
    try:
        return await request.json()
    except Exception:
        return None


async def handle_feedback(request):
    body = await _parse_body(request)
    if not body:
        return web.json_response({"success": False, "error": "请求体必须为JSON格式"}, status=400)

    session_id = body.get("session_id", "")
    rating = body.get("rating", "")
    feedback_text = body.get("feedback_text", "")

    if not session_id:
        return web.json_response({"success": False, "error": "session_id 为必填"}, status=400)

    if rating == "pending":
        try:
            update_rating(session_id, None, "")
            return web.json_response({"success": True, "message": "已撤销评价"})
        except Exception as e:
            return web.json_response({"success": False, "error": str(e)}, status=500)

    if rating not in ("satisfied", "partial", "unsatisfied"):
        return web.json_response({"success": False, "error": "rating 必须为 satisfied/partial/unsatisfied"}, status=400)

    try:
        update_rating(session_id, rating, feedback_text)
        return web.json_response({"success": True, "message": "感谢你的反馈！"})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_feedback_stats(request):
    try:
        stats = get_feedback_stats()
        return web.json_response({"success": True, "data": stats})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_feedback_list(request):
    username = _get_username(request)
    if username != "admin":
        return web.json_response({"success": False, "error": "仅管理员可查看全部反馈"}, status=403)
    try:
        records = get_recent_feedback(limit=200)
        return web.json_response({"success": True, "data": records})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_feedback_my(request):
    username = _get_username(request)
    if not username:
        return web.json_response({"success": False, "error": "未登录"}, status=401)
    try:
        records = get_recent_feedback(limit=100, user_id=username)
        return web.json_response({"success": True, "data": records})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_feedback_update(request):
    username = _get_username(request)
    if not username:
        return web.json_response({"success": False, "error": "未登录"}, status=401)
    body = await _parse_body(request)
    if not body:
        return web.json_response({"success": False, "error": "请求体必须为JSON格式"}, status=400)
    record_id = body.get("id")
    rating = body.get("rating")
    feedback_text = body.get("feedback_text", "")
    if not record_id or rating not in ("satisfied", "partial", "unsatisfied"):
        return web.json_response({"success": False, "error": "参数无效"}, status=400)
    try:
        records = get_recent_feedback(limit=100, user_id=username)
        record = next((r for r in records if r["id"] == record_id), None)
        if not record:
            return web.json_response({"success": False, "error": "记录不存在或不属于你"}, status=403)
        update_feedback_by_id(record_id, rating, feedback_text)
        return web.json_response({"success": True, "message": "已更新"})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_feedback_delete(request):
    username = _get_username(request)
    if not username:
        return web.json_response({"success": False, "error": "未登录"}, status=401)
    record_id = request.match_info.get("id")
    if not record_id:
        return web.json_response({"success": False, "error": "缺少id"}, status=400)
    try:
        record_id = int(record_id)
        records = get_recent_feedback(limit=100, user_id=username)
        record = next((r for r in records if r["id"] == record_id), None)
        if not record:
            return web.json_response({"success": False, "error": "记录不存在或不属于你"}, status=403)
        delete_feedback_by_id(record_id)
        return web.json_response({"success": True, "message": "已删除"})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_feedback_pin(request):
    username = _get_username(request)
    if username != "admin":
        return web.json_response({"success": False, "error": "仅管理员可操作"}, status=403)
    body = await _parse_body(request)
    if not body:
        return web.json_response({"success": False, "error": "请求体必须为JSON格式"}, status=400)
    record_id = body.get("id")
    if not record_id:
        return web.json_response({"success": False, "error": "缺少id"}, status=400)
    try:
        pin_feedback(int(record_id))
        return web.json_response({"success": True, "message": "已加入待优化"})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_feedback_unpin(request):
    username = _get_username(request)
    if username != "admin":
        return web.json_response({"success": False, "error": "仅管理员可操作"}, status=403)
    body = await _parse_body(request)
    if not body:
        return web.json_response({"success": False, "error": "请求体必须为JSON格式"}, status=400)
    record_id = body.get("id")
    if not record_id:
        return web.json_response({"success": False, "error": "缺少id"}, status=400)
    try:
        unpin_feedback(int(record_id))
        return web.json_response({"success": True, "message": "已移出待优化"})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_feedback_pinned_list(request):
    try:
        records = get_pinned_feedback(limit=200)
        return web.json_response({"success": True, "data": records})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_admin_conversation_summary(request):
    username = _get_username(request)
    if username != "admin":
        return web.json_response({"success": False, "error": "仅管理员可查看"}, status=403)
    hours = request.query.get("hours", "72")
    try:
        hours = int(hours)
    except ValueError:
        hours = 72
    try:
        data = get_user_conversation_summary(hours=hours)
        return web.json_response({"success": True, "data": data})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)