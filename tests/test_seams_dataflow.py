"""Comprehensive seam audit and end-to-end dataflow test suite (Phase 27)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentcli.agent.loop import AgentLoop
from agentcli.config import Config
from agentcli.embeddings.chunker import CodeChunk
from agentcli.embeddings.index import VectorIndex
from agentcli.embeddings.store import VectorStore
from agentcli.session import AgentSession
from agentcli.subagents.base import SubAgentTask, SubAgentType
from agentcli.subagents.consensus import AgentVote, ConsensusEngine, ConsensusStrategy
from agentcli.subagents.workspace import WorkspaceAgent


@pytest.fixture(autouse=True)
def set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-api-key")


def test_session_prepare_prompt_token_expansion(tmp_path: Path) -> None:
    """Test AgentSession.prepare_prompt expanding @file tokens."""
    test_file = tmp_path / "sample.py"
    test_file.write_text("def hello():\n    return 'world'\n", encoding="utf-8")

    config = Config()
    session = AgentSession(config=config)

    raw_input = f"Inspect this file @{test_file.resolve()} and refactor it."
    expanded = session.prepare_prompt(raw_input)

    assert "def hello():" in expanded
    assert "### File:" in expanded
    assert "Inspect this file" in expanded


def test_session_prepare_prompt_missing_repo_graceful() -> None:
    """Test @repo token expansion gracefully handles nonexistent workspaces without crash."""
    config = Config()
    session = AgentSession(config=config)

    raw_input = "Please review @repo:nonexistent_pkg/src/main.rs"
    expanded = session.prepare_prompt(raw_input)

    assert "[Error resolving @repo:nonexistent_pkg/src/main.rs" in expanded


def test_agent_loop_goal_expansion(tmp_path: Path) -> None:
    """Test AgentLoop automatically expands @file references in initial goal."""
    doc_file = tmp_path / "spec.txt"
    doc_file.write_text("REQUIREMENT: Support AES-256 encryption", encoding="utf-8")

    goal = f"Implement feature according to @{doc_file.resolve()}"
    loop = AgentLoop(goal=goal)

    assert "REQUIREMENT: Support AES-256 encryption" in loop.goal
    assert "### File:" in loop.goal


@pytest.mark.asyncio
async def test_vector_index_sync_file_lifecycle(tmp_path: Path) -> None:
    """Test VectorIndex.sync_file on create, edit, and deletion."""
    db_path = tmp_path / "vector_test.db"
    store = VectorStore(db_path=str(db_path))
    index = VectorIndex(store=store)

    code_file = tmp_path / "service.py"
    code_file.write_text("def fetch_user(uid: int):\n    return {'id': uid}\n", encoding="utf-8")

    # 1. Sync new file
    inserted = await index.sync_file(code_file)
    assert inserted >= 1

    records = store.get_all_for_model(index.engine.model)
    assert len(records) >= 1
    assert "fetch_user" in records[0][0].content

    # 2. Modify file and sync again
    code_file.write_text("def fetch_user_v2(uid: int, org: str):\n    return {'id': uid, 'org': org}\n", encoding="utf-8")
    updated = await index.sync_file(code_file)
    assert updated >= 1

    records_after = store.get_all_for_model(index.engine.model)
    assert len(records_after) >= 1
    assert "fetch_user_v2" in records_after[0][0].content

    # 3. Delete file and sync
    code_file.unlink()
    deleted_count = await index.sync_file(code_file)
    assert deleted_count >= 1

    records_final = store.get_all_for_model(index.engine.model)
    assert len(records_final) == 0

    store.close()


@pytest.mark.asyncio
async def test_consensus_partial_quorum_with_degraded_voters() -> None:
    """Test ConsensusEngine surviving degraded voter timeouts when min_quorum is met."""
    engine = ConsensusEngine()

    async def healthy_voter_1() -> AgentVote:
        return AgentVote(voter_id="agent_1", choice="OPTION_A", confidence=0.9, rationale="Clean architecture")

    async def healthy_voter_2() -> AgentVote:
        return AgentVote(voter_id="agent_2", choice="OPTION_A", confidence=0.85, rationale="Better performance")

    async def failing_voter_3() -> AgentVote:
        await asyncio.sleep(0.5)
        raise TimeoutError("HTTP 429 Rate Limit from provider")

    # Should achieve majority consensus on OPTION_A with degraded notice
    result = await engine.gather_and_evaluate(
        proposal="Choose caching strategy",
        options=["OPTION_A", "OPTION_B"],
        voter_callables=[healthy_voter_1, healthy_voter_2, failing_voter_3],
        strategy=ConsensusStrategy.MAJORITY,
        timeout=0.1,
        min_quorum=2,
    )

    assert result.consensus_reached is True
    assert result.decision == "OPTION_A"
    assert result.agreement_ratio == 1.0
    assert "Degraded node notice: 1/3" in result.summary


@pytest.mark.asyncio
async def test_consensus_quorum_failure() -> None:
    """Test ConsensusEngine cleanly failing when quorum count is insufficient."""
    engine = ConsensusEngine()

    async def failing_voter() -> AgentVote:
        raise RuntimeError("Service unavailable")

    result = await engine.gather_and_evaluate(
        proposal="Select router",
        options=["ROUTER_1", "ROUTER_2"],
        voter_callables=[failing_voter],
        min_quorum=1,
    )

    assert result.consensus_reached is False
    assert result.decision is None
    assert "Quorum failure" in result.summary


@pytest.mark.asyncio
async def test_workspace_agent_scoped_monorepo_target(tmp_path: Path) -> None:
    """Test WorkspaceAgent running scoped within a designated workspace root."""
    sub_project = tmp_path / "packages" / "backend"
    sub_project.mkdir(parents=True)
    (sub_project / "app.py").write_text("print('backend')", encoding="utf-8")

    agent = WorkspaceAgent()
    task = SubAgentTask(
        id="ws_task_01",
        agent_type=SubAgentType.WORKSPACE,
        payload={
            "operation": "search_files",
            "path": str(sub_project),
            "pattern": "*.py",
        },
    )

    result = await agent.run(task)
    assert result.success is True
    assert result.output is not None
    assert "app.py" in result.output.get("matches", [])


def test_sqlite_handle_release_and_wal_checkpoint(tmp_path: Path) -> None:
    """Test MemoryStore explicitly releases SQLite WAL locks upon close."""
    db_file = tmp_path / "chat_mem.db"
    store = VectorStore(db_path=str(db_file))
    chunk = CodeChunk(
        file_path="foo.py",
        chunk_id="chk_1",
        start_line=1,
        end_line=5,
        content="x = 1",
        sha256="abc",
        chunk_type="python",
    )
    store.save_embedding(chunk, "test-model", [0.1, 0.2, 0.3])
    store.close()

    # Verify file can be opened, deleted or overwritten without WinError 32
    assert db_file.exists()
    db_file.unlink()
    assert not db_file.exists()
