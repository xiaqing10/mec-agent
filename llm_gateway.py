"""Unified LLM gateway for all model calls.

Keeps model selection request-scoped, caches immutable clients per model/role,
and centralizes bounded retry/timeout/error handling.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any, Iterable

from langchain_core.messages import AIMessage, BaseMessage
from langchain_openai import ChatOpenAI

from config import AVAILABLE_MODELS

logger = logging.getLogger(__name__)

_DEFAULT_MODEL_ID = next(iter(AVAILABLE_MODELS))
_current_model_id: ContextVar[str] = ContextVar(
    "llm_gateway_model_id", default=_DEFAULT_MODEL_ID
)
_client_cache: dict[tuple[str, int, int], ChatOpenAI] = {}
_tool_client_cache: dict[tuple[str, int, int, int], Any] = {}


class LLMGatewayError(RuntimeError):
    """Normalized LLM gateway failure."""

    def __init__(self, message: str, *, kind: str = "unknown", status_code=None):
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code


def get_current_model_id() -> str:
    return _current_model_id.get()


def get_model_config(model_id: str | None = None) -> dict:
    mid = model_id or get_current_model_id()
    cfg = AVAILABLE_MODELS.get(mid)
    if not cfg:
        raise LLMGatewayError(f"未知模型: {mid}", kind="configuration")
    return cfg


def switch_model(model_id: str) -> bool:
    if model_id not in AVAILABLE_MODELS:
        logger.warning("未知模型: %s，可用=%s", model_id, list(AVAILABLE_MODELS))
        return False
    _current_model_id.set(model_id)
    return True


def _get_client(*, model_id: str | None = None, timeout: int = 45, max_tokens: int = 4096) -> ChatOpenAI:
    mid = model_id or get_current_model_id()
    key = (mid, int(timeout), int(max_tokens))
    if key not in _client_cache:
        cfg = get_model_config(mid)
        _client_cache[key] = ChatOpenAI(
            model=mid,
            api_key=cfg["api_key"],
            base_url=cfg["base_url"],
            temperature=0.1,
            max_retries=0,
            timeout=timeout,
            max_tokens=max_tokens,
        )
    return _client_cache[key]


def get_chat_model(*, with_tools=False, tools=None, model_id=None, timeout=45, max_tokens=4096):
    client = _get_client(model_id=model_id, timeout=timeout, max_tokens=max_tokens)
    if not with_tools:
        return client
    tools_key = tuple(sorted(getattr(t, "name", repr(t)) for t in (tools or [])))
    cache_key = (
        model_id or get_current_model_id(),
        int(timeout),
        int(max_tokens),
        hash(tools_key),
    )
    if cache_key not in _tool_client_cache:
        _tool_client_cache[cache_key] = client.bind_tools(tools or [])
    return _tool_client_cache[cache_key]


def _classify_error(exc: Exception) -> tuple[str, int | None]:
    msg = str(exc)
    lower = msg.lower()
    status = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    if status is None:
        for code in (400, 401, 429, 500, 502, 503):
            if str(code) in msg:
                status = code
                break
    if status == 401 or "unauthorized" in lower:
        return "auth", status
    if status == 429 or "quota" in lower:
        return "quota", status
    if status in (502, 503) or "gateway" in lower:
        return "gateway", status
    if "timeout" in lower or "timed out" in lower:
        return "timeout", status
    if status == 400 or "invalidparameter" in lower:
        return "parameter", status
    if status == 500 or "internal server error" in lower:
        return "server", status
    return "unknown", status


def invoke_messages(
    messages: Iterable[BaseMessage | tuple | dict],
    *,
    with_tools: bool = False,
    tools=None,
    model_id: str | None = None,
    timeout: int = 45,
    max_tokens: int = 4096,
    retry: int = 1,
) -> AIMessage:
    """Invoke once, then retry a bounded number of times with the same request.

    Retry is deliberately centralized so individual tools do not implement
    conflicting retry/timeout behavior.
    """
    last_exc: Exception | None = None
    msg_list = list(messages)
    for attempt in range(max(1, retry + 1)):
        try:
            model = get_chat_model(
                with_tools=with_tools,
                tools=tools,
                model_id=model_id,
                timeout=timeout,
                max_tokens=max_tokens,
            )
            return model.invoke(msg_list)
        except Exception as exc:
            last_exc = exc
            kind, status = _classify_error(exc)
            logger.warning(
                "LLM gateway failure attempt=%d/%d model=%s kind=%s status=%s: %s",
                attempt + 1, max(1, retry + 1),
                model_id or get_current_model_id(), kind, status, str(exc)[:300],
            )
            if kind in {"auth", "parameter"}:
                break
    kind, status = _classify_error(last_exc or RuntimeError("unknown LLM error"))
    raise LLMGatewayError(
        f"LLM请求失败: {str(last_exc)[:500] if last_exc else 'unknown'}",
        kind=kind,
        status_code=status,
    ) from last_exc


def invoke_text(
    system_prompt: str,
    user_prompt: str,
    *,
    model_id: str | None = None,
    timeout: int = 45,
    max_tokens: int = 4096,
    retry: int = 1,
) -> str:
    response = invoke_messages(
        [
            ("system", system_prompt),
            ("user", user_prompt),
        ],
        model_id=model_id,
        timeout=timeout,
        max_tokens=max_tokens,
        retry=retry,
    )
    content = response.content
    if isinstance(content, list):
        content = "".join(
            x.get("text", "") if isinstance(x, dict) else str(x)
            for x in content
        )
    return str(content or "").strip()


__all__ = [
    "LLMGatewayError",
    "get_current_model_id",
    "get_model_config",
    "switch_model",
    "get_chat_model",
    "invoke_messages",
    "invoke_text",
]
