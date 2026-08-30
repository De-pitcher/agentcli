# ADR 0001: Pure Python Runtime without External Harness Dependencies

## Status
Accepted (Pre-Phase 1, binding)

## Context
Earlier prototypes explored embedding or wrapping external TypeScript/Node.js agent harnesses (e.g. DeepSeek Harness) to drive agentic loops. However, doing so introduced multi-runtime dependencies (Node.js + Python), bloated binary distributions (>150MB), complicated cross-platform deployment, and prevented low-latency in-process sub-agent coordination.

## Decision
Build `agentcli` purely in modern Python (3.11+) with standard library modules wherever possible and `httpx` as the sole external runtime dependency. All agentic loop logic (Plan → Act → Reflect) and sub-agent messaging are implemented natively in-process.

## Consequences
- **Positive**: Single runtime dependency, cold-start time <100ms, install footprint <5MB, seamless cross-platform support (Windows/Linux/macOS).
- **Negative**: Required building lightweight in-process agent loop and sub-agent coordinators natively rather than reusing external JS packages.
