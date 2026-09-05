"""Unit and integration tests for Phase 20: Multi-Agent Swarm & Peer Delegation."""

from __future__ import annotations

import pytest

from agentcli.subagents.base import (
    SubAgent,
    SubAgentConfig,
    SubAgentResult,
    SubAgentTask,
    SubAgentType,
)
from agentcli.subagents.bus import MessageBus
from agentcli.subagents.code_analyzer import CodeAnalyzerAgent
from agentcli.subagents.consensus import (
    AgentVote,
    ConsensusEngine,
    ConsensusStrategy,
)
from agentcli.subagents.file_ops import FileOpsAgent
from agentcli.subagents.spawner import SubAgentSpawner

# ---------------------------------------------------------------------------
# Unit Tests: SubAgentTask & Delegation Attributes
# ---------------------------------------------------------------------------


def test_subagent_task_delegation_metadata() -> None:
    task = SubAgentTask(
        agent_type=SubAgentType.CODE_ANALYZER,
        payload={"query": "test"},
        depth=1,
        max_depth=3,
        delegation_path=("planner", "code_analyzer"),
        delegator_id="planner-123",
    )
    assert task.depth == 1
    assert task.max_depth == 3
    assert task.delegation_path == ("planner", "code_analyzer")
    assert task.delegator_id == "planner-123"


# ---------------------------------------------------------------------------
# Unit Tests: SubAgent.delegate() Depth Bounding & Cycle Detection
# ---------------------------------------------------------------------------


class MockWorkerAgent(SubAgent):
    async def run(self, task: SubAgentTask) -> SubAgentResult:
        return SubAgentResult(
            task_id=task.id,
            agent_type=self.agent_type,
            success=True,
            output={"processed": task.payload},
        )


@pytest.mark.asyncio
async def test_subagent_delegate_max_depth_exceeded() -> None:
    bus = MessageBus()
    agent = MockWorkerAgent(agent_type=SubAgentType.PLANNER, message_bus=bus)

    # Simulate running a task at max depth
    task = SubAgentTask(
        agent_type=SubAgentType.PLANNER,
        payload={},
        depth=3,
        max_depth=3,
    )
    await agent.on_start(task)

    # Attempt delegation at max depth
    res = await agent.delegate(SubAgentType.FILE_OPS, {"op": "read"})
    assert res.success is False
    assert "Maximum delegation depth (3) reached" in (res.error or "")


@pytest.mark.asyncio
async def test_subagent_delegate_cycle_detection() -> None:
    bus = MessageBus()
    agent = MockWorkerAgent(agent_type=SubAgentType.CODE_ANALYZER, message_bus=bus)

    # Task already has a history with code_analyzer and file_ops twice
    task = SubAgentTask(
        agent_type=SubAgentType.CODE_ANALYZER,
        payload={},
        depth=1,
        max_depth=5,
        delegation_path=("file_ops", "code_analyzer", "file_ops"),
    )
    await agent.on_start(task)

    # Attempt to delegate to file_ops again (which would make file_ops appear 3 times in path)
    res = await agent.delegate(SubAgentType.FILE_OPS, {"op": "read"})
    assert res.success is False
    assert "Delegation cycle detected targeting file_ops" in (res.error or "")


@pytest.mark.asyncio
async def test_subagent_delegate_no_message_bus() -> None:
    agent = MockWorkerAgent(agent_type=SubAgentType.PLANNER, message_bus=None)
    task = SubAgentTask(agent_type=SubAgentType.PLANNER, payload={})
    await agent.on_start(task)

    res = await agent.delegate(SubAgentType.FILE_OPS, {})
    assert res.success is False
    assert "Message bus unavailable" in (res.error or "")


# ---------------------------------------------------------------------------
# Unit & Integration Tests: MessageBus & Spawner Peer Delegation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_message_bus_delegate_task_timeout() -> None:
    bus = MessageBus()
    task = SubAgentTask(agent_type=SubAgentType.FILE_OPS, payload={})
    # No spawner listening, should timeout
    res = await bus.delegate_task(task, timeout=0.05)
    assert res.success is False
    assert "timed out after 0.1s" in (res.error or "") or "timed out" in (res.error or "")


