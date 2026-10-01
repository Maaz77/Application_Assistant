# Re-core HANDOVER — end of P3

- **Phase:** P3 — Replay harness, facts in code, lean Jev, new LLM inference, park at unanswered question.
- **Date:** 2026-10-01
- **Branch:** `recore/p3-decisions-and-llm-inference` (off `main`; P2 is already in main).
- **Build spec:** v4.0 (not yet bumped to v4.1 — A4 outstanding).
- **PR:** https://github.com/Maaz77/Application_Assistant/pull/1
- **State:** **code complete, live gate not yet passed.** The offline suite covers every T1–T6 change. The live run
  (`runs/20261001-214422`, `--limit 3`) reached the Easy Apply fill stage on Linda AI (page 5, 9 LLM inference
  calls, $0.006) but stopped at `broken_form` — the same `div role=radio` widget limitation from P2. No job was
  parked end-to-end (A3 not met). The two other jobs (Genesys, Mastercard) were `external_ats` and never entered
  Easy Apply.

## What changed

**T1 — Replay harness** (`tests/replay/`, `061f19d`): offline re-run of recorded jobs from captured
`browser_actions.jsonl` and inference logs. `ReplayBrowser` replays observe/act calls in order; `ReplayGateway`
matches model requests by content (Jev: `State` + questions; LLM: `messages` + `parameters`). Modes: `strict`
(missing = fail) and `live` (missing = real API, save response). The P2 gate run (`linda-ai-p2`) is the first
fixture. `cli.py` gained `replay` subcommand.

**T2 — Facts in code** (`assistant/pages.py`, `af638bc`): `judge()` is fully deterministic — zero Jev calls for
page classification. Moved to code: application fields (by `scope`), required (element field + `REQUIRED_EMPTY`
probe), placeholder detection, read-back (value comparison, whitespace/case normalized), settle (two equal observe
hashes 300 ms apart), signed-out (URL rules), closed/applied (LinkedIn texts), captcha (`CAPTCHA_PRESENT` probe),
final step (refused submit + no advance button), resume/cover-letter input detection.

**T3 — Lean Jev** (`assistant/fill.py`, `assistant/decide.py`, `1b5f862` + `f4153d1`): Jev reserved for residual
ambiguity only — `must_not_generate`, total-vs-specific years, option mapping (when answer ≠ visible option),
final-step confirmation (`noul ≥ 0.5`), `kind` on non-LinkedIn pages, and resume/cover-letter when code finds
zero or several candidates. Cache by page-state hash (`sha256(URL + control signatures + page-text hash)`); same
hash reuses judgment. Budget: `jev.max_requests_per_job = 40` (Jev + fallback together); above → Needs Attention
`decision_budget`. Cache key uses full question body (fix: `f4153d1`); digit-suffix guard at 7+ digits prevents
phone/zip collisions.

**T4 — New LLM inference** (`assistant/llm_inference.py`, `6db39c7`): `extract_questions(page)` (code) builds
structured question list from the element table — `id`, `question`, `kind`, `options`, `required`,
`current_value`, `maxlength`. The model receives pre-structured questions and returns only answers:
`{"answers": [{"id", "answer", "source", "quote", "relies_on"}]}`. Prompt rewritten (`prompts/llm_inference.md`)
keeping every rule of build spec §7.3. One call per dialog step, plus at most one regeneration for `generated`
answers. All §7.4 checks retained (quotes, choices, generated, computed recompute, pre-fill, keep-if-silent).

**T5 — Park at unanswered required question** (`assistant/fill.py`, `assistant/records.py`, `4f5f18f`): if a
required field has no valid answer and no kept value after checks, every other field is filled, the gate runs
without `REQUIRED_EMPTY` for those fields, the screenshot is taken, and the job is recorded as Pending Review with
Notes `Answer before you submit: <q1>; <q2>`. The report lists the questions under "Questions for your Scratch Pad".

**T6 — Tests** (`tests/test_answers.py`, `tests/test_fill_loop.py`, `tests/test_records.py`, `dd9ae07`): extraction
logic on fixtures (labels, groups, options, required), park-at-question end-to-end with temp records, request-count
assertions.

**Answers.json schema header** (`assistant/fill.py`, `d1565cf`): `_ANSWERS_SCHEMA` dict inserted as first element
when creating a new `answers.json`, documenting every field. Updated `prompts/llm_inference.md` to the P3 schema
(code extracts questions, model answers). Removed stale duplicate at a bogus nested path.

