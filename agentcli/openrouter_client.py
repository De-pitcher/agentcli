"""Minimal async OpenRouter client: streaming, retries/backoff, connection pooling."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from types import TracebackType
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
        self.last_served_model: str | None = None
        self._client = httpx.AsyncClient(
            base_url=config.base_url,
            timeout=httpx.Timeout(config.timeout_seconds),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/De-pitcher/agentcli",
                "X-Title": "agentcli",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        models: list[str] | None = None,
    ) -> AsyncIterator[str]:
        """
        Yields text deltas as they arrive over SSE. Retries transient failures
        (network errors, 429, 5xx) with exponential backoff up to
        config.max_retries attempts; raises OpenRouterError on exhaustion or
        on a non-retryable 4xx.

        Pass `models` (ordered candidates) to use OpenRouter's server-side
        model fallback; otherwise `model` selects a single model. After the
        stream ends, `last_served_model` holds the model that actually
        answered (it can differ from the request when fallbacks fire). Errors
        raised mid-stream (inside the 200 SSE body) surface as
        OpenRouterError too.
        """
        self.last_served_model = None
        payload: dict[str, object] = {
            "messages": [m.to_dict() for m in messages],
            "stream": True,
        }
        if models:
            payload["models"] = models
        else:
            payload["model"] = model or self._config.default_model

        last_error: Exception | None = None
        for attempt in range(self._config.max_retries):
            try:
                async with self._client.stream(
                    "POST", "/chat/completions", json=payload
                ) as response:
                    if response.status_code == 429:
                        # Use first model from models array, or the single model
                        display_model = (
                            models[0] if models else model
                        ) or self._config.default_model
                        last_error = RateLimitedError(f"Rate limited on model '{display_model}'")
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
                        data = line[len("data: ") :]
                        if data.strip() == "[DONE]":
                            return
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(chunk, dict):
                            continue
                        error = chunk.get("error")
                        if isinstance(error, dict):
                            raise OpenRouterError(
                                f"OpenRouter stream error "
                                f"{error.get('code', 'unknown')}: "
                                f"{error.get('message', 'mid-stream failure')}"
                            )
                        choices = chunk.get("choices")
                        if not isinstance(choices, list) or not choices:
                            continue
                        choice = choices[0]
                        if isinstance(choice, dict) and choice.get("finish_reason") == "error":
                            error = chunk.get("error", {})
                            raise OpenRouterError(
                                f"OpenRouter stream error "
                                f"{error.get('code', 'unknown')}: "
                                f"{error.get('message', 'mid-stream failure')}"
                            )
                        served = chunk.get("model")
                        if isinstance(served, str) and served:
                            self.last_served_model = served
                        delta = (
                            choice.get("delta", {}).get("content")
                            if isinstance(choice, dict)
                            else None
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
        await asyncio.sleep(min(2**attempt * 0.5, 8.0))
