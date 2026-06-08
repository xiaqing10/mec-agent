"""
handlers 公共工具函数
提取各 handler 文件中重复的函数，避免代码重复。
"""
import json
import logging

from aiohttp import web

logger = logging.getLogger(__name__)


async def _parse_body(request) -> dict | None:
    """解析请求体为 JSON 字典。

    Returns:
        dict: 解析后的 JSON 字典，解析失败返回 None
    """
    try:
        return await request.json()
    except Exception:
        return None


def _get_username(request) -> str | None:
    """从请求中获取当前登录用户名。

    优先从 X-Username Header 获取（前端代理设置），
    其次从 Cookie 获取。

    Returns:
        str: 用户名，未登录返回 None
    """
    # 优先从 Header 获取（前端代理设置）
    username = request.headers.get("X-Username", "")
    if username:
        return username
    # 从 Cookie 获取
    return request.cookies.get("username", "") or None
