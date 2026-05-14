from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from typing import Any

from config import settings


DeltaCallback = Callable[[str], Awaitable[None]]


class LLMService:
    def __init__(self) -> None:
        self.enabled = bool(settings.groq_api_key)
        self.openai_enabled = bool(settings.openai_api_key)
        self._client = None
        if self.enabled:
            from groq import Groq

            self._client = Groq(api_key=settings.groq_api_key)

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        on_delta: DeltaCallback,
        model: str | None = None,
        temperature: float = 0.6,
        max_tokens: int = 700,
    ) -> str:
        if not self.enabled or self._client is None:
            raise RuntimeError("GROQ_API_KEY is not configured. Create example_interviewcopilot_v2/.env.")

        loop = asyncio.get_running_loop()
        output: list[str] = []

        def worker() -> str:
            stream = self._client.chat.completions.create(
                messages=messages,
                model=model or settings.groq_text_model,
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

        return await asyncio.to_thread(worker)

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
        payload = json.dumps(
            {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI setup request failed: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenAI setup request failed: {exc.reason}") from exc

        return str(body["choices"][0]["message"]["content"]).strip()

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
