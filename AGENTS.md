# agentcli Project Agent Instructions

## Host profile and performance guardrails

This project is developed on `peregrine001`: a 15 W Intel i7-8650U (4 cores / 8 threads), 32 GB RAM, Intel UHD 620 integrated graphics, and a Windows x64/PowerShell host. The RAM is ample for Python development; sustained CPU heat and battery power limits are the constraint. No agent can guarantee that Windows or the firmware will never thermally throttle. Keep work within the following operating envelope to avoid causing avoidable throttling.

- Keep local development commands sequential. Do not run concurrent builds, coverage, benchmarks, Docker builds, or background agent processes.
- This is an API-backed agent. Do not run local LLM inference, GPU workloads, virtual machines, or CPU-heavy model-serving experiments on this host. Use OpenRouter/remote providers for inference.
- Prefer focused tests while iterating: `python -m pytest tests/test_<area>.py`. Run the full suite only at a phase gate, with `--basetemp` inside the repository when sandboxing prevents use of `%TEMP%`.
- Do not add pytest-xdist or parallel test execution. Keep benchmark concurrency at 1 by default on this laptop; multi-agent concurrency is a controlled benchmark, not a normal development setting.
- Keep `agent_loop.max_iterations` small (5-8) and sub-agent concurrency conservative. Every tool must have bounded output, timeout, cancellation, and a workspace scope.
- For a full validation run, connect AC power, use Windows **Best performance** power mode only for the run, close local LLMs/containers and heavy IDE indexing, and allow the laptop to cool between long runs. Revert to Balanced mode afterwards. Do not disable thermal protections or alter firmware power limits.
- Prefer `PYTHONIOENCODING=utf-8` for piped/non-ASCII CLI tests on Windows. Configure memory and test temporary files inside a writable project path when host policy blocks `%LOCALAPPDATA%` or `%TEMP%`.

## MVP truthfulness

- Do not label a capability as working merely because its unit test uses mocks or because it emits a successful `SubAgentResult`.
- An agentic capability is complete only after an end-to-end test proves planning, tool execution, result grounding, final user-facing synthesis, error handling, and Windows CLI behavior.
- Clearly mark stubs, prompt builders, unconnected configuration, and unverified integrations in documentation and release notes.
- Preserve existing user changes. Do not reset, checkout, or overwrite unrelated work.
