# Phase 9-10 Remaining Work Plan

## Current State (as of 2026-09-02)

**Status**: All tests pass (246 passed, 89% coverage), quality gates clean (ruff ✅, mypy ✅)

### Completed in Phase 9
- ✅ **WebSearchAgent** - Brave Search API (2000 q/mo free) + DuckDuckGo HTML fallback
- ✅ **CodeAnalyzerAgent** - Provider-backed LLM analysis with fallback
- ✅ **ToolRegistry** - Config wiring for all agents
- ✅ **Planner goal_criterion** - Fixed with meaningful criteria for all task types
- ✅ **is_agentic_task** - Improved detection for "read...analyze" patterns
- ✅ **File path regex** - Windows backslash handling fixed
- ✅ **All quality gates pass** - 246 tests pass, 89% coverage, ruff/mypy clean

---

## Remaining Work Plan

### Priority 1: Critical Bug Fixes (Must Fix)

#### 1. `pop_last_message()` doesn't sync with memory store
**File**: `agentcli/session.py` line 207
**Issue**: `pop_last_message()` only removes from in-memory `self.history` but not from persistent `MemoryStore`
**Impact**: Resumed sessions show stale messages
**Fix**: Add `self.memory_store.delete_last_message(self.session_id)` call (need to add this method to MemoryStore)

#### 2. `MemoryStore.__del__` fails during interpreter shutdown
**File**: `agentcli/memory/store.py` lines 429, 418
**Issue**: `__del__` calls `close()` but `_conn` attribute may not exist during interpreter shutdown
**Error**: `AttributeError: 'MemoryStore' object has no attribute '_conn'`
**Fix**: Use `getattr(self, '_conn', None)` or `hasattr` check in `close()`

#### 3. MCP subprocess fails on Windows (Proactor pipe issue)
**File**: `agentcli/mcp.py` line 139
**Issue**: `agentcli mcp` subprocess crashes with Proactor pipe handle errors
**Root cause**: Windows asyncio Proactor doesn't handle stdio pipes in subprocesses
**Workaround**: Document that `agentcli mcp` only works in-process on Windows; use WSL2/Docker for external MCP clients
**Proper fix**: Use `asyncio.create_subprocess_exec` with proper pipe handling or switch to thread-based I/O

---

### Priority 2: Feature Completion

#### 4. Provider-backed Planner (LLM-based planning)
**File**: `agentcli/subagents/planner.py` `_generate_plan_llm`
**Status**: LLM path exists but goal_criterion often empty; reflector can't verify
**Issue**: LLM planner returns valid JSON but `goal_criterion` often empty → reflector can't verify
**Fix**: 
- Improve system prompt to require specific, verifiable `goal_criterion`
- Add post-processing to generate criteria from step content if LLM omits
- Test with real LLM calls (not just mocks)

#### 5. WebSearchAgent snippet extraction (minor)
**File**: `agentcli/subagents/web_search.py` - DuckDuckGoProvider
**Status**: Works but snippets sometimes empty
**Issue**: Regex parsing sometimes misses snippets
**Fix**: Improve regex robustness, add fallback selectors

---

### Priority 3: Windows Support & Reliability

#### 6. Unicode/CP1252 encoding in CLI output
**File**: `agentcli/agent/loop.py` - status strings with Unicode arrows
**Issue**: CP1252 `UnicodeEncodeError` on piped output
**Fix**: Detect encoding capability, fallback to ASCII

#### 7. `__del__` cleanup for interpreter shutdown
**File**: `agentcli/memory/store.py` and `agentcli/session.py`
**Fix**: Use `getattr(self, '_conn', None)` pattern, suppress exceptions in `__del__`

#### 8. Test infrastructure for Windows
- Use `--basetemp=.pytest-temp` for pytest temp dir
- Add subprocess-level Windows tests for MCP

---

### Priority 4: Phase 10+ (Post-Phase 9)

#### Phase 10: Tool Adapters & Ecosystem
- MCP stdio server hardening (Windows)
- Plugin system sandboxing
- Tool marketplace/registry

#### Phase 11: E2E Hardening
- Full Windows CI pipeline
- Subprocess-level MCP tests
- Performance benchmarks

#### Phase 12: Release Evidence
- PyPI release automation
- Signed artifacts
- Documentation (MkDocs)

---

## Implementation Order Recommendation

### Sprint 1 (Week 1): Critical Bugs
1. Fix `pop_last_message()` memory store sync
2. Fix `MemoryStore.__del__` / `close()` shutdown safety
3. Test and verify fixes

### Sprint 2 (Week 2): Planner & Search Polish
4. Improve Planner LLM prompt for goal_criterion
5. Fix WebSearch snippet extraction edge cases
4. Test end-to-end planner → act → reflect loop

### Sprint 3 (Week 3): Windows & Reliability
5. Fix `MemoryStore.__del__` shutdown safety
6. Fix CLI Unicode encoding for Windows
6. Document MCP Windows limitations

### Sprint 4 (Week 4): Release Prep
7. Version bump to 1.0.0 (or 0.8.0)
8. Release automation (GitHub Actions)
9. Documentation updates

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| LLM rate limits during testing | High | Medium | Use mocks for CI, live only for manual |
| Windows Proactor issues | High | High | Document WSL2/Docker workaround |
| LLM prompt brittleness | Medium | Medium | Add fallback, test with multiple models |
| Memory store sync bugs | Low | High | Add integration tests for pop/clear |

---

## Success Criteria for Phase 9 Completion

- [ ] All 246+ tests pass
- [ ] Coverage ≥ 85% (currently 89%)
- [ ] `pop_last_message` syncs to memory store
- [ ] `MemoryStore` shutdown safe
- [ ] Planner LLM produces verifiable goal_criterion
- [ ] WebSearch returns snippets reliably
- [ ] Windows CLI runs without Unicode errors
- [ ] All quality gates pass (ruff, mypy, pytest)

---

## Questions for User

1. **Priority**: Should we fix `pop_last_message` + `MemoryStore.__del__` first (critical bugs), or improve Planner LLM first (feature)?
2. **Scope**: Is Windows MCP subprocess fix required for v1.0, or document as known limitation?
3. **Release**: Target v0.8.0 (beta) or v1.0.0 (MVP)?
4. **Testing**: Add integration tests for `pop_last_message` + memory store sync?

Please confirm priority order and any scope adjustments before I create detailed implementation tasks.