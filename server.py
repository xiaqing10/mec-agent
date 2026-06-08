#!/usr/bin/env python3
"""
智慧交通垂域智能体 API Server - MEC日志分析与设备诊断Agent (LangGraph版)
"""
import sys
import os
import logging
from pathlib import Path

# 配置 logging：INFO 及以下输出到 stdout，WARNING 及以上输出到 stderr
handler_out = logging.StreamHandler(sys.stdout)
handler_out.setLevel(logging.DEBUG)
handler_out.addFilter(lambda record: record.levelno <= logging.INFO)

handler_err = logging.StreamHandler(sys.stderr)
handler_err.setLevel(logging.WARNING)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[handler_out, handler_err],
)
logging.getLogger('aiohttp.access').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

SELF_AGENT_DIR = Path(__file__).parent
sys.path.insert(0, str(SELF_AGENT_DIR))
os.chdir(str(SELF_AGENT_DIR))

from config import API_HOST, API_PORT, SSL_CERT, SSL_KEY

try:
    from aiohttp import web
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    web = None

from handlers import (
    handle_login, handle_logout, handle_me,
    handle_feedback, handle_feedback_stats, handle_feedback_list,
    handle_feedback_my, handle_feedback_update, handle_feedback_delete,
    handle_feedback_pin, handle_feedback_unpin, handle_feedback_pinned_list,
    handle_chat, handle_chat_stream, handle_raw_diagnose,
    handle_repair_execute, handle_admin_conversation_summary,
)
from handlers.memory import (
    handle_memory_list, handle_memory_summary,
    handle_memory_create, handle_memory_update, handle_memory_delete,
)
from webui import handle_webui, handle_static
from handlers.chat import get_agent, _agent_init_time_since_init, _agent
from config import EVENT_IMAGE_TEMP_DIR, EVENT_IMAGE_TTL_HOURS


async def handle_health(request):
    return web.json_response({
        "status": "ok",
        "service": "traffic-domain-agent-langgraph",
        "agent_initialized": _agent is not None,
        "init_time_s": _agent_init_time_since_init[0]
    })


async def handle_version(request):
    return web.json_response({
        "version": "3.4",
        "service": "智慧交通垂域智能体",
        "features": ["日志分析", "设备诊断", "钉钉推送", "流式输出", "Markdown渲染", "LangGraph持久记忆"]
    })


async def handle_clear_session(request):
    body = await _parse_body(request) or {}
    session_id = body.get("session_id", "default")
    try:
        from langchain_core.messages import HumanMessage
        agent = await get_agent()
        config = {"configurable": {"thread_id": session_id}}
        await agent.ainvoke(
            {"messages": [], "last_ip": "", "last_project": ""},
            config
        )
        return web.json_response({"success": True, "message": f"会话 {session_id} 已清除"})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_event_image(request):
    record_id = request.match_info.get("record_id", "")
    filename = request.match_info.get("filename", "")
    if not record_id or not filename:
        return web.json_response({"error": "缺少参数"}, status=400)
    cache_dir = Path(str(EVENT_IMAGE_TEMP_DIR)) / record_id
    filepath = cache_dir / filename
    if not filepath.exists() or not filepath.is_file():
        return web.json_response({"error": "图片未找到，请先调用 fetch_event_image 抓取"}, status=404)
    return web.FileResponse(filepath)


async def _parse_body(request):
    try:
        return await request.json()
    except Exception:
        return None


def _auth_middleware():
    from config import API_KEY, USERS
    from handlers.auth import _get_username

    @web.middleware
    async def auth_middleware(request, handler):
        path = request.path
        if path in ("/", "/webui", "/api/v1/health",
                     "/api/v1/login", "/api/v1/logout") or path.startswith("/static/"):
            return await handler(request)
        api_key = request.headers.get("X-API-Key", "")
        if api_key == API_KEY:
            return await handler(request)
        username = _get_username(request)
        if username and username in USERS:
            return await handler(request)
        return web.json_response(
            {"success": False, "error": "未登录或无效的 API Key"},
            status=401
        )
    return auth_middleware


def create_app():
    if not AIOHTTP_AVAILABLE:
        print("❌ 需要安装 aiohttp: pip install aiohttp")
        sys.exit(1)
    app = web.Application(middlewares=[_auth_middleware()])
    app.router.add_get("/api/v1/health", handle_health)
    app.router.add_get("/", handle_webui)
    app.router.add_get("/webui", handle_webui)
    app.router.add_get("/static/{filename:.*}", handle_static)
    app.router.add_post("/api/v1/login", handle_login)
    app.router.add_post("/api/v1/logout", handle_logout)
    app.router.add_get("/api/v1/me", handle_me)
    app.router.add_post("/api/v1/chat", handle_chat)
    app.router.add_post("/api/v1/chat/stream", handle_chat_stream)
    app.router.add_post("/api/v1/diagnose", handle_raw_diagnose)
    app.router.add_get("/api/v1/version", handle_version)
    app.router.add_post("/api/v1/session/clear", handle_clear_session)
    app.router.add_post("/api/v1/feedback", handle_feedback)
    app.router.add_get("/api/v1/feedback/stats", handle_feedback_stats)
    app.router.add_get("/api/v1/feedback/list", handle_feedback_list)
    app.router.add_get("/api/v1/feedback/my", handle_feedback_my)
    app.router.add_post("/api/v1/feedback/update", handle_feedback_update)
    app.router.add_delete("/api/v1/feedback/{id}", handle_feedback_delete)
    app.router.add_post("/api/v1/feedback/pin", handle_feedback_pin)
    app.router.add_post("/api/v1/feedback/unpin", handle_feedback_unpin)
    app.router.add_get("/api/v1/feedback/pinned", handle_feedback_pinned_list)
    app.router.add_get("/api/v1/admin/conversations", handle_admin_conversation_summary)
    app.router.add_post("/api/v1/repair/execute", handle_repair_execute)
    app.router.add_get("/api/v1/memory/list", handle_memory_list)
    app.router.add_get("/api/v1/memory/summary", handle_memory_summary)
    app.router.add_post("/api/v1/memory/create", handle_memory_create)
    app.router.add_post("/api/v1/memory/update", handle_memory_update)
    app.router.add_delete("/api/v1/memory/{id}", handle_memory_delete)
    app.router.add_get("/event_image/{record_id}/{filename:.*}", handle_event_image)
    return app


if __name__ == "__main__":
    app = create_app()
    has_ssl = bool(SSL_CERT and SSL_KEY and os.path.isfile(SSL_CERT) and os.path.isfile(SSL_KEY))
    protocol = "https" if has_ssl else "http"
    print(f"🌐 WebUI: {protocol}://{API_HOST}:{API_PORT}/")
    print(f"   流式API: {protocol}://{API_HOST}:{API_PORT}/api/v1/chat/stream")
    print(f"   非流式API: {protocol}://{API_HOST}:{API_PORT}/api/v1/chat")
    print(f"   健康: {protocol}://{API_HOST}:{API_PORT}/api/v1/health")
    if has_ssl:
        print(f"🔒 HTTPS 已启用 (cert={SSL_CERT}, key={SSL_KEY})")
    else:
        print(f"⚠️  仅 HTTP，如需 HTTPS 请设置 SSL_CERT 和 SSL_KEY 环境变量")
    print(f"ⓘ 首次请求时初始化 Agent，约需 20-40秒")
    ssl_ctx = None
    if has_ssl:
        import ssl
        ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(SSL_CERT, SSL_KEY)
    web.run_app(app, host=API_HOST, port=API_PORT, ssl_context=ssl_ctx)
