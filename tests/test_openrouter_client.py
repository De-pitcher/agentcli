async def async_mock_sleep(t): pass
import json
import pytest
import httpx

from agentcli.config import OpenRouterConfig
from agentcli.openrouter_client import ChatMessage, OpenRouterClient, OpenRouterError, RateLimitedError


@pytest.mark.asyncio
async def test_chat_stream_success(monkeypatch):
    monkeypatch.setenv("DUMMY", "sk-123")
    def handler(request):
        async def stream():
            yield b'data: {"choices": [{"delta": {"content": "Hello"}}]}\n\n'
            yield b'data: {"choices": [{"delta": {"content": " World"}}]}\n\n'
            yield b'data: [DONE]\n\n'
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
    monkeypatch.setenv('DUMMY', 'sk-123')
    monkeypatch.setattr("asyncio.sleep", async_mock_sleep)  # speed up backoff

    attempt = 0
    def handler(request):
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            return httpx.Response(429, content=b"Too Many Requests")
        
        async def stream():
            yield b'data: {"choices": [{"delta": {"content": "Success"}}]}\n\n'
            yield b'data: [DONE]\n\n'
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
    monkeypatch.setenv('DUMMY', 'sk-123')
    monkeypatch.setattr("asyncio.sleep", async_mock_sleep)

    def handler(request):
        return httpx.Response(429, content=b"Too Many Requests")

    transport = httpx.MockTransport(handler)
    config = OpenRouterConfig(api_key_env="DUMMY", max_retries=2)
    
    client = OpenRouterClient(config)
    client._client = httpx.AsyncClient(transport=transport, base_url=config.base_url)
    
    with pytest.raises(RateLimitedError):
        async for chunk in client.chat_stream([]):
            pass
    await client.aclose()


@pytest.mark.asyncio
async def test_chat_stream_immediate_4xx(monkeypatch):
    monkeypatch.setenv('DUMMY', 'sk-123')
    def handler(request):
        return httpx.Response(400, content=b"Bad Request")

    transport = httpx.MockTransport(handler)
    config = OpenRouterConfig(api_key_env="DUMMY")
    
    client = OpenRouterClient(config)
    client._client = httpx.AsyncClient(transport=transport, base_url=config.base_url)
    
    with pytest.raises(OpenRouterError, match="OpenRouter error 400"):
        async for chunk in client.chat_stream([]):
            pass
    await client.aclose()


@pytest.mark.asyncio
async def test_chat_stream_malformed_sse(monkeypatch):
    monkeypatch.setenv('DUMMY', 'sk-123')
    def handler(request):
        async def stream():
            yield b'data: {"broken JSON\n\n'
            yield b'data: {"choices": [{"delta": {"content": "OK"}}]}\n\n'
            yield b'data: [DONE]\n\n'
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
