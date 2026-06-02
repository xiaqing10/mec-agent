"""
Per-user memory store for MEC diagnostic agent.
Each user has their own memory (facts, preferences, habits) that persists across sessions.
Memories are automatically extracted from conversations and stored as structured facts.
"""

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "user_memory.db"

_local = threading.local()


def _get_conn():
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(str(DB_PATH))
        _local.conn.row_factory = sqlite3.Row
        _init_db(_local.conn)
    return _local.conn


def _init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            fact_type TEXT NOT NULL DEFAULT 'preference',
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            source TEXT DEFAULT 'auto',
            confidence INTEGER DEFAULT 5,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_accessed_at TEXT
        )
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_user_memory_key
        ON user_memory(user_id, fact_type, key)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_memory_user
        ON user_memory(user_id)
    """)
    conn.commit()


CHAR_LIMITS = {
    "fact": 2000,
    "preference": 1500,
    "habit": 1500,
}


def get_memory_char_usage(user_id: str, fact_type: str = None) -> tuple:
    """Return (current_chars, limit_chars) for the given user and fact_type.
    If fact_type is None, return total across all types."""
    conn = _get_conn()
    if fact_type:
        rows = conn.execute(
            "SELECT value FROM user_memory WHERE user_id=? AND fact_type=?",
            (user_id, fact_type)
        ).fetchall()
        total = sum(len(r["value"]) for r in rows)
        limit = CHAR_LIMITS.get(fact_type, 2000)
        return total, limit
    else:
        rows = conn.execute(
            "SELECT fact_type, value FROM user_memory WHERE user_id=?",
            (user_id,)
        ).fetchall()
        by_type = {}
        for r in rows:
            by_type.setdefault(r["fact_type"], 0)
            by_type[r["fact_type"]] += len(r["value"])
        total = sum(by_type.values())
        limit = sum(CHAR_LIMITS.get(t, 2000) for t in by_type)
        return total, limit


def get_user_memories(user_id: str, fact_type: str = None) -> list[dict]:
    conn = _get_conn()
    if fact_type:
        rows = conn.execute(
            "SELECT * FROM user_memory WHERE user_id=? AND fact_type=? ORDER BY confidence DESC, updated_at DESC",
            (user_id, fact_type)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM user_memory WHERE user_id=? ORDER BY confidence DESC, updated_at DESC",
            (user_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_user_memory_summary(user_id: str) -> str:
    memories = get_user_memories(user_id)
    if not memories:
        return ""
    lines = []
    for m in memories:
        lines.append(f"- {m['key']}: {m['value']}")
    return "\n".join(lines)


def upsert_memory(user_id: str, fact_type: str, key: str, value: str, source: str = "auto", confidence: int = 5):
    conn = _get_conn()
    now = datetime.now().isoformat()
    existing = conn.execute(
        "SELECT id, value, confidence FROM user_memory WHERE user_id=? AND fact_type=? AND key=?",
        (user_id, fact_type, key)
    ).fetchone()
    if existing:
        new_confidence = min(10, existing["confidence"] + 1) if existing["value"] == value else max(1, existing["confidence"] - 1)
        conn.execute("""
            UPDATE user_memory SET value=?, confidence=?, updated_at=?, source=?
            WHERE id=?
        """, (value, new_confidence, now, source, existing["id"]))
    else:
        conn.execute("""
            INSERT INTO user_memory (user_id, fact_type, key, value, source, confidence, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, fact_type, key, value, source, confidence, now, now))
    conn.commit()


def delete_memory(memory_id: int, user_id: str = None) -> bool:
    conn = _get_conn()
    if user_id:
        cur = conn.execute("DELETE FROM user_memory WHERE id=? AND user_id=?", (memory_id, user_id))
    else:
        cur = conn.execute("DELETE FROM user_memory WHERE id=?", (memory_id,))
    conn.commit()
    return cur.rowcount > 0


def update_memory(memory_id: int, key: str = None, value: str = None, user_id: str = None) -> bool:
    conn = _get_conn()
    updates = []
    params = []
    if key is not None:
        updates.append("key=?")
        params.append(key)
    if value is not None:
        updates.append("value=?")
        params.append(value)
    if not updates:
        return False
    updates.append("updated_at=?")
    params.append(datetime.now().isoformat())
    params.append(memory_id)
    if user_id:
        params.append(user_id)
        cur = conn.execute(f"UPDATE user_memory SET {', '.join(updates)} WHERE id=? AND user_id=?", params)
    else:
        cur = conn.execute(f"UPDATE user_memory SET {', '.join(updates)} WHERE id=?", params)
    conn.commit()
    return cur.rowcount > 0


