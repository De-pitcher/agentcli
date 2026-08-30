"""File reading tool: injects file contents into the prompt context via @path tokens."""

from __future__ import annotations

from pathlib import Path

from .memory.cache import ContextCache, get_default_context_cache

MAX_FILE_BYTES = 200_000  # keeps a single file injection within a safe token budget


class FileReadError(Exception):
    pass


def _read_file_uncached(p: Path) -> str:
    """Internal reader that reads and formats a file block without checking cache."""
    if not p.exists():
        raise FileReadError(f"File not found: {p}")
    if not p.is_file():
        raise FileReadError(f"Not a file: {p}")
    if p.stat().st_size > MAX_FILE_BYTES:
        raise FileReadError(
            f"File too large ({p.stat().st_size} bytes, max {MAX_FILE_BYTES}). "
            "Trim it or split it before including."
        )

    try:
        content = p.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise FileReadError(f"Could not decode {p} as UTF-8 (binary file?): {exc}")

    lang = p.suffix.lstrip(".") or "text"
    return f"### File: {p}\n```{lang}\n{content}\n```"


def read_file_for_context(path: str | Path, cache: ContextCache | None = None) -> str:
    """
    Reads a file and returns a fenced, labeled block suitable for injection
    into a chat message. Uses ContextCache if available to avoid redundant disk I/O.
    Raises FileReadError for missing/binary/oversized files.
    """
    active_cache = cache if cache is not None else get_default_context_cache()
    if active_cache is not None and active_cache.enabled:
        content, _ = active_cache.get_or_read(path, _read_file_uncached)
        return content
    return _read_file_uncached(Path(path))


def expand_file_references(text: str, cache: ContextCache | None = None) -> str:
    """
    Expands @path/to/file tokens in user input into fenced file content blocks
    appended after the message text. Minimal v1: any whitespace-delimited
    token starting with '@' (length > 1) is treated as a file reference.
    Missing/oversized/binary references raise FileReadError so the caller can
    surface a clear message instead of silently dropping context.
    """
    lines = text.split("\n")
    cleaned_lines: list[str] = []
    file_blocks: list[str] = []

    for line in lines:
        if "@" not in line:
            cleaned_lines.append(line)
            continue

        tokens = line.split()
        line_parts: list[str] = []
        has_file_ref = False
        for tok in tokens:
            if tok.startswith("@") and len(tok) > 1:
                clean_tok = tok.rstrip(".,;:?!)]}\"'")
                file_blocks.append(read_file_for_context(clean_tok[1:], cache=cache))
                has_file_ref = True
            else:
                line_parts.append(tok)

        if has_file_ref:
            cleaned_lines.append(" ".join(line_parts))
        else:
            cleaned_lines.append(line)

    prompt = "\n".join(cleaned_lines).strip()
    if file_blocks:
        if prompt:
            prompt = prompt + "\n\n" + "\n\n".join(file_blocks)
        else:
            prompt = "\n\n".join(file_blocks)
    return prompt


def find_agents_md(start_dir: str | Path | None = None) -> Path | None:
    """Search for AGENTS.md or agents.md starting at start_dir and walking up the tree."""
    curr = Path(start_dir).resolve() if start_dir is not None else Path.cwd().resolve()
    for directory in [curr, *curr.parents]:
        for candidate in ("AGENTS.md", "agents.md"):
            target = directory / candidate
            if target.is_file():
                return target
        if (directory / ".git").exists():
            break
    return None


def load_agents_md(start_dir: str | Path | None = None) -> str | None:
    """Load and format project-level AGENTS.md instructions if present."""
    target = find_agents_md(start_dir)
    if target is None:
        return None
    try:
        if target.stat().st_size > MAX_FILE_BYTES:
            return None
        content = target.read_text(encoding="utf-8").strip()
        if not content:
            return None
        return f"### Project Instructions ({target.name})\n{content}"
    except Exception:  # noqa: BLE001
        return None
