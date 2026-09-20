"""Compatibility facade for the canonical diagnosis cache.

The actual cache lives in ``diagnose_mec._diag_cache`` so the trusted
diagnosis workflow and legacy Tool wrappers share the same state.
"""

from diagnose_mec._diag_cache import (
    cache_diag_data,
    clear_diag_cache,
    get_diag_cache,
)

__all__ = ["cache_diag_data", "get_diag_cache", "clear_diag_cache"]
