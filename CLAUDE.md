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

A plain venv is used, because Poetry's pyenv shim is broken on this Mac. The browser package comes from the patched wheel in `vendor/`:

```bash
/opt/homebrew/bin/python3 -m venv .venv
.venv/bin/pip install vendor/jev_ultrafast_mcp-0.1.5+aa6-py3-none-any.whl httpx "pydantic>=2" pypdf python-dotenv numbers-parser pytest
```

```bash
.venv/bin/python -m assistant preflight                  # key, Jev, package, tracker, Chrome, LinkedIn sign-in; writes nothing
.venv/bin/python -m assistant run --dry-run              # print the queue; no browser, no writes
.venv/bin/python -m assistant run --no-record --job URL  # fill and park one job; no tracker/folder/job.md writes
.venv/bin/python -m assistant run --limit 3              # a recorded run
.venv/bin/python -m assistant requeue [--job URL]        # Needs-Attention/ → Applications/, Status back to Resume Built
.venv/bin/python -m assistant capture URL                # read-only page snapshot into tests/captured/
.venv/bin/python -m assistant tripwire [--live]          # the never-submit test suite
.venv/bin/python -m assistant.contract_check             # package browser_* signatures vs what jev.py expects
```

Exit codes: 0 all parked, 1 preflight failed (nothing written), 2 some job needs attention, 3 run stopped (alarm, signed out, tracker changed on disk, folder clash, hung browser call).

Tests (markers are defined in `pyproject.toml`; there is no linter configured):

```bash
.venv/bin/pytest -m unit -q                                  # no browser, no network
.venv/bin/pytest -q                                          # + browser tests (throwaway headless Chrome, port 9223, local fixtures)
.venv/bin/pytest -q --live                                   # + live_model tests (real Jev / OpenRouter; needs .env)
.venv/bin/pytest -q tests/test_fill_loop.py::test_name       # one test
```

`tests/test_model_access.py` has no `live_model` marker on purpose (the user removed it; do not restore it), so a plain `pytest` makes real OpenRouter calls through it. Deselect it with `--deselect tests/test_model_access.py` when that isn't wanted.

## Keys and models

`.env` (git-ignored) holds `OPENROUTER_API_KEY` and `AI_GATEWAY_API_KEY` (Vercel AI Gateway); which one each model call uses follows `models.chat_route` and `models.jev_route`. `config.toml` is strict: unknown keys are errors. `models.chat_route` (`openrouter` or `vercel`) picks who serves the chat models, and with it the `[models.openrouter]` or `[models.vercel]` table, the key (`config.chat_key`) and the URL (`config.chat_url`); `cfg.models.answer_engine`/`text_helper` are properties that read the active table. Each is a list of models tried in turn (`rotation.Rotation`; the text helper's rotation is a wrap of the package's `policy.text_for` installed by `jev.load()`). Each route names Jev differently: `typesafe-ai/jev` on Vercel AI Gateway, `typesafe/jev-1.13` on OpenRouter. Never print keys: `calls.jsonl` redacts the OpenRouter key, and decisions are logged without keys.

## Architecture

**One run:** `cli.run`
→ preflight
→ `records.build_queue` (the folders in `Applications/` joined to tracker rows at "Resume Built" by `tracker.job_id`)
→ per job, `cli.process`:
  - it opens the LinkedIn posting and checks its state with `pages.classify_entry`;
  - then `fill.run_pages` takes over;
→ `pages.gate` on the last step
→ parked, or `NeedsAttention`
→ `records.Recorder.record` (a `job.md` note, then the tracker row and the folder move together, journaled so `records.recover` can finish an interrupted record)
→ `report.md`.

**`fill.run_pages` is a two-stage loop:**
- **Navigate:** the package's goal agent (`browser_goal`, one action per goal) gets from the posting to the form, past cookie banners, pop-ups, job pages and start dialogs.
- **Form:** each step is filled, then the agent clicks Next.

The never-submit rule (`guard.never_click`) refuses "Submit" and "Send" everywhere and "Apply" once `fill_page` has started (`guard.FORM.started`, reset per job in `run_pages`). The package applies it to every click through `jev.guard_clicks`, which replaces its `confirm_reason`. A refused click comes back as `needs_confirmation`: in the form it means the last step; in navigation a refused Submit on a page with fields means the form is here. Before filling, "Apply" / "Easy Apply" is the agent's own click.

