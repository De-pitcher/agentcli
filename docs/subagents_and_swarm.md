# Multi-Agent Swarm, Peer Delegation & Consensus Engine

`agentcli` (v2.0.0+) implements an event-driven **Multi-Agent Swarm** framework designed for low-latency (<10ms) coordination, peer-to-peer delegation, and multi-round debate consensus among specialized sub-agents.

---

## ⚡ Architecture Overview

```
                          ┌───────────────────────────┐
                          │   AgentLoop / Orchestrator│
                          └─────────────┬─────────────┘
                                        │
                                        ▼
                     ┌────────────────────────────────────┐
                     │            MessageBus              │
                     │  (Pub/Sub, Peer Delegate, Latency) │
                     └───────┬────────────────────┬───────┘
                             │                    │
              ┌──────────────┴──────┐      ┌──────┴──────────────┐
              ▼                     ▼      ▼                     ▼
      ┌───────────────┐     ┌───────────┐ ┌────────────────┐ ┌───────────────┐
      │ Code Analyzer │     │ File Ops  │ │ Shell Execution│ │ Consensus Eng │
      └───────────────┘     └───────────┘ └────────────────┘ └───────────────┘
```

### Core Components
1. **`MessageBus` (`agentcli.subagents.bus`)**:
   - Asynchronous pub/sub message router supporting broadcast and point-to-point targeted routing.
   - Sub-10ms delivery overhead across in-process agent pools.
   - Built-in timeout protection for subscribers (`handler_timeout=5.0s`).
2. **`SubAgentSpawner` (`agentcli.subagents.spawner`)**:
   - Manages pools of long-lived, idle-timeout-bounded sub-agents.
   - Intercepts `MessageType.PEER_DELEGATE` events to execute sub-agent tasks dynamically.
   - Enforces recursion depth bounds (`task.depth <= task.max_depth`) and cycle detection.
3. **`ConsensusEngine` (`agentcli.subagents.consensus`)**:
   - Evaluates multi-agent votes and orchestrates peer debate rounds for ambiguous architectural decisions.

---

## 🔄 Peer-to-Peer Delegation Protocol

Sub-agents can delegate sub-tasks directly to peers without escalating back to the main user loop:

```python
from agentcli.subagents.base import SubAgentTask, SubAgentType
from agentcli.subagents.bus import MessageBus

# Delegate a code analysis task from another agent
task = SubAgentTask(
    agent_type=SubAgentType.CODE_ANALYZER,
    payload={"files": ["src/core.py"], "focus": "security"},
    delegator_id="planner_01",
    depth=1,
    max_depth=3,
)

result = await bus.delegate_task(task, timeout=15.0)
if result.success:
    print("Analysis findings:", result.output)
```

### Recursion & Cycle Safeguards
- **Max Depth Clamp**: Default `max_depth = 3`. Tasks exceeding this limit are aborted immediately with `Maximum delegation depth exceeded`.
- **Delegation Trace**: Each delegation step appends the agent's ID to `task.delegation_path`, preventing infinite circular delegation loops.

---

## 🗳️ Consensus Voting Strategies

When evaluating plans, refactoring strategies, or ambiguous code changes, `ConsensusEngine` aggregates perspectives across multiple model personas or sub-agents:

```python
from agentcli.subagents.consensus import (
    AgentVote,
    ConsensusEngine,
    ConsensusStrategy,
)

engine = ConsensusEngine()
votes = [
    AgentVote(voter_id="claude_35", choice="asyncio_pool", confidence=0.95, rationale="Fastest event-loop"),
    AgentVote(voter_id="gpt_4o", choice="asyncio_pool", confidence=0.90, rationale="Thread-safe async"),
    AgentVote(voter_id="deepseek_r1", choice="process_pool", confidence=0.70, rationale="CPU bound tasks"),
]

# Evaluate majority consensus
result = engine.evaluate_votes(votes, strategy=ConsensusStrategy.MAJORITY)
print(f"Consensus Reached: {result.consensus_reached}")
print(f"Winning Decision: {result.decision} (Score: {result.winning_score})")
print(f"Agreement Ratio: {result.agreement_ratio:.1%}")
```

### Supported Strategies
| Strategy | Rule | Typical Use Case |
|---|---|---|
| `MAJORITY` | > 50% of total votes cast | Default plan and tool execution validation |
| `SUPERMAJORITY` | >= 66.7% of total votes | Breaking API changes, schema migrations |
| `UNANIMOUS` | 100% agreement | Production release gates, security-critical edits |
| `WEIGHTED` | Highest cumulative confidence score | Multi-model evaluation with uncertainty metrics |
| `PLURALITY` | Option with the most votes | Selecting among 3+ design alternatives |

---

## 💬 Multi-Round Peer Debate

For high-complexity tasks, `ConsensusEngine.run_debate()` facilitates multi-turn deliberation:

1. **Round 1 (Initial Proposals)**: Each model persona proposes a solution with confidence and rationale.
2. **Critique & Rebuttal**: Each participant reviews all peer rationales and updates their vote.
3. **Synthesis**: The engine evaluates if consensus has converged or returns the highest-confidence majority choice with minority dissents recorded.
