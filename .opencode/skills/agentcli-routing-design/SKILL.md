---
name: agentcli-routing-design
description: >
  Phase 2 (multi-model routing) binding design contract for agentcli:
  hybrid server/client fallback, task classifier, model registry with health
  tracking, CLI surface, config schema, testing strategy, and acceptance
  criteria. Use when planning or implementing model switching, fallback
  chains, the /model command, classifier, registry, or router code in
  C:\Users\sam\Documents\sparkz\agentcli. Read together with the
  openrouter-api and agentcli-dev skills.
---

# agentcli Phase 2 — Multi-Model Routing (Binding Contract)

Status: **v2, agreed 2026-08-26** (original blueprint merged with the task
spec + research amendments). Deviations must be justified in the PR and
reflected back here.

## Goal

`agentcli chat` classifies each user message and routes it to the best
available free OpenRouter model, with automatic fallback on
failure/rate-limit — without breaking Phase 1's budget philosophy (free-tier
first, `httpx`-only deps, lazy startup).

## Non-negotiable constraints

1. **Zero new runtime dependencies.** No pydantic/SDK/rich. `httpx` only.
2. **One pooled `httpx.AsyncClient`** — routing reuses it; never per-request.
3. **Exit codes, config precedence, REPL error-survival are contracts.**
4. **No live API in tests.** Registry/classifier/router are pure data+logic,
   unit-tested offline; client paths use `httpx.MockTransport`.
5. **Backward compat:** Phase 1 TOML files (only `[openrouter]`/`[app]`)
   work unchanged; `[routing]` is optional with defaults.
6. **Startup stays lazy:** routing modules are imported inside `run_chat`,
   not at module top. `config show` / `--help` must not slow down.
7. **Phase boundaries:** no sub-agents (P3), no agent loop (P4), no
   memory/persistence (P5). Routing layer must be a swappable component with
   a clear interface — Phase 4 plugs into it.

## Core architectural decision (do not relitigate casually)

**Hybrid fallback: server-side via the `models` array, client-side only for
what the server cannot do.**

- Router produces an ordered candidate list for the classified category →
  sent as `"models": [primary, *fallbacks]` (OpenRouter fails over across
  models/providers remotely, including on 429/5xx — see openrouter-api skill).
- Client retry loop (existing backoff) handles pre-response transport errors
  and final chain exhaustion only. No bespoke client model-hopping loop.
- Health tracking (below) marks models to steer *future* candidate ordering.

## Components

### 1. Model registry (`routing/registry.py`)

- Shipped as package data (Python dict): per model — `id`, `categories`
  (tags: `code`/`chat`/`reasoning`), rough `context_window`, `priority`
  per category. Free-tier rate limits are platform-wide (20 rpm, 50/1000
  rpd) — do NOT store per-model limit folklore.
- **User extension via TOML** `[[routing.models]]` array-of-tables: entries
  with an existing `id` replace built-ins; new ids are appended.
- **Health tracking (in-memory, per session):** after
  `failure_threshold` (default 3) consecutive failures → cooldown for
  `cooldown_seconds` (default 300). A `RateLimitedError` (429) imposes
  cooldown directly. Success resets the streak. Router skips cooling-down
  models. No persistence (Phase 5 territory).

### 2. Task classifier (`routing/classifier.py`)

- Heuristic-only (regex/keyword/prompt-shape), pure functions, <5ms,
  zero I/O. Categories: `code`, `reasoning`, `chat` (default/fallback).
- Extension point for a model-based classifier exists but is NOT the
  default (latency); opt-in via config only if ever implemented.
- Unit-testable in isolation: prompt string in → category out.

### 3. Router (`routing/router.py`)

- Input: category + registry state (+ `[routing]` config). Output:
  `RoutingDecision` (primary + ordered fallbacks, capped at
  `max_fallbacks`), skipping cooling-down models. Deterministic and
  unit-testable with a synthetic registry — no network.
- Routing disabled (`[routing] enabled = false`) or `--model` given →
  decision is None → Phase 1 single-model behavior exactly.

### 4. Client prerequisites (`openrouter_client.py`) — MUST land with routing

- **Mid-stream SSE errors:** after HTTP 200, failures arrive as events with
  `error` objects / `finish_reason: "error"` (exact shape in openrouter-api
  skill). `chat_stream` must raise `OpenRouterError` (with code) on these —
  never silently truncate.
- **`models` array support:** `chat_stream(..., models=[...])` sends the
  array; `model=` keeps single-model semantics (Phase 1 compat).
- **Served-model capture:** chunks carry the actual served `model` field;
  client exposes it (e.g. `last_served_model`) — with fallbacks active it
  can differ from the requested primary.

### 5. CLI integration (`cli.py`)

| Surface | Behavior |
|---|---|
| `agentcli chat` | Auto-routes per user message (classifier runs each turn) |
| `agentcli chat --model X` | Forces X, **skips routing entirely** (regression-tested) |
| `--show-model` | Prints the actual served model after each reply |
| `--verbose` | Implies show-model behavior |
| Fallback visibility | Served ≠ requested primary → visible notice under `--show-model`/verbose |

## Config additions (`[routing]`, all optional)

```toml
[routing]
enabled = true
max_fallbacks = 2          # fallbacks after the primary
cooldown_seconds = 300
failure_threshold = 3

[[routing.models]]         # optional user registry extension/override
id = "z-ai/glm-5.2:free"
categories = ["code", "reasoning"]
priority = 10
context_window = 128000
```

## Testing strategy

- Classifier: table-driven category assertions (code/reasoning/chat, empty
  input, mixed signals precedence).
- Registry: built-in load; TOML merge (replace-by-id + append); health
  transitions (streak → cooldown → skip → success reset; 429 direct cooldown).
- Router: ordering per category, cooldown skipping, `max_fallbacks` cap,
  disabled → None.
- Client: payload contains `models` array (assert request body via
  MockTransport); mid-stream error event → `OpenRouterError`; served-model
  capture.
- CLI: `--model` bypass regression (no `models` in payload, exact model
  used); auto-route path; `--show-model` output.

## Acceptance criteria (Phase 2 done = all checked)

- [ ] `agentcli chat` picks a model automatically per task without user input
- [ ] Simulated failure/rate-limit of the top candidate → automatic,
      user-visible fallback (under `--show-model`/verbose), not a hard error
- [ ] `--model` works exactly as Phase 1, regression-tested
- [ ] Mid-stream SSE errors surface as clean errors; REPL survives, history intact
- [ ] Classifier, registry, router fully unit-tested offline
- [ ] Phase 1 config files work unchanged; `[routing]` fully optional
- [ ] `pytest` (≥85% floor maintained), `ruff check .`, `mypy .` all clean
- [ ] README (routing, flags, config) + CHANGELOG `[Unreleased]` updated
- [ ] Startup benchmark not meaningfully regressed (routing imports lazy)

## Explicitly out of scope (later phases)

Sub-agent orchestration (P3) · custom plan/act/reflect loop (P4) ·
memory/token-budget persistence (P5) · startup optimization beyond laziness (P6) ·
packaging/ecosystem/non-OpenRouter providers (P7).
