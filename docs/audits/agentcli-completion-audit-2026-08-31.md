# Agentcli Completion Audit - 2026-08-31

## Verdict

**Not ready for a first MVP release.** The project is a credible alpha foundation and its simple hosted chat works, but the advertised agentic product is mostly orchestration scaffolding. It can execute some local helpers, yet it cannot reliably plan a general task, reason over tool results, or return a grounded final answer. Calling the project "Phase 7 complete" or "v1.0.0" is a false positive based on the repository's own runtime version (`0.7.0`), workflows, and end-to-end behavior.

**Required remediation: 5 development iterations (Phases 8-12).** The separate phase contexts are under `.workspace/06_phase8-mvp-truth-and-safety/` through `.workspace/10_phase12-release-evidence/`.

## Audit method and limits

Audit ran on `peregrine001` (Windows x64, i7-8650U, 32 GB RAM) from the project checkout on 2026-08-31. Existing uncommitted changes were inspected but not altered. "Working" below means an actual local or provider-backed invocation, not a mocked unit test.

| Check | Result | Evidence |
|---|---|---|
| Lint and formatting | Pass | `python -m ruff check .`; `python -m ruff format --check .` |
| Static types | Pass | `python -m mypy .` |
| Test suite in default host temp path | Fail | 157 passed, 62 setup errors because pytest could not create `%TEMP%\\pytest-of-sam`; 2 UI failures; 74.61% coverage |
| Test suite with project-local temp path | Fail | `python -m pytest --basetemp=.audit-pytest --cov`: 219 passed, 2 failed, 93.88% coverage |
| Package build (network-enabled) | Pass | Built `agentcli-0.7.0.tar.gz` and `agentcli-0.7.0-py3-none-any.whl` |
| Dependency audit in current developer environment | Fail | `pip_audit --local` found 28 vulnerabilities in globally installed `lxml`, `pillow`, and `pip`; it also could not audit installed metadata for `agentcli (1.0.0)` |
| Simple live OpenRouter chat | Pass | One controlled `google/gemma-4-31b-it:free` request returned exactly `AGENTCLI_TRANSPORT_OK` |
| Direct registry/MCP handler: file read | Pass | Returned `README.md` contents |
| Direct registry/MCP handler: shell | Pass | `python --version` returned successfully |
| Direct registry/MCP handler: code analyzer | Partial only | Returned an assembled analysis prompt; did not call an LLM or return analysis |
| Direct registry/MCP handler: web search | Fail by design | Explicit "not yet implemented" result |
| Agent loop, safe file task | Partial only | Planned two steps, ran them, and returned only `2/2 step(s) completed successfully.` |
| Agent loop, typical audit request without paths | Fail | Replanned the identical invalid `code_analyzer(files=[])` until `LoopIterationLimitError` |
| Piped Windows agent-loop CLI | Fail | CP1252 `UnicodeEncodeError` on `Plan->Act->Reflect` output |
| Same CLI with UTF-8 plus writable local memory DB | Partial only | Loop runs, but no result synthesis |
| MCP stdio subprocess on Windows | Fail | `agentcli mcp` piped input hit Proactor invalid-handle errors; direct `MCPServer.handle_request` works |

The ordinary sandbox blocks outbound traffic and default Windows data directories. The live chat verification was rerun with explicit outbound approval; therefore its success is meaningful. The `%TEMP%` and `%LOCALAPPDATA%` failures are environment-policy effects, but the product offers poor diagnostics and no project-local test guidance. They must still be handled for a release-quality Windows experience.

The build succeeds when it can fetch isolated build requirements. The dependency-audit result is a failing local gate, but its vulnerable packages are not declared runtime dependencies in this repository; repeat it in a clean project virtual environment before assigning ownership. The installed `agentcli (1.0.0)` metadata versus source `0.7.0` reinforces the version-drift finding.

## What genuinely works

- The basic CLI parses commands, loads configuration, connects to OpenRouter, streams a simple response, and records the served model when network access is available.
- Routing, retry primitives, SQLite/session code, file-reference expansion, registry dispatch, basic file read/list/write/delete logic, direct non-shell subprocess execution, and the MCP request handler have substantial unit coverage.
- File operations constrain paths to their configured working directory by default. Shell execution avoids `shell=True`, bounds output, applies a timeout, and blocks a small command-name denylist.
- The loop has a real event lifecycle and an iteration ceiling. It is not imaginary; it is simply not sufficient to be a dependable agent.

## Why the agentic claim does not hold

### Planning is keyword heuristics, not agent planning

