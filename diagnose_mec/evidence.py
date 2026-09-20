"""Deterministic evidence correlation for MEC diagnosis results.

This layer never invents telemetry. It only correlates problem codes and facts
already collected by the diagnostic collectors.
"""
from __future__ import annotations

from typing import Any


def _dims(result: dict) -> list[dict]:
    return [d for d in result.get("dimensions", []) if isinstance(d, dict)]


def _problems(result: dict) -> set[str]:
    return {str(d.get("problem", "")) for d in _dims(result) if d.get("problem")}


def _text(result: dict) -> str:
    parts = []
    for d in _dims(result):
        parts.append(str(d.get("detail", "")))
        for item in d.get("log_errors_detail", []) or []:
            parts.append(str(item))
    return " ".join(parts).lower()


def correlate_root_cause(result: dict) -> dict:
    """Attach a conservative root-cause hypothesis based only on collected evidence."""
    result = dict(result or {})
    problems = _problems(result)
    text = _text(result)

    root = result.get("root_cause") or ""
    confidence = "low"
    evidence = list(result.get("evidence") or [])
    symptoms = list(result.get("symptoms") or [])

    # Infrastructure failures are causal when downstream checks were skipped.
    if "docker_service_down" in problems:
        root, confidence = "docker_service_down", "high"
    elif "dev_container_missing" in problems:
        root, confidence = "dev_container_missing", "high"
    elif "dev_container_stopped" in problems:
        root, confidence = "dev_container_stopped", "high"
    elif "device_unreachable" in problems or "ssh_unreachable" in problems:
        root, confidence = "device_unreachable", "medium"
    # Explicit OOM evidence outranks the downstream process/topic/image symptoms.
    elif any(k in text for k in ("oom", "out of memory", "内存溢出")):
        root, confidence = "container_memory_exhaustion", "high"
    elif "gpu_driver_error" in problems:
        root, confidence = "gpu_driver_error", "high"
    elif "process_fatal" in problems and (
        "driver" in text or "cuda" in text or "gpu" in text
    ):
        root, confidence = "gpu_driver_error", "medium"
    elif "supervisor_error" in problems:
        root, confidence = "supervisor_error", "high"
    elif "ros_master_error" in problems or "roscore_down" in problems:
        root, confidence = "roscore_down", "medium"
    elif "process_fatal" in problems:
        root, confidence = "process_fatal", "medium"
    elif "process_error" in problems:
        root, confidence = "process_error", "low"
    elif "topic_all_zero" in problems:
        root, confidence = "topic_all_zero", "low"
    elif "zero_images" in problems:
        # Zero images is intentionally a symptom unless a stronger upstream
        # cause was observed.
        root, confidence = "zero_images", "low"

    if root:
        result["root_cause"] = root
    result["root_cause_confidence"] = confidence

    # Preserve collector evidence and explicitly mark downstream symptoms.
    causal_codes = {
        "docker_service_down", "dev_container_missing", "dev_container_stopped",
        "device_unreachable", "ssh_unreachable", "container_memory_exhaustion",
        "gpu_driver_error", "supervisor_error", "roscore_down", "process_fatal",
    }
    for d in _dims(result):
        problem = d.get("problem")
        name = d.get("name", "")
        if problem and problem != root and (problem not in causal_codes or root in causal_codes):
            symptom = f"{name}: {d.get('detail', '')}"[:300]
            if symptom and symptom not in symptoms:
                symptoms.append(symptom)

    result["symptoms"] = symptoms[:20]
    result["evidence"] = evidence[:30]
    result["root_cause_basis"] = {
        "method": "deterministic_problem_code_correlation",
        "confidence": confidence,
        "observed_problem_codes": sorted(p for p in problems if p),
    }
    return result


__all__ = ["correlate_root_cause"]
