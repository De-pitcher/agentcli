from agentcli.config import RoutingConfig, RoutingModelEntry
from agentcli.routing.registry import CHAT, CODE, ModelRegistry

GEMMA = "google/gemma-4-31b-it:free"
COHERE = "cohere/north-mini-code:free"
MINIMAX = "minimax/minimax-m2.7:free"


def test_builtin_catalog_loaded():
    registry = ModelRegistry(RoutingConfig())
    chat_candidates = registry.candidates(CHAT)
    assert chat_candidates
    assert chat_candidates[0].id == GEMMA
    code_candidates = registry.candidates(CODE)
    assert code_candidates
    assert code_candidates[0].id == COHERE


def test_user_entries_replace_by_id_and_append():
    config = RoutingConfig(
        models=[
            RoutingModelEntry(id=GEMMA, categories=[CODE], priority=1),
            RoutingModelEntry(id="custom/model:free", categories=[CHAT], priority=5),
        ]
    )
    registry = ModelRegistry(config)
    assert registry.candidates(CODE)[0].id == GEMMA
    chat_ids = [record.id for record in registry.candidates(CHAT)]
    assert "custom/model:free" in chat_ids


def test_failure_threshold_triggers_cooldown():
    registry = ModelRegistry(RoutingConfig(failure_threshold=2, cooldown_seconds=300))
    registry.mark_failure(GEMMA)
    assert not registry.is_cooling(GEMMA)
    registry.mark_failure(GEMMA)
    assert registry.is_cooling(GEMMA)
    assert all(record.id != GEMMA for record in registry.candidates(CHAT))


def test_rate_limit_cools_down_immediately():
    registry = ModelRegistry(RoutingConfig())
    registry.mark_failure(GEMMA, rate_limited=True)
    assert registry.is_cooling(GEMMA)
    assert all(record.id != GEMMA for record in registry.candidates(CHAT))


def test_success_resets_failure_streak():
    registry = ModelRegistry(RoutingConfig(failure_threshold=2))
    registry.mark_failure(GEMMA)
    registry.mark_success(GEMMA)
    registry.mark_failure(GEMMA)
    assert not registry.is_cooling(GEMMA)


def test_cooldown_expires():
    registry = ModelRegistry(RoutingConfig(cooldown_seconds=300))
    registry.mark_failure(GEMMA, rate_limited=True)
    assert registry.is_cooling(GEMMA)
    registry._state.health[GEMMA].cooldown_until = 0.0
    assert not registry.is_cooling(GEMMA)
    assert any(record.id == GEMMA for record in registry.candidates(CHAT))


def test_minimax_is_second_chat_candidate():
    registry = ModelRegistry(RoutingConfig())
    chat_ids = [record.id for record in registry.candidates(CHAT)]
    assert MINIMAX == chat_ids[1]


def test_adaptive_rate_limit_exponential_backoff():
    base_cooldown = 100.0
    registry = ModelRegistry(RoutingConfig(cooldown_seconds=base_cooldown))

    import time

    t0 = time.monotonic()
    # 1st 429 -> multiplier = 1 (100s)
    registry.mark_failure(GEMMA, rate_limited=True)
    c1 = registry._state.health[GEMMA].cooldown_until - t0
    assert 99.0 <= c1 <= 101.0

    # 2nd 429 -> multiplier = 2 (200s)
    registry.mark_failure(GEMMA, rate_limited=True)
    c2 = registry._state.health[GEMMA].cooldown_until - t0
    assert 198.0 <= c2 <= 202.0

    # 3rd 429 -> multiplier = 4 (400s)
    registry.mark_failure(GEMMA, rate_limited=True)
    c3 = registry._state.health[GEMMA].cooldown_until - t0
    assert 396.0 <= c3 <= 404.0

    # Success resets consecutive_rate_limits
    registry.mark_success(GEMMA)
    assert registry._state.health[GEMMA].consecutive_rate_limits == 0
    assert not registry.is_cooling(GEMMA)


def test_independent_model_cooldowns():
    registry = ModelRegistry(RoutingConfig())
    registry.mark_failure(GEMMA, rate_limited=True)
    assert registry.is_cooling(GEMMA)
    assert not registry.is_cooling(MINIMAX)
    assert not registry.is_cooling(COHERE)
