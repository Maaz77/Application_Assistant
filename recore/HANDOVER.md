# Re-core HANDOVER — end of P2

- **Phase:** P2 — owned browser driver, absolute never-submit guard (v2), deterministic Easy Apply navigation.
- **Date:** 2026-09-29
- **Branch:** `recore/p2-driver-guard-navigation` (off `main`; P1 is already in main).
- **Build spec:** v4.0 (in progress — §3 driver, §4 guard v2, §5.1 navigation; see "Docs" below).
- **State:** code complete, the **offline suite is green** (371 passed, 30 skipped/live, 0 failed). The live gate
  (A4) has **not** been run by the user yet.

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
- **A2:** `git grep` for the package literal finds only `DISCOVERY.md` and `assistant/driver/LICENSE` **once README.md and
  the build spec are scrubbed** (in progress — see below).
- **A3:** no `browser_goal`, no text helper; `run --dry-run` prints the 7-job queue.

## Known issues / open questions

- **The live gate (A4) has not been run.** The user runs `LIVE_TEST.md` § "P2 gate".
- **Queue reality:** of the 7 queued jobs, the P1 gate found Genesys + Mastercard carry "Apply on company website"
  (→ now `external_ats`). Confirm at least one **Easy Apply** job is queued or the gate shows only external_ats.
- **"Page is final" heuristic** (`guard.looks_final`): a design choice (submit-like present, no advance button),
  deterministic and table-only — **confirm at the live gate**.
- **Top-card scoping:** `navigate.enter` matches text/controls page-wide; a real posting has a "similar jobs"
  sidebar (its cards carry their own "Applied"/"Easy Apply" badges). Checking the Easy Apply *button* first covers
  the common case; confirm on a real posting.
- **Resume upload button type:** if LinkedIn's in-dialog "Upload resume" is a `<button>` with no `type` inside the
  form, the structural rule refuses it — watch item in the live gate.
- `websockets` sync `connect()` prints a `DeprecationWarning` (used as in the vendored code); functional.

## Deviations from the phase file

- The goal-based navigation/fill tests could not be "ported" verbatim (the behaviour is gone); the equivalent
  coverage moved to `test_navigate.py` (real driver) + a rewritten `test_fill_loop.py` (fake driver). Recorded in
  DISCOVERY.
- `test_baseline_p0` (a P0 no-behaviour-change snapshot) and its golden were deleted: P2 intentionally rewrites the
  browser-call stream, so the pre-P2 baseline cannot hold.
- `test_clean_stop`'s P1 sticky-re-raise/real-Jev cleanup tests were dropped: the driver makes no model request on
  its own thread, so the P1 threading bug they guarded cannot occur in P2.

## Docs still to finish (§9)

- Rewrite `application_assistant_build_spec.md` §3 (driver), §4 (guard v2), §5.1 (navigation) and bump to **v4.0**;
  scrub the package name (7 occurrences).
- Scrub `README.md` (4 occurrences; update setup to the owned driver + websockets).
- `LIVE_TEST.md`: add the P2 gate procedure.

## Commands the next session needs

```bash
# from Tools/Application_Assistant/
./run_kev_server.command                                    # System One route is "local": start it first
.venv/bin/python -m assistant preflight                     # LLM inference + System One + one Chrome connection
.venv/bin/python -m assistant run --no-record --limit 3     # the P2 live gate
.venv/bin/pytest -m "unit or browser" -q                    # the offline suite (371 pass)
```
