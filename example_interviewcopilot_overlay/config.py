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
    ws_port = int(os.getenv("INTERVIEW_COPILOT_WS_PORT", "8015"))
    stt_model = os.getenv("MEETING_COPILOT_STT_MODEL", "medium.en")
    realtime_model = os.getenv("MEETING_COPILOT_REALTIME_MODEL", "tiny.en")
    language = os.getenv("MEETING_COPILOT_LANGUAGE", "en")
    groq_api_key = os.getenv("GROQ_API_KEY", "")
    groq_api_key_2 = os.getenv("GROQ_API_KEY_2", "")
    groq_text_model = os.getenv("GROQ_TEXT_MODEL", "openai/gpt-oss-20b")
    groq_fallback_model = os.getenv("GROQ_FALLBACK_MODEL", "")
    groq_quality_model = os.getenv("GROQ_QUALITY_MODEL", "llama-3.3-70b-versatile")
    groq_vision_model = os.getenv(
        "GROQ_VISION_MODEL",
        "meta-llama/llama-4-scout-17b-16e-instruct",
    )
    openai_api_key = os.getenv("OPENAI_API_KEY", "")
    openai_setup_model = os.getenv("OPENAI_SETUP_MODEL", "gpt-4.1-mini")
    openai_text_model = os.getenv("OPENAI_TEXT_MODEL", "gpt-4.1-mini")
    openai_improve_model = os.getenv("OPENAI_IMPROVE_MODEL", "gpt-5.4-mini")
    openai_fallback_model = os.getenv("OPENAI_FALLBACK_MODEL", openai_text_model)
    llm_request_timeout_seconds = float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "30"))
    llm_first_wait_notice_seconds = float(os.getenv("LLM_FIRST_WAIT_NOTICE_SECONDS", "10"))
    db_path = BASE_DIR / "interview_runtime.sqlite3"
    screenshot_dir = BASE_DIR / "tmp"


settings = Settings()
