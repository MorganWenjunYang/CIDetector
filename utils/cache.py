"""Lightweight SQLite cache to avoid redundant network requests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

_DB_PATH = Path(__file__).resolve().parent.parent / ".cache.db"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cache "
        "(key TEXT PRIMARY KEY, value TEXT, expires_at REAL)"
    )
    return conn


def cache_key(prefix: str, params: dict[str, Any]) -> str:
    raw = json.dumps(params, sort_keys=True, default=str)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"{prefix}:{digest}"


def get(key: str) -> Any | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    value, expires_at = row
    if expires_at and time.time() > expires_at:
        delete(key)
        return None
    return json.loads(value)


def put(key: str, value: Any, ttl_seconds: int = 3600) -> None:
    conn = _get_conn()
    expires_at = time.time() + ttl_seconds
    conn.execute(
        "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
        (key, json.dumps(value, default=str), expires_at),
    )
    conn.commit()
    conn.close()


def delete(key: str) -> None:
    conn = _get_conn()
    conn.execute("DELETE FROM cache WHERE key = ?", (key,))
    conn.commit()
    conn.close()


def clear_expired() -> int:
    conn = _get_conn()
    cur = conn.execute(
        "DELETE FROM cache WHERE expires_at < ?", (time.time(),)
    )
    conn.commit()
    count = cur.rowcount
    conn.close()
    return count
