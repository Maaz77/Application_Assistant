# Re-core HANDOVER — end of P1 (code complete, live gate not yet run)

- **Phase:** P1 — paid routes, one model gateway, circuit breaker, spend cap, one Chrome connection per run.
- **Date:** 2026-09-29 (the work and the DISCOVERY entries are dated 2026-09-28/29)
- **Branch:** `recore/p1-infrastructure`
- **Build spec:** v3.2
- **State:** every task implemented, the offline suite green. The user has **not** yet run the live gate.

The phase files still live in `~/Downloads/` (`00_common.md`, `P0_…` … `P5_…`); `recore/` holds only this file.

## The three user decisions that shaped this phase

P1's text assumes paid OpenRouter Jev (D14) and a `--live` contract test. Asked before any code was written, the
user chose otherwise, and everything below follows from that:

1. **System One stays `local`** (a Kev server on this Mac), not paid OpenRouter Jev.
2. **The chat route stays `vercel`** (`mistral/mistral-small`, `mistral/mistral-nemo`), not P1's
   `deepseek/deepseek-v4.1-flash` + `openai/gpt-5.4-mini`. The paid OpenRouter models are configured but are not the
   default, and no `:free` model remains anywhere (T7).
3. **No test may reach a real API.** "Use vercel for any LLM text inference completion. The tests and fixtures must
   not hit the real api. … manage it so that it is best economic-wise."

