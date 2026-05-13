from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from config import settings


DeltaCallback = Callable[[str], Awaitable[None]]


class LLMService:
    def __init__(self) -> None:
        self.enabled = bool(settings.groq_api_key)
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
            raise RuntimeError("GROQ_API_KEY is not configured. Create example_meetingcopilot/.env.")

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

    async def analyze_image(
        self,
        image_base64: str,
        prompt: str,
        on_delta: DeltaCallback,
    ) -> str:
        if not self.enabled or self._client is None:
            raise RuntimeError("GROQ_API_KEY is not configured. Create example_meetingcopilot/.env.")

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
