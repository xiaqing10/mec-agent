import time
import threading

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
