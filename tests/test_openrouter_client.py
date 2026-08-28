import json

import httpx
import pytest

from agentcli.config import OpenRouterConfig
from agentcli.openrouter_client import (
    ChatMessage,
    OpenRouterClient,
    OpenRouterError,
    RateLimitedError,
)


async def async_mock_sleep(t):
    pass


@pytest.mark.asyncio
async def test_chat_stream_success(monkeypatch):
    monkeypatch.setenv("DUMMY", "sk-123")

    def handler(request):
        async def stream():
            yield b'data: {"choices": [{"delta": {"content": "Hello"}}]}\n\n'
            yield b'data: {"choices": [{"delta": {"content": " World"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        return httpx.Response(200, content=stream())

    transport = httpx.MockTransport(handler)
    config = OpenRouterConfig(api_key_env="DUMMY")

    client = OpenRouterClient(config)
    client._client = httpx.AsyncClient(transport=transport, base_url=config.base_url)

    parts = []
    async for chunk in client.chat_stream([ChatMessage(role="user", content="hi")]):
        parts.append(chunk)

    assert "".join(parts) == "Hello World"
    await client.aclose()


@pytest.mark.asyncio
async def test_chat_stream_429_then_success(monkeypatch):
    monkeypatch.setenv("DUMMY", "sk-123")
    monkeypatch.setattr("asyncio.sleep", async_mock_sleep)  # speed up backoff

    attempt = 0

    def handler(request):
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            return httpx.Response(429, content=b"Too Many Requests")

        async def stream():
            yield b'data: {"choices": [{"delta": {"content": "Success"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        return httpx.Response(200, content=stream())

    transport = httpx.MockTransport(handler)
    config = OpenRouterConfig(api_key_env="DUMMY")

    client = OpenRouterClient(config)
    client._client = httpx.AsyncClient(transport=transport, base_url=config.base_url)

    parts = []
    async for chunk in client.chat_stream([ChatMessage(role="user", content="hi")]):
        parts.append(chunk)

    assert "".join(parts) == "Success"
    assert attempt == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_chat_stream_rate_limit_exhausted(monkeypatch):
    monkeypatch.setenv("DUMMY", "sk-123")
    monkeypatch.setattr("asyncio.sleep", async_mock_sleep)

    def handler(request):
        return httpx.Response(429, content=b"Too Many Requests")

    transport = httpx.MockTransport(handler)
    config = OpenRouterConfig(api_key_env="DUMMY", max_retries=2)

    client = OpenRouterClient(config)
    client._client = httpx.AsyncClient(transport=transport, base_url=config.base_url)

    with pytest.raises(RateLimitedError):
        async for _ in client.chat_stream([]):
            pass
    await client.aclose()


@pytest.mark.asyncio
async def test_chat_stream_immediate_4xx(monkeypatch):
    monkeypatch.setenv("DUMMY", "sk-123")

    def handler(request):
        return httpx.Response(400, content=b"Bad Request")

    transport = httpx.MockTransport(handler)
    config = OpenRouterConfig(api_key_env="DUMMY")

    client = OpenRouterClient(config)
    client._client = httpx.AsyncClient(transport=transport, base_url=config.base_url)

    with pytest.raises(OpenRouterError, match="OpenRouter error 400"):
        async for _ in client.chat_stream([]):
            pass
    await client.aclose()


@pytest.mark.asyncio
async def test_chat_stream_malformed_sse(monkeypatch):
    monkeypatch.setenv("DUMMY", "sk-123")

    def handler(request):
        async def stream():
            yield b'data: {"broken JSON\n\n'
            yield b'data: {"choices": [{"delta": {"content": "OK"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        return httpx.Response(200, content=stream())

    transport = httpx.MockTransport(handler)
    config = OpenRouterConfig(api_key_env="DUMMY")

    client = OpenRouterClient(config)
    client._client = httpx.AsyncClient(transport=transport, base_url=config.base_url)

    parts = []
    async for chunk in client.chat_stream([]):
        parts.append(chunk)

    assert "".join(parts) == "OK"
    await client.aclose()


