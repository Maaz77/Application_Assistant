# P1 — Infrastructure: OpenRouter routes, one model gateway, circuit breaker, spend cap, one Chrome connection

Read `recore/00_common.md` and `recore/HANDOVER.md` first. P1 changes how the program talks to models and to Chrome. Page logic, the guard and the browser package stay as they are; the package only gets new settings.

## Goal
Reliable and cheap model access; a failure stops the run cleanly; one "Allow" click per run.

## Evidence (build spec v3)
§13.1: 222 Jev requests in 19 min, 4 batches in flight, nested retries (up to ~18 HTTP requests per goal step), batches of 4 that multiplied the requests. §13.2: Vercel free-tier 429/503; free chat models weak or out of quota. §13.6: runs blocked on "Allow remote debugging?".

## Tasks

### T1 — Routes and models (00_common D14)
- Defaults in `config.toml`:

```toml
[models]
chat_route = "openrouter"
jev_route  = "openrouter"
jev        = "typesafe/jev-1.13"

[models.openrouter]
llm_inference = ["deepseek/deepseek-v4.1-flash", "openai/gpt-5.4-mini"]
text_helper   = ["deepseek/deepseek-v4.1-flash", "openai/gpt-5.4-mini"]   # the package's text helper, until P2
```

  The Vercel route stays available, but it is not the default.
- One Jev endpoint for the Decider **and** the package: `https://openrouter.ai/api/v1/systemone`, TypeSafe System One shape `{model, state, questions}`. It replaces the package's OpenRouter value `…/api/alpha/decisions` (build spec §3.2).
- Contract test (`--live`): one request with one `noul`, one `choice` and one `score` question, sent once through the Decider and once through the package's sender; assert the TypeSafe response shape. If the endpoint rejects the System One shape, switch both senders to `https://openrouter.ai/api/alpha/decisions`, adapt the shapes, and record the evidence in DISCOVERY.md.
- Chat requests (OpenRouter `chat/completions`): keep `temperature: 0`, JSON-schema output and reasoning off. Add `"provider": {"require_parameters": true}`, so OpenRouter routes only to providers that support every parameter sent. Check the current OpenRouter docs for how a response reports the cost of the call (usage accounting); use the flag if one is needed; record it in DISCOVERY.md.
- `LLM_INFERENCE_TIMEOUT` = 45 s (was 120 s).
- Preflight also checks that each configured LLM inference model returns valid JSON for a trivial schema. A 401 or 402 fails preflight with "check OPENROUTER_API_KEY / add credit on OpenRouter".

### T2 — `assistant/gateway.py`: one path for every model request
Every model request goes through one `Gateway`: the Decider, LLM inference, and the package's `_post` (route it through the Gateway inside the existing wrapper, and return what the package expects).
- **One queue:** at most `limits.max_in_flight = 1` request at a time; at least `limits.min_interval_s = 0.25` between request starts.
- **One retry layer:** at most `limits.max_attempts = 3` attempts per request, only on 429, 5xx, timeout or connection error. Wait for `Retry-After` when it is given (≤ 30 s), else 2 s, then 6 s. No other layer retries: delete the Decider's `RETRY_WAITS` loop; our side retries a `browser_goal` at most once (the package still retries inside — record the resulting worst case in DISCOVERY.md).
- **Batching:** delete `BATCH = 4` and `PARALLEL = 4`. A judgment is one request. Split it only above `jev.max_questions_per_request = 24` questions, and send the parts one after another. Record in DISCOVERY.md whether large requests fail on OpenRouter.
- **Timeouts:** Jev 20 s per attempt; chat 45 s.
- Every attempt is logged through `inference_log` (P0) before the result returns.
- **Counters**, per job and per run: requests, attempts, failures, cost (as the gateway reports it; else estimated from `[prices]` and marked "estimated"), and the highest number in flight observed.

```toml
[limits]
max_in_flight = 1
min_interval_s = 0.25
max_attempts = 3

[budget]
max_usd_per_run = 1.00

[jev]
max_questions_per_request = 24

[decider]
fallback = "chat"          # "chat" | "none"

[prices]                   # USD per million tokens [input, output]; used only when the gateway reports no cost
"typesafe/jev-1.13"            = [0.042, 0.0]
"deepseek/deepseek-v4.1-flash" = [0.099, 0.60]
"openai/gpt-5.4-mini"          = [0.75, 4.50]
```

