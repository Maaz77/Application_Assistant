# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## How to answer the user (required in every session)
If and only if in a turn you made some code edits read this section carefully. Otherwise, skip it.
End every turn's reply with these six headings, in this order, each kept short. This rule overrides any output style, and nothing may come after the last section:

```markdown
## What I understood?
A brief description of what I took the user's prompt to mean.
## How did I do it?
A very brief, high-level account of what I did this turn.
## What challenges I faced?
Briefly, what got in the way and how I got past it ("None." if nothing did).
## Is the task finished?
One line: finished as asked, or not, and what only the user can do (e.g. an API key is invalid or out of credit, or a card is needed on file).
## Do I need user decision?
The questions I need answered to finish, when there is more than one way to do it ("No." if none).
## How did I implement it?
Provide an informative compact explanation of the code edits you made, including the architecture decision you made, the algorithms you used, the libraries or packages you used. and any other relevant information. This section should be detailed enough for a developer to understand the changes you made and why you made them, but concise enough to be easily readable.
```

## What this is

Application Assistant v2 fills job applications in the user's own signed-in Chrome and **stops one click before submission**, then records the result. It must never submit an application. Build spec: `application_assistant_build_spec.md`. `DISCOVERY.md` is the running decision log: live-site findings, package behaviours (B1–B9) and user decisions, each dated. Read the relevant section before changing behaviour, and append new findings there.

This folder is its own git repo, nested inside the job-search repo, which ignores it. `config.toml`'s `paths.base = "../.."` points at that parent repo. The parent holds the user's real `Applications/`, `Pending-Review/`, `Needs-Attention/`, `Profile.md` and `Job_Tracker.numbers`. Tracker I/O reuses the sibling tool `../Reconcile/reconcile.py`.

## Commands

A plain venv is used, because Poetry's pyenv shim is broken on this Mac. The browser driver is owned in
`assistant/driver/` (P2), so there is no vendored wheel to install — only the runtime deps:

```bash
/opt/homebrew/bin/python3 -m venv .venv
.venv/bin/pip install websockets httpx "pydantic>=2" pypdf python-dotenv numbers-parser pytest
```

```bash
.venv/bin/python -m assistant preflight                  # key, System One, driver, tracker, Chrome, LinkedIn sign-in; writes nothing
.venv/bin/python -m assistant run --dry-run              # print the queue; no browser, no writes
.venv/bin/python -m assistant run --no-record --job URL  # fill and park one job; no tracker/folder/job.md writes
.venv/bin/python -m assistant run --limit 3              # a recorded run
.venv/bin/python -m assistant requeue [--job URL]        # Needs-Attention/ → Applications/, Status back to Resume Built
.venv/bin/python -m assistant capture URL                # read-only page snapshot into tests/captured/
.venv/bin/python -m assistant tripwire [--live]          # the never-submit test suite
```

Exit codes: 0 all parked, 1 preflight failed (nothing written), 2 some job needs attention, 3 run stopped (alarm, signed out, tracker changed on disk, folder clash, hung browser call, or one of the two clean stops: a provider outage or a key/credit failure).

Tests (markers are defined in `pyproject.toml`; there is no linter configured):

```bash
.venv/bin/pytest -m unit -q                                  # no browser, no network
.venv/bin/pytest -q                                          # + browser tests (throwaway headless Chrome, port 9223, local fixtures)
.venv/bin/pytest -q --live                                   # + live_model tests (real Kev + FreeLLMAPI; needs both servers up)
.venv/bin/pytest -q tests/test_fill_loop.py::test_name       # one test
```

`tests/test_model_access.py` has no `live_model` marker on purpose (the user removed it; do not restore it), so a plain `pytest` makes real calls through it — free now, against the local router, and it checks every configured model against the live catalogue. Deselect it with `--deselect tests/test_model_access.py` when that isn't wanted.

`preflight` requires **one** model to answer, not all of them (`_probe_llm_inference` returns `(answered, out)` and raises only when none answers). Free tiers go in and out of quota minute by minute, and a run only needs one model per page — that is what the rotation is for. Do not tighten this back to "every model".

Known unrelated flake: `tests/test_gateway.py::test_requests_start_at_least_the_minimum_interval_apart` fails under full-suite load when `time.sleep` returns ~0.2 ms early against its `>= 0.145` threshold. It passes in isolation and fails identically on the pre-P6 code.

## The model gateway

