"""Multi-Agent Consensus & Peer Debate Pattern (Phase 20).

Provides consensus evaluation, voting strategies, and multi-round peer debate
for complex architectural choices, plan evaluations, and ambiguous refactoring decisions.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ConsensusStrategy(str, Enum):
    """Strategies for aggregating agent votes into a consensus decision."""

    MAJORITY = "majority"          # > 50% of total votes
    SUPERMAJORITY = "supermajority"  # >= 66.7% of total votes
    UNANIMOUS = "unanimous"        # 100% of total votes
    WEIGHTED = "weighted"          # Highest cumulative confidence score
    PLURALITY = "plurality"        # Choice with the most votes (even if <= 50%)


@dataclass
class AgentVote:
    """An individual vote and rationale submitted by an agent.

    Attributes:
        voter_id: Identifier of the voting agent or model persona.
        choice: Selected option or action.
        confidence: Confidence level of the vote in range [0.0, 1.0].
        rationale: Explanation and justification for the choice.
        metadata: Optional metadata (e.g. latency, token usage, model ID).
    """

    voter_id: str
    choice: str
    confidence: float = 1.0
    rationale: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Clamp confidence to [0.0, 1.0]
        self.confidence = max(0.0, min(1.0, float(self.confidence)))


@dataclass
class ConsensusResult:
    """Result of evaluating votes across multiple agent perspectives.

    Attributes:
        decision: The winning choice if consensus is reached, or None if inconclusive.
        strategy: The voting strategy applied.
        consensus_reached: True if consensus criteria were satisfied.
        agreement_ratio: Fraction of votes agreeing with the winning choice (0.0 - 1.0).
        winning_score: Total vote count or weighted score for the winning choice.
        votes: All individual votes recorded.
        tally: Mapping from each voted choice to its aggregate score.
        summary: Human-readable summary of the voting outcome and rationales.
    """

    decision: str | None
    strategy: ConsensusStrategy
    consensus_reached: bool
    agreement_ratio: float
    winning_score: float
    votes: list[AgentVote] = field(default_factory=list)
    tally: dict[str, float] = field(default_factory=dict)
    summary: str = ""


class ConsensusEngine:
    """Evaluates agent votes and orchestrates peer debate sessions."""

    def evaluate_votes(
        self,
        votes: list[AgentVote],
        strategy: ConsensusStrategy = ConsensusStrategy.MAJORITY,
        *,
        min_threshold: float = 0.5,
    ) -> ConsensusResult:
        """Evaluate a set of submitted agent votes according to the specified strategy.

        Args:
            votes: List of AgentVote instances.
            strategy: ConsensusStrategy to apply.
            min_threshold: Minimum threshold ratio for approval (default: 0.5).

        Returns:
            ConsensusResult detailing the decision, consensus status, and breakdown.
        """
        if not votes:
            return ConsensusResult(
                decision=None,
                strategy=strategy,
                consensus_reached=False,
                agreement_ratio=0.0,
                winning_score=0.0,
                votes=[],
                tally={},
                summary="No votes were provided for evaluation.",
            )

        total_votes = len(votes)
        tally: dict[str, float] = defaultdict(float)
        vote_counts: dict[str, int] = defaultdict(int)

        for v in votes:
            vote_counts[v.choice] += 1
            if strategy == ConsensusStrategy.WEIGHTED:
                tally[v.choice] += v.confidence
            else:
                tally[v.choice] += 1.0

        # Sort choices by score descending
        sorted_choices = sorted(tally.items(), key=lambda item: item[1], reverse=True)
        top_choice, top_score = sorted_choices[0]
        top_count = vote_counts[top_choice]

        # Check for tie among top choices
        is_tied = len(sorted_choices) > 1 and sorted_choices[0][1] == sorted_choices[1][1]

        consensus_reached = False
        decision: str | None = None
        agreement_ratio = top_count / total_votes

        if not is_tied:
            if strategy == ConsensusStrategy.MAJORITY:
                consensus_reached = agreement_ratio > 0.5
            elif strategy == ConsensusStrategy.SUPERMAJORITY:
                consensus_reached = agreement_ratio >= (2.0 / 3.0)
            elif strategy == ConsensusStrategy.UNANIMOUS:
                consensus_reached = agreement_ratio == 1.0
            elif strategy == ConsensusStrategy.PLURALITY:
                consensus_reached = top_count > 0
            elif strategy == ConsensusStrategy.WEIGHTED:
                total_weight = sum(v.confidence for v in votes)
                weighted_ratio = (top_score / total_weight) if total_weight > 0 else 0.0
                consensus_reached = weighted_ratio >= min_threshold

        if consensus_reached:
            decision = top_choice

        # Build rationales summary
        rationales = [f"[{v.voter_id}] voted '{v.choice}': {v.rationale}" for v in votes if v.rationale]
        rationale_text = " | ".join(rationales) if rationales else "No rationales provided."

        if consensus_reached:
            summary = (
                f"Consensus reached on '{decision}' ({strategy.value} strategy, "
                f"agreement: {agreement_ratio:.1%}, score: {top_score:.2f}/{total_votes}). "
                f"Rationales: {rationale_text}"
            )
        else:
            reason = "Tie detected" if is_tied else f"Threshold not met for {strategy.value}"
            summary = (
                f"Consensus NOT reached ({reason}, top choice: '{top_choice}', "
                f"agreement: {agreement_ratio:.1%}). Rationales: {rationale_text}"
            )

        return ConsensusResult(
            decision=decision,
            strategy=strategy,
            consensus_reached=consensus_reached,
            agreement_ratio=agreement_ratio,
            winning_score=top_score,
            votes=votes,
            tally=dict(tally),
            summary=summary,
        )

    async def gather_and_evaluate(
        self,
        proposal: str,
        options: list[str],
        voter_callables: list[Callable[[], Awaitable[AgentVote]]],
        *,
        strategy: ConsensusStrategy = ConsensusStrategy.MAJORITY,
        timeout: float = 15.0,
    ) -> ConsensusResult:
        """Gather votes asynchronously from voters and evaluate the consensus.

        Voters are executed sequentially to respect the single-worker laptop envelope.

        Args:
            proposal: Context or question being voted on.
            options: Permitted choices.
            voter_callables: Callables returning an Awaitable[AgentVote].
            strategy: ConsensusStrategy to apply.
            timeout: Maximum timeout per voter execution.

        Returns:
            ConsensusResult summarizing the outcome.
        """
        votes: list[AgentVote] = []
        for fn in voter_callables:
            try:
                vote = await asyncio.wait_for(fn(), timeout=timeout)
                if vote.choice in options or not options:
                    votes.append(vote)
                else:
                    logger.warning("Voter %s voted for invalid option '%s'", vote.voter_id, vote.choice)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Voter failed during consensus gather: %s", exc)

        return self.evaluate_votes(votes, strategy=strategy)

    async def debate_and_converge(
        self,
        proposal: str,
        options: list[str],
        debater_callables: list[Callable[[str, list[AgentVote]], Awaitable[AgentVote]]],
        *,
        rounds: int = 2,
        strategy: ConsensusStrategy = ConsensusStrategy.MAJORITY,
        timeout: float = 15.0,
    ) -> ConsensusResult:
        """Orchestrate a multi-round debate where each round shares previous rationales.

        Debate rounds continue until consensus is reached or maximum rounds elapse.

        Args:
            proposal: The proposal or decision prompt.
            options: Allowed choice options.
            debater_callables: Callables accepting (proposal, prior_votes) and returning AgentVote.
            rounds: Maximum number of debate rounds.
            strategy: Strategy to test after each round.
            timeout: Maximum timeout per debater.

        Returns:
            Final ConsensusResult after debate convergence or exhaustion of rounds.
        """
        prior_votes: list[AgentVote] = []
        final_result = self.evaluate_votes([], strategy=strategy)

        for round_idx in range(1, max(1, rounds) + 1):
            logger.debug("Starting debate round %d/%d for proposal '%s'", round_idx, rounds, proposal[:50])
            round_votes: list[AgentVote] = []

            for debater in debater_callables:
                try:
                    vote = await asyncio.wait_for(debater(proposal, prior_votes), timeout=timeout)
                    if vote.choice in options or not options:
                        round_votes.append(vote)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Debater failed in round %d: %s", round_idx, exc)

            final_result = self.evaluate_votes(round_votes, strategy=strategy)
            prior_votes = round_votes

            if final_result.consensus_reached:
                logger.debug("Consensus achieved in round %d on '%s'", round_idx, final_result.decision)
                break

        return final_result