`config.py` rejects unknown keys: add these sections to its pydantic models.

### T3 — Circuit breaker, spend cap, credit (00_common D15, D21)
- `ProviderOutage`: 3 requests in a row fail after all attempts, or 5 of the last 10 do.
- `BudgetExceeded`: the run's model spend reaches `budget.max_usd_per_run` (checked before each request).
- `CreditOrKey`: HTTP 401 or 402 (no retry).
- Each one **stops the run cleanly**: the current job gets no record (no folder move, no tracker write, no `job.md` note), its tab stays as it is, the report states the reason ("Stopped: provider outage — <route> <status>", "Stopped: spend cap $1.00 reached", "Stopped: check the key / add credit on OpenRouter"), and the exit code is 3. Queued jobs stay "Resume Built".

### T4 — Jev fallback backend (00_common D19)
- `decide.ChatDecider` answers the same typed questions with the primary LLM inference model. JSON output per question: the chosen option and a probability (choice), the probability of yes (noul), or the level and a probability (score).
- The Decider asks Jev first. When a Jev request fails after all attempts, the same questions go once to `ChatDecider`. Its calls are chat-model calls, so they go to `llm_inference_logs.json` (D4).
- The breaker counts a request as failed only when both fail. The report shows "Decisions by fallback: N".
- `decider.fallback = "none"` turns the fallback off.

### T5 — One Chrome connection per run (00_common D13)
- Find out how many CDP connections one run opens today (preflight, the `tabbook` helper, each job session). Record it in DISCOVERY.md.
- `run` opens **one** connection before its preflight and reuses it for the whole run. Before connecting, print: `Chrome will ask "Allow remote debugging?" — click Allow (waiting up to 180 s).`
- If the package cannot share one connection, keep the minimum number, document why, and leave the rest to P2.

### T6 — Report (same sections as v3; new rows only)
- Summary: totals of Jev requests and attempts, LLM calls, fallback decisions, cost (reported / estimated), and the highest number in flight.
- Timings: one row per job with time, Jev requests, LLM calls and cost.

### T7 — Tests
- Gateway, on a local fake HTTP server: the in-flight limit, the minimum interval, `Retry-After`, max attempts, no retry on other 4xx, timeouts, and logging of every attempt.
- Breaker: both trip rules; a clean stop leaves no records and exits with 3 (temp copies); `BudgetExceeded`; `CreditOrKey`.
- Fallback: `ChatDecider` parsing and validation; Jev fails → the fallback answers; both fail → one failure for the breaker.
- Config defaults; no `:free` model in the defaults.
- Only the Gateway sends: patch the HTTP client used by our modules and by the package; a send that bypasses the Gateway fails the test.
- `--live`, within the $0.50 session budget: the Jev contract test (T1); one LLM inference call per model on the `f02` fixture page.

## Acceptance criteria
- A1 The offline suite passes; the `--live` contract tests pass.
- A2 No model request bypasses the Gateway.
- A3 The live gate passes.
- A4 Docs updated: build spec v3.2; `LIVE_TEST.md` describes the one-click connection and the user setup below; `recore/HANDOVER.md` written.

## User setup (before the live gate)
- Add credit on OpenRouter (≥ $5) and set `OPENROUTER_API_KEY` in `.env`.
- Chrome → Settings → Performance → Memory Saver → "Always keep these sites active": add `linkedin.com`, so that Chrome does not discard a parked tab.

## Live gate (the user runs)
1. `python -m assistant preflight` → one "Allow" click; it passes.
2. `python -m assistant run --no-record --limit 3`.
3. Check:
   - one "Allow" click for the whole run;
   - `report.md` Summary: highest number in flight = 1; cost < $1;
   - the logs show no request with more than 3 attempts, and no `:free` model in `llm_inference_logs.json`;
   - if a provider failed, the run stopped with a clear reason and the job was left untouched.

The jobs do not have to park in P1.
