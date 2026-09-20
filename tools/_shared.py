"""Compatibility facade for diagnostic shared state.

Canonical implementation lives in diagnose_mec._shared so the deterministic
diagnosis package does not depend on the LangChain tools package.
"""
from diagnose_mec._shared import (
    ROOT_CAUSE_CN,
    _build_diag_result,
    _notify_progress,
    _summarize_log_errors,
    get_diag_progress_callback,
    reset_diag_progress_callback,
    set_diag_progress_callback,
)

__all__ = [
    "ROOT_CAUSE_CN",
    "_build_diag_result",
    "_notify_progress",
    "_summarize_log_errors",
    "get_diag_progress_callback",
    "reset_diag_progress_callback",
    "set_diag_progress_callback",
]
