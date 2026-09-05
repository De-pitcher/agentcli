"""Router: category + registry state -> ordered candidate list.

The primary plus fallbacks are sent as OpenRouter's `models` array so the
server performs model failover remotely; the client only handles what the
server cannot (transport errors, chain exhaustion, health marking).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .registry import CHAT, CODE, REASONING, ModelRegistry

logger = logging.getLogger(__name__)


class NoAvailableModelError(Exception):
    """Raised when all candidate models across all categories are cooling down or rate-limited."""


CATEGORY_FALLBACKS: dict[str, tuple[str, ...]] = {
    CODE: (REASONING, CHAT),
    REASONING: (CODE, CHAT),
    CHAT: (REASONING, CODE),
}


@dataclass(frozen=True)
class RoutingDecision:
    primary: str
    fallbacks: tuple[str, ...]
    is_fallback: bool = False
    requested_category: str | None = None
    served_category: str | None = None
    budget_tier: str | None = None

    @property
    def models(self) -> list[str]:
        return [self.primary, *self.fallbacks]


class Router:
    def __init__(self, registry: ModelRegistry, max_fallbacks: int = 2, budget_tier: str = "low"):
        self._registry = registry
        self._max_fallbacks = max(0, max_fallbacks)
        self._budget_tier = budget_tier

    def decide(self, category: str, budget_tier: str | None = None) -> RoutingDecision:
        """Best healthy candidates with cross-category and cross-tier fallbacks, or raises NoAvailableModelError."""
        tier = budget_tier or self._budget_tier
        candidates = self._registry.candidates(category, budget_tier=tier)
        is_fallback = False
        served_cat: str | None = category

        # Cross-category fallback chain if target category is fully cooling down in this tier
        if not candidates:
            for fallback_cat in CATEGORY_FALLBACKS.get(category, ()):
                candidates = self._registry.candidates(fallback_cat, budget_tier=tier)
                if candidates:
                    is_fallback = True
                    served_cat = fallback_cat
                    logger.info(
                        "Category '%s' exhausted; falling back to category '%s' in tier '%s'",
                        category,
                        fallback_cat,
                        tier,
                    )
                    break

        # Fallback to any healthy model in this budget tier
        if not candidates:
            candidates = self._registry.healthy_models(budget_tier=tier)
            if candidates:
                is_fallback = True
                served_cat = "global"

        # If still empty, fallback across all tiers
        if not candidates and tier != "high":
            candidates = self._registry.healthy_models(budget_tier="high")
            if candidates:
                is_fallback = True
                served_cat = "all_tiers"

        if not candidates:
            total_models = len(self._registry.all_models())
            raise NoAvailableModelError(
                f"No healthy models available across any category. All {total_models} registered "
                f"models are currently cooling down or rate-limited."
            )

        primary = candidates[0]
        fallbacks = tuple(record.id for record in candidates[1 : 1 + self._max_fallbacks])
        return RoutingDecision(
            primary=primary.id,
            fallbacks=fallbacks,
            is_fallback=is_fallback,
            requested_category=category,
            served_category=served_cat,
            budget_tier=tier,
        )
