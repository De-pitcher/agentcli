# Phase 9 - Model-Driven Orchestration & Grounded Completion

Status: PLANNED. Depends on Phase 8.

## Outcome

Replace keyword planning and heuristic success with a real, bounded agent run that plans against registered tool schemas, executes validated calls, and returns a grounded answer.

## Scope

1. Implement provider-backed structured planning with a strict schema, model selection, retry, and fallback accounting.
2. Give the planner the actual available tools, their input schemas, workspace context, and prior tool results.
3. Implement a model-backed or strongly evidence-grounded reflection/finalization stage; surface tool outputs and citations in the final answer.
4. Replace fragile keyword routing with an explicit `--agent` mode plus a conservative intent policy.

## Acceptance evidence

- An end-to-end fixture with a real or recorded provider response completes a multi-step task and returns its findings, not only a step count.
- Missing file paths, invalid tool arguments, and failed calls produce a single useful final answer without runaway re-plans.
- Token, model, iteration, and tool-call usage are recorded per run.
