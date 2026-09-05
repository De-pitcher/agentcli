"""Multi-Root Workspace Registry & Project Discovery (Phase 25).

Discovers and registers projects across monorepos and multi-repository workspaces,
detecting manifest types (Python, Node, Rust, Go, Git) and loading configured roots.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config import WorkspaceConfig

logger = logging.getLogger(__name__)


class WorkspaceType(str, Enum):
    """Identified project/workspace technology stack."""

    PYTHON = "python"
    NODE = "node"
    RUST = "rust"
    GO = "go"
    GIT = "git"
    GENERIC = "generic"


MANIFEST_SIGNATURES: list[tuple[str, WorkspaceType]] = [
    ("pyproject.toml", WorkspaceType.PYTHON),
    ("setup.py", WorkspaceType.PYTHON),
    ("requirements.txt", WorkspaceType.PYTHON),
    ("package.json", WorkspaceType.NODE),
    ("Cargo.toml", WorkspaceType.RUST),
    ("go.mod", WorkspaceType.GO),
    (".git", WorkspaceType.GIT),
]

DEFAULT_IGNORE_DIRS: set[str] = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
    "target",
    ".agentcli_worktrees",
}


@dataclass
class WorkspaceRoot:
    """Represents a discovered or configured project root within a mesh."""

    name: str
    path: str
    workspace_type: WorkspaceType = WorkspaceType.GENERIC
    manifest_file: str | None = None
    dependencies: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    description: str = ""

    @property
    def resolved_path(self) -> Path:
        return Path(self.path).resolve()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": str(self.resolved_path),
            "workspace_type": self.workspace_type.value,
            "manifest_file": self.manifest_file,
            "dependencies": list(self.dependencies),
            "tags": list(self.tags),
            "description": self.description,
        }


def _detect_workspace_type(dir_path: Path) -> tuple[WorkspaceType, str | None]:
    """Inspect directory contents to detect project manifest and type."""
    for manifest_name, wtype in MANIFEST_SIGNATURES:
        target = dir_path / manifest_name
        if target.exists():
            return wtype, str(target.resolve())
    return WorkspaceType.GENERIC, None


def _detect_manifest_dependencies(manifest_path: str | None, wtype: WorkspaceType) -> list[str]:
    """Attempt to detect intra-workspace dependencies from package manifests."""
    if not manifest_path:
        return []
    p = Path(manifest_path)
    if not p.exists():
        return []

    deps: list[str] = []
    if wtype == WorkspaceType.NODE and p.name == "package.json":
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            for section in ("dependencies", "devDependencies", "peerDependencies"):
                for dep, ver in data.get(section, {}).items():
                    if isinstance(ver, str) and ver.startswith(("workspace:", "file:")):
                        clean_name = dep.split("/")[-1].replace("@", "")
                        deps.append(clean_name)
        except Exception:  # noqa: BLE001,S110
            pass

    return deps


class WorkspaceRegistry:
    """Central registry of all interconnected projects in the monorepo mesh."""

    def __init__(self) -> None:
        self._workspaces: dict[str, WorkspaceRoot] = {}

    def register(self, root: WorkspaceRoot) -> None:
        """Register or overwrite a workspace root."""
        self._workspaces[root.name] = root
        logger.debug("Registered workspace root '%s' at %s", root.name, root.path)

    def get(self, name: str) -> WorkspaceRoot | None:
        """Retrieve a registered workspace by name."""
        return self._workspaces.get(name)

    def list_workspaces(self) -> list[WorkspaceRoot]:
        """Return all registered workspaces sorted alphabetically by name."""
        return sorted(self._workspaces.values(), key=lambda w: w.name)

    def names(self) -> list[str]:
        """Return all registered workspace names."""
        return sorted(self._workspaces.keys())

    def clear(self) -> None:
        """Clear all registered workspaces."""
        self._workspaces.clear()

    def auto_discover(
        self,
        root_dir: str | Path = ".",
        max_depth: int = 3,
        exclude_dirs: set[str] | None = None,
    ) -> list[WorkspaceRoot]:
        """Recursively scan root_dir up to max_depth to discover sub-projects.

        Detects projects with package manifests (e.g. pyproject.toml, package.json).
        """
        base_path = Path(root_dir).resolve()
        excludes = exclude_dirs or DEFAULT_IGNORE_DIRS
        discovered: list[WorkspaceRoot] = []

        # Check the root itself
        root_type, root_manifest = _detect_workspace_type(base_path)
        if root_manifest and not root_manifest.endswith(".git"):
            root_ws = WorkspaceRoot(
                name=base_path.name or "root",
                path=str(base_path),
                workspace_type=root_type,
                manifest_file=root_manifest,
                dependencies=_detect_manifest_dependencies(root_manifest, root_type),
            )
            self.register(root_ws)
            discovered.append(root_ws)

        # Scan subdirectories up to max_depth
        for current_root, dirnames, _filenames in os.walk(base_path):
            curr = Path(current_root).resolve()
            # Calculate depth relative to base_path
            try:
                rel = curr.relative_to(base_path)
                depth = len(rel.parts)
            except ValueError:
                depth = 0

            if depth > max_depth:
                dirnames.clear()
                continue

            # Filter out ignored directories
            dirnames[:] = [d for d in dirnames if d not in excludes and not d.startswith(".")]

            if curr == base_path:
                continue

            wtype, manifest = _detect_workspace_type(curr)
            if manifest and not manifest.endswith(".git"):
                name = curr.name
                if name in self._workspaces and self._workspaces[name].resolved_path != curr:
                    name = str(rel).replace("\\", "_").replace("/", "_")

                manifest_deps = _detect_manifest_dependencies(manifest, wtype)
                ws = WorkspaceRoot(
                    name=name,
                    path=str(curr),
                    workspace_type=wtype,
                    manifest_file=manifest,
                    dependencies=manifest_deps,
                )
                self.register(ws)
                discovered.append(ws)

        return discovered

    def load_from_config(
        self,
        config_workspaces: list[WorkspaceConfig],
        base_dir: Path | None = None,
    ) -> None:
        """Populate registry from configured workspace definitions."""
        base = (base_dir or Path.cwd()).resolve()
        for c in config_workspaces:
            p = Path(c.path)
            if not p.is_absolute():
                p = (base / p).resolve()

            wtype, manifest = _detect_workspace_type(p)
            ws = WorkspaceRoot(
                name=c.name,
                path=str(p),
                workspace_type=wtype,
                manifest_file=manifest,
                dependencies=list(c.dependencies),
                tags=list(c.tags),
                description=c.description,
            )
            self.register(ws)

    def resolve_path(self, target_ref: str) -> tuple[WorkspaceRoot, Path]:
        """Resolve a repo reference like 'backend/src/api.py' or 'backend:src/api.py'.

        Returns tuple of (WorkspaceRoot, resolved_file_path).
        Raises KeyError if workspace is unknown.
        """
        clean_ref = target_ref.strip()
        if ":" in clean_ref:
            ws_name, rel_path = clean_ref.split(":", 1)
        elif "/" in clean_ref or "\\" in clean_ref:
            parts = clean_ref.replace("\\", "/").split("/", 1)
            ws_name, rel_path = parts[0], parts[1]
        else:
            ws_name, rel_path = clean_ref, ""

        ws = self.get(ws_name)
        if not ws:
            raise KeyError(f"Workspace '{ws_name}' is not registered in the monorepo mesh.")

        file_path = (ws.resolved_path / rel_path).resolve() if rel_path else ws.resolved_path
        return ws, file_path
