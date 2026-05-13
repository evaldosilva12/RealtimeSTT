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
from storage import Storage


Emit = Callable[[dict[str, Any]], Awaitable[None]]


ACTION_LABELS = {
    "answer_as_me": "Interview answer",
    "answer_last_n": "Answer recent question",
    "clarify": "Clarify",
    "ask_question": "Question to ask",
    "push_back": "Handle concern",
    "summarize_context": "Interview notes",
    "next_step": "Follow-up",
    "improve_answer": "Improve",
    "objection_response": "Concern response",
    "screenshot_code_help": "Screenshot code help",
}


INTERVIEW_SYSTEM_PROMPT = """
You are a real-time job interview copilot helping the user answer as themselves.
Your job is to produce natural spoken English that sounds like a smart professional,
not like a scripted AI answer.

Hard rules:
- Answer in first person, as the candidate.
- Never invent experience, tools, metrics, titles, credentials, or achievements.
- Treat the job description as guidance only. The user's real background comes first.
- Do not repeat job-description phrases verbatim.
- Avoid corporate-template language, keyword stuffing, LinkedIn-style phrasing, and motivational speech.
- Avoid perfect STAR formatting unless the user explicitly asks for a structured answer.
- Keep answers easy to say out loud, with realistic pacing and short-to-medium length.
- The user may be a fluent non-native English speaker, so keep language clear, natural, and slightly conversational.
""".strip()


class ActionService:
    def __init__(
        self,
        storage: Storage,
        llm: LLMService,
        emit: Emit,
    ) -> None:
        self.storage = storage
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

            messages = self._build_messages(session_id, action_type, source_text, payload)

            async def on_delta(delta: str) -> None:
                await self.emit({"type": "action.delta", "action_id": action_id, "delta": delta})

            model = settings.groq_quality_model if action_type == "improve_answer" else settings.groq_text_model
            response = await self.llm.stream_chat(messages, on_delta, model=model)
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

    def _build_messages(
        self,
        session_id: str,
        action_type: str,
        source_text: str,
        payload: dict[str, Any],
    ) -> list[dict[str, str]]:
        profile = self.storage.get_active_interview_profile() or self.storage.ensure_default_interview_profile()
        recent_others = self.storage.recent_utterances(session_id, limit=8)
        recent_context = "\n".join(row["text"] for row in reversed(recent_others))
        hidden_context = self.storage.recent_hidden_context(session_id)

        task = {
            "answer_as_me": "Create a natural interview answer to the selected question.",
            "answer_last_n": "Use the recent transcript to answer the interviewer's latest question.",
            "clarify": "Create a short clarification I can say if the question is unclear.",
            "ask_question": "Create one thoughtful question I can ask the interviewer next.",
            "push_back": "Create a calm response that addresses a concern without sounding defensive.",
            "summarize_context": "Summarize the interview context and identify what I should emphasize next.",
            "next_step": "Create a concise follow-up or next-step response I can say.",
            "improve_answer": "Improve the previous draft while keeping it natural and spoken.",
            "objection_response": "Write a calm, honest response to the concern or objection in the selected text.",
        }.get(action_type, "Create a useful short interview response as me.")

        previous_response = str(payload.get("previous_response") or "").strip()
        profile_context = self._format_profile_context(profile)
        style = profile.get("response_style") or "Natural"
        style_instruction = {
            "Natural": "Use the most conversational and human version. This is the default.",
            "Professional": "Make it slightly more polished, but still spoken and not corporate.",
            "Concise": "Keep it brief enough for a quick live answer.",
            "Expanded": "Give a fuller answer, but do not turn it into an essay.",
        }.get(style, "Use a natural conversational style.")

        user_content = (
            "Active interview profile:\n"
            f"{profile_context}\n\n"
            "Internal candidate profile:\n"
            f"{profile.get('internal_candidate_profile') or '(not generated yet; infer cautiously from the pasted profile text)'}\n\n"
            "Interviewer recently said:\n"
            f"{recent_context or '(no recent transcript yet)'}\n\n"
            "My recent hidden context, for continuity only:\n"
            f"{hidden_context or '(none)'}\n\n"
            "Selected or latest interviewer prompt:\n"
            f"{source_text or '(none)'}\n\n"
            "Previous draft, if improving:\n"
            f"{previous_response or '(none)'}\n\n"
            f"Response style:\n{style} - {style_instruction}\n\n"
            f"Task:\n{task}\n\n"
            "Rules: answer in first person, keep it natural, do not invent facts, and make it easy to say out loud."
        )

        return [
            {"role": "system", "content": INTERVIEW_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

    def _format_profile_context(self, profile: dict[str, Any]) -> str:
        labels = [
            ("Interview name", "name"),
            ("Target role", "target_role"),
            ("Company", "company_name"),
            ("Resume", "resume_text"),
            ("Job description", "job_description_text"),
            ("Cover letter", "cover_letter_text"),
            ("LinkedIn/profile", "linkedin_text"),
            ("Company information", "company_info"),
            ("Recruiter notes", "recruiter_notes"),
            ("Personal notes", "personal_notes"),
            ("Previous interview context", "previous_interview_context"),
            ("Additional instructions", "additional_instructions"),
        ]
        lines = []
        for label, key in labels:
            value = str(profile.get(key) or "").strip()
            if value:
                lines.append(f"{label}:\n{value}")
        return "\n\n".join(lines) or "(no interview profile details saved yet)"

    async def generate_internal_profile(self, profile: dict[str, Any]) -> str:
        profile_context = self._format_profile_context(profile)
        messages = [
            {
                "role": "system",
                "content": (
                    "Create an internal candidate profile for a real-time job interview copilot. "
                    "Do not write a candidate-facing answer. Be factual, conservative, and explicit "
                    "about what should not be claimed."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Use only the pasted text below. The job description is guidance, not truth.\n\n"
                    f"{profile_context}\n\n"
                    "Return a concise internal profile with these headings:\n"
                    "- Candidate Identity Summary\n"
                    "- Communication Style\n"
                    "- Strongest Themes\n"
                    "- Safe Examples To Reuse\n"
                    "- Risk Areas\n"
                    "- Strategic Framing Opportunities\n"
                    "- Claims To Avoid"
                ),
            },
        ]
        return await self.llm.complete_chat(
            messages,
            temperature=0.25,
            max_tokens=1200,
            prefer_openai=True,
        )

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
