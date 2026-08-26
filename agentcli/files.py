"""File reading tool: injects file contents into the prompt context via @path tokens."""
from __future__ import annotations

from pathlib import Path

MAX_FILE_BYTES = 200_000  # keeps a single file injection within a safe token budget


class FileReadError(Exception):
    pass


def read_file_for_context(path: str | Path) -> str:
    """
    Reads a file and returns a fenced, labeled block suitable for injection
    into a chat message. Raises FileReadError for missing/binary/oversized files.
    """
    p = Path(path)
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


def expand_file_references(text: str) -> str:
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
                file_blocks.append(read_file_for_context(clean_tok[1:]))
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
