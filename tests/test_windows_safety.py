"""Test suite for Windows filesystem safety and SQLite handle lifecycle (Phase 29)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from agentcli.embeddings.store import VectorStore
from agentcli.files import safe_rmtree, safe_unlink
from agentcli.memory.store import MemoryStore


def test_safe_unlink_and_rmtree_read_only(tmp_path: Path) -> None:
    """Test safe_unlink and safe_rmtree cleanly remove read-only files and directories."""
    ro_dir = tmp_path / "readonly_dir"
    ro_dir.mkdir()
    ro_file = ro_dir / "readonly_file.txt"
    ro_file.write_text("protected content", encoding="utf-8")

    # Set read-only attribute
    os.chmod(ro_file, stat.S_IREAD)

    # safe_unlink test
    assert safe_unlink(ro_file)
    assert not ro_file.exists()

    # Create another file and make directory read-only
    ro_subfile = ro_dir / "sub.txt"
    ro_subfile.write_text("sub content", encoding="utf-8")
    os.chmod(ro_subfile, stat.S_IREAD)

    # safe_rmtree test
    safe_rmtree(ro_dir)
    assert not ro_dir.exists()


def test_safe_rmtree_nonexistent_path(tmp_path: Path) -> None:
    """Test safe_rmtree handles nonexistent directories gracefully."""
    nonexistent = tmp_path / "does_not_exist"
    safe_rmtree(nonexistent)  # Should not raise
    assert not nonexistent.exists()


def test_memory_store_context_manager_close(tmp_path: Path) -> None:
    """Test MemoryStore context manager ensures clean connection release."""
    db_file = tmp_path / "memory.db"
    with MemoryStore(db_path=db_file) as store:
        s_rec = store.create_session(title="Test Session")
        assert s_rec.id is not None
        assert store.get_session(s_rec.id) is not None

    # Connection must be closed, file can be safely unlinked
    assert safe_unlink(db_file)
    assert not db_file.exists()


def test_vector_store_context_manager_close(tmp_path: Path) -> None:
    """Test VectorStore context manager ensures clean connection release."""
    db_file = tmp_path / "vectors.db"
    with VectorStore(db_path=db_file) as store:
        stats = store.stats()
        assert stats["total_chunks"] == 0

    # Connection must be closed, file can be safely unlinked
    assert safe_unlink(db_file)
    assert not db_file.exists()
