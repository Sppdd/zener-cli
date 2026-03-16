"""
server/hive.py
Hive Memory — in-session state store.
Keeps the last N key/value facts across agent turns so the executor
has context without re-reading the full conversation history.

Note: This is per-container in-memory state.
      Firestore (server/session.py) persists session metadata across restarts.
"""
from __future__ import annotations

import threading
from typing import Any

_HIVE: dict[str, dict[str, str]] = {}
_LOCK = threading.Lock()

MAX_KEYS_PER_SESSION = 20


def hive_write(session_id: str, key: str, value: Any) -> None:
    """Store a key/value pair in the session hive."""
    with _LOCK:
        bucket = _HIVE.setdefault(session_id, {})
        bucket[key] = str(value)[:500]  # cap value length
        # Evict oldest keys if over limit
        if len(bucket) > MAX_KEYS_PER_SESSION:
            oldest = next(iter(bucket))
            del bucket[oldest]


def hive_read(session_id: str, key: str = "*") -> dict[str, str]:
    """Read one or all keys from the session hive."""
    with _LOCK:
        mem = _HIVE.get(session_id, {})
        if key == "*":
            return dict(mem)
        return {key: mem[key]} if key in mem else {}


def hive_for_prompt(session_id: str) -> str:
    """
    Format the last 5 hive entries as a prompt snippet.
    Injected into the executor system prompt for context continuity.
    """
    with _LOCK:
        mem = _HIVE.get(session_id, {})
    if not mem:
        return ""
    recent = list(mem.items())[-5:]
    lines = [f"  - {k}: {v[:100]}" for k, v in recent]
    return "[Session Memory]:\n" + "\n".join(lines)


def hive_clear(session_id: str) -> None:
    """Remove all hive data for a session (called on session end)."""
    with _LOCK:
        _HIVE.pop(session_id, None)