@pytest.mark.asyncio
async def test_peer_delegation_spawner_integration() -> None:
    bus = MessageBus()
    configs = {
        SubAgentType.FILE_OPS.value: SubAgentConfig(enabled=True),
        SubAgentType.CODE_ANALYZER.value: SubAgentConfig(enabled=True),
    }
    factories = {
        SubAgentType.FILE_OPS.value: lambda: FileOpsAgent(config={"read_only": False}, message_bus=bus),
        SubAgentType.CODE_ANALYZER.value: lambda: CodeAnalyzerAgent(message_bus=bus),
    }
    spawner = SubAgentSpawner(config=configs, agent_factories=factories, message_bus=bus)
    await spawner.start()

    try:
        # Create a delegating agent
        caller = CodeAnalyzerAgent(message_bus=bus)
        parent_task = SubAgentTask(agent_type=SubAgentType.CODE_ANALYZER, payload={})
        await caller.on_start(parent_task)

        # Delegate file_ops write and read
        write_res = await caller.delegate(
            SubAgentType.FILE_OPS,
            {"operation": "write", "path": "swarm_test.tmp", "content": "swarm_content"},
        )
        assert write_res.success is True

        read_res = await caller.delegate(
            SubAgentType.FILE_OPS,
            {"operation": "read", "path": "swarm_test.tmp"},
        )
        assert read_res.success is True
        assert read_res.output.get("content") == "swarm_content"

        # Cleanup created file
        await caller.delegate(
            SubAgentType.FILE_OPS,
            {"operation": "delete", "path": "swarm_test.tmp"},
        )
    finally:
        await spawner.shutdown()


# ---------------------------------------------------------------------------
# Unit Tests: ConsensusEngine & Voting Strategies
# ---------------------------------------------------------------------------


def test_consensus_majority_success() -> None:
    engine = ConsensusEngine()
    votes = [
        AgentVote(voter_id="agent1", choice="OptionA", rationale="Faster"),
        AgentVote(voter_id="agent2", choice="OptionA", rationale="Clean architecture"),
        AgentVote(voter_id="agent3", choice="OptionB", rationale="Alternative approach"),
    ]
    res = engine.evaluate_votes(votes, strategy=ConsensusStrategy.MAJORITY)
    assert res.consensus_reached is True
    assert res.decision == "OptionA"
    assert res.agreement_ratio == pytest.approx(2 / 3)
    assert "Consensus reached on 'OptionA'" in res.summary


def test_consensus_majority_tie_fails() -> None:
    engine = ConsensusEngine()
    votes = [
        AgentVote(voter_id="agent1", choice="OptionA"),
        AgentVote(voter_id="agent2", choice="OptionB"),
    ]
    res = engine.evaluate_votes(votes, strategy=ConsensusStrategy.MAJORITY)
    assert res.consensus_reached is False
    assert res.decision is None
    assert "Tie detected" in res.summary


def test_consensus_unanimous() -> None:
    engine = ConsensusEngine()
    unanimous_votes = [
        AgentVote(voter_id="agent1", choice="Approve"),
        AgentVote(voter_id="agent2", choice="Approve"),
        AgentVote(voter_id="agent3", choice="Approve"),
    ]
    res = engine.evaluate_votes(unanimous_votes, strategy=ConsensusStrategy.UNANIMOUS)
    assert res.consensus_reached is True
    assert res.decision == "Approve"

    split_votes = [
        AgentVote(voter_id="agent1", choice="Approve"),
        AgentVote(voter_id="agent2", choice="Reject"),
    ]
    res_split = engine.evaluate_votes(split_votes, strategy=ConsensusStrategy.UNANIMOUS)
    assert res_split.consensus_reached is False
    assert res_split.decision is None


