from __future__ import annotations

import asyncio
import base64
import re
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
    "give_example": "Example answer",
    "recover_answer": "Recover answer",
    "summarize_context": "Interview notes",
    "next_step": "Follow-up",
    "improve_answer": "Improve",
    "objection_response": "Concern response",
    "screenshot_code_help": "Screenshot code help",
}

MAX_CONTEXT_BLOCK_CHARS = 5000
MAX_PROFILE_SECTION_CHARS = 1800
MAX_DETECTED_QUESTION_CHARS = 700
MAX_ACTION_MEMORY_CHARS = 3500
MAX_MEMORY_SOURCE_CHARS = 260
MAX_MEMORY_RESPONSE_CHARS = 520
RECENT_ACTION_MEMORY_LIMIT = 5

QUESTION_PATTERNS = [
    r"\bcan you tell me\b",
    r"\bcould you tell me\b",
    r"\bwalk me through\b",
    r"\btell me about\b",
    r"\bdescribe a time\b",
    r"\bgive me an example\b",
    r"\bhow would you\b",
    r"\bhow do you\b",
    r"\bwhat would you\b",
    r"\bwhat do you\b",
    r"\bwhy\b",
    r"\bwhat is your experience\b",
    r"\bdo you have experience\b",
    r"\bmy question is\b",
    r"\bthe question is\b",
]


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
            messages, context_debug, context_inspector = self._build_messages(
                session_id,
                action_type,
                source_text,
                source_label,
                payload,
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
                }
            )

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

    def _resolve_source_label(self, action_type: str, payload: dict[str, Any], source_text: str) -> str:
        if action_type == "improve_answer":
            return "improve"
        if payload.get("utterance_id"):
            return "selected utterance"
        if payload.get("count"):
            count = int(payload.get("count") or 0)
            return f"last {count}" if source_text else f"last {count} fallback"
        return "direct prompt" if source_text else "recent transcript fallback"

    def _build_messages(
        self,
        session_id: str,
        action_type: str,
        source_text: str,
        source_label: str,
        payload: dict[str, Any],
    ) -> tuple[list[dict[str, str]], dict[str, str], dict[str, Any]]:
        profile = self.storage.get_active_interview_profile() or self.storage.ensure_default_interview_profile()
        recent_others = self.storage.recent_utterances(session_id, limit=8)
        raw_recent_context = "\n".join(row["text"] for row in reversed(recent_others))
        raw_hidden_context = self.storage.recent_hidden_context(session_id)
        recent_actions = self.storage.recent_completed_actions(session_id, limit=RECENT_ACTION_MEMORY_LIMIT)
        raw_action_memory = self._format_action_memory(recent_actions)
        recent_context_block = self._build_context_block(raw_recent_context, MAX_CONTEXT_BLOCK_CHARS)
        hidden_context_block = self._build_context_block(raw_hidden_context, MAX_CONTEXT_BLOCK_CHARS)
        action_memory_block = self._build_context_block(raw_action_memory, MAX_ACTION_MEMORY_CHARS)
        recent_context = recent_context_block["text"]
        hidden_context = hidden_context_block["text"]
        action_memory = action_memory_block["text"]
        question_detection = self._detect_main_question(source_text)

        task = {
            "answer_as_me": "Create a natural interview answer to the selected question.",
            "answer_last_n": "Use the recent transcript to answer the interviewer's latest question.",
            "clarify": "Create one short clarification question I can say if the interviewer prompt is unclear.",
            "ask_question": "Create one thoughtful, role-specific question I can ask the interviewer next. Keep it natural and concise.",
            "push_back": "Create a calm response that addresses interviewer concern, skepticism, or pushback without sounding defensive.",
            "give_example": (
                "Create a concise first-person example answer. Use a real/safe example from the candidate context "
                "when available. If no specific example is supported, say what kind of example I would give without "
                "inventing details."
            ),
            "recover_answer": (
                "Create a graceful recovery line I can say after an unclear, incomplete, or weak previous answer. "
                "It should sound natural, briefly reset the point, and then give a cleaner answer."
            ),
            "summarize_context": "Create brief private interview notes: what was asked, what I covered, and what I should emphasize next.",
            "next_step": "Create a concise follow-up or transition I can say to move the conversation forward.",
            "improve_answer": "Improve the previous draft while keeping it natural and spoken.",
            "objection_response": "Create a calm response that addresses interviewer concern, skepticism, or pushback without sounding defensive.",
        }.get(action_type, "Create a useful short interview response as me.")

        previous_response = str(payload.get("previous_response") or "").strip()
        previous_response_block = self._build_context_block(previous_response, MAX_CONTEXT_BLOCK_CHARS)
        previous_response = previous_response_block["text"]
        stable_profile_block = self._build_context_block(
            str(profile.get("internal_candidate_profile") or "").strip(),
            MAX_CONTEXT_BLOCK_CHARS,
        )
        stable_profile = stable_profile_block["text"]
        candidate_facts = self._format_profile_context(
            profile,
            [
                ("Resume", "resume_text"),
                ("Cover letter", "cover_letter_text"),
                ("LinkedIn/profile", "linkedin_text"),
                ("Personal notes", "personal_notes"),
                ("Previous interview context", "previous_interview_context"),
                ("Additional instructions", "additional_instructions"),
            ],
        )
        role_context = self._format_profile_context(
            profile,
            [
                ("Interview name", "name"),
                ("Target role", "target_role"),
                ("Company", "company_name"),
                ("Job description", "job_description_text"),
                ("Company information", "company_info"),
                ("Recruiter notes", "recruiter_notes"),
            ],
        )
        style = profile.get("response_style") or "Natural"
        style_instruction = {
            "Natural": "Use the most conversational and human version. This is the default.",
            "Professional": "Make it slightly more polished, but still spoken and not corporate.",
            "Concise": "Keep it brief enough for a quick live answer.",
            "Expanded": "Give a fuller answer, but do not turn it into an essay.",
        }.get(style, "Use a natural conversational style.")

        user_content = (
            "Context priority, highest first:\n"
            "1. The detected main question/request is the immediate target.\n"
            "2. My recent hidden context can disambiguate intent and continuity.\n"
            "3. Previous generated answers help continuity and prevent repetition.\n"
            "4. The internal candidate profile is the main stable source of candidate truth.\n"
            "5. Raw candidate profile text is supporting evidence only.\n"
            "6. Job description and company context are guidance, not facts about me.\n\n"
            "Detected main question/request, answer this first:\n"
            f"{question_detection['target'] or '(none detected)'}\n\n"
            "Question detection details:\n"
            f"confidence={question_detection['confidence']}; method={question_detection['method']}\n\n"
            "Original selected or recent transcript, use as secondary context:\n"
            f"{source_text or '(none)'}\n\n"
            f"Task:\n{task}\n\n"
            "My recent hidden context, for continuity and nuance only:\n"
            f"{hidden_context or '(none)'}\n\n"
            "Interviewer recently said, newest live context for this session:\n"
            f"{recent_context or '(no recent transcript yet)'}\n\n"
            "Previous generated answers, use only for continuity; avoid repeating examples unless asked:\n"
            f"{action_memory or '(no previous completed answers yet)'}\n\n"
            "Internal candidate profile, use as the primary stable candidate context:\n"
            f"{stable_profile or '(not generated yet; infer cautiously from the raw candidate facts below)'}\n\n"
            "Raw candidate facts, supporting evidence only:\n"
            f"{candidate_facts}\n\n"
            "Role, company, and interview guidance, do not treat job requirements as candidate claims:\n"
            f"{role_context}\n\n"
            "Previous draft, only relevant when improving:\n"
            f"{previous_response or '(none)'}\n\n"
            f"Response style:\n{style} - {style_instruction}\n\n"
            "Final rules: answer in first person, keep it natural, do not invent facts, "
            "prefer honest uncertainty over unsupported claims, and make it easy to say out loud."
        )

        messages = [
            {"role": "system", "content": INTERVIEW_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        context_inspector = {
            "source_label": source_label,
            "profile_name": profile.get("name") or "Untitled Interview",
            "profile_id": profile.get("id") or "",
            "target_role": profile.get("target_role") or "",
            "company_name": profile.get("company_name") or "",
            "question_detection": question_detection,
            "has_internal_candidate_profile": bool(stable_profile),
            "hidden_context_present": bool(hidden_context),
            "recent_transcript_count": len(recent_others),
            "blocks": {
                "source_text": self._inspect_text_block(source_text),
                "hidden_context": self._inspect_context_block(hidden_context_block),
                "recent_transcript": self._inspect_context_block(recent_context_block),
                "internal_candidate_profile": self._inspect_context_block(stable_profile_block),
                "candidate_facts": self._inspect_text_block(candidate_facts),
                "role_company_guidance": self._inspect_text_block(role_context),
                "previous_draft": self._inspect_context_block(previous_response_block),
            },
            "prompt_chars": {
                "system": len(INTERVIEW_SYSTEM_PROMPT),
                "user": len(user_content),
                "total": len(INTERVIEW_SYSTEM_PROMPT) + len(user_content),
                "assessment": self._assess_prompt_size(len(INTERVIEW_SYSTEM_PROMPT) + len(user_content)),
            },
            "context_priority": [
                "Detected main question/request",
                "Recent hidden context",
                "Previous generated answers",
                "Internal candidate profile",
                "Raw candidate facts",
                "Role/company guidance",
            ],
            "memory": {
                "completed_actions_used": len(recent_actions),
                "limit": RECENT_ACTION_MEMORY_LIMIT,
            },
        }
        context_inspector["blocks"]["previous_generated_answers"] = self._inspect_context_block(action_memory_block)
        return messages, {"system": INTERVIEW_SYSTEM_PROMPT, "user": user_content}, context_inspector

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
            value = self._truncate_text(str(profile.get(key) or "").strip(), MAX_PROFILE_SECTION_CHARS)
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

    def _build_context_block(self, text: str, max_chars: int) -> dict[str, Any]:
        return {
            "text": self._truncate_text(text, max_chars),
            "original_chars": len(text),
            "truncated": len(text) > max_chars,
            "max_chars": max_chars,
        }

    @staticmethod
    def _inspect_context_block(block: dict[str, Any]) -> dict[str, Any]:
        text = str(block.get("text") or "")
        return {
            "present": bool(text.strip()),
            "chars": len(text),
            "original_chars": int(block.get("original_chars") or 0),
            "truncated": bool(block.get("truncated")),
            "max_chars": int(block.get("max_chars") or 0),
        }

    @staticmethod
    def _inspect_text_block(text: str) -> dict[str, Any]:
        return {
            "present": bool(text.strip()),
            "chars": len(text),
            "original_chars": len(text),
            "truncated": "[... truncated " in text,
            "max_chars": 0,
        }

    def _format_action_memory(self, actions: list[dict[str, Any]]) -> str:
        if not actions:
            return ""

        lines = []
        for index, action in enumerate(reversed(actions), start=1):
            action_type = str(action.get("action_type") or "action")
            source = self._truncate_text(
                re.sub(r"\s+", " ", str(action.get("source_text") or "")).strip(),
                MAX_MEMORY_SOURCE_CHARS,
            )
            response = self._truncate_text(
                re.sub(r"\s+", " ", str(action.get("response") or "")).strip(),
                MAX_MEMORY_RESPONSE_CHARS,
            )
            if not response:
                continue
            lines.append(
                f"{index}. Action: {ACTION_LABELS.get(action_type, action_type)}\n"
                f"Question/context: {source or '(none)'}\n"
                f"Generated answer: {response}"
            )
        return "\n\n".join(lines)

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

    def _detect_main_question(self, source_text: str) -> dict[str, Any]:
        normalized = re.sub(r"\s+", " ", source_text or "").strip()
        if not normalized:
            return {
                "target": "",
                "confidence": "low",
                "method": "empty",
                "reason": "No source transcript was available.",
            }

        candidates = self._question_candidates(normalized)
        if not candidates:
            return {
                "target": self._truncate_text(normalized, MAX_DETECTED_QUESTION_CHARS),
                "confidence": "low",
                "method": "fallback_full_source",
                "reason": "No question-like sentence was detected.",
            }

        best = max(candidates, key=lambda item: item["score"])
        confidence = "high" if best["score"] >= 8 else "medium" if best["score"] >= 5 else "low"
        return {
            "target": self._truncate_text(best["text"], MAX_DETECTED_QUESTION_CHARS),
            "confidence": confidence,
            "method": "heuristic",
            "reason": best["reason"],
        }

    def _question_candidates(self, text: str) -> list[dict[str, Any]]:
        sentences = self._split_question_sentences(text)
        candidates: list[dict[str, Any]] = []
        total = max(1, len(sentences))
        for index, sentence in enumerate(sentences):
            cleaned = sentence.strip(" ,;:-")
            if len(cleaned) < 8:
                continue
            lowered = cleaned.lower()
            score = 0.0
            reasons = []
            if "?" in cleaned:
                score += 4
                reasons.append("question mark")
            if re.match(r"^(who|what|when|where|why|how|can|could|would|do|does|did|tell|describe|walk)\b", lowered):
                score += 3
                reasons.append("question/request opener")
            for pattern in QUESTION_PATTERNS:
                if re.search(pattern, lowered):
                    score += 4
                    label = pattern.replace(r"\b", "")
                    reasons.append(f"matched '{label}'")
                    break
            score += (index + 1) / total * 2
            if 20 <= len(cleaned) <= 280:
                score += 1
            if len(cleaned) > 520:
                score -= 1
            if score >= 3:
                candidates.append(
                    {
                        "text": cleaned,
                        "score": score,
                        "reason": ", ".join(reasons) or "recent sentence",
                    }
                )

        for pattern in QUESTION_PATTERNS:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            extracted = text[match.start() :].strip(" ,;:-")
            extracted = self._split_question_sentences(extracted)[0] if extracted else ""
            if extracted:
                candidates.append(
                    {
                        "text": extracted,
                        "score": 12,
                        "reason": "question phrase inside longer sentence",
                    }
                )
        return candidates

    @staticmethod
    def _split_question_sentences(text: str) -> list[str]:
        parts = re.split(r"(?<=[.!?])\s+|\n+", text)
        return [part.strip() for part in parts if part and part.strip()]

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
