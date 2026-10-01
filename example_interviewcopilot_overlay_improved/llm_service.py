from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from typing import Any

from config import settings


DeltaCallback = Callable[[str], Awaitable[None]]

# Own logger with its own level: RealtimeSTT forces the root logger to WARNING.
logger = logging.getLogger("interview_copilot.llm")
logger.setLevel(logging.INFO)


class LLMRequestError(RuntimeError):
    def __init__(self, message: str, *, retry_after: str = "", rate_limited: bool = False) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.rate_limited = rate_limited


class LLMService:
    def __init__(self) -> None:
        self.enabled = bool(settings.groq_api_key)
        self.secondary_enabled = bool(settings.groq_api_key_2)
        self.openai_enabled = bool(settings.openai_api_key)
        self._client = None
        self._secondary_client = None
        if self.enabled:
            from groq import Groq

            self._client = Groq(
                api_key=settings.groq_api_key,
                timeout=settings.llm_request_timeout_seconds,
            )
        if self.secondary_enabled:
            from groq import Groq

            self._secondary_client = Groq(
                api_key=settings.groq_api_key_2,
                timeout=settings.llm_request_timeout_seconds,
            )

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        on_delta: DeltaCallback,
        model: str | None = None,
        temperature: float = 0.6,
        max_tokens: int = 700,
    ) -> str:
        if not self.enabled or self._client is None:
            raise LLMRequestError("GROQ_API_KEY is not configured. Add it to example_interviewcopilot_overlay/.env.")

        return await self._stream_groq_client(
            self._client,
            messages,
            on_delta,
            model or settings.groq_text_model,
            temperature,
            max_tokens,
            "Groq primary",
        )

    async def stream_secondary_chat(
        self,
        messages: list[dict[str, Any]],
        on_delta: DeltaCallback,
        model: str | None = None,
        temperature: float = 0.6,
        max_tokens: int = 700,
    ) -> str:
        if not self.secondary_enabled or self._secondary_client is None:
            raise LLMRequestError("GROQ_API_KEY_2 is not configured. Add it to enable the secondary Groq fallback.")

        return await self._stream_groq_client(
            self._secondary_client,
            messages,
            on_delta,
            model or settings.groq_text_model,
            temperature,
            max_tokens,
            "Groq secondary",
        )

    async def _stream_groq_client(
        self,
        client: Any,
        messages: list[dict[str, Any]],
        on_delta: DeltaCallback,
        model: str,
        temperature: float,
        max_tokens: int,
        provider_label: str,
    ) -> str:
        loop = asyncio.get_running_loop()
        output: list[str] = []

        def worker() -> str:
            try:
                stream = client.chat.completions.create(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    top_p=1,
                    stream=True,
                )
                for chunk in stream:
                    delta = chunk.choices[0].delta.content or ""
                    if not delta:
                        continue
                    output.append(delta)
                    asyncio.run_coroutine_threadsafe(on_delta(delta), loop).result(timeout=10)
                return "".join(output)
            except Exception as exc:
                raise self._friendly_groq_error(exc, model, provider_label) from exc

        return await asyncio.to_thread(worker)

    async def complete_openai_chat(
        self,
        messages: list[dict[str, Any]],
        on_delta: DeltaCallback,
        model: str | None = None,
        temperature: float = 0.35,
        max_tokens: int = 700,
    ) -> str:
        if not self.openai_enabled:
            raise LLMRequestError("OpenAI fallback is not configured. Add OPENAI_API_KEY to enable it.")

        loop = asyncio.get_running_loop()

        def emit_delta(delta: str) -> None:
            asyncio.run_coroutine_threadsafe(on_delta(delta), loop).result(timeout=10)

        return await asyncio.to_thread(
            self._stream_openai,
            messages,
            model or settings.openai_fallback_model,
            temperature,
            max_tokens,
            emit_delta,
        )

    async def complete_chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.35,
        max_tokens: int = 1100,
        prefer_openai: bool = False,
    ) -> str:
        if prefer_openai and self.openai_enabled:
            return await asyncio.to_thread(
                self._complete_openai,
                messages,
                model or settings.openai_setup_model,
                temperature,
                max_tokens,
            )

        chunks: list[str] = []

        async def on_delta(delta: str) -> None:
            chunks.append(delta)

        await self.stream_chat(
            messages,
            on_delta,
            model=model or settings.groq_quality_model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return "".join(chunks)

    def _complete_openai(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        payload_data = self._openai_payload(messages, model, temperature, max_tokens)
        payload = json.dumps(payload_data).encode("utf-8")
        request = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LLMRequestError(f"OpenAI request failed: {self._extract_error_message(detail)}") from exc
        except urllib.error.URLError as exc:
            raise LLMRequestError(f"OpenAI request failed: {exc.reason}") from exc

        self._log_openai_usage(model, started, None, body.get("usage") or {})
        return str(body["choices"][0]["message"]["content"]).strip()

    async def warm_openai(self, messages: list[dict[str, Any]], model: str) -> None:
        """Tiny request that makes OpenAI cache the stable prompt prefix."""
        if not self.openai_enabled:
            return
        await asyncio.to_thread(self._complete_openai, messages, model, 0.0, 16)

    def _stream_openai(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
        emit_delta: Callable[[str], None],
    ) -> str:
        payload_data = self._openai_payload(messages, model, temperature, max_tokens)
        payload_data["stream"] = True
        payload_data["stream_options"] = {"include_usage": True}
        payload = json.dumps(payload_data).encode("utf-8")
        request = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        chunks: list[str] = []
        started = time.perf_counter()
        first_delta_at: float | None = None
        usage: dict[str, Any] = {}
        try:
            with urllib.request.urlopen(request, timeout=settings.llm_request_timeout_seconds) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        break
                    try:
                        body = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    error = body.get("error")
                    if error:
                        raise LLMRequestError(f"OpenAI request failed: {self._format_provider_error(error)}")
                    if body.get("usage"):
                        usage = body["usage"]
                    choices = body.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {}).get("content") or ""
                    if delta:
                        if first_delta_at is None:
                            first_delta_at = time.perf_counter() - started
                        text = str(delta)
                        chunks.append(text)
                        emit_delta(text)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LLMRequestError(f"OpenAI request failed: {self._extract_error_message(detail)}") from exc
        except urllib.error.URLError as exc:
            raise LLMRequestError(f"OpenAI request failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise LLMRequestError("OpenAI request timed out.") from exc

        self._log_openai_usage(model, started, first_delta_at, usage)
        return "".join(chunks).strip()

    @staticmethod
    def _log_openai_usage(model: str, started: float, first_delta_at: float | None, usage: dict[str, Any]) -> None:
        prompt_tokens = usage.get("prompt_tokens", 0)
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
        logger.info(
            "OpenAI %s first_token=%.2fs total=%.2fs prompt=%s cached=%s completion=%s reasoning=%s",
            model,
            first_delta_at if first_delta_at is not None else -1,
            time.perf_counter() - started,
            prompt_tokens,
            cached,
            usage.get("completion_tokens", 0),
            reasoning,
        )

    def _openai_payload(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        payload_data: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if self._uses_max_completion_tokens(model):
            # Reasoning-style models: no temperature, token cap includes reasoning.
            effort = self._reasoning_effort(model)
            if effort:
                payload_data["reasoning_effort"] = effort
            headroom = 0 if effort == "none" else 700
            payload_data["max_completion_tokens"] = max_tokens + headroom
        else:
            payload_data["temperature"] = temperature
            payload_data["max_tokens"] = max_tokens
        return payload_data

    @staticmethod
    def _uses_max_completion_tokens(model: str) -> bool:
        normalized = (model or "").lower()
        return normalized.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4"))

    @staticmethod
    def _reasoning_effort(model: str) -> str:
        """GPT-6 Luna accepts "none"; Sol/Astra need at least "low"."""
        normalized = (model or "").lower()
        if settings.openai_reasoning_effort:
            return settings.openai_reasoning_effort
        if normalized.startswith("gpt-6-luna"):
            return "none"
        if normalized.startswith("gpt-6"):
            return "low"
        return ""

    @staticmethod
    def _format_provider_error(error: Any) -> str:
        if isinstance(error, dict):
            return str(error.get("message") or error)
        return str(error)

    @staticmethod
    def _extract_error_message(detail: str) -> str:
        try:
            body = json.loads(detail)
            error = body.get("error", {})
            message = error.get("message") if isinstance(error, dict) else error
            if message:
                return str(message)
        except Exception:
            pass
        return detail.strip() or "Unknown provider error."

    def _friendly_groq_error(self, exc: Exception, model: str, provider_label: str = "Groq") -> LLMRequestError:
        raw = str(exc)
        retry_after = ""
        retry_match = re.search(r"try again in ([^.]+(?:\.\d+)?s)", raw, flags=re.IGNORECASE)
        if retry_match:
            retry_after = retry_match.group(1)

        if "rate limit" in raw.lower() or "rate_limit" in raw.lower() or "429" in raw:
            message = (
                f"{provider_label} rate limit reached for {model}."
                f" Try again in {retry_after}." if retry_after else f"{provider_label} rate limit reached for {model}."
            )
            return LLMRequestError(message, retry_after=retry_after, rate_limited=True)

        if "timed out" in raw.lower() or "timeout" in raw.lower():
            return LLMRequestError(
                f"{provider_label} request to {model} timed out after {settings.llm_request_timeout_seconds:g}s."
            )

        return LLMRequestError(f"{provider_label} request to {model} failed: {raw}")

    async def analyze_image(
        self,
        image_base64: str,
        prompt: str,
        on_delta: DeltaCallback,
    ) -> str:
        if not self.enabled or self._client is None:
            raise RuntimeError("GROQ_API_KEY is not configured. Create example_interviewcopilot_v2/.env.")

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"},
                    },
                ],
            }
        ]
        return await self.stream_chat(
            messages,
            on_delta,
            model=settings.groq_vision_model,
            temperature=0.4,
            max_tokens=900,
        )
