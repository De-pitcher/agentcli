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

    @property
    def models(self) -> list[str]:
        return [self.primary, *self.fallbacks]


class Router:
    def __init__(self, registry: ModelRegistry, max_fallbacks: int):
        self._registry = registry
        self._max_fallbacks = max(0, max_fallbacks)

    def decide(self, category: str) -> RoutingDecision:
        """Best healthy candidates with cross-category fallbacks, or raises NoAvailableModelError."""
        candidates = self._registry.candidates(category)

        # Cross-category fallback chain if target category is fully cooling down
        if not candidates:
            for fallback_cat in CATEGORY_FALLBACKS.get(category, ()):
                candidates = self._registry.candidates(fallback_cat)
                if candidates:
                    logger.info(
                        "Category '%s' exhausted; falling back to category '%s'",
                        category,
                        fallback_cat,
                    )
                    break

        # Final global fallback to any healthy model in registry
        if not candidates:
            candidates = self._registry.healthy_models()

        if not candidates:
            total_models = len(self._registry.all_models())
            raise NoAvailableModelError(
                f"No healthy models available across any category. All {total_models} registered "
                f"models are currently cooling down or rate-limited."
            )

        primary = candidates[0]
        fallbacks = tuple(record.id for record in candidates[1 : 1 + self._max_fallbacks])
        return RoutingDecision(primary=primary.id, fallbacks=fallbacks)
