"""Deterministic MEC diagnosis workflow.

The workflow is intentionally outside LangChain's ToolNode.  The LLM may
request a diagnosis, but it never chooses the SSH/container/process commands
or their execution order.  This module is the single entry point for runtime
device diagnosis.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


DEVICE_DIAGNOSIS_TOOL = "mec_diagnose_device"
PROJECT_DIAGNOSIS_TOOL = "mec_diagnose_project"


def _parse_result(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def run_device_diagnosis(ip: str, project: str = "") -> dict:
    """Run the canonical deterministic device diagnosis pipeline."""
    from tools.tool_device import mec_diagnose_device

    raw = mec_diagnose_device.invoke({"ip": ip, "project": project or ""})
    result = _parse_result(raw)
    if not result:
        return {
            "type": "diagnose_device_result",
            "ip": ip,
            "project": project,
            "overall": "error",
            "root_cause": "diagnosis_result_invalid",
            "next_action": "report",
            "summary_for_llm": "诊断执行完成，但返回结果无法解析。",
            "diagnosis_error": str(raw)[:1000],
        }

    # The collector owns facts; the workflow owns the execution boundary.
    from .evidence import correlate_root_cause
    result = correlate_root_cause(result)
    result.setdefault("workflow", "deterministic_device_diagnosis")
    result.setdefault("execution_policy", {
        "llm_controls_commands": False,
        "llm_controls_order": False,
        "ssh_exposed_as_agent_tool": False,
    })
    return result


def run_project_diagnosis(project: str = "") -> dict:
    """Run the canonical deterministic project diagnosis pipeline."""
    from tools.tool_project import mec_diagnose_project

    raw = mec_diagnose_project.invoke({"project": project or ""})
    result = _parse_result(raw)
    if not result:
        return {
            "type": "diagnose_project_result",
            "project": project,
            "overall": "error",
            "summary_for_llm": "项目诊断执行完成，但返回结果无法解析。",
            "diagnosis_error": str(raw)[:1000],
        }
    result.setdefault("workflow", "deterministic_project_diagnosis")
    result.setdefault("execution_policy", {
        "llm_controls_commands": False,
        "llm_controls_order": False,
        "ssh_exposed_as_agent_tool": False,
    })
    return result


def run_diagnosis(route: str, ip: str = "", project: str = "") -> dict:
    """Dispatch only approved deterministic diagnosis workflows."""
    if route == "device_diagnosis":
        if not ip:
            return {
                "type": "diagnose_device_result",
                "overall": "warning",
                "root_cause": "device_target_missing",
                "next_action": "ask_user",
                "summary_for_llm": "未提供设备IP或可解析的设备名称。",
            }
        return run_device_diagnosis(ip, project)

    if route == "project_diagnosis":
        if not project:
            return {
                "type": "diagnose_project_result",
                "overall": "warning",
                "root_cause": "project_target_missing",
                "next_action": "ask_user",
                "summary_for_llm": "未提供明确项目名称。",
            }
        return run_project_diagnosis(project)

    raise ValueError(f"Unsupported diagnosis route: {route}")


__all__ = [
    "DEVICE_DIAGNOSIS_TOOL",
    "PROJECT_DIAGNOSIS_TOOL",
    "run_device_diagnosis",
    "run_project_diagnosis",
    "run_diagnosis",
]
