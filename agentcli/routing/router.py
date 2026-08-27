"""Router: category + registry state -> ordered candidate list.

The primary plus fallbacks are sent as OpenRouter's `models` array so the
server performs model failover remotely; the client only handles what the
server cannot (transport errors, chain exhaustion, health marking).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .registry import ModelRegistry

logger = logging.getLogger(__name__)


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

    def decide(self, category: str) -> RoutingDecision | None:
        """Best healthy candidates for a category, or None if nothing qualifies."""
        candidates = self._registry.candidates(category)
        if not candidates:
            logger.warning("No healthy models available for category '%s'", category)
            return None
        primary = candidates[0]
        fallbacks = tuple(record.id for record in candidates[1 : 1 + self._max_fallbacks])
        return RoutingDecision(primary=primary.id, fallbacks=fallbacks)
