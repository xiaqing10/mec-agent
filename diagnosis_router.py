"""Shared deterministic router for device diagnosis results.
There is exactly one place that decides whether a basic device diagnosis needs LLM deep analysis.
"""
from __future__ import annotations
import json
from typing import Any, Callable

def parse_result(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}

def should_run_deep_analysis(result: dict) -> bool:
    return (
        isinstance(result, dict)
        and result.get("type") == "diagnose_device_result"
        and bool(result.get("deep_analysis_recommended"))
        and bool(result.get("ip") or result.get("entity", {}).get("ip"))
    )

def route_device_result(result: dict, *, deep_analysis_invoke: Callable[[str, str], Any] | None = None) -> dict:
    routed = dict(result or {})
    if not should_run_deep_analysis(routed):
        routed.setdefault("next_action", "report")
        return routed
    ip = routed.get("ip") or routed.get("entity", {}).get("ip", "")
    project = routed.get("project") or routed.get("entity", {}).get("project", "")
    routed["next_action"] = "deep_analysis"
    if deep_analysis_invoke is None:
        return routed
    try:
        deep_raw = deep_analysis_invoke(ip, project)
        deep_result = parse_result(deep_raw)
        if deep_result:
            routed["deep_analysis"] = deep_result
            routed["stage"] = "deep"
            routed["next_action"] = "report"
            routed["deep_analysis_recommended"] = False
        else:
            routed["deep_analysis_error"] = "深度分析返回了无法解析的结果"
    except Exception as exc:
        routed["deep_analysis_error"] = str(exc)[:500]
    return routed

__all__ = ["parse_result", "should_run_deep_analysis", "route_device_result"]