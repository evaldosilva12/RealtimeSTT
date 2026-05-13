from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent
ENV_PATH = BASE_DIR / ".env"


def load_env_file(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_env_file()


class Settings:
    host = os.getenv("MEETING_COPILOT_HOST", "localhost")
    ws_port = int(os.getenv("MEETING_COPILOT_WS_PORT", "8011"))
    stt_model = os.getenv("MEETING_COPILOT_STT_MODEL", "large-v2")
    realtime_model = os.getenv("MEETING_COPILOT_REALTIME_MODEL", "tiny.en")
    language = os.getenv("MEETING_COPILOT_LANGUAGE", "en")
    groq_api_key = os.getenv("GROQ_API_KEY", "")
    groq_text_model = os.getenv("GROQ_TEXT_MODEL", "llama-3.3-70b-versatile")
    groq_vision_model = os.getenv(
        "GROQ_VISION_MODEL",
        "meta-llama/llama-4-scout-17b-16e-instruct",
    )
    setup_path = BASE_DIR / "setup_data.json"
    db_path = BASE_DIR / "meeting_copilot.sqlite3"
    screenshot_dir = BASE_DIR / "tmp"


settings = Settings()
