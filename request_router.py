"""Deterministic request routing hints.

The router never invents entities and never executes side effects. It provides
stable intent/route hints to the agent, while tool schemas remain authoritative.
"""
from __future__ import annotations

import re

ROUTE_GENERAL = "general"
ROUTE_DEVICE_DIAGNOSIS = "device_diagnosis"
ROUTE_DEVICE_INFO = "device_info"
ROUTE_PROJECT_DIAGNOSIS = "project_diagnosis"
ROUTE_MEC_QUERY = "mec_query"
ROUTE_SERVER_QUERY = "server_query"
ROUTE_REPAIR = "repair"
ROUTE_REPORT = "report"

_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def route_request(text: str) -> dict:
    raw = (text or "").strip()
    lower = raw.lower()
    has_ip = bool(_IP_RE.search(raw))

    repair = any(k in raw for k in ("修复", "恢复", "重启", "清理缓存", "清理日志", "清理临时"))
    diagnosis = any(k in raw for k in ("诊断", "排查", "故障", "离线", "异常原因", "为什么"))
    info = any(k in raw for k in ("CPU", "cpu", "内存", "硬盘", "磁盘", "网络", "运行时间", "指标"))
    project = "项目" in raw or any(k in raw for k in ("整个项目", "项目整体", "全项目"))
    server = any(k in raw for k in ("服务器", "道路", "交通流", "路段", "雷达流量", "拥堵"))

    if repair:
        intent = ROUTE_REPAIR
    elif diagnosis and project:
        intent = ROUTE_PROJECT_DIAGNOSIS
    elif diagnosis or (has_ip and "设备" in raw and "状态" not in raw):
        intent = ROUTE_DEVICE_DIAGNOSIS
    elif info:
        intent = ROUTE_DEVICE_INFO
    elif project:
        intent = ROUTE_MEC_QUERY
    elif server:
        intent = ROUTE_SERVER_QUERY
    elif any(k in raw for k in ("日志", "飞书", "报告")):
        intent = ROUTE_REPORT
    else:
        intent = ROUTE_GENERAL

    return {
        "route": intent,
        "explicit_ip": _IP_RE.search(raw).group(0) if has_ip else "",
        "has_project_reference": project,
        "has_server_context": server,
        "requires_confirmation": repair,
        "confidence": "rule",
    }
