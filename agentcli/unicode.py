"""Safe Unicode printing utilities with ASCII fallback for Windows CP1252."""

from __future__ import annotations

import io
import sys
from typing import Any


def configure_utf8_io() -> None:
    """Ensure standard input, output, and error streams use UTF-8 with safe error replacement."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, io.UnsupportedOperation, ValueError):
                continue


configure_utf8_io()


def _supports_unicode() -> bool:
    """Check if the current terminal supports Unicode output."""
    if sys.platform != "win32":
        return True
    # On Windows, check if we can encode Unicode
    try:
        "→".encode(sys.stdout.encoding or "cp1252")
        return True
    except UnicodeEncodeError:
        return False


_UNICODE_SUPPORTED = _supports_unicode()


def safe_print(*args: Any, **kwargs: Any) -> None:
    """Print with automatic Unicode-to-ASCII fallback on Windows CP1252."""
    try:
        if _UNICODE_SUPPORTED:
            print(*args, **kwargs)
            return

        ascii_args = []
        for arg in args:
            if isinstance(arg, str):
                ascii_args.append(safe_format(arg))
            else:
                ascii_args.append(arg)

        print(*ascii_args, **kwargs)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "ascii"
        fallback_args = []
        for arg in args:
            s = str(arg)
            fallback_args.append(s.encode(encoding, errors="replace").decode(encoding))
        print(*fallback_args, **kwargs)


_ASCII_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("→", "->"),
    ("←", "<-"),
    ("—", "--"),
    ("–", "-"),
    ("…", "..."),
    ("✓", "[OK]"),
    ("✗", "[FAIL]"),
    ("✔", "[OK]"),
    ("✘", "[FAIL]"),
    ("▶", ">"),
    ("▼", "v"),
    ("▲", "^"),
    ("•", "*"),
    ("·", "."),
    ("✕", "[X]"),
    ("⚠", "[WARN]"),
    ("✅", "[OK]"),
    ("❌", "[FAIL]"),
    ("⚡", "[!]"),
    ("🔍", "[SEARCH]"),
    ("📁", "[FILE]"),
    ("📄", "[DOC]"),
    ("⚙", "[CONFIG]"),
    ("🤖", "[BOT]"),
    ("💡", "[TIP]"),
    ("✨", "[*]"),
    ("🎉", "[DONE]"),
    ("🚀", "[LAUNCH]"),
    ("🔧", "[TOOL]"),
    ("📝", "[NOTE]"),
    ("🗑", "[DELETE]"),
    ("🔑", "[KEY]"),
    ("🔒", "[LOCK]"),
    ("🔓", "[UNLOCK]"),
    ("💾", "[SAVE]"),
)


def safe_format(text: str) -> str:
    """Convert Unicode text to ASCII-safe equivalent."""
    if _UNICODE_SUPPORTED:
        return text

    for src, dst in _ASCII_REPLACEMENTS:
        text = text.replace(src, dst)
    return text
