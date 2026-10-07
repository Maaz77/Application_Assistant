# Re-core HANDOVER — end of P5

- **Phase:** P5 — External ATS (Greenhouse, Ashby, Lever) by one general loop; no host adapters.
- **Date:** 2026-10-07
- **Branch:** `recore/p5-external-ats` (off `main`; P0–P4 are already in `main`).
- **Build spec:** v5.0 (bumped this phase; the P3→v4.1 and P4→v4.2 banners, deferred earlier, were also written now).
- **State:** **code complete, offline suite green; the live gate is the user's to run and has not been run yet**
  (invariant §4.4 — the agent never drives real sites). The offline suite, the tripwire (including each ATS's
  final button) and replay all pass; the end-to-end external hand-off and park are validated on local fixtures.

## Decisions taken this phase (by the user, 2026-10-07)

- **Gate:** ≥ 5 of 10 external Greenhouse/Ashby/Lever jobs parked (the phase file's proposal, kept).
- **No host adapters.** The user asked for a smarter, more general approach ("the LLM/KEV decides what to do on
  the page, the driver acts"). An Opus brainstorm (clean context) + a reviewer pass confirmed the generic
  pipeline already reads all three hosts, so P5 adds one thin loop, not three adapters. **Supersedes D12.**
- **Skip T4** (Greenhouse boards-API cross-check): DOM extraction already yields required markers.
- **Defer T2** (cross-origin frame attach): LinkedIn's external "Apply" opens the ATS's own hosted top-document
  form, read in full by the top-level observer; the `browser_open(src)` hop stays for a genuinely embedded form.

## What changed

**T6 — `requeue --class <cls>`** (`assistant/records.py`, `assistant/cli.py`, `8693334`). `requeue` gained an
optional class filter (CLI `--class external_ats`) so the live gate can move only the jobs an earlier run
recorded as `external_ats` back to the queue. `records.recorded_class(notes, job)` reads the class from the
tracker Notes line (authoritative, `NA_CLASS_RE`) or the `job.md` heading (fallback).

**T1 + T3 — external hand-off and the general loop** (`f224a03`). New signal `blockers.GoExternal`:
`navigate.enter` raises it on an external "Apply" (was `NeedsAttention("external_ats")`, D12); `cli.process`
catches it and runs `external.run_external`. New module `assistant/external.py`:
- `_hand_off` clicks the posting's external Apply and adopts the ATS tab — immediately, after a delayed
  `window.open`, or after a "You are leaving LinkedIn" interstitial's Continue (polled; never Cancel / "Continue
  with <provider>"), or a same-tab navigation to the form.
- the loop: `pages.read_page` → `pages.classify` (the previously-unused `Verdict` dispatcher, revived) → per
  verdict: `alarm`→`StopRun`; `google`/`google_wall`→`google_signin` (once/job); `blocker`→`fill._attempt2`
  (captcha wait-reload / signup guest link) or reload for `load_failure`; `iframe`→`browser.open(src)` hop;
  `form`/`final`→ `looks_like_application` then `fill.fill_page` (first pass) / page-advance (multi-step) /
  `pages.gate`+`fill._park`; `navigate`→ a safe pre-fill apply/advance click, else `unsupported_ats`.
- `run_external` mirrors `run_pages`' full exception mapping (`_Refill`, `DriverTimeout`→`StopRun`,
  `DriverError`→`attempts`, `ParkedAtQuestion`→park), because it is caught through `process()`'s
  `except GoExternal:` — anything escaping would crash the whole run.
- **Two safety predicates:** `external.looks_like_application(p)` (fill a real application — résumé upload, or
  name+email — not a lone job-alert box; closes the Mastercard mis-fill) gates fill-vs-`unsupported_ats`; the
  pre-fill apply click (`_safe_apply`) is allowed only with no form present and the control not a form's own and
  guard-permitted. They are separate, so the strict predicate is never load-bearing for never-submit.
- `pages.forward_submit` lets an apply-labelled control satisfy the gate (still never clicked); kept out of
  `_submit_like`/`looks_final` so a LinkedIn posting's top-card "Apply" is not read as `final_step`.
- `fill.park_at_question` factored out of `run_pages` for reuse.
- Driver: `Session.adopt()` now sets device metrics + focus emulation + `addScriptToEvaluateOnNewDocument`
  (an adopted background tab otherwise dropped typed values → `broken_form`).

**T7 + fixes + docs** (`513378c`, `1417912`).
- `pages._code_resume` deprioritises an `autofill|parse|populate` file input when a real one is present (Ashby
  lists "Autofill from resume" before "Resume").
- Fixtures: `tests/fixtures/ats/{toast_like,lever_like,alert_box}.html`,
  `tests/fixtures/jobs/view/4012345610-alertbox.html`, with golden tables in `tests/golden/tables/`.
- Tests: `test_external.py` (`looks_like_application`); `test_tripwire.py`
  `test_each_ats_final_button_is_refused_by_the_driver` (A1); `test_process_browser.py` external cases moved into
  the offline browser suite (deterministic now — `browser_goal` is gone) + an `unsupported_ats` job-alert-box
  case asserting zero clicks; `test_pages_unit.py` `forward_submit` and autofill-input picks; `test_records.py`
  `requeue --class`. The two `external_ats` tests were rewritten to assert `GoExternal` (phase-mandated, §4.5).
- A shared-Chrome test flake fixed (`1417912`): the external process test now excludes pre-existing `baseline`
  tabs from its "LinkedIn tab closed" check (an earlier browser test leaves that fixture tab open, by D13).
- Docs: `DISCOVERY.md` 2026-10-07 entry; `LIVE_TEST.md` P5 gate; build spec v5.0 (cumulative P3/P4/P5 banners,
  §13 update).

## Config / CLI

- CLI: `requeue --class <cls>`. No config schema change this phase.

## Evidence (offline; live gate pending)

- **Offline suite (A1):** `pytest -m "unit or browser"` → unit 337 pass; full browser suite 105 pass / 0 fail
  (`2026-10-07`, after the flake fix). Replay (`test_replay.py`) and the tripwire are included and pass.
- **Tripwire (A1):** `test_each_ats_final_button_is_refused_by_the_driver` proves the driver refuses
  Greenhouse/Lever "Submit application", Toast "Apply now!", and Ashby "Submit Application"; `fixture_server.posts()`
  is empty.
- **End-to-end external hand-off + park:** `test_process_browser.py` parks the three hand-off variants
  (immediate / delayed tab / leaving-dialog) on the generic ATS form, LinkedIn tab closed, one tab per job, no
  POST; the job-alert box ends `unsupported_ats` with zero clicks.
- **A2 (live gate — ≥ 5 of 10 parked):** NOT YET RUN. The agent does not drive real sites (§4.4); this is the
  user's step (`LIVE_TEST.md`, P5).

## Known issues / open questions

- **No Lever capture in the repo.** `ats/lever_like.html` is authored from Lever's known form shape; the live
  gate needs a real `python -m assistant capture <lever-url>` to confirm the extraction.
- **Ashby autofill-input fix** is verified against the 2026-09-23 capture + a fixture; confirm on a live Ashby.
- **T2 (embedded cross-origin Greenhouse)** is deferred — only the `browser_open(src)` hop handles it, and that
  fails if the embed `src` is trimmed by the `IFRAME_SRCS` ~170-char cap. Revisit only if a live run hits it.

## Deviations from the phase file

- **No host adapters (T3)** and **T2 deferred** — both user-approved (above); both flagged by the brainstorm as
  needing sign-off, and signed off via AskUserQuestion on 2026-10-07.
- **T4 skipped** (optional in the phase file), user-approved.

## Merge

Not yet mergeable: A2 (the live gate) is not passed. Code + offline suite are complete. After the user runs the
P5 gate and it passes (≥ 5 of 10 parked, zero submissions, one tab per job, correct classes for the rest), the
branch is ready to merge.

## Commands the next session needs

```bash
# from Tools/Application_Assistant/
./run_kev_server.command                                      # if the System One route is "local"
.venv/bin/python -m assistant preflight
.venv/bin/python -m assistant requeue --class external_ats    # T6: external jobs back to the queue
.venv/bin/python -m assistant run --no-record --limit 5       # dry pass over external jobs
.venv/bin/python -m assistant run --limit 10                  # the recorded P5 gate
.venv/bin/pytest -m "unit or browser" -q                      # the offline suite
.venv/bin/pytest tests/test_replay.py -q                      # replay harness
```
