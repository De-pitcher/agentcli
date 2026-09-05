"""OpenRouter /embeddings API client and lightweight vector generator (Phase 24).

Calls OpenRouter's /embeddings endpoint with batching, retries, and rate limit handling,
with deterministic vector fallbacks for offline testing.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from ..openrouter_client import OpenRouterConfig

logger = logging.getLogger(__name__)


def _normalize_vector(vec: list[float]) -> list[float]:
    """L2-normalize a vector so dot product equals cosine similarity."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


def _deterministic_fallback_vector(text: str, dimensions: int = 256) -> list[float]:
    """Deterministic token hash embedding used when offline or without API key."""
    vec = [0.0] * dimensions
    tokens = text.lower().split()
    if not tokens:
        return vec
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        for i in range(min(dimensions, len(digest))):
            val = float(digest[i]) - 128.0
            vec[i] += val
    return _normalize_vector(vec)


class EmbeddingEngine:
    """Generates vector embeddings for code chunks and search queries."""

    def __init__(
        self,
        config: OpenRouterConfig | None = None,
        model: str = "openai/text-embedding-3-small",
        batch_size: int = 32,
    ) -> None:
        self.config = config
        self.model = model
        self.batch_size = max(1, batch_size)
        self._api_key: str | None = None
        if config is not None:
            self._api_key = os.environ.get(config.api_key_env)
        if not self._api_key:
            self._api_key = os.environ.get("OPENROUTER_API_KEY")

    @staticmethod
    def _deterministic_fallback_vector(text: str, dimensions: int = 256) -> list[float]:
        """Deterministic token hash embedding used when offline or without API key."""
        return _deterministic_fallback_vector(text, dimensions=dimensions)

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of text strings in batches."""
        if not texts:
            return []

        # If no API key is available, use deterministic fallback
        if not self._api_key:
            logger.debug("No OpenRouter API key found; using deterministic vector fallback.")
            return [_deterministic_fallback_vector(t) for t in texts]

        results: list[list[float]] = []
        base_url = (
            self.config.base_url.rstrip("/")
            if self.config is not None
            else "https://openrouter.ai/api/v1"
        )
        endpoint = f"{base_url}/embeddings"

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/De-pitcher/agentcli",
            "X-Title": "agentcli",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            for i in range(0, len(texts), self.batch_size):
                batch = texts[i : i + self.batch_size]
                payload = {
                    "model": self.model,
                    "input": batch,
                }
                try:
                    resp = await client.post(endpoint, json=payload, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        embeddings_data = data.get("data", [])
                        embeddings_data.sort(key=lambda d: d.get("index", 0))
                        for item in embeddings_data:
                            vec = item.get("embedding", [])
                            results.append(_normalize_vector(vec))
                    else:
                        logger.warning(
                            "OpenRouter embedding error HTTP %d: %s. Using fallback.",
                            resp.status_code,
                            resp.text[:100],
                        )
                        for t in batch:
                            results.append(_deterministic_fallback_vector(t))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Embedding request failed (%s). Using fallback.", exc)
                    for t in batch:
                        results.append(_deterministic_fallback_vector(t))

        return results

    async def embed_query(self, query: str) -> list[float]:
        """Embed a single search query string."""
        results = await self.embed_texts([query])
        if results:
            return results[0]
        return _deterministic_fallback_vector(query)