`PlannerAgent` selects tools from words such as `analyze`, `read`, and `run`. It does not call the configured planning model, does not receive actual registered tool schemas, and ignores much of its own `model`/`models` payload. A normal request such as "analyze the project and report issues" becomes `code_analyzer(files=[])`, fails, and re-plans identically. The supplied `plan_model_override` and `reflect_model_override` do not make planning or reflection provider-backed.

### "Code analyzer" does not analyze

`CodeAnalyzerAgent.run()` reads files and returns a prompt containing them. `_get_client()` is unused. It produces no model call, issue list, security assessment, or final result. The loop marks that prompt construction successful, which is a semantic false positive.

### The loop cannot complete the user's task

The default reflector only tests tool success/error strings and optional criteria. Default plans set every `goal_criterion` to empty, so any successful local calls finish the task. `_build_summary()` discards every tool result and says only `N/N step(s) completed successfully.` The user never receives the file contents, analyzer findings, shell output, citations, or a conclusion.

### Tool configuration is disconnected from the agent path

`Config.subagents` exists, but `AgentSession.run_loop()` constructs `ToolRegistry()` with no tool configurations and does not use `SubAgentSpawner`. Settings such as `enabled`, `max_concurrent`, models, idle timeout, default timeout, and output budget therefore do not govern normal agent-loop execution. The spawner/pool is tested infrastructure with no production wiring.

### Default tools are incomplete or unsafe for an MVP promise

- `web_search` is explicitly a stub, but it is advertised as a built-in MCP tool.
- `file_ops` permits write and recursive directory deletion in the process working directory with no confirmation or immutable policy boundary.
- The shell default is a denylist, not an allowlist. The executable can be an arbitrary absolute path; blocking `cmd`, `powershell`, and a short list does not constitute a robust execution sandbox.
- Plugins execute arbitrary Python with the host process's privileges. This is documented, but must be separated from a safe default MVP tool policy.

### Windows support is not release proven

The actual piped agent loop crashes on non-UTF-8 Windows output because its user-facing status strings include Unicode arrows/marks. MCP stdio run as a subprocess hits Windows Proactor pipe errors. These are primary advertised integration paths, not edge cases. The in-process MCP handler passes, which proves protocol dispatch but not host interoperability.

### Release and documentation claims conflict with the repository

- `README.md` says `v1.0.0 (Phase 7 Complete)`; `agentcli.__version__` and `pyproject.toml` say `0.7.0` and classify the package as Alpha.
- The Phase 7 acceptance criteria claim PyPI release automation, signed artifacts, Brew/Winget manifests, and MkDocs. The repository has a CI-only GitHub workflow and no visible release workflow, publication configuration, manifests, MkDocs configuration, or PyInstaller dependency.
- The benchmark claims prove local framework overhead and synthetic concurrency, not end-to-end agent quality, tool correctness, host MCP operation, provider availability, or release readiness.

## Test and quality analysis

The project has meaningful unit-test breadth, but many agent-loop tests inject mocked planners, registries, and reflectors. Those tests verify control flow, not autonomous behavior. The full suite's two remaining UI failures are caused by test setup assuming rich output after only overriding `isatty`; `ConsoleRenderer` correctly also disables rich output when `TERM=dumb`, which this host exposes. This is test fragility rather than evidence that rich rendering itself is broken. It nevertheless leaves the stated local quality gate red.

The full suite also produces Windows Proactor cleanup warnings. Fixing environment-controlled tests, using a repository-local `--basetemp` in controlled sandboxes, and adding subprocess-level Windows tests belong in the reliability phase.

## MVP definition recommended before release

Ship a narrow, honest MVP: OpenRouter chat plus an explicit `--agent` mode with read-only workspace file inspection, a small command allowlist, one real web-search adapter or no web search, structured provider-backed planning, and a final evidence-grounded response. Keep arbitrary plugins and destructive tools behind explicit trusted/developer flags. Do not advertise autonomous coding, generic project auditing, or MCP interoperability until the corresponding end-to-end tests pass on Windows and Linux.

## Host guidance adopted for this project

The i7-8650U/32 GB host is adequate for this API-backed Python CLI. RAM is not the bottleneck; sustained 15 W CPU heat is. Use sequential focused tests during development, avoid local model inference and parallel test workers, and reserve a full suite/benchmark for phase gates while on AC power. Windows Best Performance can improve responsiveness for a short validation run, but it cannot and should not bypass firmware thermal protections. Repository-root and workspace agent instructions now record this operating policy.
