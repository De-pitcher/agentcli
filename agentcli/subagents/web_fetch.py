"""Web Fetch sub-agent (Phase 31).

Directly fetches web pages, online documentation, GitHub issues/PRs, and API references,
converting HTML into clean Markdown without external dependencies.
"""

from __future__ import annotations

import logging
import re
from html import unescape
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

import httpx

from .base import SubAgent, SubAgentResult, SubAgentTask, SubAgentType

if TYPE_CHECKING:
    from .bus import MessageBus

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_BYTES = 50_000
USER_AGENT = (
    "AgentCLI/2.13.0 (Autonomous Developer Assistant; +https://github.com/De-pitcher/agentcli)"
)


class HTMLToMarkdownConverter(HTMLParser):
    """Zero-dependency HTML to clean Markdown converter."""

    SKIP_TAGS = frozenset(
        {
            "script",
            "style",
            "noscript",
            "svg",
            "canvas",
            "iframe",
            "nav",
            "footer",
            "header",
            "aside",
        }
    )

    def __init__(self, base_url: str = "") -> None:
        super().__init__()
        self.base_url = base_url
        self.output: list[str] = []
        self._skip_depth = 0
        self._in_pre = False
        self._in_code = False
        self._current_link_href: str | None = None
        self._current_link_text: list[str] = []
        self._heading_level = 0
        self._in_list_item = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        if tag_lower in self.SKIP_TAGS:
            self._skip_depth += 1
            return

        if self._skip_depth > 0:
            return

        attr_dict = dict(attrs)

        if tag_lower in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._heading_level = int(tag_lower[1])
            self.output.append(f"\n\n{'#' * self._heading_level} ")
        elif tag_lower == "p":
            self.output.append("\n\n")
        elif tag_lower == "br":
            self.output.append("\n")
        elif tag_lower == "hr":
            self.output.append("\n\n---\n\n")
        elif tag_lower == "pre":
            self._in_pre = True
            self.output.append("\n\n```\n")
        elif tag_lower == "code" and not self._in_pre:
            self._in_code = True
            self.output.append("`")
        elif tag_lower == "a":
            href = attr_dict.get("href")
            if href and not href.startswith(("javascript:", "mailto:", "#")):
                self._current_link_href = urljoin(self.base_url, href)
                self._current_link_text = []
        elif tag_lower in ("ul", "ol"):
            self.output.append("\n")
        elif tag_lower == "li":
            self._in_list_item = True
            self.output.append("\n- ")
        elif tag_lower in ("blockquote", "q"):
            self.output.append("\n> ")

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower in self.SKIP_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return

        if self._skip_depth > 0:
            return

        if tag_lower in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._heading_level = 0
            self.output.append("\n")
        elif tag_lower == "pre":
            self._in_pre = False
            self.output.append("\n```\n\n")
        elif tag_lower == "code" and not self._in_pre:
            self._in_code = False
            self.output.append("`")
        elif tag_lower == "a":
            if self._current_link_href:
                text = "".join(self._current_link_text).strip()
                if text:
                    self.output.append(f"[{text}]({self._current_link_href})")
                elif self._current_link_href:
                    self.output.append(self._current_link_href)
                self._current_link_href = None
                self._current_link_text = []
        elif tag_lower == "li":
            self._in_list_item = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return

        text = unescape(data)
        if self._current_link_href is not None:
            self._current_link_text.append(text)
            return

        if self._in_pre:
            self.output.append(text)
        else:
            cleaned = re.sub(r"[ \t]+", " ", text)
            self.output.append(cleaned)

    def get_markdown(self) -> str:
        raw = "".join(self.output)
        # Collapse multiple blank lines
        cleaned = re.sub(r"\n{3,}", "\n\n", raw).strip()
        return cleaned


def html_to_markdown(html_content: str, base_url: str = "") -> str:
    """Convert HTML string to clean Markdown."""
    converter = HTMLToMarkdownConverter(base_url=base_url)
    converter.feed(html_content)
    return converter.get_markdown()


class WebFetchAgent(SubAgent):
    """Sub-agent for directly fetching and reading web pages and documentation.

    Supported payload:
        url (str): The HTTP/HTTPS URL to fetch.
        content_offset (int, optional): Byte offset into content.
        max_bytes (int, optional): Maximum bytes to return (default 50KB).
        raw (bool, optional): If True, returns raw body without HTML-to-markdown conversion.
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        message_bus: MessageBus | None = None,
    ) -> None:
        super().__init__(SubAgentType.WEB_FETCH, config, message_bus)
        self.timeout_seconds = float(self.config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        self.max_bytes = int(self.config.get("max_bytes", DEFAULT_MAX_BYTES))

    async def run(self, task: SubAgentTask) -> SubAgentResult:
        payload = task.payload
        url = payload.get("url", "").strip()

        if not url:
            return SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error="No URL provided for web_fetch",
            )

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error=f"Invalid URL scheme '{parsed.scheme}'. Only http:// and https:// are supported.",
            )

        content_offset = max(0, int(payload.get("content_offset", 0)))
        max_bytes = max(100, int(payload.get("max_bytes", self.max_bytes)))
        raw_mode = bool(payload.get("raw", False))

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/markdown, text/html, application/xhtml+xml, application/json, text/plain;q=0.9, */*;q=0.8",
        }

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=True,
                headers=headers,
            ) as client:
                response = await client.get(url)

                if response.status_code >= 400:
                    return SubAgentResult(
                        task_id=task.id,
                        agent_type=self.agent_type,
                        success=False,
                        error=f"HTTP {response.status_code}: {response.reason_phrase} for {url}",
                        output={"status_code": response.status_code, "url": str(response.url)},
                    )

                content_type = response.headers.get("content-type", "").lower()
                text_content = response.text

                # If HTML and not raw mode, convert to markdown
                if "html" in content_type and not raw_mode:
                    converted_text = html_to_markdown(text_content, base_url=str(response.url))
                else:
                    converted_text = text_content

                encoded = converted_text.encode("utf-8")
                total_bytes = len(encoded)

                # Apply offset and slice
                sliced_bytes = encoded[content_offset : content_offset + max_bytes]
                result_text = sliced_bytes.decode("utf-8", errors="replace")
                truncated = (content_offset + max_bytes) < total_bytes

                if truncated:
                    remaining = total_bytes - (content_offset + max_bytes)
                    result_text += f"\n\n[... Truncated. {remaining:,} bytes remaining. Use content_offset={content_offset + max_bytes} to view more ...]"

                return SubAgentResult(
                    task_id=task.id,
                    agent_type=self.agent_type,
                    success=True,
                    output={
                        "url": str(response.url),
                        "status_code": response.status_code,
                        "content_type": content_type,
                        "content": result_text,
                        "total_bytes": total_bytes,
                        "content_offset": content_offset,
                        "truncated": truncated,
                    },
                )
        except httpx.TimeoutException:
            return SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error=f"Request timed out after {self.timeout_seconds}s for {url}",
            )
        except httpx.RequestError as exc:
            return SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error=f"Network request error: {exc}",
            )
        except Exception as exc:  # noqa: BLE001
            return SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error=f"Unexpected error fetching {url}: {exc}",
            )
