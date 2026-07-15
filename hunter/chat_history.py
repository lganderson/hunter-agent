"""Durable local storage for Hunter's agent conversation."""

import json
from datetime import datetime

from . import sqlite_store


DEFAULT_HISTORY_LIMIT = 200
API_VERSION = 2


def _json_value(value, fallback):
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def list_messages(limit=DEFAULT_HISTORY_LIMIT):
    sqlite_store.initialize()
    try:
        limit = max(1, min(DEFAULT_HISTORY_LIMIT, int(limit)))
    except (TypeError, ValueError):
        limit = DEFAULT_HISTORY_LIMIT
    with sqlite_store.connect() as connection:
        rows = connection.execute(
            "SELECT id, role, content, tool_calls_json, context_json, created_at "
            "FROM agent_messages ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "role": row["role"],
            "content": row["content"] or "",
            "tool_calls": _json_value(row["tool_calls_json"], []),
            "context": _json_value(row["context_json"], {}),
            "created_at": row["created_at"],
        }
        for row in reversed(rows)
    ]


def record_exchange(user_content, assistant_content, tool_calls=None, context=None):
    user_content = str(user_content or "").strip()
    assistant_content = str(assistant_content or "").strip()
    if not user_content:
        raise ValueError("Chat message is required.")
    if not assistant_content:
        raise ValueError("Assistant response is required.")

    sqlite_store.initialize()
    created_at = datetime.now().isoformat(timespec="seconds")
    tool_calls_json = json.dumps(tool_calls or [], sort_keys=True)
    context_json = json.dumps(context or {}, sort_keys=True)
    with sqlite_store.connect() as connection:
        user_cursor = connection.execute(
            "INSERT INTO agent_messages(role, content, tool_calls_json, context_json, created_at) "
            "VALUES ('user', ?, '[]', ?, ?)",
            (user_content, context_json, created_at),
        )
        assistant_cursor = connection.execute(
            "INSERT INTO agent_messages(role, content, tool_calls_json, context_json, created_at) "
            "VALUES ('assistant', ?, ?, ?, ?)",
            (assistant_content, tool_calls_json, context_json, created_at),
        )
        user_id = int(user_cursor.lastrowid)
        assistant_id = int(assistant_cursor.lastrowid)
    return {
        "user_id": user_id,
        "assistant_id": assistant_id,
    }


def clear_messages():
    sqlite_store.initialize()
    with sqlite_store.connect() as connection:
        count = int(connection.execute("SELECT COUNT(*) AS total FROM agent_messages").fetchone()["total"])
        connection.execute("DELETE FROM agent_messages")
    return {"cleared": count}
