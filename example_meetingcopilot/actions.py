from __future__ import annotations

import asyncio
import base64
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from config import settings
from llm_service import LLMService
from prompt_store import PromptStore
from storage import Storage


Emit = Callable[[dict[str, Any]], Awaitable[None]]


ACTION_LABELS = {
    "answer_as_me": "Answer as me",
    "answer_last_n": "Answer with recent context",
    "summarize_context": "Summarize context",
    "objection_response": "Handle objection",
    "screenshot_code_help": "Screenshot code help",
}


class ActionService:
    def __init__(
        self,
        storage: Storage,
        prompt_store: PromptStore,
        llm: LLMService,
        emit: Emit,
    ) -> None:
        self.storage = storage
        self.prompt_store = prompt_store
        self.llm = llm
        self.emit = emit
        self.running: dict[str, asyncio.Task[Any]] = {}

    async def request(self, session_id: str, payload: dict[str, Any]) -> None:
        action_type = payload.get("action_type", "answer_as_me")
        action_id = payload.get("action_id") or str(uuid.uuid4())

        task = asyncio.create_task(self._run_action(session_id, action_id, action_type, payload))
        self.running[action_id] = task
        task.add_done_callback(lambda _: self.running.pop(action_id, None))

    async def cancel(self, action_id: str) -> None:
        task = self.running.get(action_id)
        if task:
            task.cancel()
            await self.emit({"type": "action.error", "action_id": action_id, "message": "Action cancelled."})

    async def _run_action(
        self,
        session_id: str,
        action_id: str,
        action_type: str,
        payload: dict[str, Any],
    ) -> None:
        try:
            if action_type == "screenshot_code_help":
                await self._run_screenshot_action(session_id, action_id)
                return

            source_text = self._resolve_source_text(session_id, payload)
            self.storage.create_action(action_id, session_id, action_type, source_text)
            await self.emit(
                {
                    "type": "action.requested",
                    "action_id": action_id,
                    "action_type": action_type,
                    "source_text": source_text,
                    "label": ACTION_LABELS.get(action_type, action_type),
                }
            )

            messages = self._build_messages(session_id, action_type, source_text)

            async def on_delta(delta: str) -> None:
                await self.emit({"type": "action.delta", "action_id": action_id, "delta": delta})

            response = await self.llm.stream_chat(messages, on_delta)
            self.storage.complete_action(action_id, response)
            await self.emit({"type": "action.completed", "action_id": action_id, "response": response})
        except asyncio.CancelledError:
            self.storage.complete_action(action_id, "", status="cancelled")
            raise
        except Exception as exc:
            self.storage.complete_action(action_id, "", status="error")
            await self.emit({"type": "action.error", "action_id": action_id, "message": str(exc)})

    def _resolve_source_text(self, session_id: str, payload: dict[str, Any]) -> str:
        explicit_text = str(payload.get("text") or "").strip()
        if explicit_text:
            return explicit_text

        count = int(payload.get("count") or 4)
        recent = self.storage.recent_utterances(session_id, limit=max(1, min(count, 12)))
        return " ".join(row["text"] for row in reversed(recent)).strip()

    def _build_messages(self, session_id: str, action_type: str, source_text: str) -> list[dict[str, str]]:
        setup = self.prompt_store.load()
        recent_others = self.storage.recent_utterances(session_id, limit=8)
        recent_context = "\n".join(row["text"] for row in reversed(recent_others))
        hidden_context = (
            self.storage.recent_hidden_context(session_id)
            if setup.get("include_hidden_context", True)
            else ""
        )

        task = {
            "answer_as_me": "Create a short answer as me. It must sound natural spoken out loud.",
            "answer_last_n": "Use the recent context to create a concise answer as me.",
            "summarize_context": "Summarize what the other participants are saying and identify the best next response.",
            "objection_response": "Write a calm, helpful response to the concern or objection in the selected text.",
        }.get(action_type, "Create a useful short response as me.")

        user_content = (
            "Important background:\n"
            f"{setup['additional_info']}\n\n"
            "Other participants recently said:\n"
            f"{recent_context or '(no recent transcript yet)'}\n\n"
            "My recent hidden context, for continuity only:\n"
            f"{hidden_context or '(none)'}\n\n"
            "Selected trigger from the other participants:\n"
            f"{source_text or '(none)'}\n\n"
            f"Task:\n{task}\n\n"
            "Rules: answer in first person, keep it brief, simple, friendly, and practical."
        )

        return [
            {"role": "system", "content": setup["system_role"]},
            {"role": "user", "content": user_content},
        ]

    async def _run_screenshot_action(self, session_id: str, action_id: str) -> None:
        self.storage.create_action(action_id, session_id, "screenshot_code_help", "Screenshot analysis")
        await self.emit(
            {
                "type": "action.requested",
                "action_id": action_id,
                "action_type": "screenshot_code_help",
                "source_text": "Screenshot analysis",
                "label": ACTION_LABELS["screenshot_code_help"],
            }
        )

        try:
            from PIL import ImageGrab
        except ImportError as exc:
            raise RuntimeError("Pillow is required for screenshot actions.") from exc

        settings.screenshot_dir.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(
            suffix=".jpg",
            dir=settings.screenshot_dir,
            delete=False,
        ) as handle:
            screenshot_path = Path(handle.name)

        screenshot = ImageGrab.grab()
        screenshot.convert("RGB").save(screenshot_path, quality=45)
        image_base64 = base64.b64encode(screenshot_path.read_bytes()).decode("utf-8")

        prompt = (
            "Analyze this screenshot for code, meeting content, or technical context. "
            "If code is present, explain the issue and suggest a clear fix. "
            "If no code is present, summarize the useful context and suggest what I should say next."
        )

        async def on_delta(delta: str) -> None:
            await self.emit({"type": "action.delta", "action_id": action_id, "delta": delta})

        response = await self.llm.analyze_image(image_base64, prompt, on_delta)
        self.storage.complete_action(action_id, response)
        await self.emit({"type": "action.completed", "action_id": action_id, "response": response})