def mark_memory_accessed(memory_id: int):
    conn = _get_conn()
    conn.execute("UPDATE user_memory SET last_accessed_at=? WHERE id=?", (datetime.now().isoformat(), memory_id))
    conn.commit()


def extract_memories_from_conversation(user_id: str, user_message: str, ai_reply: str, intent: str = ""):
    if not user_message:
        return
    user_lower = user_message.lower()

    memory_rules = [
        ("preference", "关注项目", lambda: _extract_project_preference(user_message)),
        ("preference", "报告格式", lambda: _extract_format_preference(user_message)),
        ("habit", "关注维度", lambda: _extract_dimension_preference(user_message)),
        ("habit", "常用操作", lambda: _extract_operation_preference(user_message, intent)),
        ("preference", "告警偏好", lambda: _extract_alert_preference(user_message)),
    ]

    for fact_type, key, extractor in memory_rules:
        value = extractor()
        if value:
            upsert_memory(user_id, fact_type, key, value, source="auto", confidence=3)

    if "记住" in user_message and len(user_message) > 4:
        parts = user_message.split("记住", 1)
        if len(parts) > 1 and parts[1].strip():
            content = parts[1].strip().rstrip("。，！？,.!?")
            if "叫" in content or "是" in content or "喜欢" in content or "偏好" in content:
                upsert_memory(user_id, "preference", content[:40], content, source="explicit", confidence=8)
            elif "关注" in content or "监控" in content:
                upsert_memory(user_id, "preference", content[:40], content, source="explicit", confidence=8)
            else:
                upsert_memory(user_id, "fact", content[:40], content, source="explicit", confidence=7)


def _extract_project_preference(text: str) -> str:
    projects = ["德会", "德会隧道", "柯诸", "汉宜", "南京仙新路", "山西灵石", "汕梅", "沈海", "绵九", "贵阳", "青海"]
    mentioned = [p for p in projects if p in text]
    if mentioned:
        return f"常关注项目: {', '.join(mentioned)}"
    return ""


def _extract_format_preference(text: str) -> str:
    if "简洁" in text or "简短" in text or "摘要" in text:
        return "偏好简洁回复"
    if "详细" in text or "完整" in text or "全部" in text:
        return "偏好详细回复"
    if "表格" in text:
        return "偏好表格展示"
    return ""


def _extract_dimension_preference(text: str) -> str:
    dimensions = {
        "硬盘": "disk", "内存": "memory", "CPU": "cpu", "进程": "process",
        "传感器": "sensor", "容器": "container", "ROS": "ros", "图片": "image",
    }
    mentioned = [v for k, v in dimensions.items() if k in text]
    if mentioned:
        return f"常查看维度: {', '.join(mentioned)}"
    return ""


def _extract_operation_preference(text: str, intent: str) -> str:
    operations = []
    if "诊断" in text or "排查" in text:
        operations.append("诊断排查")
    if "查看" in text or "看" in text or "查询" in text:
        operations.append("查看状态")
    if "推送" in text or "钉钉" in text or "告警" in text:
        operations.append("告警推送")
    if operations:
        return f"常用操作: {', '.join(operations)}"
    return ""


def _extract_alert_preference(text: str) -> str:
    if "只推送" in text or "仅推送" in text:
        return "告警过滤偏好"
    if "推送给" in text or "通知" in text:
        return "告警接收偏好"
    return ""


def close():
    if hasattr(_local, "conn") and _local.conn:
        _local.conn.close()
        _local.conn = None


def _evict_low_confidence(user_id: str, fact_type: str, needed_chars: int):
    """Evict lowest-confidence + least-recently-accessed memories to free up space."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, value, confidence, last_accessed_at FROM user_memory WHERE user_id=? AND fact_type=? ORDER BY confidence ASC, last_accessed_at ASC NULLS FIRST",
        (user_id, fact_type)
    ).fetchall()
    freed = 0
    for r in rows:
        if freed >= needed_chars:
            break
        conn.execute("DELETE FROM user_memory WHERE id=?", (r["id"],))
        freed += len(r["value"])
    conn.commit()


def _ensure_capacity(user_id: str, fact_type: str, new_value: str):
    """Ensure there is enough capacity for new_value by evicting if needed."""
    current, limit = get_memory_char_usage(user_id, fact_type)
    new_len = len(new_value)
    if current + new_len > limit:
        needed = (current + new_len) - limit
        _evict_low_confidence(user_id, fact_type, needed)


def extract_memories_with_llm(user_id: str, user_message: str, ai_reply: str, intent: str = ""):
    pass