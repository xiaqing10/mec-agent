import json
import re

from langchain_core.tools import tool

from query_sensor_status import lookup_device


@tool
def resolve_mec_device(query: str, project: str = "") -> str:
    """解析MEC设备名称/IP并返回候选结果。

    用途：当用户提供的是设备名、编号、后缀或存在多个同名设备时，
    先调用此工具确定唯一设备，不要让模型自行猜IP或项目。

    Args:
        query: 用户提供的设备IP、完整名称或设备编号/后缀，例如 10.145.4.1、zk26_690、690
        project: 可选项目名，用于缩小候选范围，例如 柯诸、德会

    Returns:
        JSON，包括 resolved（是否唯一解析）、device（唯一设备）、
        candidates（候选列表）及提示信息。
    """
    if not query or not query.strip():
        return json.dumps({
            "resolved": False,
            "error": "未提供设备标识"
        }, ensure_ascii=False)

    query = query.strip()
    project = (project or "").strip()

    try:
        candidates = lookup_device(query, project or None)
    except Exception as exc:
        return json.dumps({
            "resolved": False,
            "error": f"设备解析失败: {exc}"
        }, ensure_ascii=False)

    candidates = [
        {
            "name": d.get("name", ""),
            "ip": d.get("ip", ""),
            "project": d.get("project", ""),
            "pole": d.get("pole", ""),
        }
        for d in candidates
        if d.get("ip")
    ]

    if len(candidates) == 1:
        return json.dumps({
            "resolved": True,
            "device": candidates[0],
            "candidates": candidates,
            "message": "已唯一解析设备"
        }, ensure_ascii=False)

    if len(candidates) > 1:
        return json.dumps({
            "resolved": False,
            "ambiguous": True,
            "candidates": candidates,
            "message": "匹配到多个设备，请指定项目或直接提供IP"
        }, ensure_ascii=False)

    return json.dumps({
        "resolved": False,
        "ambiguous": False,
        "candidates": [],
        "message": f"未找到设备 '{query}'" + (f"（项目：{project}）" if project else "")
    }, ensure_ascii=False)
