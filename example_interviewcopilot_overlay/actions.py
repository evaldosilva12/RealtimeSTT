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
from llm_service import LLMRequestError, LLMService
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
UNTRUNCATED_PROFILE_FIELDS = {"resume_text"}
MAX_DETECTED_QUESTION_CHARS = 700
MAX_ACTION_MEMORY_CHARS = 1800
MAX_MEMORY_SOURCE_CHARS = 260
MAX_MEMORY_RESPONSE_CHARS = 260
RECENT_ACTION_MEMORY_LIMIT = 3
QUICK_CONTEXT_BLOCK_CHARS = 1800
QUICK_ACTION_MEMORY_CHARS = 700
QUICK_RECENT_ACTION_MEMORY_LIMIT = 1
QUICK_RESPONSE_MAX_TOKENS = 380
DEFAULT_RESPONSE_MAX_TOKENS = 450

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
Your job is to produce natural spoken English that sounds like a reliable,
organized, practical person in a real conversation, not like a scripted AI answer.

Hard rules:
- Answer in first person, as the candidate.
- Never invent experience, tools, metrics, titles, credentials, or achievements.
- Treat the job description as guidance only. The user's real background comes first.
- Treat the resume as the primary factual source of the candidate's timeline and company attribution.
- Treat the internal candidate profile as a helpful summary, not a replacement for the resume.
- Treat each company, client, role, and project in the resume/profile as a separate source of truth.
- Never move tools, responsibilities, achievements, compliance work, domains, metrics, or examples from one company/project to another.
- When giving an example, name a company only if the resume/profile clearly supports that the experience happened there.
- If the relevant topic belongs to a different company/project, use that correct company/project.
- If company/project attribution is uncertain, avoid naming the company and give a more general answer.
- For broad introduction questions, preserve the main resume timeline, but do not recite the full resume.
- For broad introductions, aim for a 60-90 second spoken answer with only the career arc, 2-3 strongest themes, and a short fit statement.
- Save detailed metrics, long examples, and responsibility lists for follow-up questions unless the interviewer explicitly asks for detail.
- Never use markdown, headings, bold text, bullet points, numbered lists, or labels like "Situation" and "Action" in a spoken answer.
- For normal answers, aim for 2 short paragraphs or about 45-75 seconds.
- For simple questions, answer in about 20-40 seconds.
- Behavioral examples may be a little longer, but should still sound spoken and not use rigid STAR formatting.
- Use at most one strong metric in an answer, and only if the resume or profile clearly supports it.
- Do not repeat job-description phrases verbatim.
- Do not mirror the job description too directly or force an obvious match.
- Show fit through the candidate's real experience and working style, not by repeating company wording.
- Do not end every answer with an obvious role-fit line like "that is why this role is a natural fit."
- Mention fit with the role or company only when it directly answers the question.
- Vary openings, examples, transitions, and closings across answers.
- Avoid corporate-template language, keyword stuffing, LinkedIn-style phrasing, motivational speech, TED Talk tone, and keynote-speaker energy.
- Avoid perfect STAR formatting unless the user explicitly asks for a structured answer.
- Keep answers easy to say out loud, with realistic pacing and short-to-medium length.
- The user is a Brazilian Portuguese speaker with non-fluent English, so use simple, clear, natural English that is easy for a Brazilian to pronounce.
- Prefer common everyday words over advanced vocabulary or idioms.
- Use short sentences. Leave room to breathe.
- Avoid difficult tongue-twister sounds, overly long sentences, slang, phrasal verbs, and complex grammar.
- Write in a conversational and confident way, but keep pronunciation-friendly sentence flow.
- Prefer human wording like "I help keep things clear" over polished claims like "I drive cross-functional alignment."
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

            model = settings.groq_quality_model if action_type == "improve_answer" else settings.groq_text_model
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
        attempts = [self._model_attempt("groq", primary_model)]
        if self.llm.secondary_enabled:
            attempts.append(self._model_attempt("groq_secondary", primary_model))
        if self.llm.openai_enabled:
            attempts.append(self._model_attempt("openai", settings.openai_fallback_model))
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
        return cleaned.endswith((",", ";", ":")) or tail in incomplete_tail_words or len(cleaned) >= 80

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
        recent_others = self.storage.recent_utterances(session_id, limit=4 if response_mode == "quick" else 8)
        raw_recent_context = "\n".join(row["text"] for row in reversed(recent_others))
        raw_hidden_context = self.storage.recent_hidden_context(session_id)
        memory_limit = QUICK_RECENT_ACTION_MEMORY_LIMIT if response_mode == "quick" else RECENT_ACTION_MEMORY_LIMIT
        recent_actions = self.storage.recent_completed_actions(session_id, limit=memory_limit)
        raw_action_memory = self._format_action_memory(recent_actions)
        live_context_chars = QUICK_CONTEXT_BLOCK_CHARS if response_mode == "quick" else MAX_CONTEXT_BLOCK_CHARS
        action_memory_chars = QUICK_ACTION_MEMORY_CHARS if response_mode == "quick" else MAX_ACTION_MEMORY_CHARS
        recent_context_block = self._build_context_block(raw_recent_context, live_context_chars)
        hidden_context_block = self._build_context_block(raw_hidden_context, live_context_chars)
        action_memory_block = self._build_context_block(raw_action_memory, action_memory_chars)
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
                "inventing details. Do not attribute the example to a company/project unless that company/project "
                "clearly supports the topic."
            ),
            "recover_answer": (
                "Create a graceful recovery line I can say after an unclear, incomplete, or weak previous answer. "
                "It should sound natural, briefly reset the point, and then give a cleaner answer. Preserve the correct "
                "company/project attribution for any example mentioned."
            ),
            "summarize_context": "Create brief private interview notes: what was asked, what I covered, and what I should emphasize next.",
            "next_step": "Create a concise follow-up or transition I can say to move the conversation forward.",
            "improve_answer": "Improve the previous draft while keeping it natural and spoken. Correct any unsupported or mismatched company/project attribution.",
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
            "Natural": "Use a spoken, simple, human answer with small natural transitions. This is the default live interview voice.",
            "Professional": "Make it slightly more polished, but still short, spoken, and not corporate.",
            "Concise": "Answer directly in 1-2 short paragraphs.",
            "Expanded": "Give a fuller spoken answer, but do not turn it into an essay, list, or presentation.",
        }.get(style, "Use a natural conversational style.")
        if response_mode == "quick":
            style_instruction = (
                f"{style_instruction} Prioritize speed: give a compact live answer in 1-2 short paragraphs, "
                "with only the most relevant evidence."
            )
        primary_model_for_action = settings.groq_quality_model if action_type == "improve_answer" else settings.groq_text_model

        user_content = (
            "Context priority, highest first:\n"
            "1. The current selected question/request is the immediate target.\n"
            "2. The full selected utterance preserves scope, qualifiers, company names, and time references.\n"
            "3. Raw candidate profile text, especially the resume, is the primary factual source of truth.\n"
            "4. The internal candidate profile is a stable summary and retrieval aid, but it may omit details.\n"
            "5. Recent transcript and hidden context can disambiguate continuity.\n"
            "6. Previous generated answers are only for continuity and avoiding repetition.\n"
            "7. Job description and company context are guidance, not facts about me.\n\n"
            "Experience attribution rules:\n"
            "- Treat each company, client, role, and project as separate evidence.\n"
            "- Never transfer tools, responsibilities, achievements, compliance work, domains, metrics, or examples between companies/projects.\n"
            "- Before giving a concrete example, verify that the topic is supported by that specific company/project context.\n"
            "- If the topic is supported under a different company/project, use that correct company/project.\n"
            "- If the correct attribution is unclear, avoid naming the company/project and answer at a general level.\n\n"
            "Question scope rules:\n"
            "- Answer the current question in its full original scope.\n"
            "- Do not rely only on the detected target if it removes important qualifiers from the selected utterance.\n"
            "- Preserve qualifiers such as company names, role names, time periods, tools, project names, and words like recent, current, previous, or at a specific company.\n"
            "- If the question asks about one company, role, project, or time period, stay within that scope.\n"
            "- Do not summarize the full career unless the question is broad, like 'tell me about yourself' or 'walk me through your background.'\n\n"
            "Voice and fit rules:\n"
            "- Sound trustworthy, organized, clear, practical, and human.\n"
            "- Do not sound like a TED Talk, corporate speaker, motivational pitch, LinkedIn post, or memorized script.\n"
            "- Use shorter sentences with natural pauses. Make the answer easy to breathe and say out loud.\n"
            "- Do not force-fit my background to the job description by echoing its wording.\n"
            "- Show fit through my real experience, habits, and working style.\n"
            "- Do not close every answer with a role-fit statement. Only mention fit when the question asks for it.\n"
            "- Prefer plain wording over polished corporate phrases.\n\n"
            "Spoken format and length rules:\n"
            "- Write only the words I can say out loud. Do not use markdown, headings, bold text, bullets, numbered lists, or labels.\n"
            "- For a normal answer, use 2 short paragraphs or about 45-75 seconds.\n"
            "- For a simple warm-up or follow-up question, use 1-2 short paragraphs or about 20-40 seconds.\n"
            "- For a behavioral example, give enough detail to be credible, but keep it conversational and avoid rigid STAR structure.\n"
            "- Answer follow-up questions directly. Do not recap my full career unless the interviewer asks for it.\n\n"
            "Repetition control rules:\n"
            "- Vary my opening line, example, transition, and closing from previous answers.\n"
            "- Avoid repeating the same themes in every answer, especially board, owners, blockers, release tracking, QA, and developers focusing on code.\n"
            "- Use at most one strong metric per answer, and only when the raw candidate facts clearly support it.\n"
            "- Save other useful facts for later instead of using every strong point at once.\n\n"
            "Broad introduction rules:\n"
            "- If the question asks me to introduce myself, give a 60-90 second overview, not a full resume walkthrough.\n"
            "- Use 2-3 short paragraphs at most.\n"
            "- Cover only: my career arc, 2-3 strongest themes, and one short reason this role fits.\n"
            "- Mention each recent role that explains my fit, even if each role gets only one short sentence.\n"
            "- Avoid detailed metrics, long responsibility lists, and deep examples unless the question asks for them.\n"
            "- Keep at least one strong metric or example available for a later follow-up instead of using all of them now.\n"
            "- Do not skip a resume role just because the internal candidate profile did not mention it.\n"
            "- Use 'most recently' instead of 'right now' unless the resume clearly supports current employment.\n\n"
            "Detected main question/request, use as a clue but preserve the full selected utterance scope:\n"
            f"{question_detection['target'] or '(none detected)'}\n\n"
            "Question detection details:\n"
            f"confidence={question_detection['confidence']}; method={question_detection['method']}\n\n"
            "Full selected utterance or recent transcript, use this to preserve the full scope:\n"
            f"{source_text or '(none)'}\n\n"
            f"Task:\n{task}\n\n"
            "Raw candidate facts, especially the resume, are the primary factual evidence:\n"
            f"{candidate_facts}\n\n"
            "Internal candidate profile, use as a summary/retrieval aid only:\n"
            f"{stable_profile or '(not generated yet; infer cautiously from the raw candidate facts above)'}\n\n"
            "My recent hidden context, for continuity and nuance only:\n"
            f"{hidden_context or '(none)'}\n\n"
            "Interviewer recently said, newest live context for this session:\n"
            f"{recent_context or '(no recent transcript yet)'}\n\n"
            "Previous generated answers, use only to notice themes already used and avoid repetition. They are not source material. Do not copy their structure, tone, phrases, opening, example, closing, or level of detail:\n"
            f"{action_memory or '(no previous completed answers yet)'}\n\n"
            "Role, company, and interview guidance, do not treat job requirements as candidate claims:\n"
            f"{role_context}\n\n"
            "Previous draft, only relevant when improving:\n"
            f"{previous_response or '(none)'}\n\n"
            f"Response style:\n{style} - {style_instruction}\n\n"
            f"Response mode:\n{response_mode}\n\n"
            "Final rules: answer in first person, keep it natural, do not invent facts, "
            "do not move experience between companies/projects, prefer honest uncertainty over unsupported claims, "
            "and make it easy to say out loud."
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
                    "estimated_tokens": self._estimate_tokens(len(INTERVIEW_SYSTEM_PROMPT) + len(user_content)),
                    "max_output_tokens": QUICK_RESPONSE_MAX_TOKENS if response_mode == "quick" else DEFAULT_RESPONSE_MAX_TOKENS,
                    "assessment": self._assess_prompt_size(len(INTERVIEW_SYSTEM_PROMPT) + len(user_content)),
                },
                "response_mode": response_mode,
                "models": {
                    "primary": primary_model_for_action,
                    "planned_chain": self._planned_model_chain(primary_model_for_action),
                    "groq_secondary": primary_model_for_action if self.llm.secondary_enabled else "",
                    "openai_fallback": settings.openai_fallback_model if self.llm.openai_enabled else "",
                },
            "context_priority": [
                "Current selected question/request",
                "Full selected utterance scope",
                "Raw candidate facts",
                "Internal candidate profile",
                "Recent transcript and hidden context",
                "Previous generated answers only to avoid repetition",
                "Role/company guidance",
            ],
            "memory": {
                "completed_actions_used": len(recent_actions),
                "limit": memory_limit,
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
                f"Prior answer excerpt for repetition avoidance only: {response}"
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

    @staticmethod
    def _estimate_tokens(total_chars: int) -> int:
        return max(1, round(total_chars / 4))

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
                    "- Company Timeline\n"
                    "  Include every role from the resume with Company, Title, Dates, Core Responsibilities, "
                    "Safe Claims, Metrics, and Attribution Warnings. Do not omit or merge companies.\n"
                    "- Communication Style\n"
                    "  Define a natural interview voice that sounds trustworthy, organized, clear, practical, and human. "
                    "Avoid TED Talk tone, corporate-speaker tone, motivational pitch, and scripted wording.\n"
                    "- Strongest Themes\n"
                    "- Company-Grounded Examples To Reuse\n"
                    "  For each example, include Company/Project, Topics, Safe Claims, and Attribution Warnings.\n"
                    "- Broad Introduction Guidance\n"
                    "  Explain how to answer 'tell me about yourself' in 60-90 seconds using the full resume timeline, "
                    "without reciting every responsibility, using all metrics at once, or mirroring the job description.\n"
                    "- Risk Areas\n"
                    "- Strategic Framing Opportunities\n"
                    "- Claims To Avoid\n"
                    "- Attribution Boundaries\n"
                    "  Explicitly list topics/tools/achievements that must not be moved between companies/projects."
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
