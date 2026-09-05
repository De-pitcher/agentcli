"""Language-aware code and text chunker for vector indexing (Phase 24).

Splits source files into semantic chunks (functions, classes, markdown sections, or
sliding line windows) while preserving file path, line numbers, and content hashes.
"""

from __future__ import annotations

import ast
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# File extensions to index by default
CODE_EXTENSIONS: set[str] = {
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
    ".rb",
    ".php",
    ".sh",
    ".bash",
    ".ps1",
    ".md",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".sql",
}


@dataclass
class CodeChunk:
    """Represents a chunk of code or text from a project file."""

    file_path: str
    chunk_id: str
    start_line: int
    end_line: int
    content: str
    sha256: str
    chunk_type: str = "block"

    def summary(self) -> str:
        return f"{self.file_path}:{self.start_line}-{self.end_line} ({self.chunk_type})"


def _compute_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_python_file(path: Path, content: str, max_lines: int = 60) -> list[CodeChunk]:
    """Chunk Python file using AST parsing for top-level classes and functions."""
    chunks: list[CodeChunk] = []
    lines = content.splitlines(keepends=True)
    if not lines:
        return []

    p_str = str(path.resolve())
    try:
        tree = ast.parse(content, filename=str(path))
        extracted_spans: list[tuple[int, int, str]] = []

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end_lineno = getattr(node, "end_lineno", node.lineno)
                extracted_spans.append((node.lineno, end_lineno, "function"))
            elif isinstance(node, ast.ClassDef):
                end_lineno = getattr(node, "end_lineno", node.lineno)
                extracted_spans.append((node.lineno, end_lineno, "class"))

        # Sort spans by starting line
        extracted_spans.sort(key=lambda s: s[0])

        # If AST found top-level definitions, construct chunks from them
        if extracted_spans:
            last_end = 0
            for start, end, chunk_type in extracted_spans:
                # Catch preceding module lines/imports if any
                if start > last_end + 1:
                    preceding_lines = lines[last_end : start - 1]
                    preceding_text = "".join(preceding_lines).strip()
                    if preceding_text:
                        cid = _compute_sha256(f"{p_str}:{last_end+1}:{start-1}:{preceding_text}")[:16]
                        chunks.append(
                            CodeChunk(
                                file_path=p_str,
                                chunk_id=cid,
                                start_line=last_end + 1,
                                end_line=start - 1,
                                content=preceding_text,
                                sha256=_compute_sha256(preceding_text),
                                chunk_type="header",
                            )
                        )

                # Add definition chunk
                chunk_lines = lines[start - 1 : end]
                chunk_text = "".join(chunk_lines).strip()
                if chunk_text:
                    cid = _compute_sha256(f"{p_str}:{start}:{end}:{chunk_text}")[:16]
                    chunks.append(
                        CodeChunk(
                            file_path=p_str,
                            chunk_id=cid,
                            start_line=start,
                            end_line=end,
                            content=chunk_text,
                            sha256=_compute_sha256(chunk_text),
                            chunk_type=chunk_type,
                        )
                    )
                last_end = end

            # Catch remaining trailing lines
            if last_end < len(lines):
                trailing_lines = lines[last_end:]
                trailing_text = "".join(trailing_lines).strip()
                if trailing_text:
                    cid = _compute_sha256(f"{p_str}:{last_end+1}:{len(lines)}:{trailing_text}")[:16]
                    chunks.append(
                        CodeChunk(
                            file_path=p_str,
                            chunk_id=cid,
                            start_line=last_end + 1,
                            end_line=len(lines),
                            content=trailing_text,
                            sha256=_compute_sha256(trailing_text),
                            chunk_type="footer",
                        )
                    )
            return chunks
    except SyntaxError:
        pass

    # Fallback to sliding window chunking if syntax error or no AST definitions
    return chunk_sliding_window(path, content, max_lines=max_lines)


def chunk_markdown_file(path: Path, content: str, max_lines: int = 60) -> list[CodeChunk]:
    """Chunk Markdown file by header sections (#, ##, ###)."""
    chunks: list[CodeChunk] = []
    lines = content.splitlines(keepends=True)
    if not lines:
        return []

    p_str = str(path.resolve())
    section_starts: list[int] = []

    for i, line in enumerate(lines):
        if line.startswith("#"):
            section_starts.append(i + 1)

    if not section_starts or section_starts[0] != 1:
        section_starts.insert(0, 1)

    for idx, start in enumerate(section_starts):
        end = section_starts[idx + 1] - 1 if idx + 1 < len(section_starts) else len(lines)
        section_lines = lines[start - 1 : end]
        section_text = "".join(section_lines).strip()
        if section_text:
            cid = _compute_sha256(f"{p_str}:{start}:{end}:{section_text}")[:16]
            chunks.append(
                CodeChunk(
                    file_path=p_str,
                    chunk_id=cid,
                    start_line=start,
                    end_line=end,
                    content=section_text,
                    sha256=_compute_sha256(section_text),
                    chunk_type="markdown",
                )
            )
    return chunks


def chunk_sliding_window(
    path: Path,
    content: str,
    max_lines: int = 60,
    overlap_lines: int = 10,
) -> list[CodeChunk]:
    """Generic sliding window chunking for any source code or text file."""
    chunks: list[CodeChunk] = []
    lines = content.splitlines(keepends=True)
    if not lines:
        return []

    p_str = str(path.resolve())
    total_lines = len(lines)
    step = max(1, max_lines - overlap_lines)

    for start_idx in range(0, total_lines, step):
        end_idx = min(total_lines, start_idx + max_lines)
        chunk_lines = lines[start_idx:end_idx]
        chunk_text = "".join(chunk_lines).strip()
        if chunk_text:
            start_line = start_idx + 1
            end_line = end_idx
            cid = _compute_sha256(f"{p_str}:{start_line}:{end_line}:{chunk_text}")[:16]
            chunks.append(
                CodeChunk(
                    file_path=p_str,
                    chunk_id=cid,
                    start_line=start_line,
                    end_line=end_line,
                    content=chunk_text,
                    sha256=_compute_sha256(chunk_text),
                    chunk_type="block",
                )
            )
        if end_idx >= total_lines:
            break

    return chunks


def chunk_file(
    path: str | Path,
    max_lines: int = 60,
    overlap_lines: int = 10,
) -> list[CodeChunk]:
    """Chunk a single file using the appropriate language parser."""
    p = Path(path).resolve()
    if not p.is_file():
        return []

    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    ext = p.suffix.lower()
    if ext == ".py":
        return chunk_python_file(p, content, max_lines=max_lines)
    if ext == ".md":
        return chunk_markdown_file(p, content, max_lines=max_lines)

    return chunk_sliding_window(p, content, max_lines=max_lines, overlap_lines=overlap_lines)
