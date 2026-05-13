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
        self.connection.execute("PRAGMA journal_mode=MEMORY")
        self.connection.execute("PRAGMA temp_store=MEMORY")
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

            CREATE TABLE IF NOT EXISTS interview_profiles (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                target_role TEXT NOT NULL DEFAULT '',
                company_name TEXT NOT NULL DEFAULT '',
                resume_text TEXT NOT NULL DEFAULT '',
                job_description_text TEXT NOT NULL DEFAULT '',
                cover_letter_text TEXT NOT NULL DEFAULT '',
                linkedin_text TEXT NOT NULL DEFAULT '',
                company_info TEXT NOT NULL DEFAULT '',
                recruiter_notes TEXT NOT NULL DEFAULT '',
                personal_notes TEXT NOT NULL DEFAULT '',
                previous_interview_context TEXT NOT NULL DEFAULT '',
                additional_instructions TEXT NOT NULL DEFAULT '',
                response_style TEXT NOT NULL DEFAULT 'Natural',
                internal_candidate_profile TEXT NOT NULL DEFAULT '',
                archived INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS app_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
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

    def ensure_default_interview_profile(self) -> dict[str, Any]:
        profiles = self.list_interview_profiles(include_archived=True)
        if profiles:
            active = self.get_active_interview_profile()
            if active:
                return active
            self.set_active_interview_profile(profiles[0]["id"])
            return profiles[0]

        profile = self.save_interview_profile(
            {
                "name": "Default Job Interview",
                "target_role": "",
                "company_name": "",
                "resume_text": "",
                "job_description_text": "",
                "cover_letter_text": "",
                "linkedin_text": "",
                "company_info": "",
                "recruiter_notes": "",
                "personal_notes": "",
                "previous_interview_context": "",
                "additional_instructions": "",
                "response_style": "Natural",
                "internal_candidate_profile": "",
                "archived": 0,
            }
        )
        self.set_active_interview_profile(profile["id"])
        return profile

    def list_interview_profiles(self, include_archived: bool = False) -> list[dict[str, Any]]:
        if include_archived:
            rows = self.connection.execute(
                """
                SELECT * FROM interview_profiles
                ORDER BY archived ASC, updated_at DESC
                """
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT * FROM interview_profiles
                WHERE archived = 0
                ORDER BY updated_at DESC
                """
            ).fetchall()
        return [self._profile_from_row(row) for row in rows]

    def get_interview_profile(self, profile_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM interview_profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
        return self._profile_from_row(row) if row else None

    def get_active_interview_profile(self) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT value FROM app_state WHERE key = 'active_interview_profile_id'"
        ).fetchone()
        if row:
            profile = self.get_interview_profile(row["value"])
            if profile and not profile["archived"]:
                return profile
        profiles = self.list_interview_profiles()
        if not profiles:
            return None
        self.set_active_interview_profile(profiles[0]["id"])
        return profiles[0]

    def set_active_interview_profile(self, profile_id: str) -> dict[str, Any] | None:
        profile = self.get_interview_profile(profile_id)
        if not profile or profile["archived"]:
            return None
        self.connection.execute(
            """
            INSERT INTO app_state (key, value)
            VALUES ('active_interview_profile_id', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (profile_id,),
        )
        self.connection.commit()
        return profile

    def save_interview_profile(self, data: dict[str, Any]) -> dict[str, Any]:
        profile_id = str(data.get("id") or "")
        if not profile_id:
            import uuid

            profile_id = str(uuid.uuid4())

        now = time.time()
        existing = self.get_interview_profile(profile_id)
        cleaned = self._clean_profile(data)
        created_at = existing["created_at"] if existing else now
        self.connection.execute(
            """
            INSERT INTO interview_profiles (
                id, name, target_role, company_name, resume_text, job_description_text,
                cover_letter_text, linkedin_text, company_info, recruiter_notes,
                personal_notes, previous_interview_context, additional_instructions,
                response_style, internal_candidate_profile, archived, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                target_role = excluded.target_role,
                company_name = excluded.company_name,
                resume_text = excluded.resume_text,
                job_description_text = excluded.job_description_text,
                cover_letter_text = excluded.cover_letter_text,
                linkedin_text = excluded.linkedin_text,
                company_info = excluded.company_info,
                recruiter_notes = excluded.recruiter_notes,
                personal_notes = excluded.personal_notes,
                previous_interview_context = excluded.previous_interview_context,
                additional_instructions = excluded.additional_instructions,
                response_style = excluded.response_style,
                internal_candidate_profile = excluded.internal_candidate_profile,
                archived = excluded.archived,
                updated_at = excluded.updated_at
            """,
            (
                profile_id,
                cleaned["name"],
                cleaned["target_role"],
                cleaned["company_name"],
                cleaned["resume_text"],
                cleaned["job_description_text"],
                cleaned["cover_letter_text"],
                cleaned["linkedin_text"],
                cleaned["company_info"],
                cleaned["recruiter_notes"],
                cleaned["personal_notes"],
                cleaned["previous_interview_context"],
                cleaned["additional_instructions"],
                cleaned["response_style"],
                cleaned["internal_candidate_profile"],
                int(cleaned["archived"]),
                created_at,
                now,
            ),
        )
        self.connection.commit()
        return self.get_interview_profile(profile_id) or {}

    def duplicate_interview_profile(self, profile_id: str) -> dict[str, Any] | None:
        profile = self.get_interview_profile(profile_id)
        if not profile:
            return None
        copy = dict(profile)
        copy.pop("id", None)
        copy["name"] = f"{profile['name']} copy"
        copy["archived"] = 0
        return self.save_interview_profile(copy)

    def archive_interview_profile(self, profile_id: str) -> dict[str, Any] | None:
        profile = self.get_interview_profile(profile_id)
        if not profile:
            return None
        profile["archived"] = 1
        saved = self.save_interview_profile(profile)
        active = self.get_active_interview_profile()
        if not active or active["id"] == profile_id:
            profiles = self.list_interview_profiles()
            if profiles:
                self.set_active_interview_profile(profiles[0]["id"])
        return saved

    def update_internal_candidate_profile(self, profile_id: str, content: str) -> dict[str, Any] | None:
        profile = self.get_interview_profile(profile_id)
        if not profile:
            return None
        profile["internal_candidate_profile"] = content.strip()
        return self.save_interview_profile(profile)

    def _clean_profile(self, data: dict[str, Any]) -> dict[str, Any]:
        allowed_styles = {"Natural", "Professional", "Concise", "Expanded"}
        style = str(data.get("response_style") or "Natural").strip()
        if style not in allowed_styles:
            style = "Natural"
        name = str(data.get("name") or "").strip()
        target_role = str(data.get("target_role") or "").strip()
        company_name = str(data.get("company_name") or "").strip()
        if not name:
            parts = [part for part in [target_role, company_name] if part]
            name = " - ".join(parts) or "Untitled Interview"
        return {
            "name": name,
            "target_role": target_role,
            "company_name": company_name,
            "resume_text": str(data.get("resume_text") or "").strip(),
            "job_description_text": str(data.get("job_description_text") or "").strip(),
            "cover_letter_text": str(data.get("cover_letter_text") or "").strip(),
            "linkedin_text": str(data.get("linkedin_text") or "").strip(),
            "company_info": str(data.get("company_info") or "").strip(),
            "recruiter_notes": str(data.get("recruiter_notes") or "").strip(),
            "personal_notes": str(data.get("personal_notes") or "").strip(),
            "previous_interview_context": str(data.get("previous_interview_context") or "").strip(),
            "additional_instructions": str(data.get("additional_instructions") or "").strip(),
            "response_style": style,
            "internal_candidate_profile": str(data.get("internal_candidate_profile") or "").strip(),
            "archived": bool(data.get("archived", False)),
        }

    def _profile_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["archived"] = bool(data["archived"])
        return data


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=True, separators=(",", ":"))