Consequence, stated to the user before starting and repeated here: **A3 (the live gate's three jobs) cannot be met
this phase.** kev-0.8b answers `kind="other"` at 0.12 on a real LinkedIn posting (P0 finding), so no job passes the
entry decision, and the chat fallback does not rescue it — Kev answers *badly*, not *failingly*, and the fallback
fires only on a failure. P1 itself says "the jobs do not have to park in P1", so the gate is reduced to what T2–T6
can show (see the live gate below).

## What changed

**New module `assistant/gateway.py`** (T2). `Gateway.send(kind, url, body, headers, …)` is the only way a model
request leaves the program. It owns the queue (`limits.max_in_flight = 1`, `limits.min_interval_s = 0.25`), the
single retry layer (`limits.max_attempts = 3`, on 429/529/5xx/timeout/no connection; `Retry-After` ≤ 30 s else 2 s
then 6 s), the per-kind timeout (System One 20 s, or `models.local.timeout` on the local route; chat 45 s), the cost
accounting, the counters, the single `inference_log` call, and the three clean stops. `gateway.required()` raises
rather than fall back to an HTTP client; `gateway.private(post=…)` gives a test-injected sender a Gateway of its own
so nothing has a way around one.

**Senders rewired.**
- `decide.Decider`: `BATCH`, `PARALLEL`, the `ThreadPoolExecutor`, `RETRY_WAITS`, `LOCAL_RETRY_WAITS` and `TIMEOUT`
  are deleted. A judgment is one request, split only above `jev.max_questions_per_request = 24` and sent in parts
  one after another.
- `llm_inference._ask_model`: sends through the Gateway; its own `_httpx_post`, `_retry_after` and `RETRY_AFTER_MAX`
  are deleted. `LLM_INFERENCE_TIMEOUT` 120 → 45 s. On the OpenRouter route the body gains
  `provider: {require_parameters: true}`.
- `jev.clean_requests`: the package's `policy._post` is answered from the Gateway and **never calls the package's
  own sender**, so its internal `range(3)` and its fixed 30 s `policy.CLIENT` are off the send path.
  `jev.set_call_timeout` was deleted with them. Failures are re-raised as the package's own `TurboUnavailable` with
  the message strings `TRANSIENT_GOAL_RE` matches.
- `jev.Jev.goal`: one retry (`GOAL_RETRY_WAIT = 2 s`) instead of the five-wait ladder.
- **Logging happens once**, in the Gateway, classified by body shape (`state`+`questions` = System One,
  `messages` = chat). Every sender's own `log_jev`/`log_llm` call was removed; leaving them would have double-logged.

**Clean stops** (T3). `ProviderOutage` (3 in a row, or 5 of the last 10), `BudgetExceeded`
(`budget.max_usd_per_run`, checked before each request) and `CreditOrKey` (401/402/**403**, never retried) all
subclass `blockers.StopRun`, so `cli.run`'s existing handler gives exit 3, a report reason, and **no record** for
the current job. `gateway.tripped` is sticky and `Jev.call` re-raises it on the run's thread, because a stop raised
inside the package's worker thread would be swallowed by that method's broad `except Exception`. A stop during
preflight is a stop (exit 3), not a preflight failure (exit 1).

**Chat fallback** (T4). `decide.ChatDecider` answers the same typed questions with the chat route's models and
returns the shapes `Answer.parse` already reads. The Decider asks System One first and falls back once. Both the
System One send and the fallback send defer their verdict, and the Decider reports **one** verdict for the pair.

**One Chrome connection** (T5). `jev.connect_chrome(cfg, 180)` + `cli.connect_once`, called before preflight.
`contract_check.connection_differences` checks the four package internals it reaches for (`MANAGER._cdp`,
`cfg.attach_data_dirs`, `attach_chrome`'s parameters, and that `BrowserManager.cdp` still sets its own
`open_timeout`).

**Preflight** (T1). It now probes **each** configured LLM inference model, one at a time, and names the one that
failed. A 401/402/403 during preflight is a `PreflightError` naming the key variable (`config.KEY_NAMES`) and exit 1
— "nothing written" is the accurate outcome there, and it is what T1 asks for; the same condition during the job
loop is a `CreditOrKey` stop with exit 3 (T3).

**Report** (T6). Summary gains the model-request totals, the spend (reported/estimated), the highest number in
flight and `Decisions by fallback: N`; Timings gains a per-job breakdown.

**Config** (T1). New strict sections `[limits]`, `[budget]`, `[jev]`, `[decider]`, `[prices]`.

## Four defects found before the gate

1. **The sticky re-raise fired during cleanup.** `Jev.call` asked the Gateway for a stop after *every* browser call,
   including the tab cleanup that runs after one. `StopRun` is not a `RuntimeError`, so it escaped the `except`
   clauses in `cli.process`'s and `cli.run`'s `finally` blocks, `report.write()` never ran, and the run ended in a
   traceback with exit 1 instead of a report and exit 3 — failing T3 exactly where it matters. `Jev.call` now notes
   `gateway.tripped` before the call and re-raises only a stop that this call caused. Covered by three tests in
   `test_clean_stop.py`, verified by reintroducing the bug.
2. **The live fixture installed no Gateway.** `tests/conftest.py`'s `decider` fixture built a `Decider` without one,
   so the first `ask` in any `--live` test (and `tripwire --live`) would have raised "may not bypass". It now
   installs `gateway.for_config(cfg)` for a live test and none for an offline one — a sender with no Gateway refuses
   rather than reaching a provider, which is what keeps the offline suite off the network.
3. **The chat rotation fed the breaker per model, not per request** — the same defect as (1) in T4's terms, found by
   a second review. On the shipped settings it trips a false outage: Vercel allows 5 requests a minute per model, so
   a busy `mistral-small` with `mistral-nemo` answering gives failure, success, failure, success, which reaches
   "5 of the last 10" while every page was answered. `_ask_model` now defers and `call_engine` reports one verdict.
   Verified by reintroducing the bug. **The package's own text-helper rotation has the same shape and is not fixed**:
   our wrapper sees individual `_post` calls and cannot group them. P2 removes the package from the send path.
4. `Gateway.release()` was dead code: `send` frees the queue slot before returning, so the chat fallback's send was
   never nested and could not self-deadlock. Deleted; the test that proved it stayed, because a regression there
   would hang rather than fail.
5. `llm_inference` still carried an `httpx` sender no longer on any path. A static test now forbids `httpx.post` in
   any model-sending module, so a second path cannot be reintroduced quietly.

## Evidence

**Offline:** 331 unit + 50 browser pass; `contract_check` clean; `run --dry-run` unchanged (7 jobs);
`test_baseline_p0` green after the one intended golden change (`provider.require_parameters`).

**New tests, 62 functions in four files:** `tests/test_gateway.py` (30, against a real local `http.server`),
`tests/test_fallback.py` (16), `tests/test_clean_stop.py` (9 — one parametrized over the three stops, three over the
cleanup path with a real `Jev` and `TabBook`, all on temp copies of the tracker and the job folders),
`tests/test_no_bypass.py` (7, both HTTP clients sealed off plus a static check), plus five P1 config-default tests
and three preflight-probe tests in `tests/test_config.py` / `tests/test_cli.py`.

**Changed assertions**, each required by P1 and recorded in `DISCOVERY.md`: `test_decide`'s batching and retry
ladders; `test_jev`'s goal retry; the 401 assertions in `test_answers` (now a clean stop, not one model's failure).
No guard, tripwire or records test was touched.

**Measured, not live:** worst case per goal step is now at most 6 HTTP requests (was ~18 — spec §13.1).

**No live numbers at all this phase.** Nothing here ran against a real provider or a real site.

## Known issues / open questions

- **kev-0.8b still cannot classify a real LinkedIn posting.** Unchanged from P0, and the reason no job can park.
  Needs a stronger or fine-tuned System One model (P3/P4). The logged `jev_inference_logs.json` entries are already
  in the shape `kev.train` takes.
- **Paid System One is unavailable on the account:** OpenRouter 402, Vercel 403.
- **P1 T1's single endpoint is only half-done.** On the `local` route both senders already share one URL
  (`models.local.base_url` + `/v1/systemone`), which is the task's goal. The OpenRouter value stays
  `https://openrouter.ai/api/alpha/decisions`; P1 asked for `api/v1/systemone`, but DISCOVERY 2026-09-28 measured
  that this is not the System One route, and the contract test that would settle it needs a real API.
- **Not measured, because no test may reach a real API:** whether large requests fail on OpenRouter (the
  `max_questions_per_request = 24` split is P1's number, justified only by the 2026-09-24 Vercel evidence), and the
  behaviour of `provider.require_parameters` when no provider qualifies.
- **`tests/test_model_access.py`** still has no `live_model` marker, by the earlier user decision recorded in
  `CLAUDE.md`. It is the one file a plain `pytest` spends money through. Left untouched; ask the user before
  changing it.

## Deviations from the phase file

1. **T1's defaults** — the user kept `local` + `vercel` instead of paid OpenRouter (above).
2. **T1's contract test and T7's `--live` items** — not implemented; no test may reach a real API.
3. **A1's "the `--live` contract tests pass"** — dropped, for the same reason.
4. **T3 covers 403 as well as 401/402** — Vercel answers 403 for an account with no card, which is the same
   condition and is not cured by retrying or by another model.
5. **T2's timeout for the local route** — `models.local.timeout` (120 s), not the 20 s P1 names for System One: a
   pass on an Apple GPU is seconds, not a data-centre's milliseconds.
6. **A key failure is exit 1 in preflight and exit 3 in the job loop.** T1 says it "fails preflight"; T3 says it
   stops the run. Both hold, split by where it happens.
7. **`ChatDecider` answers `noul` and `choice`, not `score`.** T4 names score answers, but nothing in the program
   builds a score question — `decide.noul` and `decide.choice` are the only builders, and `Answer.parse` reads only
   those two. Adding a third shape with no caller would be dead code; `inference_log`'s `Score` bucket already exists
   for the day one is sent.
8. **One bypass path remains in `jev.clean_requests`**, by choice: when no Gateway is installed it calls the
   package's own sender, which is what lets `tests/test_jev.py` drive the package directly. Every command installs a
   Gateway first (`run`, `preflight`, `capture`), and `tests/test_no_bypass.py` proves the gateway path is the one
   taken when one exists. P2 removes the package from the send path altogether.
9. **A model rotation hands over before it retries** — a chat request passes `attempts=1` while an untried model
   remains. P1 caps attempts per request; this keeps the cheaper behaviour the live 2026-09-24 evidence encodes,
   and is still one retry layer (another model is a different request).

## The live gate the user still has to run

`LIVE_TEST.md` § "P1 re-core gate" has it in full. In short: start `./run_kev_server.command`, then
`preflight` (one "Allow" click), then `run --no-record --limit 3`; check one "Allow" click for the whole run,
`Highest number of requests in flight: 1`, spend under $1, no request over 3 attempts, no `:free` model in the
logs, nothing submitted, and a clean untouched job if a provider failed. Every job ending in Needs Attention
`load_failure` is expected on these settings and still passes.

## Commands the next session needs

```bash
# from Tools/Application_Assistant/
./run_kev_server.command                                    # the System One route is "local": start it first
.venv/bin/python -m assistant preflight                     # LLM inference + System One + one Chrome connection
.venv/bin/python -m assistant run --no-record --limit 3     # the P1 gate
.venv/bin/pytest -m "unit or browser" -q                    # the offline suite — never a plain `pytest`
.venv/bin/python -m assistant.contract_check                # the four package hooks still apply
```

Next phase is **P2** (`P2_driver_guard_navigation.md`): an owned browser driver, the absolute never-submit guard,
deterministic Easy Apply navigation. Note for P2: the package's text helper still rotates models inside
`policy.text_for`, and that rotation does not know about `attempts=1`, so it gets the full ladder per model; P2
removes the package from the send path anyway.
