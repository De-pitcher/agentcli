"""Skill discovery and loading module for Phase 33."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from .manifest import SkillManifest, parse_skill_markdown

logger = logging.getLogger(__name__)

BUILTIN_SKILLS_DIR = Path(__file__).parent / "builtin"


class SkillLoader:
    """Discovers, parses, and indexes skills from workspace and system directories."""

    def __init__(
        self,
        workspace_dir: str | Path | None = None,
        user_skills_dir: str | Path | None = None,
        extra_paths: Sequence[str | Path] | None = None,
        include_builtin: bool = True,
    ) -> None:
        self.workspace_dir = Path(workspace_dir).resolve() if workspace_dir else Path.cwd()
        self.user_skills_dir = (
            Path(user_skills_dir).resolve()
            if user_skills_dir
            else (Path.home() / ".agentcli" / "skills").resolve()
        )
        self.extra_paths = [Path(p).resolve() for p in (extra_paths or [])]
        self.include_builtin = include_builtin
        self._skills: dict[str, SkillManifest] = {}
        self.reload()

    def reload(self) -> dict[str, SkillManifest]:
        """Re-scan all skill search directories and refresh indexed manifests."""
        self._skills.clear()

        # 1. Load Builtin Skills (Lowest priority)
        if self.include_builtin and BUILTIN_SKILLS_DIR.exists():
            self._scan_directory(BUILTIN_SKILLS_DIR, source_type="builtin")

        # 2. Load User Home Skills (Medium priority)
        if self.user_skills_dir.exists():
            self._scan_directory(self.user_skills_dir, source_type="user")

        # 3. Load Extra Custom Paths
        for p in self.extra_paths:
            if p.exists():
                self._scan_directory(p, source_type="user")

        # 4. Load Workspace Skills (Highest priority, overrides global/builtin)
        workspace_candidates = [
            self.workspace_dir / ".agentcli" / "skills",
            self.workspace_dir / ".skills",
            self.workspace_dir / "skills",
        ]
        for w_dir in workspace_candidates:
            if w_dir.exists():
                self._scan_directory(w_dir, source_type="project")

        return self._skills

    def _scan_directory(self, root: Path, source_type: str) -> None:
        """Scan a directory for SKILL.md files."""
        try:
            for item in root.iterdir():
                if item.is_dir():
                    skill_file = item / "SKILL.md"
                    if skill_file.is_file():
                        self._load_skill_file(skill_file, source_type=source_type)
                elif item.is_file() and item.name.lower() == "skill.md":
                    self._load_skill_file(item, source_type=source_type)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Error scanning directory '%s' for skills: %s", root, exc)

    def _load_skill_file(self, skill_file: Path, source_type: str) -> None:
        """Load and parse an individual SKILL.md file."""
        try:
            content = skill_file.read_text(encoding="utf-8", errors="replace")
            manifest = parse_skill_markdown(content, source_path=skill_file, source_type=source_type)
            # Store normalized name (lowercase, stripped)
            norm_name = manifest.name.strip().lower()
            self._skills[norm_name] = manifest
            logger.debug("Loaded skill '%s' (%s) from %s", norm_name, source_type, skill_file)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to load skill file '%s': %s", skill_file, exc)

    def get_skill(self, name: str) -> SkillManifest | None:
        """Retrieve a loaded skill manifest by name."""
        return self._skills.get(name.strip().lower())

    def list_skills(self) -> list[SkillManifest]:
        """Return a sorted list of all currently loaded skill manifests."""
        return sorted(self._skills.values(), key=lambda s: s.name)

    def has_skill(self, name: str) -> bool:
        """Check if a skill exists by name."""
        return name.strip().lower() in self._skills
