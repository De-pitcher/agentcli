"""Model registry: candidate free OpenRouter models plus per-session health.

The built-in catalog is plain data (easy to extend); user config entries
replace built-ins by id or append new ones. Health tracking is in-memory
per session: consecutive failures trigger a cooldown; a 429 cools down
immediately. No persistence — that belongs to a later phase.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..config import ConfigError, RoutingConfig

CODE = "code"
REASONING = "reasoning"
CHAT = "chat"

VALID_CATEGORIES = frozenset([CODE, REASONING, CHAT])


def _validate_categories(categories: list[str], model_id: str) -> tuple[str, ...]:
    for cat in categories:
        if cat not in VALID_CATEGORIES:
            raise ConfigError(
                f"Model '{model_id}' has invalid category '{cat}'. Valid categories: {', '.join(VALID_CATEGORIES)}"
            )
    return tuple(categories)


@dataclass(frozen=True)
class ModelRecord:
    id: str
    categories: tuple[str, ...]
    priority: int
    context_window: int


_BUILTIN_MODELS: tuple[ModelRecord, ...] = (
    ModelRecord(
        id="google/gemma-4-31b-it:free", categories=(CHAT,), priority=10, context_window=128000
    ),
    ModelRecord(
        id="cohere/north-mini-code:free", categories=(CODE,), priority=10, context_window=32768
    ),
    ModelRecord(
        id="z-ai/glm-5.2:free", categories=(CODE, REASONING), priority=20, context_window=128000
    ),
    ModelRecord(
        id="nvidia/nemotron-3-super-120b-a12b:free",
        categories=(REASONING,),
        priority=20,
        context_window=128000,
    ),
    ModelRecord(
        id="minimax/minimax-m2.7:free", categories=(CHAT,), priority=20, context_window=128000
    ),
    ModelRecord(
        id="poolside/laguna-s-2.1:free", categories=(CODE,), priority=30, context_window=64000
    ),
    ModelRecord(
        id="nvidia/nemotron-3-ultra-550b-a55b:free",
        categories=(REASONING,),
        priority=30,
        context_window=128000,
    ),
    ModelRecord(
        id="minimax/minimax-m3:free", categories=(CHAT,), priority=30, context_window=128000
    ),
    ModelRecord(
        id="thinkingmachines/inkling-small:free",
        categories=(CHAT,),
        priority=40,
        context_window=64000,
    ),
    ModelRecord(
        id="google/gemma-4-26b-a4b-it:free", categories=(CHAT,), priority=40, context_window=128000
    ),
    ModelRecord(
        id="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        categories=(REASONING,),
        priority=40,
        context_window=64000,
    ),
    ModelRecord(
        id="dots-studio/dots-3-note-preview:free",
        categories=(CHAT,),
        priority=50,
        context_window=32768,
    ),
    ModelRecord(
        id="nvidia/nemotron-3.5-lightning:free",
        categories=(CHAT,),
        priority=50,
        context_window=64000,
    ),
    ModelRecord(
        id="liquid/lfm-2.5-2.6b:free", categories=(CHAT,), priority=60, context_window=32768
    ),
)


@dataclass
class _Health:
    consecutive_failures: int = 0
    consecutive_rate_limits: int = 0
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
            validated_categories = _validate_categories(entry.categories, entry.id)
            self._state.models[entry.id] = ModelRecord(
                id=entry.id,
                categories=validated_categories,
                priority=entry.priority,
                context_window=entry.context_window,
            )

    def all_models(self) -> list[ModelRecord]:
        """Return all registered model records."""
        return list(self._state.models.values())

    def healthy_models(self) -> list[ModelRecord]:
        """Return all currently non-cooling model records sorted by priority."""
        now = time.monotonic()
        usable = [
            record for record in self._state.models.values() if not self._is_cooling(record.id, now)
        ]
        usable.sort(key=lambda record: (record.priority, record.id))
        return usable

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
        now = time.monotonic()
        health = self._state.health.setdefault(model_id, _Health())

        # Time-windowed failure counting: only count failures within the cooldown window
        if (
            health.cooldown_until > 0
            and now - health.cooldown_until > self._config.cooldown_seconds
        ):
            # Previous cooldown expired, reset streak
            health.consecutive_failures = 0
            health.consecutive_rate_limits = 0

        if rate_limited:
            # Adaptive exponential backoff per model: 1x, 2x, 4x, 8x, 16x base cooldown (max 1 hour)
            health.consecutive_rate_limits += 1
            multiplier = min(2 ** (health.consecutive_rate_limits - 1), 16)
            cooldown_dur = min(self._config.cooldown_seconds * multiplier, 3600.0)
            health.cooldown_until = now + cooldown_dur
        else:
            health.consecutive_failures += 1
            if health.consecutive_failures >= self._config.failure_threshold:
                health.cooldown_until = now + self._config.cooldown_seconds

    def is_cooling(self, model_id: str) -> bool:
        return self._is_cooling(model_id, time.monotonic())

    def get_model(self, model_id: str) -> ModelRecord | None:
        """Retrieve a ModelRecord by ID from the registry."""
        return self._state.models.get(model_id)

    def _is_cooling(self, model_id: str, now: float) -> bool:
        health = self._state.health.get(model_id)
        return bool(health and health.cooldown_until > now)
