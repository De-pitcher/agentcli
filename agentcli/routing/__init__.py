"""Task routing: classify a message, pick candidate models, track health."""

from .classifier import classify
from .registry import CHAT, CODE, REASONING, ModelRecord, ModelRegistry
from .router import NoAvailableModelError, Router, RoutingDecision

__all__ = [
    "CHAT",
    "CODE",
    "REASONING",
    "ModelRecord",
    "ModelRegistry",
    "NoAvailableModelError",
    "Router",
    "RoutingDecision",
    "classify",
]