**Three kinds of model calls:**
- **The answer engine** (`answers.py`): one OpenRouter chat call per form page. It gets the page's questions plus `Profile.md`, `job.md` and the resume text. `check_answers` then verifies every answer deterministically (the quote is in a source file, the option is on the page, a computed total adds up); an answer that fails is dropped, never guessed. The code carries out Jev's fill plan with direct `browser_act` ops (toggle, select, type, with the answer engine's exact text); a page goal handles only custom widgets.
- **Jev page decisions** (`decide.py` + `pages.judge`): one judgment per `Page` snapshot, cached on the Page, sent as System One requests of `decide.BATCH` (4) questions, 4 in flight (TypeSafe fails a whole request when one question fails, so big requests rarely get through). It answers every judgment: the page kind, covered, still loading, the application's fields, the resume upload, validation messages, the Submit button, Google steps. `answers.judge_answers` and `judge_questions` ask Jev about the questions themselves. Filling is Jev's too: `fill.plan_fill` asks, per answer, how it goes in (type/select/check/widget), which field and which option, and the code only checks the pick can be carried out; `fill.mismatches` (the read-back) asks Jev whether each field now "holds" its answer; an unsure resume pick is asked again narrowly (`decide.narrow`), then settled by the answer engine's ref. Thresholds live in `decide.THRESHOLDS`. A `DecisionError` sends the job to Needs Attention (class `decision`); there is no rule-based fallback.
- **The browser agent:** the package's goal agent; Jev picks its actions, and a chat "text helper" writes the typed values. It is configured through the environment by `jev.env_values`/`agent_route` and uses the same route as `decide.py`.

**`jev.py` is the only door to the browser package** (`jev_ultrafast_mcp`, called in-process, no MCP client):
- `apply_env()` must run before `load()`, because the package reads its config once, at import.
- Calls run one at a time on a single worker thread. A call that hangs past its timeout writes the report and does `os._exit(3)`.
- Every `browser_act` op passes `guard.check` first.
- Everything the package returns passes `clean_text` (lone UTF-16 surrogates from JS-cut page text), and `load()` wraps two package internals: `policy.text_for` (model rotation) and `policy._post` (clean request bodies). `contract_check` verifies both hooks still apply.
- `Jev.goal` asks again, after each `decide.RETRY_WAITS` step, when the decision model was unavailable before any step, and then raises `DecisionError`.
- Eval results are capped at 200 characters, so every read-only probe in `probes.py` trims itself to 190 and reports counts.

**Tabs** (`tabs.TabBook`): Chrome runs in `chrome://inspect` remote-debugging mode, which has no `/json/list`. The package lists tabs by 8-character handles, but switching and closing need full target IDs, which a helper session learns from its `NEW TAB` notices. The package's exit hook closes each session's current tab, so `release()` moves the session to a scratch tab first; the job's tab stays open for the user.

## Safety invariants (enforced by tests; keep them)

- Only `jev.py` imports the package or calls `browser_act` (AST tests in `test_jev.py` and `test_guard.py`).
- Nothing sends `confirm: true` (`guard.check` rejects it; `test_nothing_sends_confirm`).
- The never-submit rule is `guard.never_click`, and it is deliberately narrow (user decision 2026-09-24): "Submit" and "Send" always, "Apply" once the form is being filled, no model call. "Done", "Finish", "Confirm" and Enter-submitting forms are outside it. Don't widen it, or replace it with per-click model calls, without the user: they chose this to save API usage. `contract_check` fails if the package stops calling `confirm_reason` before a click.
- Our own ops: `eval` only runs `probes.py` constants (model text never becomes JavaScript), and an upload never targets a Submit/Send/Apply control.
- The confirmation-text tripwire (`pages.ALARM_RE`) stays beside Jev's "submitted" answer. An alarm stops the run with exit 3.
- Probes must stay read-only (checked by `test_probes_are_read_only`).

## Tests: how they stand in for the real world

- `tests/fake_mcp.py` (`FakeMCP`, `FakeBook`) fakes the package at the text level, so the real guard, parsers and loop run unchanged. Its `_navigate` simulates the agent. A confirmed click on an element with `submits=True` is recorded in `fake.sent`, which must stay empty.
- `tests/rule_decider.py` answers Jev's questions offline with the old regex rules. The autouse fixture `decider` in `conftest.py` installs it, or the real Jev for `live_model` tests. The program itself never imports it. A test can pin a page's kind by URL (`RuleDecider(kinds=...)`).
- `tests/captured/` holds real page snapshots (`observe.txt`, plus `probes.txt` for the pages from the failed run `run-20260923-*`). `test_decisions_live.py` checks Jev's judgments against them.
- Browser tests serve `tests/fixtures/` through `FixtureServer`, and assert that the server received no POST.

## Working on live sites

- Live runs act in the user's real Chrome and on their real files: prefer `--no-record`, never write `Profile.md`, and never submit.
- Chrome asks the user to **Allow** each new remote-debugging connection. Preflight waits up to 180 s for that click, and only the user can make it.
- Debug a run from `runs/<time>/`:
  - `calls.jsonl`: every `browser_*` call with its result;
  - `decisions.jsonl`: every Jev question and answer;
  - `answers/`: every answer with its source and quote;
  - `report.md`.
- New page cases go into `tests/captured/` (with `capture`), and the finding into `DISCOVERY.md`.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
