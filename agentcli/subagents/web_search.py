"""Web Search sub-agent with free providers.

Supports multiple free search providers:
- Brave Search API (2000 queries/month free)
- DuckDuckGo HTML scraping (no API key, fallback)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from .base import SubAgent, SubAgentResult, SubAgentTask, SubAgentType

if TYPE_CHECKING:
    from .bus import MessageBus


@dataclass
class SearchResult:
    """A single search result."""
    title: str
    url: str
    snippet: str


class SearchProvider:
    """Base class for search providers."""

    async def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        raise NotImplementedError


class BraveSearchProvider(SearchProvider):
    """Brave Search API provider (2000 queries/month free)."""

    BASE_URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("BRAVE_API_KEY")
        if not self.api_key:
            raise ValueError("Brave API key required (set BRAVE_API_KEY env var or pass in config)")

    async def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        assert self.api_key is not None  # validated in __init__
        headers: dict[str, str] = {
            "Accept": "application/json",
            "X-Subscription-Token": self.api_key,
        }
        params: dict[str, str | int] = {
            "q": query,
            "count": min(max_results, 20),
            "search_lang": "en",
            "country": "us",
            "safesearch": "moderate",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(self.BASE_URL, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()

        results = []
        for item in data.get("web", {}).get("results", [])[:max_results]:
            results.append(SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("description", ""),
            ))
        return results


class DuckDuckGoProvider(SearchProvider):
    """DuckDuckGo HTML scraping (no API key, fallback only)."""

    BASE_URL = "https://html.duckduckgo.com/html/"

    @staticmethod
    def _parse_results(html: str, max_results: int = 10) -> list[SearchResult]:
        """Parse DuckDuckGo HTML results."""
        # Extract all result positions first (title links)
        title_pattern = r'<h2 class="result__title">\s*<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>\s*</h2>'
        title_matches = list(re.finditer(title_pattern, html, re.DOTALL))
        
        # Extract all snippet positions
        snippet_pattern = r'<a class="result__snippet"[^>]*href="([^"]*)"[^>]*>(.*?)</a>'
        snippet_matches = list(re.finditer(snippet_pattern, html, re.DOTALL))
        
        # Extract display URLs
        url_pattern = r'class="result__url"[^>]*>(.*?)</a>'
        url_matches = list(re.finditer(url_pattern, html, re.DOTALL))
        
        results = []
        for i, title_match in enumerate(title_matches[:max_results]):
            url = title_match.group(1)
            title = re.sub(r'<[^>]+>', '', title_match.group(2)).strip()
            
            # Find matching snippet (by URL proximity or index)
            snippet = ""
            if i < len(snippet_matches):
                snippet_match = snippet_matches[i]
                snippet_url = snippet_match.group(1)
                snippet_text = snippet_match.group(2)
                # Verify URL matches (or use index as fallback)
                if snippet_url == url or i < len(snippet_matches):
                    snippet = re.sub(r'<[^>]+>', '', snippet_text).strip()
            
            # Find matching display URL
            display_url = ""
            if i < len(url_matches):
                display_url = re.sub(r'<[^>]+>', '', url_matches[i].group(1)).strip()
            
            # Use the actual URL from the title link if display_url is not clean
            if not display_url or display_url.startswith('\n'):
                final_url = url
            else:
                final_url = display_url
            
            if title and final_url:
                results.append(SearchResult(
                    title=title,
                    url=final_url,
                    snippet=snippet[:300] if snippet else "",
                ))
        
        return results

    async def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        params = {"q": query, "kl": "us-en"}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(self.BASE_URL, data=params, headers=headers)
            response.raise_for_status()
            return self._parse_results(response.text, max_results)


class WebSearchAgent(SubAgent):
    """Sub-agent for web search with multiple free providers."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        message_bus: MessageBus | None = None,
    ) -> None:
        super().__init__(SubAgentType.WEB_SEARCH, config, message_bus)

        # Provider configuration
        self.provider_name = str(self.config.get("provider", "brave")).lower()
        self.brave_api_key = self.config.get("brave_api_key") or os.environ.get("BRAVE_API_KEY")

        # Initialize providers
        self.providers: dict[str, SearchProvider] = {}

        if self.brave_api_key:
            try:
                self.providers["brave"] = BraveSearchProvider(self.brave_api_key)
            except ValueError:
                pass

        # Always add DuckDuckGo as fallback
        self.providers["duckduckgo"] = DuckDuckGoProvider()

    def _get_provider(self) -> SearchProvider:
        """Get the configured provider, falling back to DuckDuckGo."""
        if self.provider_name in self.providers:
            return self.providers[self.provider_name]
        # Fallback to first available
        if "brave" in self.providers:
            return self.providers["brave"]
        return self.providers["duckduckgo"]

    async def run(self, task: SubAgentTask) -> SubAgentResult:
        """Execute a web search.

        Expected payload:
            - query: search query string
            - max_results: maximum number of results (default: 10)
            - provider: optional provider override ("brave" or "duckduckgo")
        """
        payload = task.payload
        query = payload.get("query", "")

        if not query:
            return SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error="No search query provided",
            )

        max_results = payload.get("max_results", 10)
        provider_override = payload.get("provider", "").lower()

        # Select provider
        if provider_override and provider_override in self.providers:
            provider = self.providers[provider_override]
        else:
            provider = self._get_provider()

        try:
            results = await provider.search(query, max_results)

            if not results:
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=self.agent_type,
                    success=True,
                    output={
                        "query": query,
                        "results": [],
                        "message": "No results found",
                    },
                )

            return SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=True,
                output={
                    "query": query,
                    "results": [
                        {"title": r.title, "url": r.url, "snippet": r.snippet}
                        for r in results
                    ],
                    "count": len(results),
                    "provider": provider.__class__.__name__.replace("Provider", ""),
                },
            )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429 and provider.__class__.__name__ != "DuckDuckGoProvider":
                # Rate limited, try fallback to DuckDuckGo
                fallback = self.providers.get("duckduckgo")
                if fallback:
                    try:
                        results = await fallback.search(query, max_results)
                        return SubAgentResult(
                            task_id=task.id,
                            agent_type=self.agent_type,
                            success=True,
                            output={
                                "query": query,
                                "results": [
                                    {"title": r.title, "url": r.url, "snippet": r.snippet}
                                    for r in results
                                ],
                                "count": len(results),
                                "provider": "DuckDuckGo (fallback)",
                                "warning": "Primary provider rate limited, used fallback",
                            },
                        )
                    except Exception:  # noqa: BLE001,S110
                        # Fallback also failed, continue to error handling
                        pass

            return SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error=f"Search failed: HTTP {e.response.status_code}",
            )

        except (httpx.RequestError, httpx.TimeoutException) as e:
            # Network/timeout errors - try fallback to DuckDuckGo
            fallback = self.providers.get("duckduckgo")
            if fallback:
                try:
                    results = await fallback.search(query, max_results)
                    return SubAgentResult(
                        task_id=task.id,
                        agent_type=self.agent_type,
                        success=True,
                        output={
                            "query": query,
                            "results": [
                                {"title": r.title, "url": r.url, "snippet": r.snippet}
                                for r in results
                            ],
                            "count": len(results),
                            "provider": "DuckDuckGo (fallback)",
                            "warning": "Primary provider failed, used fallback",
                        },
                    )
                except Exception:  # noqa: BLE001,S110
                    # Fallback failed, continue to error handling
                    pass

            return SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error=f"Search failed: {e}",
            )

        except Exception as e:  # noqa: BLE001
            return SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error=f"Search failed: {e}",
            )