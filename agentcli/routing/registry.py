"""Model registry: candidate free OpenRouter models plus per-session health.

The built-in catalog is plain data (easy to extend); user config entries
replace built-ins by id or append new ones. Health tracking is in-memory
per session: consecutive failures trigger a cooldown; a 429 cools down
immediately. No persistence — that belongs to a later phase.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..config import RoutingConfig

CODE = "code"
REASONING = "reasoning"
CHAT = "chat"

@dataclass(frozen=True)
class ModelRecord:
    id: str
    categories: tuple[str, ...]
    priority: int
    context_window: int


_BUILTIN_MODELS: tuple[ModelRecord, ...] = (
    ModelRecord(id="google/gemma-4-31b-it:free", categories=(CHAT,), priority=10, context_window=128000),
    ModelRecord(id="cohere/north-mini-code:free", categories=(CODE,), priority=10, context_window=32768),
    ModelRecord(id="z-ai/glm-5.2:free", categories=(CODE, REASONING), priority=20, context_window=128000),
    ModelRecord(id="nvidia/nemotron-3-super-120b-a12b:free", categories=(REASONING,), priority=20, context_window=128000),
    ModelRecord(id="minimax/minimax-m2.7:free", categories=(CHAT,), priority=20, context_window=128000),
    ModelRecord(id="poolside/laguna-s-2.1:free", categories=(CODE,), priority=30, context_window=64000),
    ModelRecord(id="nvidia/nemotron-3-ultra-550b-a55b:free", categories=(REASONING,), priority=30, context_window=128000),
    ModelRecord(id="minimax/minimax-m3:free", categories=(CHAT,), priority=30, context_window=128000),
    ModelRecord(id="thinkingmachines/inkling-small:free", categories=(CHAT,), priority=40, context_window=64000),
    ModelRecord(id="google/gemma-4-26b-a4b-it:free", categories=(CHAT,), priority=40, context_window=128000),
    ModelRecord(id="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", categories=(REASONING,), priority=40, context_window=64000),
    ModelRecord(id="dots-studio/dots-3-note-preview:free", categories=(CHAT,), priority=50, context_window=32768),
    ModelRecord(id="nvidia/nemotron-3.5-lightning:free", categories=(CHAT,), priority=50, context_window=64000),
    ModelRecord(id="liquid/lfm-2.5-2.6b:free", categories=(CHAT,), priority=60, context_window=32768),
)


@dataclass
class _Health:
    consecutive_failures: int = 0
    cooldown_until: float = 0.0


@dataclass
class RegistryState:
    models: dict[str, ModelRecord] = field(default_factory=dict)
    health: dict[str, _Health] = field(default_factory=dict)


class ModelRegistry:
    """Candidate models for each category, with cooldown-aware selection."""

    def __init__(self, config: RoutingConfig):
        self._config = config
        self._state = RegistryState()
        for record in _BUILTIN_MODELS:
            self._state.models[record.id] = record
        for entry in config.models:
            self._state.models[entry.id] = ModelRecord(
                id=entry.id,
                categories=tuple(entry.categories),
                priority=entry.priority,
                context_window=entry.context_window,
            )

    def candidates(self, category: str) -> list[ModelRecord]:
        """Healthy models serving `category`, best-first."""
        now = time.monotonic()
        usable = [
            record
            for record in self._state.models.values()
            if category in record.categories and not self._is_cooling(record.id, now)
        ]
        usable.sort(key=lambda record: (record.priority, record.id))
        return usable

    def mark_success(self, model_id: str) -> None:
        self._state.health[model_id] = _Health()

    def mark_failure(self, model_id: str, rate_limited: bool = False) -> None:
        health = self._state.health.setdefault(model_id, _Health())
        health.consecutive_failures += 1
        if rate_limited or health.consecutive_failures >= self._config.failure_threshold:
            health.cooldown_until = time.monotonic() + self._config.cooldown_seconds

    def is_cooling(self, model_id: str) -> bool:
        return self._is_cooling(model_id, time.monotonic())

    def _is_cooling(self, model_id: str, now: float) -> bool:
        health = self._state.health.get(model_id)
        return bool(health and health.cooldown_until > now)
