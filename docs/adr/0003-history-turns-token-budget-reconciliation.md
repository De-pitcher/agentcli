# ADR 0003: Dynamic Token Budgeting and History Turns Reconciliation

## Status
Accepted (Phase 5, binding)

## Context
Phase 1 trimmed chat history strictly by turn count (`history_turns * 2 + 1` messages). In Phase 5, dynamic context window tracking was introduced across heterogeneous models (from 8k to 128k+ tokens). If turn-count trimming and token budgeting operate independently, they can conflict or truncate unnecessarily.

## Decision
Reconcile token budgeting and turn limits into a single unified trimmer `trim_history_to_budget()`:
1. Always preserve the system prompt at index 0.
2. Dynamically calculate available token capacity using the model's registered `context_window` scaled by `budget_ratio` (default: 0.75).
3. Use `history_turns` as an optional turn-count ceiling / upper bound rather than a blunt truncator.

## Consequences
- **Positive**: Eliminates duplicate or competing trimming logic.
- **Positive**: Guarantees context fits within free-tier model context limits without blowing budget on large prompts.