Every model request — `decide.Decider`, `decide.ChatDecider` and `llm_inference` — leaves through one `Gateway`
(`assistant/gateway.py`, installed per run by `gateway.use`). The owned browser driver (P2) makes no model request
at all: navigation is deterministic (`assistant/navigate.py`) and the only Jev calls are `decide`'s. Nothing else
may send: `gateway.required()` raises instead of falling back to an HTTP client, and `tests/test_no_bypass.py`
seals off `httpx.post` and `httpx.Client.post` to prove it. The Gateway owns the queue
(`[limits] max_in_flight`, `min_interval_s`), the **only** retry layer (`max_attempts`, on 429/5xx/timeout/no
connection, waiting `Retry-After` ≤ 30 s else 2 s then 6 s), the per-kind timeout (System One 20 s, or
`models.local.timeout` on the local route; chat 45 s), the counters the report prints, and the single call to
`inference_log`. Do not add a retry, a queue or a log line to a sender — they all belong here.

Both servers are local and free, and neither reports `usage.cost`, so **cost accounting was removed on 2026-10-07**: no `BudgetExceeded`, no `[budget]`, no `[prices]`, no `Counters.cost`, and `report.md` prints no money. A paid route would have to bring it back. `inference_log.gateway_of` tells the two loopback servers apart **by path** (`/chat/completions` → `freellmapi`, else `local`), or a chat failure and a Kev failure would both be labelled `local` in the outage and rejected-key messages.

Two conditions end a run instead of one job (`gateway.ProviderOutage/CreditOrKey`).
Each subclasses `blockers.StopRun`, which is what keeps it out of the `NeedsAttention` paths in `cli.py` and
`fill.py` — a `NeedsAttention` writes a record, and a stopped run must leave its job untouched. Every model send
now happens on the run's own thread (the driver has no sender thread), so a stop propagates directly; a
`DriverTimeout` (a hung CDP call) is caught in `run_pages` and raised as a `StopRun` (exit 3).

`decide.ChatDecider` (`[decider] fallback`) answers the same typed questions with the chat models when the System
One model fails; the breaker counts the pair as one request.

## Keys and models

Two servers on this Mac, and nothing off it (P6, 2026-10-07 — OpenRouter and Vercel AI Gateway are **removed**, not a fallback; see the dated DISCOVERY entry):

- **The chat models** (the LLM inference and the chat fallback) go to a **FreeLLMAPI** router ([github.com/tashfeenahmed/freellmapi](https://github.com/tashfeenahmed/freellmapi)) at `models.freellmapi.base_url` (`http://127.0.0.1:31415/v1`), one OpenAI-compatible `/v1` over the free tiers of ~34 providers. `config.chat_url` appends `/chat/completions`; `config.chat_key()` is `FREELLMAPI_KEY`, which is **required** — the router answers HTTP 401 without it. `models.freellmapi.llm_inference` is the list tried in turn (`rotation.Rotation`).
- **The System One decision model** is a [Kev](https://github.com/jaredpalmer/kev) server (`[models.local]`: `base_url`, `system_one_decision_model`, `state_chars`, `timeout`) serving `POST /v1/systemone`. It is now the **only** decision route, because `POST /v1/systemone` on the FreeLLMAPI router is HTTP 404. It needs no key unless it was started with `KEV_API_KEY`. The state goes as an object, not a JSON string (Kev renders objects as labeled text), so `keep_object_state` is always on, and preflight first reads `GET /v1/models` (`decide.server_card`).

`config.toml` is strict: unknown keys are errors, and a config still naming `chat_route`, `[models.openrouter]`, `[models.vercel]` or `system_one_decision_provider` is rejected with a message saying where its keys moved.

Two rules the provider imposes, both load-bearing:

- **Never configure the router's `auto`** (or a `:free` suffix). `auto` picks whichever free model is up and one that ignores `response_format` answers **prose at HTTP 200**; the code only drops to `json_object` on HTTP 400, so every page then fails as `ModelUnavailable("output invalid")`. Only concrete IDs whose `GET /v1/models` entry lists `response_format` honour the strict schema. `config.problems()` does not catch this — `test_config.py` and `test_model_access.py` do.
- **A quota belongs to a platform, not a model ID** (`_routed_via.platform`), so the rotation must spread over platforms; two IDs on one platform share a limit. Reasoning is switched off with `reasoning_effort = "none"` (`llm_inference.REASONING_EFFORT`), **not** OpenRouter's `reasoning: {"enabled": false}`, which this router ignores.

Never print keys: `browser_actions.jsonl` redacts the key, and the inference logs (`llm_inference_logs.json`, `jev_inference_logs.json`) never contain one.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).


