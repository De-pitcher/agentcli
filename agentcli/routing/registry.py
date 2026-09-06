"""Model registry: candidate free OpenRouter models plus per-session health.

The built-in catalog is plain data (easy to extend); user config entries
replace built-ins by id or append new ones. Health tracking is in-memory
per session: consecutive failures trigger a cooldown; a 429 cools down
immediately. No persistence — that belongs to a later phase.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from ..config import ConfigError, RoutingConfig

logger = logging.getLogger(__name__)

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
    categories: tuple[str, ...] = (CHAT,)
    priority: int = 1
    context_window: int = 8192
    tier: str = "low"
    name: str | None = None

    @property
    def is_free(self) -> bool:
        """Return True if model is free of charge."""
        return self.id.endswith(":free") or self.tier == "low"


def format_models_text(
    models: list[ModelRecord],
    active_model: str | None = None,
    filter_type: str | None = None,
) -> str:
    """Format model list into an aligned text table with [FREE] and [PAID] visual indicators."""
    filtered = models
    if filter_type == "free":
        filtered = [m for m in models if m.is_free]
    elif filter_type == "paid":
        filtered = [m for m in models if not m.is_free]

    lines: list[str] = []
    lines.append(f"{'STATUS':<10} {'TYPE':<8} {'TIER':<8} {'CONTEXT':<10} {'MODEL ID'}")
    lines.append("-" * 76)

    for m in filtered:
        status = (
            "● ACTIVE"
            if active_model and (active_model == m.id or (active_model == "auto" and m.is_free))
            else ""
        )
        type_tag = "[FREE]" if m.is_free else "[PAID]"
        tier_tag = m.tier.upper()
        ctx = f"{m.context_window // 1000}k" if m.context_window >= 1000 else str(m.context_window)
        lines.append(f"{status:<10} {type_tag:<8} {tier_tag:<8} {ctx:<10} {m.id}")

    lines.append("-" * 76)
    lines.append("Tip: Switch active model using /model <model-id> or /model auto")
    return "\n".join(lines)


_BUILTIN_MODELS: tuple[ModelRecord, ...] = (
    # --- Low Tier (Free Models) ---
    ModelRecord(
        id="google/gemma-4-31b-it:free",
        categories=(CHAT,),
        priority=10,
        context_window=128000,
        tier="low",
    ),
    ModelRecord(
        id="cohere/north-mini-code:free",
        categories=(CODE,),
        priority=10,
        context_window=32768,
        tier="low",
    ),
    ModelRecord(
        id="z-ai/glm-5.2:free",
        categories=(CODE, REASONING),
        priority=20,
        context_window=128000,
        tier="low",
    ),
    ModelRecord(
        id="nvidia/nemotron-3-super-120b-a12b:free",
        categories=(REASONING,),
        priority=20,
        context_window=128000,
        tier="low",
    ),
    ModelRecord(
        id="minimax/minimax-m2.7:free",
        categories=(CHAT,),
        priority=20,
        context_window=128000,
        tier="low",
    ),
    ModelRecord(
        id="poolside/laguna-s-2.1:free",
        categories=(CODE,),
        priority=30,
        context_window=64000,
        tier="low",
    ),
    ModelRecord(
        id="nvidia/nemotron-3-ultra-550b-a55b:free",
        categories=(REASONING,),
        priority=30,
        context_window=128000,
        tier="low",
    ),
    ModelRecord(
        id="minimax/minimax-m3:free",
        categories=(CHAT,),
        priority=30,
        context_window=128000,
        tier="low",
    ),
    ModelRecord(
        id="thinkingmachines/inkling-small:free",
        categories=(CHAT,),
        priority=40,
        context_window=64000,
        tier="low",
    ),
    ModelRecord(
        id="google/gemma-4-26b-a4b-it:free",
        categories=(CHAT,),
        priority=40,
        context_window=128000,
        tier="low",
    ),
    ModelRecord(
        id="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        categories=(REASONING,),
        priority=40,
        context_window=64000,
        tier="low",
    ),
    ModelRecord(
        id="dots-studio/dots-3-note-preview:free",
        categories=(CHAT,),
        priority=50,
        context_window=32768,
        tier="low",
    ),
    ModelRecord(
        id="nvidia/nemotron-3.5-lightning:free",
        categories=(CHAT,),
        priority=50,
        context_window=64000,
        tier="low",
    ),
    ModelRecord(
        id="liquid/lfm-2.5-2.6b:free",
        categories=(CHAT,),
        priority=60,
        context_window=32768,
        tier="low",
    ),
    # --- Medium Tier (High-Efficiency Paid / Frontier Free) ---
    ModelRecord(
        id="openai/gpt-4o-mini",
        categories=(CHAT, CODE),
        priority=15,
        context_window=128000,
        tier="medium",
    ),
    ModelRecord(
        id="anthropic/claude-3.5-haiku",
        categories=(CHAT, CODE, REASONING),
        priority=15,
        context_window=200000,
        tier="medium",
    ),
    ModelRecord(
        id="deepseek/deepseek-chat",
        categories=(CHAT, CODE),
        priority=15,
        context_window=64000,
        tier="medium",
    ),
    ModelRecord(
        id="qwen/qwen-2.5-coder-32b-instruct",
        categories=(CODE,),
        priority=20,
        context_window=32768,
        tier="medium",
    ),
    ModelRecord(
        id="meta-llama/llama-3.3-70b-instruct",
        categories=(CHAT, REASONING),
        priority=25,
        context_window=128000,
        tier="medium",
    ),
    # --- High Tier (Frontier Reasoning & Coding) ---
    ModelRecord(
        id="anthropic/claude-3.5-sonnet",
        categories=(CODE, REASONING, CHAT),
        priority=5,
        context_window=200000,
        tier="high",
    ),
    ModelRecord(
        id="deepseek/deepseek-r1",
        categories=(REASONING, CODE),
        priority=5,
        context_window=64000,
        tier="high",
    ),
    ModelRecord(
        id="openai/gpt-4o",
        categories=(CHAT, CODE, REASONING),
        priority=5,
        context_window=128000,
        tier="high",
    ),
    ModelRecord(
        id="google/gemini-2.5-pro",
        categories=(REASONING, CHAT),
        priority=10,
        context_window=1000000,
        tier="high",
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
    """Candidate models for each category, with cooldown-aware and budget-tier selection."""

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
                tier=getattr(entry, "tier", "low"),
            )

    def all_models(self) -> list[ModelRecord]:
        """Return all registered model records."""
        return list(self._state.models.values())

    def get(self, model_id: str) -> ModelRecord | None:
        """Get model record by ID if registered."""
        return self._state.models.get(model_id)

    async def refresh_from_openrouter(self, client: Any | None = None) -> list[ModelRecord]:
        """Fetch remote OpenRouter models, merge into registry, and return updated list."""
        if client is not None:
            try:
                raw_models = await client.get_models()
                for item in raw_models:
                    m_id = item.get("id")
                    if not m_id:
                        continue
                    pricing = item.get("pricing", {})
                    prompt_price = float(pricing.get("prompt", 0) or 0)
                    comp_price = float(pricing.get("completion", 0) or 0)
                    is_free = m_id.endswith(":free") or (prompt_price == 0.0 and comp_price == 0.0)
                    tier = "low" if is_free else ("medium" if prompt_price < 0.000005 else "high")
                    ctx = int(item.get("context_length", 32768) or 32768)
                    name = item.get("name")
                    if m_id not in self._state.models:
                        self._state.models[m_id] = ModelRecord(
                            id=m_id,
                            categories=(CHAT, CODE, REASONING),
                            priority=60 if is_free else 120,
                            context_window=ctx,
                            tier=tier,
                            name=name,
                        )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Failed refreshing models from OpenRouter: %s", exc)
        return self.all_models()

    def healthy_models(self, budget_tier: str | None = None) -> list[ModelRecord]:
        """Return all currently non-cooling model records sorted by priority and budget tier."""
        now = time.monotonic()
        active_tier = budget_tier or getattr(self._config, "budget_tier", "low")
        if active_tier == "low":
            allowed_tiers = {"low"}
        elif active_tier == "medium":
            allowed_tiers = {"low", "medium"}
        else:
            allowed_tiers = {"low", "medium", "high"}

        usable = [
            record
            for record in self._state.models.values()
            if record.tier in allowed_tiers and not self._is_cooling(record.id, now)
        ]
        tier_weight = {"high": 0, "medium": 1, "low": 2}
        usable.sort(
            key=lambda record: (
                tier_weight.get(record.tier, 2) if active_tier != "low" else 0,
                record.priority,
                record.id,
            )
        )
        return usable

    def candidates(self, category: str, budget_tier: str | None = None) -> list[ModelRecord]:
        """Healthy models serving `category`, best-first filtered by budget tier."""
        now = time.monotonic()
        active_tier = budget_tier or getattr(self._config, "budget_tier", "low")
        if active_tier == "low":
            allowed_tiers = {"low"}
        elif active_tier == "medium":
            allowed_tiers = {"low", "medium"}
        else:
            allowed_tiers = {"low", "medium", "high"}

        usable = [
            record
            for record in self._state.models.values()
            if category in record.categories
            and record.tier in allowed_tiers
            and not self._is_cooling(record.id, now)
        ]
        tier_weight = {"high": 0, "medium": 1, "low": 2}
        usable.sort(
            key=lambda record: (
                tier_weight.get(record.tier, 2) if active_tier != "low" else 0,
                record.priority,
                record.id,
            )
        )
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