@pytest.mark.asyncio
async def test_chat_stream_5xx_then_success(monkeypatch):
    monkeypatch.setenv("DUMMY", "sk-123")
    monkeypatch.setattr("asyncio.sleep", async_mock_sleep)

    attempt = 0

    def handler(request):
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            return httpx.Response(503, content=b"Service Unavailable")

        async def stream():
            yield b'data: {"choices": [{"delta": {"content": "Recovered"}}]}\n\n'

        return httpx.Response(200, content=stream())

    transport = httpx.MockTransport(handler)
    config = OpenRouterConfig(api_key_env="DUMMY")

    client = OpenRouterClient(config)
    client._client = httpx.AsyncClient(transport=transport, base_url=config.base_url)

    parts = []
    async for chunk in client.chat_stream([ChatMessage(role="user", content="hi")]):
        parts.append(chunk)

    assert "".join(parts) == "Recovered"
    assert attempt == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_chat_stream_5xx_exhausted(monkeypatch):
    monkeypatch.setenv("DUMMY", "sk-123")
    monkeypatch.setattr("asyncio.sleep", async_mock_sleep)

    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(500, content=b"Internal Server Error")

    transport = httpx.MockTransport(handler)
    config = OpenRouterConfig(api_key_env="DUMMY", max_retries=2)

    client = OpenRouterClient(config)
    client._client = httpx.AsyncClient(transport=transport, base_url=config.base_url)

    with pytest.raises(OpenRouterError, match="Server error 500"):
        async for _ in client.chat_stream([]):
            pass
    assert attempts == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_chat_stream_transport_error_then_success(monkeypatch):
    monkeypatch.setenv("DUMMY", "sk-123")
    monkeypatch.setattr("asyncio.sleep", async_mock_sleep)

    attempt = 0

    def handler(request):
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            raise httpx.ConnectError("connection reset")

        async def stream():
            yield b'data: {"choices": [{"delta": {"content": "Back online"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        return httpx.Response(200, content=stream())

    transport = httpx.MockTransport(handler)
    config = OpenRouterConfig(api_key_env="DUMMY")

    client = OpenRouterClient(config)
    client._client = httpx.AsyncClient(transport=transport, base_url=config.base_url)

    parts = []
    async for chunk in client.chat_stream([ChatMessage(role="user", content="hi")]):
        parts.append(chunk)

    assert "".join(parts) == "Back online"
    assert attempt == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_chat_stream_transport_error_exhausted(monkeypatch):
    monkeypatch.setenv("DUMMY", "sk-123")
    monkeypatch.setattr("asyncio.sleep", async_mock_sleep)

    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("connection refused")

    transport = httpx.MockTransport(handler)
    config = OpenRouterConfig(api_key_env="DUMMY", max_retries=2)

    client = OpenRouterClient(config)
    client._client = httpx.AsyncClient(transport=transport, base_url=config.base_url)

    with pytest.raises(OpenRouterError, match="Network error"):
        async for _ in client.chat_stream([]):
            pass
    assert attempts == 2
    await client.aclose()


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("DUMMY", raising=False)
    with pytest.raises(OpenRouterError, match="No API key found"):
        OpenRouterClient(OpenRouterConfig(api_key_env="DUMMY"))


@pytest.mark.asyncio
async def test_async_context_manager_closes_client(monkeypatch):
    monkeypatch.setenv("DUMMY", "sk-123")
    config = OpenRouterConfig(api_key_env="DUMMY")

    async with OpenRouterClient(config) as client:
        assert not client._client.is_closed
    assert client._client.is_closed


@pytest.mark.asyncio
async def test_chat_stream_models_array_payload(monkeypatch):
    monkeypatch.setenv("DUMMY", "sk-123")
    captured: dict[str, object] = {}

    def handler(request):
        captured["body"] = json.loads(request.content)

        async def stream():
            yield (
                b'data: {"model":"z-ai/glm-5.2:free","choices": [{"delta": {"content": "Hi"}}]}\n\n'
            )
            yield b"data: [DONE]\n\n"

        return httpx.Response(200, content=stream())

    transport = httpx.MockTransport(handler)
    config = OpenRouterConfig(api_key_env="DUMMY")

    client = OpenRouterClient(config)
    client._client = httpx.AsyncClient(transport=transport, base_url=config.base_url)

    parts = []
    async for chunk in client.chat_stream(
        [ChatMessage(role="user", content="hi")],
        models=["a/b:free", "c/d:free"],
    ):
        parts.append(chunk)

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["models"] == ["a/b:free", "c/d:free"]
    assert body["stream_options"] == {"include_usage": True}
    assert "model" not in body
    assert "".join(parts) == "Hi"
    assert client.last_served_model == "z-ai/glm-5.2:free"
    await client.aclose()


