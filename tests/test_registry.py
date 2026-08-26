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
