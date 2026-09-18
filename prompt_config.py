"""Load agent prompts from external configuration files."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

_DEFAULT_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "agent_system_prompt.md"


def get_agent_system_prompt_path() -> Path:
    configured = os.getenv("AGENT_SYSTEM_PROMPT_FILE", "").strip()
    return Path(configured).expanduser() if configured else _DEFAULT_PROMPT_PATH


@lru_cache(maxsize=4)
def load_agent_system_prompt() -> str:
    path = get_agent_system_prompt_path()
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"无法读取 Agent Prompt 配置文件: {path}") from exc
    if not content:
        raise RuntimeError(f"Agent Prompt 配置文件为空: {path}")
    return content


def clear_agent_system_prompt_cache() -> None:
    load_agent_system_prompt.cache_clear()


__all__ = [
    "get_agent_system_prompt_path",
    "load_agent_system_prompt",
    "clear_agent_system_prompt_cache",
]
