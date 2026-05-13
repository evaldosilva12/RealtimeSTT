from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class Storage:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.connection = sqlite3.connect(db_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                started_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS utterances (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS hidden_context (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS actions (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                action_type TEXT NOT NULL,
                source_text TEXT NOT NULL,
                response TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                completed_at REAL
            );
            """
        )
        self.connection.commit()

    def create_session(self, session_id: str) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO sessions (id, started_at) VALUES (?, ?)",
            (session_id, time.time()),
        )
        self.connection.commit()

    def save_utterance(
        self,
        utterance_id: str,
        session_id: str,
        source: str,
        status: str,
        text: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO utterances
            (id, session_id, source, status, text, created_at)
            VALUES (?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM utterances WHERE id = ?), ?))
            """,
            (utterance_id, session_id, source, status, text, utterance_id, time.time()),
        )
        self.connection.commit()

    def recent_utterances(
        self,
        session_id: str,
        limit: int = 8,
        source: str = "other",
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM utterances
            WHERE session_id = ? AND source = ? AND status = 'final'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (session_id, source, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def save_hidden_context(self, context_id: str, session_id: str, text: str) -> None:
        self.connection.execute(
            """
            INSERT INTO hidden_context (id, session_id, text, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (context_id, session_id, text, time.time()),
        )
        self.connection.commit()

    def recent_hidden_context(self, session_id: str, limit: int = 5) -> str:
        rows = self.connection.execute(
            """
            SELECT text FROM hidden_context
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
        return "\n".join(row["text"] for row in reversed(rows))

    def create_action(
        self,
        action_id: str,
        session_id: str,
        action_type: str,
        source_text: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO actions (id, session_id, action_type, source_text, status, created_at)
            VALUES (?, ?, ?, ?, 'running', ?)
            """,
            (action_id, session_id, action_type, source_text, time.time()),
        )
        self.connection.commit()

    def complete_action(self, action_id: str, response: str, status: str = "completed") -> None:
        self.connection.execute(
            """
            UPDATE actions
            SET response = ?, status = ?, completed_at = ?
            WHERE id = ?
            """,
            (response, status, time.time(), action_id),
        )
        self.connection.commit()

    def dump_recent_state(self, session_id: str) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "utterances": self.recent_utterances(session_id, limit=20),
            "hidden_context": self.recent_hidden_context(session_id, limit=5),
        }


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=True, separators=(",", ":"))
