# Re-core HANDOVER — end of P2

- **Phase:** P2 — owned browser driver, absolute never-submit guard (v2), deterministic Easy Apply navigation.
- **Date:** 2026-09-29
- **Branch:** `recore/p2-driver-guard-navigation` (off `main`; P1 is already in main).
- **Build spec:** v4.0.
- **State:** **complete.** The offline suite is green (371+ passed, 0 failed) and the user **confirmed the live gate
  on 2026-09-29** (`runs/20260929-222722`). The branch is ready to merge into `main`.

## What changed

**Owned driver `assistant/driver/`** (T2), ported from the vendored browser package (0.1.5+aa6, MIT; the notice is
kept in `assistant/driver/LICENSE`) and trimmed:
- `cdp.py` — CDP transport; a hung command raises `DriverTimeout` (a `CdpError` subclass; the `os._exit(3)` path is
  gone), `attach_chrome` turns on `Target.setDiscoverTargets` and captures `targetCreated` openers.
- `observe.py` / `observer.js` (helper version 13) — the aa2–aa6 observer with **no 160/300-char cut** and new
  element fields: `type`, `tag`, `required`, `maxlength`, `placeholder`, `form`, `dialog`, `consent`, `scope`,
  plus a `descriptor(ref)` for the live press-path guard.
- `session.py` — one guarded mouse-press path (`_press` runs the injected `refuse_click` on the live descriptor
  before any click / toggle / upload / type focus-click); **no `keys` op and no `type…submit`** (the driver cannot
  send Enter/Escape); ops kept: click, type, select, toggle, upload, reload, wait_for_load, screenshot, eval; tabs
  are `Session` methods; `Settings` via constructor; one CDP connection per run (`BrowserManager.connect`);
  `forget()` drops a session without closing its tab; no atexit hook.

**`assistant/browser.py`** (`Browser`, T3) replaces `jev.py`/`Jev`: wraps the driver, runs `guard.check`, injects
`guard.never_click_element`, renders the driver's structured results into the text formats callers parse (build
spec §3.4 + the new fields), and logs to `browser_actions.jsonl`. `Table`/`Element`/`Option`/`split_json` moved
here. **Only `browser.py` imports `assistant.driver`** (static test in `test_guard.py`).

**Guard v2** (`assistant/guard.py`, T4): `never_click_element(el, page)` is absolute (00_common §4.1) — the full
refused-label set (submit/send/confirm/done/finish/complete), apply once `FORM.started`, structural submit unless
an allowlisted advance label off a non-final page, every click on a final page; the one cookie-consent exemption.
`looks_final(elements)` reads finality from the table (no circular refused-click). The label/apply/final rules are
scoped to click-role elements so a required "Confirm email" textbox stays fillable.

**`assistant/navigate.py`** (T5) replaces the three `browser_goal` calls with **no page-kind model call**: `enter`
decides the LinkedIn posting from the Easy Apply button, closed/applied text, an external Apply, a cookie banner,
or one last-resort Jev `choice`; `advance` clicks the allowlisted button and waits for the dialog (scoped by the
`dialog` field) to change. `fill.run_pages` is the deterministic Easy Apply loop; `fill_page` drops the page-goal
fallback (a required unsupported widget → `broken_form`).

**Rewired:** `cli.py` (one `Browser` per run, one connection, `browser_actions.jsonl`, no package load),
`tabs.py` (driver-native, `release`→`forget`), `pages.py`, `google_signin.py`. **Deleted:** `jev.py`,
`contract_check.py`, the goal prompts, `vendor/` (wheel + patch), `fake_mcp.py`, and obsolete tests; the package is
uninstalled and `websockets` is now a direct dep.

## Evidence

- **Offline suite (A1): 371 passed, 30 skipped (live), 0 failed** (`pytest -m "unit or browser"`).
- **Parity (T6):** `tests/test_parity.py` — the owned observer matches the T1 golden on all 38 fixtures.
- **Never-submit (T7):** `tests/test_tripwire.py` — the walk clicks every button (nothing sent); a direct submit
  click is refused by the driver press path; `tests/test_guard_v2.py` (17) covers the rule + the T5 label cases.
- **Navigation:** `tests/test_navigate.py` — Easy Apply on `4012345610-easy-dialog` opens the dialog and advances
  Contact → Resume → Questions → Review → final with **zero POSTs**.
