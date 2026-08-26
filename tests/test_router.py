from agentcli.config import RoutingConfig
from agentcli.routing.registry import CHAT, ModelRegistry
from agentcli.routing.router import Router

GEMMA = "google/gemma-4-31b-it:free"
MINIMAX = "minimax/minimax-m2.7:free"


def test_decision_orders_candidates_and_caps_fallbacks():
    router = Router(ModelRegistry(RoutingConfig()), max_fallbacks=1)
    decision = router.decide(CHAT)
    assert decision is not None
    assert decision.primary == GEMMA
    assert decision.fallbacks == (MINIMAX,)
    assert decision.models == [GEMMA, MINIMAX]


def test_decision_skips_cooling_models():
    registry = ModelRegistry(RoutingConfig())
    registry.mark_failure(GEMMA, rate_limited=True)
    decision = Router(registry, max_fallbacks=2).decide(CHAT)
    assert decision is not None
    assert decision.primary == MINIMAX
    assert GEMMA not in decision.models


def test_unknown_category_returns_none():
    router = Router(ModelRegistry(RoutingConfig()), max_fallbacks=2)
    assert router.decide("nonexistent-category") is None


def test_zero_fallbacks_yields_primary_only():
    router = Router(ModelRegistry(RoutingConfig()), max_fallbacks=0)
    decision = router.decide(CHAT)
    assert decision is not None
    assert decision.fallbacks == ()
    assert decision.models == [decision.primary]


def test_negative_fallbacks_clamped_to_zero():
    router = Router(ModelRegistry(RoutingConfig()), max_fallbacks=-3)
    decision = router.decide(CHAT)
    assert decision is not None
    assert decision.fallbacks == ()
