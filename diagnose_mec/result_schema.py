"""Common schema helpers for MEC diagnostic results.

The schema is additive: legacy fields used by the UI remain available while
new routing fields provide deterministic next-step decisions.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


SCHEMA_VERSION = "1.0"


def normalize_status(overall: str) -> str:
    if overall in {"normal", "warning", "error"}:
        return overall
    return "warning"


def build_domain_result(
    *,
    result_type: str,
    ip: str = "",
    project: str = "",
    overall: str = "warning",
    root_cause: str = "",
    dimensions: list[dict] | None = None,
    evidence: list[dict] | None = None,
    symptoms: list[str] | None = None,
    impact: list[str] | None = None,
    recommendations: list[str] | None = None,
    deep_analysis_recommended: bool = False,
    next_action: str = "report",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    dims = dimensions or []
    evidence = evidence or []
    symptoms = symptoms or []
    impact = impact or []
    recommendations = recommendations or []
    now = datetime.now().isoformat(timespec="seconds")

    return {
        "schema_version": SCHEMA_VERSION,
        "type": result_type,
        "status": normalize_status(overall),
        "overall": normalize_status(overall),
        "stage": "basic",
        "entity": {"ip": ip, "project": project},
        "ip": ip,
        "project": project,
        "root_cause": root_cause,
        "root_cause_confidence": None,
        "evidence": evidence,
        "symptoms": symptoms,
        "impact": impact,
        "recommendations": recommendations,
        "next_action": next_action,
        "deep_analysis_recommended": bool(deep_analysis_recommended),
        "diagnosis_time": now,
        "dimensions": dims,
        "summary_for_llm": "",
        **(extra or {}),
    }


def finalize_summary(result: dict[str, Any]) -> dict[str, Any]:
    dims = result.get("dimensions", [])
    summary = [
        f"设备 {result.get('ip', '')} 诊断完毕",
        f"状态: {result.get('overall', result.get('status', 'warning'))}",
    ]
    if result.get("project"):
        summary.append(f"项目: {result['project']}")
    if result.get("root_cause"):
        summary.append(f"根因: {result['root_cause']}")
    errors = [d.get("name", "") for d in dims if d.get("status") == "error"]
    warnings = [d.get("name", "") for d in dims if d.get("status") == "warning"]
    skipped = [d.get("name", "") for d in dims if d.get("status") == "skip"]
    if errors:
        summary.append("异常维度: " + ", ".join(errors))
    if warnings:
        summary.append("关注维度: " + ", ".join(warnings))
    if skipped:
        summary.append("未检查: " + ", ".join(skipped))

    result["summary_for_llm"] = "\n".join(summary)
    return result