## Evidence

- **Offline suite (A1):** unit and browser tests cover T1–T6 changes (user killed the run before completion in
  this session; re-run needed for final count).
- **Replay (T1):** `tests/test_replay.py` — the P2 gate fixture (`linda-ai-p2`) replays in `strict` mode with
  zero network calls.
- **Facts in code (T2):** `tests/test_pages_unit.py` — deterministic `judge()` on fixtures.
- **Lean Jev (T3):** `tests/test_fill_loop.py` — cache reuse, budget stop, fallback.
- **New LLM inference (T4):** `tests/test_answers.py` — extraction, schema compliance, check_answers.
- **Park at question (T5):** `tests/test_records.py`, `tests/test_fill_loop.py` — end-to-end park-at-question.
- **A2 (request counts):** not yet verified on live run (A3 blocks it).
- **A3 — live gate NOT YET PASSED.** Run `runs/20261001-214422` (`--limit 3`): Genesys and Mastercard →
  `external_ats` (never entered Easy Apply). Linda AI → Easy Apply → filled 5 pages (1 System One, 7 LLM
  inference, 9 attempts, 0 failures, $0.0058) → `broken_form` on three radio-button questions: "Have you completed
  the following level of education: Bachelor's Degree?", "Are you comfortable working in an onsite setting?",
  "Are you legally authorized to work in Ireland?" — the same `div role=radio` widget limitation from P2. **The P3
  code changes worked correctly:** questions were extracted by code, model answered them, the cache hit, the budget
  held. The blocker is the P4 widget handler, not P3 logic.
- **A4 (docs):** build spec not yet bumped to v4.1. HANDOVER.md written (this file).

## Known issues / open questions

- **P4 (widgets) still blocks the live gate.** Linda AI's `div role=radio` Yes/No questions cannot be set with
  click/type/select/toggle/upload. P4 adds the widget handlers; that is the last thing between Linda AI and an
  end-to-end park. This is the same blocker as P2.
- **"Select one" question (Linda AI page 2):** the model returned `answer: null` with `note: "pre-fill not kept"`.
  This is a question whose options are not known (the report lists it under "Questions for your Scratch Pad").
  May need a Scratch Pad entry or investigation of the actual options.
- **"Are you comfortable working in an onsite setting?":** also listed under questions for the Scratch Pad — the
  profile does not state a preference. Needs a Scratch Pad entry.
- **Build spec v4.1 not written.** §6 (page decisions) and §7 (LLM inference) need rewriting to match the P3
  code (extract_questions, structured input, lean Jev). A4 outstanding.
- **Stale `prompts/llm_inference.md` was fixed in `d1565cf`** — the correct P3 prompt is now at the project-level
  path. The bogus nested-path duplicate (`Users/maaz/.../prompts/llm_inference.md`) was removed.
- **`_run/` subfolder** in run directories holds run-level (non-job) inference logs and browser actions: preflight
  Jev calls, queue navigation, tab cleanup. By design (`inference_log.py` scope fallback).

## Deviations from the phase file

- **A3 not met:** the live gate requires "at least one Easy Apply job parked at the final step." Linda AI reached
  page 5 but stopped at `broken_form` (radio widgets). The P3 logic (extraction, answering, caching, budget)
  worked correctly; the failure is the P4 widget limitation, not a P3 regression.
- **Build spec v4.1 not written** (A4 partial): HANDOVER written, spec bump deferred.
- **Test counts not verified on live run** (A2 partial): the live run did not produce a parked job, so per-page
  request counts cannot be validated against the A3 gate.

## Merge

The branch is **not yet ready to merge** — A3 (live gate) is not passed. The code changes are complete and the
offline suite covers them, but the live gate needs Easy Apply jobs that don't hit the P4 radio-widget blocker.
Options:
1. Find an Easy Apply job without radio-button questions and re-run the live gate.
2. Accept that P3 + P4 together will pass the gate, merge P3 now, and gate P4 instead.

## Commands the next session needs

```bash
# from Tools/Application_Assistant/
./run_kev_server.command                                    # System One route is "local": start it first
.venv/bin/python -m assistant preflight                     # LLM inference + System One + one Chrome connection
.venv/bin/python -m assistant run --no-record --limit 3     # the P3 live gate (pick Easy Apply jobs)
.venv/bin/pytest -m "unit or browser" -q                    # the offline suite
.venv/bin/pytest tests/test_replay.py -q                    # replay harness on P2 fixtures
```
