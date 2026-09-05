"""Virtual terminal frame buffer and visual snapshot testing utility for AgentCLI UI."""

from __future__ import annotations

import io
import re

ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def strip_ansi(text: str) -> str:
    """Remove ANSI color and style escape sequences from text."""
    return ANSI_ESCAPE_RE.sub("", text)


class VirtualTerminalBuffer:
    """Virtual terminal screen buffer capturing output for visual layout verification."""

    def __init__(self, cols: int = 80, rows: int = 24) -> None:
        self.cols = cols
        self.rows = rows
        self._buffer = io.StringIO()

    def write(self, text: str) -> None:
        """Write text into virtual terminal buffer."""
        self._buffer.write(text)

    def get_raw_text(self) -> str:
        """Return raw buffer text including ANSI escape sequences."""
        return self._buffer.getvalue()

    def get_plain_text(self) -> str:
        """Return clean buffer text with ANSI escape codes stripped."""
        return strip_ansi(self._buffer.getvalue())

    def get_lines(self) -> list[str]:
        """Return stripped plain text lines."""
        return self.get_plain_text().splitlines()

    def assert_no_overflow(self, max_cols: int | None = None) -> None:
        """Assert that no rendered line exceeds the specified column width."""
        limit = max_cols or self.cols
        for idx, line in enumerate(self.get_lines(), 1):
            if len(line) > limit:
                raise AssertionError(
                    f"Line {idx} exceeds column width {limit} (length: {len(line)}):\n{line}"
                )
