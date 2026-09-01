## Summary

Implements Phase 8 (MVP Truth & Safety) per the completion audit findings.

### Changes

**Provider-backed planning**
- `PlannerAgent._generate_plan_llm()`: calls LLM for structured JSON planning when `model` is in payload; falls back to keyword heuristics
- `AgentLoop`: passes `Config` to `PlannerAgent` for LLM access
- Fixed `AgentLoop.run()` async generator return type for mypy

**Config-wired tool registry**
- `Session.run_loop()`: passes `Config.subagents.specific_config` to `ToolRegistry(tool_configs=...)` so subagent config actually governs agent-loop execution
- `ToolRegistry`: restored `register_callable()` and `tool_configs` parameter

**Cross-category fallback tracking**
- `RoutingDecision`: added `is_fallback`, `requested_category`, `served_category` fields
- `Router.decide()`: populates new fields for accurate model badge rendering

**Security & Windows fixes**
- `ShellExecutionAgent`: verified `security_mode: "allowlist"` works (test passes)
- Fixed Unicode encoding in CLI output (arrows, special chars)

**Quality gates**
- 206 tests pass
- 86.59% coverage (85% floor)
- `ruff check .`: clean
- `mypy .`: clean

### Coverage Note
Coverage dropped from ~90% to ~86.6% because new code paths (LLM planner, fallback tracking, config wiring) aren't fully exercised by existing tests. The 85% floor is met. Follow-up PRs should add tests for new LLM planning paths.