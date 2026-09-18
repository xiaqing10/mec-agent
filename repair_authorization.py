"""Short-lived, one-time authorization for repair execution."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from dataclasses import dataclass


@dataclass
class RepairGrant:
    token: str
    user_id: str
    session_id: str
    ip: str
    action: str
    target: str
    expires_at: float
    used: bool = False
    fingerprint: str = ""


_lock = threading.Lock()
_grants: dict[str, RepairGrant] = {}


_PROCESS_SIGNING_KEY = secrets.token_bytes(32)


def _signing_key() -> bytes:
    configured = os.getenv("REPAIR_SIGNING_KEY", "").strip()
    return configured.encode("utf-8") if configured else _PROCESS_SIGNING_KEY


def _fingerprint(user_id: str, session_id: str, ip: str, action: str, target: str) -> str:
    raw = f"{user_id}\x00{session_id}\x00{ip}\x00{action}\x00{target}".encode()
    return hmac.new(_signing_key(), raw, hashlib.sha256).hexdigest()[:16]


def issue_repair_grant(*, user_id: str, session_id: str, ip: str, action: str, target: str, ttl_seconds: int = 180) -> dict:
    token = secrets.token_urlsafe(24)
    grant = RepairGrant(
        token=token,
        user_id=user_id,
        session_id=session_id,
        ip=ip,
        action=action,
        target=target,
        expires_at=time.time() + ttl_seconds,
    )
    grant.fingerprint = _fingerprint(user_id, session_id, ip, action, target)
    with _lock:
        _grants[token] = grant
    return {
        "repair_token": token,
        "expires_at": int(grant.expires_at),
        "fingerprint": grant.fingerprint,
    }


def consume_repair_grant(*, token: str, user_id: str, session_id: str, ip: str, action: str, target: str) -> tuple[bool, str]:
    now = time.time()
    with _lock:
        grant = _grants.get(token)
        if grant is None:
            return False, "修复授权不存在或已失效"
        if grant.used:
            return False, "修复授权已使用"
        if grant.expires_at < now:
            _grants.pop(token, None)
            return False, "修复授权已过期"
        if (grant.user_id, grant.session_id, grant.ip, grant.action, grant.target) != (user_id, session_id, ip, action, target):
            return False, "修复授权与当前用户/会话/设备/动作不匹配"
        grant.used = True
        _grants.pop(token, None)
        return True, "ok"


def purge_expired() -> None:
    now = time.time()
    with _lock:
        for token, grant in list(_grants.items()):
            if grant.expires_at < now:
                _grants.pop(token, None)
