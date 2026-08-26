---
name: openrouter-api
description: >
  Verified OpenRouter API reference for agentcli development: free-tier rate
  limits, SSE streaming format including mid-stream errors, error codes,
  provider routing and model-fallback parameters, and model slug validation.
  Use when working on openrouter_client.py, model routing, rate-limit
  handling, or any code that talks to the OpenRouter API. Project:
  C:\Users\sam\Documents\sparkz\agentcli
---

# OpenRouter API Reference (verified 2026-08)

Facts below were verified against OpenRouter docs in August 2026. When a
request behaves differently from this reference, re-verify against
https://openrouter.ai/docs/llms.txt before trusting either.

## Basics

| Attribute | Value |
|---|---|
| **Base URL** | `https://openrouter.ai/api/v1` |
| **Auth** | `Authorization: Bearer <key>` (key from env var, default `OPENROUTER_API_KEY`) |
| **Attribution headers** | `HTTP-Referer: <site URL>` + `X-Title: <app name>`. Current docs show `X-OpenRouter-Title` as the new name for `X-Title` — agentcli sends `X-Title` (still honored); migrating is a candidate cleanup |
| **Models catalog** | `GET /api/v1/models` — **public, no auth needed** |
| **Key/quota info** | `GET /api/v1/key` → `limit_remaining`, `usage`, `usage_daily`, `is_free_tier` |

## Free-tier rate limits (the whole point of agentcli's budget focus)

Free model slugs end in `:free`. Limits are **per model family**, enforced globally (extra accounts/keys do NOT help):

| Credits purchased (all time) | Requests/min | Requests/day |
|---|---|---|
| < $10 | **20** | **50** |
| ≥ $10 | **20** | **1000** |

- A **negative credit balance** causes `402` even on free models.
- Successful responses carry NO `X-RateLimit-*` headers; only 429 error
  responses do (`X-RateLimit-Limit/Remaining/Reset`), plus `Retry-After` when
  providers returned retry hints.
- Check remaining quota proactively via `GET /api/v1/key`.

## Error codes (chat completions)

| Code | Meaning | agentcli behavior |
|---|---|---|
| 400 | Bad request / invalid params | Non-retryable, raise |
| 401 | Invalid/disabled key | Non-retryable, raise |
| 402 | Insufficient credits (even on free) | Non-retryable, raise |
| 404 | Model doesn't exist (e.g. retired `:free` slug) | Non-retryable, raise — body often suggests replacement slug |
| 429 | Rate limit (platform or upstream provider) | **Retryable** with backoff |
| 5xx | Server/provider failure | **Retryable** with backoff |
| 502 | Model/provider down | Retryable |
| 503 | No provider meets routing requirements | Retryable / relax routing prefs |

## SSE streaming — CRITICAL details

Response is `text/event-stream`; lines look like:

```
data: {"id":"...","choices":[{"index":0,"delta":{"content":"Hi"}}]}\n\n
data: [DONE]\n\n
```

1. **Comment lines**: OpenRouter may send `: OPENROUTER PROCESSING` keep-alive
   comments — lines not starting with `data: ` must be skipped (agentcli does this).
2. **`[DONE]` sentinel** ends the stream — but do NOT rely on it: a stream can
   also end without it (agentcli handles both; keep it that way).
3. **Mid-stream errors arrive INSIDE the 200 stream** — after status 200 is
   sent, rate limits and provider failures surface as SSE events:
   ```
   data: {"id":"...","error":{"code":429,"message":"Rate limit exceeded"},"choices":[{"index":0,"delta":{"content":""},"finish_reason":"error"}]}
   ```
   ⚠️ **agentcli's `chat_stream` currently ignores `error` events and
   `finish_reason: "error"`** — it only reads `delta.content`. Handling this
   is a Phase 2 requirement, not optional polish.
4. Usage accounting (prompt/completion token counts) can be requested via
   `usage: true` in the payload — useful for the budget-tracking goals later.

## Model fallbacks & provider routing (Phase 2 building blocks)

### Client-side multi-model fallback (the big one)

Send a `models` **array** instead of `model` — OpenRouter tries providers of
the first model, then falls through to the next models on failure:

```json
{
  "models": ["google/gemma-4-31b-it:free", "z-ai/glm-5.2:free"],
  "messages": [...],
  "stream": true
}
```

This gives agentcli server-side fallback with ZERO client orchestration —
strongly prefer it over hand-rolled retry-a-different-model logic.

### `provider` preference object

| Field | Type | Notes |
|---|---|---|
| `sort` | `"price"` \| `"throughput"` \| `"latency"` | Disables default load balancing |
| `sort.partition` | `"model"` (default) \| `"none"` | `"none"` sorts across fallback models globally |
| `order` | string[] | Try these provider slugs first |
| `allow_fallbacks` | bool (default true) | false = fail if listed providers fail |
| `require_parameters` | bool | Only providers supporting all params |
| `data_collection` | `"allow"` \| `"deny"` | Privacy routing |
| `max_price` | object | Hard price cap — request fails if unmet |

Default strategy: price-weighted load balancing with 30s-outage avoidance.

### Slug shortcuts

- `:nitro` suffix = throughput sort + priority tier (e.g. `model:nitro`)
- `:floor` suffix = price sort + flex tier
- `:free` suffix = free variant (rate-limited, see above)

## Slug volatility — ALWAYS verify live

Free models are retired frequently (agentcli's original default
`meta-llama/llama-3.1-8b-instruct:free` died within months). Before
hardcoding any model slug:

```powershell
python -c "import httpx; [print(m['id']) for m in httpx.get('https://openrouter.ai/api/v1/models', timeout=30).json()['data'] if m['id'].endswith(':free')]"
```

Model objects also carry `context_length`, `pricing` (prompt/completion per
token), and `top_provider` metadata — use these for routing decisions rather
than hardcoding assumptions.