@pytest.mark.asyncio
async def test_chat_stream_single_model_keeps_model_key(monkeypatch):
    monkeypatch.setenv("DUMMY", "sk-123")
    captured: dict[str, object] = {}

    def handler(request):
        captured["body"] = json.loads(request.content)

        async def stream():
            yield b"data: [DONE]\n\n"

        return httpx.Response(200, content=stream())

    transport = httpx.MockTransport(handler)
    config = OpenRouterConfig(api_key_env="DUMMY", default_model="single/model:free")

    client = OpenRouterClient(config)
    client._client = httpx.AsyncClient(transport=transport, base_url=config.base_url)

    async for _ in client.chat_stream([ChatMessage(role="user", content="hi")]):
        pass

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "single/model:free"
    assert body["stream_options"] == {"include_usage": True}
    assert "models" not in body
    await client.aclose()


@pytest.mark.asyncio
async def test_chat_stream_mid_stream_error_event(monkeypatch):
    monkeypatch.setenv("DUMMY", "sk-123")

    def handler(request):
        async def stream():
            yield b'data: {"choices": [{"delta": {"content": "partial"}}]}\n\n'
            yield (
                b'data: {"error":{"code":429,"message":"Rate limit exceeded"},'
                b'"choices":[{"index":0,"delta":{"content":""},'
                b'"finish_reason":"error"}]}\n\n'
            )

        return httpx.Response(200, content=stream())

    transport = httpx.MockTransport(handler)
    config = OpenRouterConfig(api_key_env="DUMMY")

    client = OpenRouterClient(config)
    client._client = httpx.AsyncClient(transport=transport, base_url=config.base_url)

    with pytest.raises(OpenRouterError, match="stream error 429"):
        async for _ in client.chat_stream([ChatMessage(role="user", content="hi")]):
            pass
    await client.aclose()


@pytest.mark.asyncio
async def test_chat_stream_mid_stream_error_without_detail(monkeypatch):
    monkeypatch.setenv("DUMMY", "sk-123")

    def handler(request):
        async def stream():
            yield (
                b'data: {"choices":[{"index":0,"delta":{"content":""},'
                b'"finish_reason":"error"}]}\n\n'
            )

        return httpx.Response(200, content=stream())

    transport = httpx.MockTransport(handler)
    config = OpenRouterConfig(api_key_env="DUMMY")

    client = OpenRouterClient(config)
    client._client = httpx.AsyncClient(transport=transport, base_url=config.base_url)

    with pytest.raises(OpenRouterError, match="mid-stream failure"):
        async for _ in client.chat_stream([ChatMessage(role="user", content="hi")]):
            pass
    await client.aclose()


@pytest.mark.asyncio
async def test_chat_stream_captures_usage(monkeypatch):
    monkeypatch.setenv("DUMMY", "sk-123")

    def handler(request):
        async def stream():
            yield b'data: {"choices": [{"delta": {"content": "Hello"}}]}\n\n'
            yield b'data: {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}}\n\n'
            yield b"data: [DONE]\n\n"

        return httpx.Response(200, content=stream())

    transport = httpx.MockTransport(handler)
    config = OpenRouterConfig(api_key_env="DUMMY")

    client = OpenRouterClient(config)
    client._client = httpx.AsyncClient(transport=transport, base_url=config.base_url)

    chunks = []
    async for chunk in client.chat_stream([ChatMessage(role="user", content="hi")]):
        chunks.append(chunk)

    assert "".join(chunks) == "Hello"
    assert client.last_usage == {
        "prompt_tokens": 12,
        "completion_tokens": 5,
        "total_tokens": 17,
    }
    await client.aclose()