- **A2:** `git grep` for the package name finds only `DISCOVERY.md` and `assistant/driver/LICENSE`.
- **A3:** no `browser_goal`, no text helper; `run --dry-run` prints the 7-job queue.
- **A4 — live gate PASSED (user-confirmed, 2026-09-29).** Five live iterations, each read from the run folder (all
  logged in DISCOVERY, "P2 live gate iteration 1–5"): the entry fix (`cli.process` no longer gates on the kev
  `judge().kind`), the "Job search safety reminder" click-through ("Continue applying", not "Dismiss"), the
  `FORM.started` reset on a broken_form re-entry, and the read-back deciding a verbatim field in code. Final run
  `runs/20260929-222722`: **one "Allow" click; Genesys + Mastercard → `external_ats` (Apply never clicked); Linda
  AI → Easy Apply → reminder passed → the Easy Apply form filled (phone typed and held) → advanced two steps by
  itself (`Next` ×2) → `broken_form "widget not supported yet: Bachelor's Degree Yes/No"`**. Never-submit airtight
  across every iteration: no submit/apply click ever returned ok, no POST, and `Next`/`Continue applying` are
  advance controls, not submits. Every job tab was left open; nothing was submitted.

## Known issues / open questions

- **P4 (widgets):** Linda AI stops at a `div role=radio` Yes/No question ("Bachelor's Degree") — `plan_fill`
  cannot set it with click/type/select/toggle/upload, so it is `broken_form "widget not supported yet"` (the
  planned P2 fallback). P4 adds the widget handlers; that is the last thing between Linda AI and an end-to-end park.
- **kev-0.8b is weak on the read-back** — it false-flagged a verbatim-correct phone field (`different` 0.3987 vs
  `holds` 0.2348). P2 works around it (`fill.mismatches` decides an exact value match in code, no model call), but
  the same weakness will bite the widget/select judgments; P3/P4 want a stronger or fine-tuned System One model.
- **Profile data (not code):** the profile phone is stored as `+39 351 935 8813`, which repeats the `+39` already in
  LinkedIn's country-code dropdown; LinkedIn may reject it at `Next`. Store the national number (`351 935 8813`).
  The program must never write `Profile.md` (§4.2), so this is the user's edit.
- **"Page is final" heuristic** (`guard.looks_final`): a design choice (submit-like present, no advance button),
  deterministic and table-only — not reached in the gate yet (Linda stops earlier at the widget).
- **Resume upload button type:** if LinkedIn's in-dialog "Upload resume" is a `<button>` with no `type` inside the
  form, the structural rule refuses it — not reached yet; watch for it once P4 gets past the questions step.
- `websockets` sync `connect()` prints a `DeprecationWarning` (used as in the vendored code); functional.

## Deviations from the phase file

- The goal-based navigation/fill tests could not be "ported" verbatim (the behaviour is gone); the equivalent
  coverage moved to `test_navigate.py` (real driver) + a rewritten `test_fill_loop.py` (fake driver). Recorded in
  DISCOVERY.
- `test_baseline_p0` (a P0 no-behaviour-change snapshot) and its golden were deleted: P2 intentionally rewrites the
  browser-call stream, so the pre-P2 baseline cannot hold.
- `test_clean_stop`'s P1 sticky-re-raise/real-Jev cleanup tests were dropped: the driver makes no model request on
  its own thread, so the P1 threading bug they guarded cannot occur in P2.

## Docs (§9) — done

- `application_assistant_build_spec.md` bumped to **v4.0** with a P2 changelog and §3 retitled to the owned driver;
  package name scrubbed.
- `README.md` and `CLAUDE.md` updated to the owned-driver setup (`websockets`, no wheel); package name scrubbed.
- `LIVE_TEST.md` has the P2 gate; `DISCOVERY.md` carries the full P2 record incl. the five live iterations.
- `git grep` for the package name → only `DISCOVERY.md` and `assistant/driver/LICENSE`.

## Merge

The branch is ready to merge into `main`. NB: this session was found on `main` once (a stray `git checkout main`);
before merging, confirm you are on `recore/p2-driver-guard-navigation` (`git branch --show-current`) at `f39e922`
or later.

## Commands the next session needs

```bash
# from Tools/Application_Assistant/
./run_kev_server.command                                    # System One route is "local": start it first
.venv/bin/python -m assistant preflight                     # LLM inference + System One + one Chrome connection
.venv/bin/python -m assistant run --no-record --limit 3     # the P2 live gate
.venv/bin/pytest -m "unit or browser" -q                    # the offline suite (371 pass)
```
