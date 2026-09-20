"""Canonical short-lived cache shared by deterministic diagnosis and deep analysis.

The cache lives in ``diagnose_mec`` because both the trusted diagnosis workflow
and the compatibility Tool wrapper depend on it. ``tools._diag_cache`` is a
compatibility facade only; it must not own a second cache.
"""

import threading
import time

_CACHE_TTL = 120

_lock = threading.Lock()
_cache: dict[str, dict] = {}


def cache_diag_data(ip: str, data: dict, ttl: int = _CACHE_TTL):
    with _lock:
        _cache[ip] = {
            **data,
            "_cached_at": time.time(),
            "_ttl": ttl,
        }


def get_diag_cache(ip: str) -> dict | None:
    with _lock:
        entry = _cache.get(ip)
        if not entry:
            return None
        if time.time() - entry["_cached_at"] > entry["_ttl"]:
            del _cache[ip]
            return None
        return entry


def clear_diag_cache(ip: str | None = None):
    with _lock:
        if ip:
            _cache.pop(ip, None)
        else:
            _cache.clear()


__all__ = ["cache_diag_data", "get_diag_cache", "clear_diag_cache"]
