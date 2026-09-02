fix(phase8): Windows path handling & agentic loop fixes

### Bug Fixes

**1. File path regex for Windows backslashes** (`agentcli/subagents/planner.py:360`)
- Fixed regex pattern to handle Windows paths with backslashes (`\`)
- `@tests\test_session.py` now parses correctly

**2. Agentic task detection** (`agentcli/agent/loop.py`)
- Added keywords: "analyze it", "analyze the", "read and", "read then"
- "read @tests\test_session.py and analyze it for bugs" now triggers agentic loop

**3. Agentic loop iteration limit fix**
- Root cause: planner failed due to bad file paths → reflector kept re-planning
- Fixed by fixing file path extraction and task detection

### Quality Gates
- 246 tests pass
- 91.25% coverage
- ruff check: clean
- mypy: clean