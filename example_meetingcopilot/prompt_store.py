from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_SETUP = {
    "system_role": (
        "You are my assistant copilot, actively listening to a meeting and "
        "helping me answer as myself. Keep responses short, natural, easy to "
        "say out loud, friendly, and professional."
    ),
    "additional_info": (
        "I am Evaldo from 908 AI. 908 AI helps businesses with automation, "
        "artificial intelligence, custom software, integrations, and workflow improvements."
    ),
    "include_hidden_context": True,
}


class PromptStore:
    def __init__(self, setup_path: Path):
        self.setup_path = setup_path
        if not setup_path.exists():
            self.save(DEFAULT_SETUP)

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.setup_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = DEFAULT_SETUP.copy()

        merged = DEFAULT_SETUP.copy()
        merged.update({k: v for k, v in data.items() if v is not None})
        return merged

    def save(self, data: dict[str, Any]) -> dict[str, Any]:
        cleaned = {
            "system_role": str(data.get("system_role", "")).strip(),
            "additional_info": str(data.get("additional_info", "")).strip(),
            "include_hidden_context": bool(data.get("include_hidden_context", True)),
        }
        if not cleaned["system_role"]:
            cleaned["system_role"] = DEFAULT_SETUP["system_role"]
        if not cleaned["additional_info"]:
            cleaned["additional_info"] = DEFAULT_SETUP["additional_info"]

        self.setup_path.write_text(
            json.dumps(cleaned, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )
        return cleaned
