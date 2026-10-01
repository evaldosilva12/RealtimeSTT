from __future__ import annotations

import asyncio
import base64
import datetime
import json
import logging
import re
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from config import settings
from llm_service import LLMRequestError, LLMService
from prompt_builder import build_system, build_user
from storage import Storage


Emit = Callable[[dict[str, Any]], Awaitable[None]]


ACTION_LABELS = {
    "answer_as_me": "Interview answer",
    "answer_last_n": "Answer recent question",
    "clarify": "Clarify",
    "ask_question": "Question to ask",
    "push_back": "Handle concern",
    "give_example": "Example answer",
    "recover_answer": "Recover answer",
    "summarize_context": "Interview notes",
    "next_step": "Follow-up",
    "improve_answer": "Improve",
    "objection_response": "Concern response",
    "screenshot_code_help": "Screenshot code help",
}

MAX_PROFILE_SECTION_CHARS = 1800
UNTRUNCATED_PROFILE_FIELDS = {"resume_text"}
RECENT_ACTION_MEMORY_LIMIT = 2
QUICK_RECENT_ACTION_MEMORY_LIMIT = 1
CONVERSATION_BEFORE_LINES = 6
QUICK_CONVERSATION_BEFORE_LINES = 4
QUICK_RESPONSE_MAX_TOKENS = 380
DEFAULT_RESPONSE_MAX_TOKENS = 450
NO_QUESTION_MARKER = "(No question yet."
WARM_INTERVAL_SECONDS = 240


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
        self._last_warm: dict[str, float] = {}
        self._background: set[asyncio.Task[Any]] = set()

    async def request(self, session_id: str, payload: dict[str, Any]) -> None:
        action_type = payload.get("action_type", "answer_as_me")
        action_id = payload.get("action_id") or str(uuid.uuid4())

        task = asyncio.create_task(self._run_action(session_id, action_id, action_type, payload))
        self.running[action_id] = task
        task.add_done_callback(lambda _: self.running.pop(action_id, None))

    @staticmethod
    def _warm_key(model: str, system_text: str) -> str:
        return f"{model}:{hash(system_text)}"

    def warm_in_background(self) -> None:
        task = asyncio.create_task(self.warm_cache())
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def warm_cache(self) -> None:
        """Keep the provider's prompt cache warm for the active profile (system prompt only)."""
        if not self.llm.openai_enabled:
            return
        profile = self.storage.get_active_interview_profile()
        if not profile:
            return
        model = settings.openai_text_model
        system_text = build_system(profile)
        key = self._warm_key(model, system_text)
        if time.time() - self._last_warm.get(key, 0) < WARM_INTERVAL_SECONDS:
            return
        self._last_warm[key] = time.time()
        try:
            await self.llm.warm_openai(
                [{"role": "system", "content": system_text}, {"role": "user", "content": "Reply with one word: ok"}],
                model,
            )
        except Exception as exc:
            self._last_warm.pop(key, None)
            logging.warning("Cache warm-up failed: %s", exc)

    async def cancel(self, action_id: str) -> None:
        task = self.running.get(action_id)
        if task:
            task.cancel()
            await self.emit({"type": "action.cancelled", "action_id": action_id, "message": "Action cancelled."})

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
            source_label = self._resolve_source_label(action_type, payload, source_text)
            response_mode = self._resolve_response_mode(payload)
            messages, context_debug, context_inspector = self._build_messages(
                session_id,
                action_type,
                source_text,
                source_label,
                payload,
                response_mode,
            )
            self.storage.create_action(action_id, session_id, action_type, source_text)
            await self.emit(
                {
                    "type": "action.requested",
                    "action_id": action_id,
                    "action_type": action_type,
                    "source_text": source_text,
                    "source_label": source_label,
                    "context_debug": context_debug,
                    "context_inspector": context_inspector,
                    "label": ACTION_LABELS.get(action_type, action_type),
                    "response_mode": response_mode,
                }
            )

            first_delta_seen = False

            async def on_delta(delta: str) -> None:
                nonlocal first_delta_seen
                first_delta_seen = True
                await self.emit({"type": "action.delta", "action_id": action_id, "delta": delta})

            model = settings.openai_improve_model if action_type == "improve_answer" else settings.openai_text_model
            notice_task = asyncio.create_task(
                self._emit_wait_notice(action_id, lambda: first_delta_seen)
            )
            try:
                result = await self._stream_with_fallback(
                    action_id,
                    messages,
                    on_delta,
                    action_type=action_type,
                    primary_model=model,
                    response_mode=response_mode,
                )
            finally:
                notice_task.cancel()
            response = result["text"]
            if result["provider"] == "openai":
                self._last_warm[self._warm_key(result["model"], messages[0]["content"])] = time.time()
            self.storage.complete_action(action_id, response)
            await self.emit(
                {
                    "type": "action.completed",
                    "action_id": action_id,
                    "response": response,
                    "model_info": {
                        "provider": result["provider"],
                        "provider_label": result["provider_label"],
                        "model": result["model"],
                        "fallbacks": result["fallbacks"],
                        "planned_chain": result["planned_chain"],
                    },
                }
            )
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

    def _resolve_source_label(self, action_type: str, payload: dict[str, Any], source_text: str) -> str:
        if action_type == "improve_answer":
            return "improve"
        if payload.get("utterance_id"):
            return "selected utterance"
        if payload.get("count"):
            count = int(payload.get("count") or 0)
            return f"last {count}" if source_text else f"last {count} fallback"
        return "direct prompt" if source_text else "recent transcript fallback"

    @staticmethod
    def _resolve_response_mode(payload: dict[str, Any]) -> str:
        mode = str(payload.get("response_mode") or "").strip().lower()
        return "quick" if mode == "quick" else "normal"

    async def _emit_wait_notice(self, action_id: str, first_delta_seen: Callable[[], bool]) -> None:
        await asyncio.sleep(settings.llm_first_wait_notice_seconds)
        if not first_delta_seen():
            await self.emit(
                {
                    "type": "action.status",
                    "action_id": action_id,
                    "message": "Still waiting for the model. You can cancel, or use Quick for the next answer.",
                }
            )

    async def _stream_with_fallback(
        self,
        action_id: str,
        messages: list[dict[str, str]],
        on_delta: Callable[[str], Awaitable[None]],
        action_type: str,
        primary_model: str,
        response_mode: str,
    ) -> dict[str, Any]:
        max_tokens = QUICK_RESPONSE_MAX_TOKENS if response_mode == "quick" else DEFAULT_RESPONSE_MAX_TOKENS
        temperature = 0.45 if response_mode == "quick" else 0.6
        failures: list[dict[str, str]] = []
        chain = self._planned_model_chain(primary_model)

        async def emit_status(message: str) -> None:
            await self.emit({"type": "action.status", "action_id": action_id, "message": message})

        for index, attempt in enumerate(chain):
            streamed_chars = 0

            async def attempt_delta(delta: str) -> None:
                nonlocal streamed_chars
                streamed_chars += len(delta)
                await on_delta(delta)

            try:
                text = await self._run_model_attempt(attempt, messages, attempt_delta, temperature, max_tokens)
                if not text.strip():
                    raise LLMRequestError(f"{attempt['label']} returned an empty response.")
                if self._looks_truncated(text):
                    raise LLMRequestError(f"{attempt['label']} returned a likely truncated response.")
                return {
                    "text": text,
                    "provider": attempt["provider"],
                    "provider_label": attempt["provider_label"],
                    "model": attempt["model"],
                    "fallbacks": failures,
                    "planned_chain": chain,
                }
            except LLMRequestError as exc:
                failures.append(
                    {
                        "provider": attempt["provider"],
                        "model": attempt["model"],
                        "reason": str(exc),
                    }
                )
                next_attempt = chain[index + 1] if index + 1 < len(chain) else None
                if next_attempt:
                    if streamed_chars:
                        await self.emit({"type": "action.reset", "action_id": action_id})
                    await emit_status(
                        f"{attempt['label']} failed. Trying {next_attempt['label']}."
                    )

        raise RuntimeError(self._format_llm_failures(failures))

    async def _run_model_attempt(
        self,
        attempt: dict[str, str],
        messages: list[dict[str, str]],
        on_delta: Callable[[str], Awaitable[None]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        provider = attempt["provider"]
        model = attempt["model"]
        if provider == "groq":
            return await self.llm.stream_chat(
                messages,
                on_delta,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        if provider == "groq_secondary":
            return await self.llm.stream_secondary_chat(
                messages,
                on_delta,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        if provider == "openai":
            return await self.llm.complete_openai_chat(
                messages,
                on_delta,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        raise LLMRequestError(f"Unknown provider: {provider}")

    def _planned_model_chain(self, primary_model: str) -> list[dict[str, str]]:
        attempts = []
        if self.llm.openai_enabled:
            attempts.append(self._model_attempt("openai", primary_model))
        if self.llm.enabled:
            attempts.append(self._model_attempt("groq", settings.groq_text_model))
        if self.llm.secondary_enabled:
            attempts.append(self._model_attempt("groq_secondary", settings.groq_text_model))
        return self._dedupe_model_chain(attempts)

    @staticmethod
    def _looks_truncated(text: str) -> bool:
        cleaned = re.sub(r"\s+", " ", text or "").strip()
        if len(cleaned) < 80:
            return False
        if re.search(r"[.!?][\])}\"']*$", cleaned):
            return False
        tail = cleaned.lower().rsplit(" ", 1)[-1].strip(" ,;:")
        incomplete_tail_words = {
            "a",
            "an",
            "and",
            "as",
            "at",
            "because",
            "but",
            "by",
            "for",
            "from",
            "in",
            "into",
            "making",
            "of",
            "on",
            "or",
            "so",
            "that",
            "the",
            "to",
            "with",
        }
        return cleaned.endswith((",", ";", ":")) or tail in incomplete_tail_words

    @staticmethod
    def _model_attempt(provider: str, model: str) -> dict[str, str]:
        provider_label = {
            "groq": "Groq key 1",
            "groq_secondary": "Groq key 2",
            "openai": "OpenAI",
        }.get(provider, provider)
        return {
            "provider": provider,
            "model": model,
            "label": f"{provider_label} {model}",
            "provider_label": provider_label,
        }

    @staticmethod
    def _dedupe_model_chain(attempts: list[dict[str, str]]) -> list[dict[str, str]]:
        chain: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for attempt in attempts:
            provider = attempt.get("provider", "")
            model = attempt.get("model", "")
            if not provider or not model:
                continue
            key = (provider, model)
            if key in seen:
                continue
            seen.add(key)
            chain.append(attempt)
        return chain

    @staticmethod
    def _format_llm_failures(failures: list[dict[str, str]]) -> str:
        if not failures:
            return "The model request failed."
        details = " ".join(
            f"{failure.get('provider', 'provider')} {failure.get('model', 'model')}: {failure.get('reason', '')}"
            for failure in failures
        )
        return "Model request failed after fallback attempts. " + details

    def _build_messages(
        self,
        session_id: str,
        action_type: str,
        source_text: str,
        source_label: str,
        payload: dict[str, Any],
        response_mode: str,
    ) -> tuple[list[dict[str, str]], dict[str, str], dict[str, Any]]:
        profile = self.storage.get_active_interview_profile() or self.storage.ensure_default_interview_profile()
        quick = response_mode == "quick"
        conversation_before = (
            [] if action_type == "improve_answer" else self._conversation_before(session_id, source_text, payload, quick)
        )
        previous_answers = self._previous_answers(session_id, action_type, quick)
        previous_draft = str(payload.get("previous_response") or "").strip()
        my_notes = self.storage.interview_notes_text(profile["id"]) if profile.get("id") else ""
        style = profile.get("response_style") or "Natural"

        system_text = build_system(profile)
        user_text = build_user(
            action_type,
            source_text,
            conversation_before,
            previous_answers,
            previous_draft,
            style,
            response_mode,
            my_notes,
        )
        messages = [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_text},
        ]

        primary_model = settings.openai_improve_model if action_type == "improve_answer" else settings.openai_text_model
        total_chars = len(system_text) + len(user_text)
        conversation_text = "\n".join(conversation_before)
        answers_text = "\n".join(item["answer"] for item in previous_answers)
        context_inspector = {
            "source_label": source_label,
            "profile_name": profile.get("name") or "Untitled Interview",
            "profile_id": profile.get("id") or "",
            "target_role": profile.get("target_role") or "",
            "company_name": profile.get("company_name") or "",
            "question_detection": {
                "target": source_text,
                "confidence": "model",
                "method": "model_inferred",
                "reason": "The model finds the pending question using the conversation before the selection.",
            },
            "recent_transcript_count": len(conversation_before),
            "blocks": {
                "selected_text": self._inspect_text_block(source_text),
                "conversation_before": self._inspect_text_block(conversation_text),
                "previous_answers": self._inspect_text_block(answers_text),
                "my_notes": self._inspect_text_block(my_notes),
                "previous_draft": self._inspect_text_block(previous_draft),
                "system_rules_and_profile": self._inspect_text_block(system_text),
            },
            "prompt_chars": {
                "system": len(system_text),
                "user": len(user_text),
                "total": total_chars,
                "estimated_tokens": self._estimate_tokens(total_chars),
                "max_output_tokens": QUICK_RESPONSE_MAX_TOKENS if quick else DEFAULT_RESPONSE_MAX_TOKENS,
                "assessment": self._assess_prompt_size(total_chars),
            },
            "response_mode": response_mode,
            "models": {
                "primary": primary_model,
                "planned_chain": self._planned_model_chain(primary_model),
                "groq_primary": settings.groq_text_model if self.llm.enabled else "",
                "groq_secondary": settings.groq_text_model if self.llm.secondary_enabled else "",
            },
            "context_priority": [
                "System: rules, resume, job description, guardrails (stable, cached)",
                "Conversation before the selection",
                "Previous answers (to avoid repetition)",
                "Selected text and task",
            ],
            "memory": {
                "completed_actions_used": len(previous_answers),
                "limit": QUICK_RECENT_ACTION_MEMORY_LIMIT if quick else RECENT_ACTION_MEMORY_LIMIT,
            },
        }
        return messages, {"system": system_text, "user": user_text}, context_inspector

    def _conversation_before(
        self,
        session_id: str,
        source_text: str,
        payload: dict[str, Any],
        quick: bool,
    ) -> list[str]:
        """Transcript lines that come BEFORE the selected text, oldest first."""
        limit = QUICK_CONVERSATION_BEFORE_LINES if quick else CONVERSATION_BEFORE_LINES
        count = max(1, min(int(payload.get("count") or 1), 12))
        display_id = str(payload.get("utterance_id") or "")
        anchor = self.storage.get_utterance(re.sub(r"-\d+$", "", display_id), session_id) if display_id else None
        if anchor:
            rows = self.storage.utterances_until(session_id, anchor["created_at"], limit + 2)
        else:
            rows = list(reversed(self.storage.recent_utterances(session_id, limit=limit + count + 2)))
        lines = [re.sub(r"\s+", " ", row["text"]).strip() for row in rows]
        lines = [line for line in lines if line]

        head = re.sub(r"\s+", " ", source_text or "").strip()[:60]
        before = lines
        if head:
            for index in range(len(lines) - 1, -1, -1):
                position = lines[index].find(head)
                if position >= 0:
                    before = lines[:index]
                    prefix = lines[index][:position].strip()
                    if prefix:
                        before.append(prefix)
                    break
        return before[-limit:]

    def _previous_answers(self, session_id: str, action_type: str, quick: bool) -> list[dict[str, str]]:
        if action_type == "improve_answer":
            return []
        limit = QUICK_RECENT_ACTION_MEMORY_LIMIT if quick else RECENT_ACTION_MEMORY_LIMIT
        answers = []
        for action in reversed(self.storage.recent_completed_actions(session_id, limit=limit + 2)):
            response = str(action.get("response") or "").strip()
            if not response or response.startswith(NO_QUESTION_MARKER):
                continue
            answers.append(
                {
                    "question": re.sub(r"\s+", " ", str(action.get("source_text") or "")).strip(),
                    "answer": response,
                }
            )
        return answers[-limit:]

    def _format_profile_context(
        self,
        profile: dict[str, Any],
        labels: list[tuple[str, str]] | None = None,
    ) -> str:
        if labels is None:
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
            raw_value = str(profile.get(key) or "").strip()
            value = raw_value if key in UNTRUNCATED_PROFILE_FIELDS else self._truncate_text(raw_value, MAX_PROFILE_SECTION_CHARS)
            if value:
                lines.append(f"{label}:\n{value}")
        return "\n\n".join(lines) or "(no interview profile details saved yet)"

    @staticmethod
    def _truncate_text(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        marker = f"\n\n[... truncated {len(text) - max_chars} chars ...]\n\n"
        keep = max(0, max_chars - len(marker))
        head = keep // 2
        tail = keep - head
        return f"{text[:head].rstrip()}{marker}{text[-tail:].lstrip()}"

    @staticmethod
    def _inspect_text_block(text: str) -> dict[str, Any]:
        return {
            "present": bool(text.strip()),
            "chars": len(text),
            "original_chars": len(text),
            "truncated": "[... truncated " in text,
            "max_chars": 0,
        }

    @staticmethod
    def _assess_prompt_size(total_chars: int) -> dict[str, str]:
        if total_chars <= 18000:
            return {
                "level": "healthy",
                "message": "Prompt size is comfortably within the target range for live answers.",
            }
        if total_chars <= 32000:
            return {
                "level": "elevated",
                "message": "Prompt size is elevated but acceptable; consider relying more on summaries if latency rises.",
            }
        return {
            "level": "large",
            "message": "Prompt size is large; reduce raw profile/context blocks before extended live use.",
        }

    @staticmethod
    def _estimate_tokens(total_chars: int) -> int:
        return max(1, round(total_chars / 4))

    async def analyze_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        """One-time review of resume vs. job. Returns guardrails, gaps and a cleaned job description."""
        resume = str(profile.get("resume_text") or "").strip()
        if not resume:
            raise RuntimeError("Add the resume text first.")

        sections = [
            ("Resume", "resume_text"),
            ("Job description", "job_description_text"),
            ("Cover letter", "cover_letter_text"),
            ("LinkedIn/profile", "linkedin_text"),
            ("Personal notes", "personal_notes"),
            ("Company information", "company_info"),
        ]
        data_blocks = []
        for label, key in sections:
            value = str(profile.get(key) or "").strip()
            if value:
                data_blocks.append(f"<{key}>\n{value}\n</{key}>")
        has_job = bool(str(profile.get("job_description_text") or "").strip())
        today = datetime.date.today().isoformat()

        messages = [
            {
                "role": "system",
                "content": (
                    "You prepare the guardrails for a live job-interview copilot. The copilot writes answers "
                    "that the candidate speaks out loud as themselves, so a wrong claim can cost the candidate the job. "
                    "Be factual and conservative. Use only the text you are given. Never invent facts about the candidate."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Today is {today}.\n\n"
                    + "\n\n".join(data_blocks)
                    + "\n\nReturn ONLY a JSON object with exactly these keys:\n"
                    '"guardrails": a string of 4 to 10 lines, each starting with "- ", written in first person '
                    "(\"I ...\"), plain English, one or two short sentences each. Cover only what the text supports:\n"
                    "  1. Concrete tools, technologies, certifications, domains or experience that the job asks for and "
                    "the resume does not show. Write: \"I have no direct experience with X, Y. Never claim it. If asked, "
                    "say so plainly in one sentence and connect it to my closest real experience.\" Skip soft skills.\n"
                    "  2. Scope limits that the resume wording shows (for example supported versus led, coordinated "
                    "versus built). Only when the verbs in the resume clearly say it.\n"
                    "  3. Whether the most recent job is current or already ended, using today's date.\n"
                    "  4. The metrics the resume actually states. Never invent other numbers and never explain how a "
                    "number was measured.\n"
                    "  5. One positioning line: how I should come across, based on my real experience.\n"
                    "  If the text does not clearly support a line, leave that line out.\n"
                    '"gaps": an array of up to 8 short strings. Each string names one job requirement and says what the '
                    "resume shows or lacks for it. Empty array if there is no job description.\n"
                    '"clean_job_description": the job description reduced to the role summary, responsibilities, '
                    "requirements, nice-to-haves and facts about the team or culture. Keep the original wording. Remove "
                    "benefits, perks, salary, how to apply, legal text and repeated slogans. Plain text with short "
                    "headings. If there is no job description, return an empty string."
                ),
            },
        ]
        raw = await self.llm.complete_chat(
            messages,
            model=settings.openai_analysis_model,
            temperature=0.2,
            max_tokens=3000,
            prefer_openai=True,
        )
        result = self._parse_analysis(raw)
        if not has_job:
            result["gaps"] = []
            result["clean_job_description"] = ""
        return result

    @staticmethod
    def _parse_analysis(raw: str) -> dict[str, Any]:
        text = (raw or "").strip()
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise RuntimeError("The analysis did not return JSON. Try again.")
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise RuntimeError("The analysis returned invalid JSON. Try again.") from exc
        guardrails = str(data.get("guardrails") or "").strip()
        gaps = [str(item).strip() for item in (data.get("gaps") or []) if str(item).strip()]
        return {
            "guardrails": guardrails,
            "gaps": gaps[:8],
            "clean_job_description": str(data.get("clean_job_description") or "").strip(),
        }

    async def _run_screenshot_action(self, session_id: str, action_id: str) -> None:
        prompt = (
            "Analyze this screenshot for code, meeting content, or technical context. "
            "If code is present, explain the issue and suggest a clear fix. "
            "If no code is present, summarize the useful context and suggest what I should say next."
        )
        self.storage.create_action(action_id, session_id, "screenshot_code_help", "Screenshot analysis")
        await self.emit(
            {
                "type": "action.requested",
                "action_id": action_id,
                "action_type": "screenshot_code_help",
                "source_text": "Screenshot analysis",
                "source_label": "screenshot",
                "context_debug": {"system": "(vision action)", "user": "Screenshot analysis"},
                "context_inspector": {
                    "source_label": "screenshot",
                    "profile_name": "",
                    "question_detection": {
                        "target": "Screenshot analysis",
                        "confidence": "high",
                        "method": "vision_action",
                        "reason": "Screenshot action uses image context.",
                    },
                    "blocks": {},
                    "prompt_chars": {"system": 0, "user": len(prompt), "total": len(prompt)},
                    "context_priority": ["Screenshot image", "Vision prompt"],
                },
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

        async def on_delta(delta: str) -> None:
            await self.emit({"type": "action.delta", "action_id": action_id, "delta": delta})

        response = await self.llm.analyze_image(image_base64, prompt, on_delta)
        self.storage.complete_action(action_id, response)
        await self.emit({"type": "action.completed", "action_id": action_id, "response": response})
