"""Minimal async OpenRouter client: streaming, retries/backoff, connection pooling."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Self

import httpx

from .config import OpenRouterConfig


class OpenRouterError(Exception):
    """Raised for non-recoverable OpenRouter API errors."""


class RateLimitedError(OpenRouterError):
    """Raised when retries are exhausted after repeated 429s."""


@dataclass
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content}


class OpenRouterClient:
    """
    Thin async wrapper around OpenRouter's OpenAI-compatible chat completions
    endpoint. Construct once per process/session — it holds a pooled
    httpx.AsyncClient that should be reused across calls, not recreated per
    request.
    """

    def __init__(self, config: OpenRouterConfig):
        if not config.api_key:
            raise OpenRouterError(
                f"No API key found in environment variable '{config.api_key_env}'. "
                f"Set it, e.g.: export {config.api_key_env}=sk-or-..."
            )
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=config.base_url,
            timeout=httpx.Timeout(config.timeout_seconds),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/your-org/agentcli",
                "X-Title": "agentcli",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """
        Yields text deltas as they arrive over SSE. Retries transient failures
        (network errors, 429, 5xx) with exponential backoff up to
        config.max_retries attempts; raises OpenRouterError on exhaustion or
        on a non-retryable 4xx.
        """
        model = model or self._config.default_model
        payload = {
            "model": model,
            "messages": [m.to_dict() for m in messages],
            "stream": True,
        }

        last_error: Exception | None = None
        for attempt in range(self._config.max_retries):
            try:
                async with self._client.stream(
                    "POST", "/chat/completions", json=payload
                ) as response:
                    if response.status_code == 429:
                        last_error = RateLimitedError(f"Rate limited on model '{model}'")
                        await self._backoff(attempt)
                        continue
                    if response.status_code >= 500:
                        last_error = OpenRouterError(
                            f"Server error {response.status_code} from OpenRouter"
                        )
                        await self._backoff(attempt)
                        continue
                    if response.status_code >= 400:
                        body = await response.aread()
                        raise OpenRouterError(
                            f"OpenRouter error {response.status_code}: "
                            f"{body.decode(errors='replace')}"
                        )

                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        data = line[len("data: "):]
                        if data.strip() == "[DONE]":
                            return
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        delta = (
                            chunk.get("choices", [{}])[0]
                            .get("delta", {})
                            .get("content")
                        )
                        if delta:
                            yield delta
                    return  # stream completed normally

            except httpx.TransportError as exc:
                last_error = OpenRouterError(f"Network error: {exc}")
                await self._backoff(attempt)
                continue

        raise last_error or OpenRouterError("Exhausted retries with unknown error")

    async def _backoff(self, attempt: int) -> None:
        await asyncio.sleep(min(2 ** attempt * 0.5, 8.0))