def test_consensus_supermajority() -> None:
    engine = ConsensusEngine()
    # 2 out of 3 = 66.7% >= 66.7% -> Pass
    votes = [
        AgentVote(voter_id="agent1", choice="Refactor"),
        AgentVote(voter_id="agent2", choice="Refactor"),
        AgentVote(voter_id="agent3", choice="Keep"),
    ]
    res = engine.evaluate_votes(votes, strategy=ConsensusStrategy.SUPERMAJORITY)
    assert res.consensus_reached is True
    assert res.decision == "Refactor"

    # 3 out of 5 = 60% < 66.7% -> Fail
    votes_fail = [
        AgentVote(voter_id="a1", choice="Refactor"),
        AgentVote(voter_id="a2", choice="Refactor"),
        AgentVote(voter_id="a3", choice="Refactor"),
        AgentVote(voter_id="a4", choice="Keep"),
        AgentVote(voter_id="a5", choice="Keep"),
    ]
    res_fail = engine.evaluate_votes(votes_fail, strategy=ConsensusStrategy.SUPERMAJORITY)
    assert res_fail.consensus_reached is False


def test_consensus_weighted() -> None:
    engine = ConsensusEngine()
    # OptionA has 2 low confidence votes (0.2 + 0.2 = 0.4)
    # OptionB has 1 high confidence vote (0.9)
    votes = [
        AgentVote(voter_id="agent1", choice="OptionA", confidence=0.2),
        AgentVote(voter_id="agent2", choice="OptionA", confidence=0.2),
        AgentVote(voter_id="agent3", choice="OptionB", confidence=0.9),
    ]
    res = engine.evaluate_votes(votes, strategy=ConsensusStrategy.WEIGHTED)
    assert res.consensus_reached is True
    assert res.decision == "OptionB"
    assert res.tally["OptionB"] == pytest.approx(0.9)
    assert res.tally["OptionA"] == pytest.approx(0.4)


def test_consensus_empty_votes() -> None:
    engine = ConsensusEngine()
    res = engine.evaluate_votes([])
    assert res.consensus_reached is False
    assert res.decision is None


@pytest.mark.asyncio
async def test_consensus_gather_and_evaluate() -> None:
    engine = ConsensusEngine()

    async def voter1() -> AgentVote:
        return AgentVote(voter_id="v1", choice="PlanA", confidence=0.8, rationale="Robust")

    async def voter2() -> AgentVote:
        return AgentVote(voter_id="v2", choice="PlanA", confidence=0.9, rationale="Modular")

    async def voter3() -> AgentVote:
        return AgentVote(voter_id="v3", choice="PlanB", confidence=0.5, rationale="Quick")

    res = await engine.gather_and_evaluate(
        proposal="Choose rollout plan",
        options=["PlanA", "PlanB"],
        voter_callables=[voter1, voter2, voter3],
        strategy=ConsensusStrategy.MAJORITY,
    )
    assert res.consensus_reached is True
    assert res.decision == "PlanA"
    assert len(res.votes) == 3


@pytest.mark.asyncio
async def test_consensus_multi_round_debate() -> None:
    engine = ConsensusEngine()

    # Round 1: Disagreement (v1 votes A, v2 votes B)
    # Round 2: Debaters see prior votes and converge on B
    async def debater1(proposal: str, prior_votes: list[AgentVote]) -> AgentVote:
        if not prior_votes:
            return AgentVote(voter_id="d1", choice="ChoiceA", rationale="Initial preference")
        # Convinced by ChoiceB in round 2
        return AgentVote(voter_id="d1", choice="ChoiceB", rationale="Convinced by peer rationale")

    async def debater2(proposal: str, prior_votes: list[AgentVote]) -> AgentVote:
        return AgentVote(voter_id="d2", choice="ChoiceB", rationale="ChoiceB has lower risk")

    res = await engine.debate_and_converge(
        proposal="Architecture choice",
        options=["ChoiceA", "ChoiceB"],
        debater_callables=[debater1, debater2],
        rounds=2,
        strategy=ConsensusStrategy.UNANIMOUS,
    )
    assert res.consensus_reached is True
    assert res.decision == "ChoiceB"
    assert len(res.votes) == 2
