"""Git worktree manager and branch sandboxing for Phase 34."""

from __future__ import annotations

import datetime
import json
import logging
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class WorktreeMetadata:
    """Metadata representing an isolated Git worktree."""

    id: str
    branch: str
    path: str
    base_ref: str = "HEAD"
    created_at: str = field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC).isoformat()
    )
    status: str = "active"  # "active", "merged", "discarded", "locked"
    commit_sha: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return serializable dictionary representation."""
        return asdict(self)


class WorktreeManager:
    """Manages creation, inspection, diffing, merging, and pruning of Git worktrees."""

    def __init__(
        self,
        repo_root: str | Path | None = None,
        worktree_dir_name: str = ".agentcli/worktrees",
    ) -> None:
        self.repo_root = Path(repo_root).resolve() if repo_root else Path.cwd().resolve()
        self.worktree_base_dir = self.repo_root / worktree_dir_name
        self._meta_file = self.worktree_base_dir / ".worktrees.json"
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        """Ensure worktree base storage directory exists."""
        try:
            self.worktree_base_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to create worktree base dir %s: %s", self.worktree_base_dir, exc)

    def _run_git(
        self,
        args: list[str],
        cwd: Path | None = None,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Execute a git command safely."""
        target_cwd = cwd or self.repo_root
        return subprocess.run(
            ["git", *args],
            cwd=str(target_cwd),
            capture_output=True,
            text=True,
            check=check,
        )

    def is_git_repo(self) -> bool:
        """Check if current repo_root is a valid git repository root."""
        git_entry = self.repo_root / ".git"
        if git_entry.exists():
            return True
        res = self._run_git(["rev-parse", "--show-toplevel"])
        if res.returncode == 0:
            top_level = Path(res.stdout.strip()).resolve()
            return top_level == self.repo_root
        return False

    def get_current_branch(self) -> str:
        """Get active branch name in repo_root."""
        res = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        if res.returncode == 0:
            return res.stdout.strip()
        return "main"

    def _sanitize_branch_name(self, branch: str) -> str:
        """Sanitize branch name for directory path creation."""
        return re.sub(r"[^a-zA-Z0-9_\-\.]", "_", branch)

    def _load_meta(self) -> dict[str, dict[str, Any]]:
        """Load worktree metadata catalog."""
        if not self._meta_file.is_file():
            return {}
        try:
            data = json.loads(self._meta_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not read worktree metadata file: %s", exc)
            return {}

    def _save_meta(self, meta: dict[str, dict[str, Any]]) -> None:
        """Persist worktree metadata catalog."""
        try:
            self._ensure_dir()
            self._meta_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not persist worktree metadata: %s", exc)

    def create_worktree(
        self,
        branch_name: str,
        base_ref: str | None = None,
        custom_id: str | None = None,
    ) -> WorktreeMetadata:
        """Create a new isolated git worktree on a new or existing branch.

        Args:
            branch_name: The branch name for the worktree.
            base_ref: Optional base commit/branch to branch off (defaults to current HEAD).
            custom_id: Optional unique identifier.
        """
        if not self.is_git_repo():
            raise RuntimeError(f"Directory '{self.repo_root}' is not a valid Git repository.")

        clean_name = branch_name.strip()
        if not clean_name:
            raise ValueError("Branch name cannot be empty.")

        wt_id = custom_id or f"wt_{int(time.time())}_{self._sanitize_branch_name(clean_name)}"
        folder_name = self._sanitize_branch_name(clean_name)
        wt_path = (self.worktree_base_dir / folder_name).resolve()
        base = base_ref or self.get_current_branch()

        # Check if worktree directory already exists
        if wt_path.exists():
            # If valid worktree already at path, return metadata
            existing = self.get_worktree(clean_name)
            if existing and Path(existing.path).exists():
                return existing
            try:
                shutil.rmtree(wt_path, ignore_errors=True)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Failed removing stale worktree dir %s: %s", wt_path, exc)

        # Check if branch exists
        branch_check = self._run_git(["rev-parse", "--verify", clean_name])
        branch_exists = branch_check.returncode == 0

        # Execute git worktree add
        if branch_exists:
            cmd = ["worktree", "add", str(wt_path), clean_name]
        else:
            cmd = ["worktree", "add", "-b", clean_name, str(wt_path), base]

        res = self._run_git(cmd)
        if res.returncode != 0:
            raise RuntimeError(
                f"Failed to create git worktree for '{clean_name}': {res.stderr.strip() or res.stdout.strip()}"
            )

        # Get initial commit sha
        sha_res = self._run_git(["rev-parse", "HEAD"], cwd=wt_path)
        commit_sha = sha_res.stdout.strip() if sha_res.returncode == 0 else ""

        meta_obj = WorktreeMetadata(
            id=wt_id,
            branch=clean_name,
            path=str(wt_path),
            base_ref=base,
            commit_sha=commit_sha,
            status="active",
        )

        catalog = self._load_meta()
        catalog[clean_name] = meta_obj.to_dict()
        self._save_meta(catalog)

        logger.info("Created Git worktree '%s' at %s (branch: %s)", wt_id, wt_path, clean_name)
        return meta_obj

    def list_worktrees(self) -> list[WorktreeMetadata]:
        """List all tracked worktrees, cross-verifying with git worktree list."""
        if not self.is_git_repo():
            return []

        catalog = self._load_meta()
        git_res = self._run_git(["worktree", "list", "--porcelain"])

        live_paths: set[str] = set()
        if git_res.returncode == 0:
            for line in git_res.stdout.splitlines():
                if line.startswith("worktree "):
                    raw_p = line[len("worktree ") :].strip()
                    live_paths.add(str(Path(raw_p).resolve()))

        results: list[WorktreeMetadata] = []
        for branch, item in list(catalog.items()):
            wt_path = str(Path(item.get("path", "")).resolve())
            if wt_path in live_paths and Path(wt_path).exists():
                results.append(
                    WorktreeMetadata(
                        id=item.get("id", branch),
                        branch=item.get("branch", branch),
                        path=wt_path,
                        base_ref=item.get("base_ref", "HEAD"),
                        created_at=item.get("created_at", ""),
                        status=item.get("status", "active"),
                        commit_sha=item.get("commit_sha", ""),
                    )
                )

        return sorted(results, key=lambda w: w.created_at, reverse=True)

    def get_worktree(self, branch_or_id: str) -> WorktreeMetadata | None:
        """Find a worktree by branch name or ID."""
        identifier = branch_or_id.strip().lower()
        for wt in self.list_worktrees():
            if wt.branch.lower() == identifier or wt.id.lower() == identifier:
                return wt
        return None

    def compute_diff(
        self,
        branch_or_id: str,
        base_ref: str | None = None,
    ) -> str:
        """Compute unified diff between worktree branch and its base ref."""
        wt = self.get_worktree(branch_or_id)
        if not wt:
            raise KeyError(f"Worktree '{branch_or_id}' not found.")

        base = base_ref or wt.base_ref or "main"
        # Compute diff between base and worktree branch
        res = self._run_git(["diff", f"{base}...{wt.branch}"], cwd=Path(wt.path))
        if res.returncode == 0:
            return res.stdout
        # Fallback to direct diff
        res_direct = self._run_git(["diff", base], cwd=Path(wt.path))
        return res_direct.stdout if res_direct.returncode == 0 else ""

    def get_worktree_status(self, branch_or_id: str) -> dict[str, Any]:
        """Get working copy status (modified, untracked files) of a worktree."""
        wt = self.get_worktree(branch_or_id)
        if not wt:
            raise KeyError(f"Worktree '{branch_or_id}' not found.")

        res = self._run_git(["status", "--porcelain"], cwd=Path(wt.path))
        changes: list[str] = []
        if res.returncode == 0:
            changes = [line.strip() for line in res.stdout.splitlines() if line.strip()]

        return {
            "branch": wt.branch,
            "path": wt.path,
            "dirty": len(changes) > 0,
            "changes_count": len(changes),
            "changes": changes[:50],
        }

    def merge_worktree(
        self,
        branch_or_id: str,
        target_branch: str | None = None,
        strategy: str = "squash",
        commit_message: str | None = None,
    ) -> dict[str, Any]:
        """Merge changes from worktree branch into the target branch.

        Args:
            branch_or_id: Worktree to merge from.
            target_branch: Target branch to merge into (defaults to wt.base_ref).
            strategy: "squash" or "merge".
            commit_message: Optional commit message for squash merge.
        """
        wt = self.get_worktree(branch_or_id)
        if not wt:
            raise KeyError(f"Worktree '{branch_or_id}' not found.")

        target = target_branch or wt.base_ref or self.get_current_branch()
        current = self.get_current_branch()

        # 1. Check if worktree has uncommitted changes and commit them
        status_info = self.get_worktree_status(branch_or_id)
        if status_info.get("dirty"):
            self._run_git(["add", "-A"], cwd=Path(wt.path))
            msg = commit_message or f"Auto-commit worktree changes for {wt.branch}"
            self._run_git(["commit", "-m", msg], cwd=Path(wt.path))

        # 2. Checkout target branch in main repo
        if current != target:
            co_res = self._run_git(["checkout", target])
            if co_res.returncode != 0:
                return {
                    "success": False,
                    "error": f"Failed to checkout target branch '{target}': {co_res.stderr.strip()}",
                }

        # 3. Perform merge in main repo
        if strategy == "squash":
            merge_res = self._run_git(["merge", "--squash", wt.branch])
            if merge_res.returncode == 0:
                final_msg = commit_message or f"feat: apply sandboxed changes from {wt.branch}"
                commit_res = self._run_git(["commit", "-m", final_msg])
                if commit_res.returncode == 0:
                    wt.status = "merged"
                    catalog = self._load_meta()
                    if wt.branch in catalog:
                        catalog[wt.branch]["status"] = "merged"
                        self._save_meta(catalog)
                    return {
                        "success": True,
                        "strategy": "squash",
                        "merged_branch": wt.branch,
                        "target_branch": target,
                    }
        else:
            merge_res = self._run_git(["merge", wt.branch, "--no-ff", "-m", f"Merge sandbox {wt.branch}"])
            if merge_res.returncode == 0:
                wt.status = "merged"
                catalog = self._load_meta()
                if wt.branch in catalog:
                    catalog[wt.branch]["status"] = "merged"
                    self._save_meta(catalog)
                return {
                    "success": True,
                    "strategy": "no-ff",
                    "merged_branch": wt.branch,
                    "target_branch": target,
                }

        return {
            "success": False,
            "error": f"Merge failed: {merge_res.stderr.strip() or merge_res.stdout.strip()}",
        }

    def remove_worktree(
        self,
        branch_or_id: str,
        force: bool = True,
        delete_branch: bool = False,
    ) -> bool:
        """Remove a git worktree and optionally delete the branch."""
        wt = self.get_worktree(branch_or_id)
        if not wt:
            return False

        wt_path = Path(wt.path)

        # 1. Remove via git worktree remove
        cmd = ["worktree", "remove", str(wt_path)]
        if force:
            cmd.append("--force")

        self._run_git(cmd)

        # 2. If path still exists, manually clean up directory
        if wt_path.exists():
            try:
                shutil.rmtree(wt_path, ignore_errors=True)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Manual cleanup error for %s: %s", wt_path, exc)

        # 3. Clean git worktree metadata
        self._run_git(["worktree", "prune"])

        # 4. Delete branch if requested
        if delete_branch:
            del_flag = "-D" if force else "-d"
            self._run_git(["branch", del_flag, wt.branch])

        # 5. Update metadata catalog
        catalog = self._load_meta()
        if wt.branch in catalog:
            del catalog[wt.branch]
            self._save_meta(catalog)

        return True

    def prune_all(self) -> int:
        """Prune all orphan worktrees and clean up catalog."""
        self._run_git(["worktree", "prune"])
        live = self.list_worktrees()
        catalog = self._load_meta()

        live_branches = {w.branch for w in live}
        pruned_count = 0
        for b in list(catalog.keys()):
            if b not in live_branches:
                del catalog[b]
                pruned_count += 1

        self._save_meta(catalog)
        return pruned_count
