"""File reading tool: injects file contents into the prompt context via @path tokens."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
    Expands @path/to/file and @semantic:<query> tokens in user input into fenced
    content blocks appended after the message text.
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
                ref = clean_tok[1:]
                if ref.startswith(("repo:", "workspace:")):
                    target = ref.split(":", 1)[1]
                    try:
                        from .config import load_config
                        from .mesh import WorkspaceRegistry

                        registry = WorkspaceRegistry()
                        cfg = load_config()
                        registry.load_from_config(cfg.mesh.workspaces)
                        if cfg.mesh.auto_discover and not registry.list_workspaces():
                            registry.auto_discover(max_depth=cfg.mesh.discovery_depth)

                        _ws, resolved_p = registry.resolve_path(target)
                        file_blocks.append(read_file_for_context(str(resolved_p), cache=cache))
                    except Exception as exc:  # noqa: BLE001
                        file_blocks.append(f"[Error resolving @{ref}: {exc}]")
                elif ref.startswith(("semantic:", "find:")):
                    query_raw = ref.split(":", 1)[1]
                    repo_scope: str | None = None
                    if ":" in query_raw:
                        repo_scope, query_raw = query_raw.split(":", 1)
                    query = query_raw.replace("_", " ")
                    try:
                        from .config import load_config
                        from .embeddings import VectorIndex, VectorStore
                        from .mesh import WorkspaceRegistry

                        store = VectorStore()
                        registry = WorkspaceRegistry()
                        cfg = load_config()
                        registry.load_from_config(cfg.mesh.workspaces)
                        if cfg.mesh.auto_discover and not registry.list_workspaces():
                            registry.auto_discover(max_depth=cfg.mesh.discovery_depth)

                        records = store.get_all_for_model(cfg.embeddings.model)
                        if records:
                            # Synchronous dot-product search with fallback embedding
                            index = VectorIndex(store=store)
                            q_vec = index.engine._deterministic_fallback_vector(query) if hasattr(index.engine, "_deterministic_fallback_vector") else None
                            if q_vec:
                                from .embeddings.index import _dot_product

                                scored = [(chunk, _dot_product(q_vec, vec)) for chunk, vec in records]
                                if repo_scope:
                                    ws_obj = registry.get(repo_scope)
                                    if ws_obj:
                                        scored = [s for s in scored if str(ws_obj.resolved_path) in s[0].file_path]
                                scored.sort(key=lambda s: s[1], reverse=True)
                                top = scored[:3]
                                prefix = f"[{repo_scope}] " if repo_scope else ""
                                snippet_lines = [f"### Semantic Search Context for: '{prefix}{query}'"]
                                for chunk, score in top:
                                    snippet_lines.append(
                                        f"```{chunk.chunk_type}\n# {chunk.file_path}:{chunk.start_line}-{chunk.end_line} (score: {score:.2f})\n{chunk.content}\n```"
                                    )
                                file_blocks.append("\n".join(snippet_lines))
                    except Exception:  # noqa: BLE001,S110
                        pass
                else:
                    file_blocks.append(read_file_for_context(ref, cache=cache))
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


def _handle_remove_readonly(func: Any, path: str, exc_info: Any) -> None:
    """Error handler for shutil.rmtree that removes read-only attributes and retries."""
    import os
    import stat

    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:  # noqa: BLE001,S110
        pass


def safe_rmtree(target: str | Path, max_retries: int = 5, retry_delay: float = 0.05) -> None:
    """Safely remove a directory tree handling Windows file locks (WinError 32) and permissions.

    Strategies:
      1. Explicit garbage collection (`gc.collect()`) to close unreferenced file handles.
      2. `shutil.rmtree` with read-only attribute clearing handler.
      3. Exponential backoff retry loop on Windows `PermissionError` / `WinError 32` / `WinError 5`.
    """
    import gc
    import shutil
    import time

    p = Path(target)
    if not p.exists():
        return

    gc.collect()
    for attempt in range(max_retries):
        try:
            shutil.rmtree(p, onerror=_handle_remove_readonly)
            return
        except Exception:  # noqa: BLE001
            gc.collect()
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (2**attempt))
            else:
                try:
                    shutil.rmtree(p, ignore_errors=True)
                except Exception:  # noqa: BLE001,S110
                    pass


def safe_unlink(target: str | Path, max_retries: int = 5, retry_delay: float = 0.05) -> bool:
    """Safely remove a single file handling Windows file locks and read-only attributes."""
    import gc
    import os
    import stat
    import time

    p = Path(target)
    if not p.exists():
        return True

    gc.collect()
    for attempt in range(max_retries):
        try:
            if not os.access(p, os.W_OK):
                os.chmod(p, stat.S_IWRITE)
            p.unlink(missing_ok=True)
            return True
        except Exception:  # noqa: BLE001
            gc.collect()
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (2**attempt))

    return not p.exists()


__all__ = [
    "MAX_FILE_BYTES",
    "FileReadError",
    "expand_file_references",
    "find_agents_md",
    "load_agents_md",
    "read_file_for_context",
    "safe_rmtree",
    "safe_unlink",
]

