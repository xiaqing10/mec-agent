"""
Feedback store for MEC diagnostic agent.
Logs each conversation's intent, actions, and user rating for optimization reference.

Schema:
- conversation_id: unique per conversation session
- user_id: session_id or user identifier
- intent: LLM-extracted user intent summary
- actions: list of tool calls executed
- rating: "satisfied" | "partial" | "unsatisfied" | null
- feedback_text: optional user comment
- auto_correctness: LLM self-evaluation score (0-10)
- pinned: 1 if pinned by admin for optimization, 0 otherwise
- created_at: timestamp
"""

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "feedback.db"

_local = threading.local()


def _get_conn():
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(str(DB_PATH))
        _local.conn.row_factory = sqlite3.Row
        _init_db(_local.conn)
    return _local.conn


def _init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            user_id TEXT NOT NULL DEFAULT 'default',
            intent TEXT,
            actions TEXT,
            rating TEXT,
            feedback_text TEXT,
            auto_correctness INTEGER,
            pinned INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
    """)
    try:
        conn.execute("ALTER TABLE feedback ADD COLUMN pinned INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_feedback_user ON feedback(user_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_feedback_conv ON feedback(conversation_id)
    """)
    conn.commit()


def create_feedback_record(conversation_id: str, user_id: str = "default",
                           intent: str = "", actions: list = None,
                           auto_correctness: int = None) -> int:
    conn = _get_conn()
    now = datetime.now().isoformat()
    # Remove previous unrated records for same conversation (keep only the latest)
    conn.execute("""
        DELETE FROM feedback WHERE conversation_id = ? AND rating IS NULL
    """, (conversation_id,))
    cur = conn.execute("""
        INSERT INTO feedback (conversation_id, user_id, intent, actions,
                              rating, feedback_text, auto_correctness, created_at)
        VALUES (?, ?, ?, ?, NULL, NULL, ?, ?)
    """, (conversation_id, user_id, intent,
          json.dumps(actions or [], ensure_ascii=False),
          auto_correctness, now))
    conn.commit()
    return cur.lastrowid


def update_rating(conversation_id: str, rating: str, feedback_text: str = ""):
    conn = _get_conn()
    now = datetime.now().isoformat()
    conn.execute("""
        UPDATE feedback SET rating = ?, feedback_text = ?, updated_at = ?
        WHERE conversation_id = ?
    """, (rating, feedback_text, now, conversation_id))
    conn.commit()


def get_feedback_stats(user_id: str = None) -> dict:
    conn = _get_conn()
    if user_id:
        rows = conn.execute(
            "SELECT rating, COUNT(*) as cnt FROM feedback WHERE user_id=? GROUP BY rating",
            (user_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT rating, COUNT(*) as cnt FROM feedback GROUP BY rating"
        ).fetchall()
    total = sum(r["cnt"] for r in rows)
    stats = {"total": total, "satisfied": 0, "partial": 0, "unsatisfied": 0, "pending": 0}
    for r in rows:
        key = r["rating"] if r["rating"] else "pending"
        if key in stats:
            stats[key] = r["cnt"]
    return stats


def get_recent_feedback(limit: int = 20, user_id: str = None) -> list:
    conn = _get_conn()
    if user_id:
        rows = conn.execute(
            "SELECT * FROM feedback WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM feedback ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def update_feedback_by_id(record_id: int, rating: str, feedback_text: str = ""):
    conn = _get_conn()
    now = datetime.now().isoformat()
    conn.execute("""
        UPDATE feedback SET rating = ?, feedback_text = ?, updated_at = ?
        WHERE id = ?
    """, (rating, feedback_text, now, record_id))
    conn.commit()


def delete_feedback_by_id(record_id: int):
    conn = _get_conn()
    conn.execute("DELETE FROM feedback WHERE id = ?", (record_id,))
    conn.commit()


def pin_feedback(record_id: int):
    conn = _get_conn()
    conn.execute("UPDATE feedback SET pinned = 1 WHERE id = ?", (record_id,))
    conn.commit()


def unpin_feedback(record_id: int):
    conn = _get_conn()
    conn.execute("UPDATE feedback SET pinned = 0 WHERE id = ?", (record_id,))
    conn.commit()


def get_pinned_feedback(limit: int = 200) -> list:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM feedback WHERE pinned = 1 ORDER BY updated_at DESC LIMIT ?",
        (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_user_conversation_summary(hours: int = 72) -> list:
    """Get conversations grouped by user, only admin visible.
    Returns list of { user_id, total, satisfied, partial, unsatisfied,
                       last_active, conversations: [{intent, actions, rating, created_at, feedback_text}] }
    """
    conn = _get_conn()
    from datetime import datetime, timedelta
    since = (datetime.now() - timedelta(hours=hours)).isoformat()
    rows = conn.execute(
        "SELECT * FROM feedback WHERE created_at >= ? ORDER BY created_at DESC",
        (since,)
    ).fetchall()
    users = {}
    for r in rows:
        d = dict(r)
        uid = d.get("user_id", "unknown")
        if uid not in users:
            users[uid] = {
                "user_id": uid,
                "total": 0, "satisfied": 0, "partial": 0, "unsatisfied": 0,
                "last_active": d["created_at"],
                "conversations": []
            }
        u = users[uid]
        u["total"] += 1
        rating = d.get("rating")
        if rating in ("satisfied", "partial", "unsatisfied"):
            u[rating] += 1
        if d["created_at"] > u["last_active"]:
            u["last_active"] = d["created_at"]
        try:
            actions = json.loads(d["actions"]) if d.get("actions") else []
        except Exception:
            actions = []
        u["conversations"].append({
            "intent": d.get("intent", ""),
            "actions": [a.get("name", "") for a in actions if isinstance(a, dict)],
            "rating": d.get("rating"),
            "feedback_text": d.get("feedback_text", ""),
            "created_at": d["created_at"],
        })
    result = sorted(users.values(), key=lambda x: x["last_active"], reverse=True)
    return result


def close():
    if hasattr(_local, "conn") and _local.conn:
        _local.conn.close()
        _local.conn = None