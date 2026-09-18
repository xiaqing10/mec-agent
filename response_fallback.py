"""Deterministic final-response fallback.

This module must never call an LLM. It guarantees that a turn has a
non-empty, user-visible response even when the model returns empty content.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable


_ERROR_KEYS = (
    "error",
    "errors",
    "diagnosis_error",
    "deep_analysis_error",
    "exception",
    "failure",
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts).strip()
    return str(value).strip()


def content_to_text(value: Any) -> str:
    """Normalize model/tool content to displayable text."""
    return _text(value)


def _dedupe(items: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen = set()
    for item in items:
        item = _text(item)
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _extract_error_strings(payload: Any) -> list[str]:
    errors: list[str] = []
    if isinstance(payload, dict):
        for key in _ERROR_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                errors.append(value.strip())
            elif isinstance(value, list):
                errors.extend(_text(v) for v in value if _text(v))
        if payload.get("status") in {"error", "failed", "unreachable", "offline"}:
            for key in ("message", "root_cause", "summary_for_llm", "status"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    errors.append(value.strip())
            dimensions = payload.get("dimensions")
            if isinstance(dimensions, list):
                abnormal = [
                    _text(d.get("name", "")) for d in dimensions
                    if isinstance(d, dict) and d.get("status") == "error" and d.get("name")
                ]
                if abnormal:
                    errors.append("异常维度: " + "、".join(abnormal))
        nested = payload.get("deep_analysis")
        if nested:
            errors.extend(_extract_error_strings(nested))
    return _dedupe(errors)


def _scan_tool_error(text: str) -> list[str]:
    text = _text(text)
    if not text:
        return []
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        payload = None
    if isinstance(payload, dict):
        errors = _extract_error_strings(payload)
        if errors:
            return errors

    markers = ("错误", "失败", "超时", "不可达", "无法连接", "异常", "未找到", "认证失败")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    hits = [line for line in lines if any(marker in line for marker in markers)]
    return _dedupe(hits[:3])


def collect_turn_tool_context(messages: list[Any]) -> tuple[list[str], list[str]]:
    """Return tool names and key errors from the current HumanMessage turn only."""
    last_human = -1
    for index in range(len(messages) - 1, -1, -1):
        if getattr(messages[index], "type", "") == "human":
            last_human = index
            break

    tool_names: list[str] = []
    errors: list[str] = []
    for msg in messages[last_human + 1:]:
        if getattr(msg, "type", "") != "tool":
            continue
        name = getattr(msg, "name", "") or "unknown_tool"
        tool_names.append(name)
        errors.extend(_scan_tool_error(_text(getattr(msg, "content", ""))))

    return _dedupe(tool_names), _dedupe(errors)


def build_deterministic_fallback(
    messages: list[Any] | None = None,
    *,
    tool_names: list[str] | None = None,
    errors: list[str] | None = None,
) -> str:
    """Build a fixed non-empty response without making another model call."""
    if tool_names is None or errors is None:
        collected_tools, collected_errors = collect_turn_tool_context(messages or [])
        if tool_names is None:
            tool_names = collected_tools
        if errors is None:
            errors = collected_errors

    tools_text = "、".join(_dedupe(tool_names or [])) or "无"
    error_text = "；".join(_dedupe(errors or [])) or "未发现明确错误信息"

    # Prevent a raw control/newline-heavy exception from breaking the response UI.
    error_text = re.sub(r"\s+", " ", error_text).strip()[:600]
    return (
        "本轮处理已完成，但模型没有返回可显示的正文。\n"
        f"本轮工具：{tools_text}\n"
        f"关键错误：{error_text}"
    )


def ensure_non_empty_response(messages: list[Any]) -> str | None:
    """Return None when a non-empty AI response already exists; otherwise a fixed fallback."""
    for msg in reversed(messages):
        if getattr(msg, "type", "") != "ai":
            continue
        content = _text(getattr(msg, "content", ""))
        if content:
            return None
        # An AI tool-call message with empty content is not a final response.
        # If it is the newest AI message, the caller should still produce a fallback.
        break
    return build_deterministic_fallback(messages)


__all__ = [
    "build_deterministic_fallback",
    "collect_turn_tool_context",
    "ensure_non_empty_response",
    "content_to_text",
]
