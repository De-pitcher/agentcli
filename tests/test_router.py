import pytest

from agentcli.config import RoutingConfig
from agentcli.routing.registry import CHAT, CODE, ModelRegistry
from agentcli.routing.router import NoAvailableModelError, Router

GEMMA = "google/gemma-4-31b-it:free"
MINIMAX = "minimax/minimax-m2.7:free"
COHERE = "cohere/north-mini-code:free"


def test_decision_orders_candidates_and_caps_fallbacks():
    router = Router(ModelRegistry(RoutingConfig()), max_fallbacks=1)
    decision = router.decide(CHAT)
    assert decision.primary == GEMMA
    assert decision.fallbacks == (MINIMAX,)
    assert decision.models == [GEMMA, MINIMAX]


def test_decision_skips_cooling_models():
    registry = ModelRegistry(RoutingConfig())
    registry.mark_failure(GEMMA, rate_limited=True)
    decision = Router(registry, max_fallbacks=2).decide(CHAT)
    assert decision.primary == MINIMAX
    assert GEMMA not in decision.models


def test_cross_category_fallback_when_category_exhausted():
    registry = ModelRegistry(RoutingConfig())
    # Mark all CODE category models as cooling
    for m in registry.candidates(CODE):
        registry.mark_failure(m.id, rate_limited=True)

    router = Router(registry, max_fallbacks=2)
    # Asking for CODE should fall back to REASONING/CHAT models rather than crashing
    decision = router.decide(CODE)
    assert decision is not None
    assert decision.is_fallback is True
    assert decision.requested_category == CODE
    assert decision.served_category in ("reasoning", "chat", "global")
    assert decision.primary in [r.id for r in registry.healthy_models()]


def test_no_available_model_error_when_all_models_cooling():
    registry = ModelRegistry(RoutingConfig())
    # Mark every model in registry as cooling
    for m in registry.all_models():
        registry.mark_failure(m.id, rate_limited=True)

    router = Router(registry, max_fallbacks=2)
    with pytest.raises(NoAvailableModelError, match="No healthy models available"):
        router.decide(CHAT)


def test_zero_fallbacks_yields_primary_only():
    router = Router(ModelRegistry(RoutingConfig()), max_fallbacks=0)
    decision = router.decide(CHAT)
    assert decision.fallbacks == ()
    assert decision.models == [decision.primary]


def test_negative_fallbacks_clamped_to_zero():
    router = Router(ModelRegistry(RoutingConfig()), max_fallbacks=-3)
    decision = router.decide(CHAT)
    assert decision.fallbacks == ()
