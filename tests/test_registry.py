import pytest

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


def test_model_record_is_free_and_format_models_text() -> None:
    from agentcli.routing.registry import ModelRecord, format_models_text

    rec_free = ModelRecord(id="google/gemma-2-9b-it:free", context_window=8192, tier="low")
    rec_paid = ModelRecord(id="anthropic/claude-3.5-sonnet", context_window=200000, tier="high")

    assert rec_free.is_free is True
    assert rec_paid.is_free is False

    models = [rec_free, rec_paid]

    # All models text
    text_all = format_models_text(models, active_model="google/gemma-2-9b-it:free")
    assert "[FREE]" in text_all
    assert "[PAID]" in text_all
    assert "● ACTIVE" in text_all
    assert "google/gemma-2-9b-it:free" in text_all
    assert "anthropic/claude-3.5-sonnet" in text_all

    # Filter free
    text_free = format_models_text(models, filter_type="free")
    assert "google/gemma-2-9b-it:free" in text_free
    assert "anthropic/claude-3.5-sonnet" not in text_free

    # Filter paid
    text_paid = format_models_text(models, filter_type="paid")
    assert "google/gemma-2-9b-it:free" not in text_paid
    assert "anthropic/claude-3.5-sonnet" in text_paid


@pytest.mark.asyncio
async def test_registry_refresh_from_openrouter() -> None:
    from unittest.mock import AsyncMock, MagicMock

    mock_client = MagicMock()
    mock_client.get_models = AsyncMock(
        return_value=[
            {
                "id": "meta-llama/llama-3.3-70b-instruct:free",
                "name": "Llama 3.3 70B (free)",
                "context_length": 131072,
                "pricing": {"prompt": "0", "completion": "0"},
            },
            {
                "id": "openai/gpt-4o",
                "name": "GPT-4o",
                "context_length": 128000,
                "pricing": {"prompt": "0.0000025", "completion": "0.00001"},
            },
        ]
    )

    registry = ModelRegistry(RoutingConfig())
    models = await registry.refresh_from_openrouter(mock_client)
    assert len(models) >= 2
    rec_llama = registry.get("meta-llama/llama-3.3-70b-instruct:free")
    assert rec_llama is not None
    assert rec_llama.is_free is True
    rec_gpt = registry.get("openai/gpt-4o")
    assert rec_gpt is not None
    assert rec_gpt.is_free is False
