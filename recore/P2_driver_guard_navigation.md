# P2 — Owned browser driver, guard v2, deterministic Easy Apply navigation

Read `recore/00_common.md` and `recore/HANDOVER.md` first.

## Goal
1. Replace the `jev-ultrafast-mcp` dependency with an owned driver in `assistant/driver/`, built from the package's CDP code.
2. Enforce the absolute never-submit rule in code (00_common §4.1).
3. Replace every use of `browser_goal` with deterministic navigation and advancing for LinkedIn Easy Apply (D12).

## Why the package's code and not Playwright
The program runs in the user's **real** Chrome profile (D13). Playwright's `connect_over_cdp` attaches to every open tab of the profile, and it can hang while an existing tab is asleep or discarded (Playwright issue #42882). The package's CDP code attaches only to the tabs the program opens, and it already works in the user's Chrome. It is MIT-licensed: move the needed parts into the repo, own them, and delete the rest.

## Keep / change / delete
Source: `vendor/jev_ultrafast_mcp-0.1.5+aa6-py3-none-any.whl` plus `vendor/*.patch`.
- **Keep (port):** the CDP connection and transport; target/tab management; the observer (element table with refs, page text, and the aa2–aa6 rules: `display:contents`, only the topmost modal `<dialog>`, opacity-0 native inputs, ARIA radio/checkbox state, file-chooser buttons); the op executor (click, type, select, toggle, upload, reload, wait_for_load, screenshot, eval of constant JS).
- **Change:**
  - no 200-character cap on eval results (keep a 256 KB sanity limit);
  - no 160-character cut of name, label or value in the table;
  - new fields per element: `required` (`required`, `aria-required="true"`, or a required marker in the label), `maxlength`, `placeholder`, `type`, `scope` (the id of the nearest `role=dialog` / `<dialog>` / `<form>`);
  - every CDP command has a timeout that raises `DriverTimeout` and keeps the process alive; delete the `os._exit(3)` path;
  - new tabs come from `Target.targetCreated`: a target whose `openerId` is the job tab was opened by the job; delete the `tabbook` helper tab;
  - no exit hook closes tabs; delete release and the scratch tabs;
  - settings come from `config.toml` through the constructor; delete `JEVMCP_*` and `apply_env`;
  - exactly one CDP connection per run (D13).
- **Delete:** the MCP server layer; `browser_goal` and its agent; the package's `policy` (the Jev and text-helper senders); confirm patterns; the hooks `rotate_text_helper`, `clean_requests` and `guard_clicks`; `contract_check.py`; `prompts/navigate_goal.md`, `next_step_goal.md` and `page_goal.md`; the vendored wheel and patch (in T6); result-prefix parsing. `JevError` → `DriverError`; the loop still maps it to `load_failure`.
- Keep the package's MIT notice in `assistant/driver/LICENSE`.

## Interface
- `assistant/browser.py` replaces `assistant/jev.py`; class `Browser` replaces `Jev`. Keep the call sites working (`open`, `observe`, `act`, `assert_`, the tab operations) with the op and table formats of build spec §3.4 plus the new element fields. Delete `goal` and the `call(name, …)` string dispatch. Only `browser.py` imports `assistant.driver`.
- `Browser` logs every call and its **full** result to `browser_actions.jsonl` in the active scope (P3's replay harness rebuilds pages from it).
- `guard.check(op, table)` still runs in `Browser.act` before any op reaches the driver. In addition, the driver refuses a click on an element that fails `guard.never_click_element` (defence in depth).
- `FakeMCP` becomes `FakeBrowser` at the same level (text and table), so the real guard, parsers, probes and loop run in the offline tests.

## Guard v2 (00_common §4.1)
`guard.never_click_element(el, page)` refuses when any of these holds:
- a label (name, aria-label, text, value) matches `\b(submit|send|confirm|done|finish|complete)\b`, case-insensitive;
- a label matches `\bapply\b` and `FORM.started` is set;
- structural submit (`type=submit`, a `<button>` without `type` inside a `<form>`, `<input type=image>`), unless the label matches `^(next|continue|continue to next step|review|review your application|save and continue)\b` **and** the page is not final;
- the page is judged final.

Exemption: the element is inside a cookie-consent container (`#onetrust-banner-sdk`, `#onetrust-consent-sdk`, `#CybotCookiebotDialog`, `#didomi-host`, `#usercentrics-root`, `#truste-consent-track`, `.qc-cmp2-container`, and LinkedIn's banner container if a user capture shows one), that container is outside any `<form>` with application fields and outside the application dialog, and the label does not match submit, send or apply.

The driver has no op that sends Enter, NumpadEnter or Escape; `type` never submits. The alarms of build spec §4.6 stay.

## Deterministic navigation (`assistant/navigate.py`)
LinkedIn job page:
- closed or applied: "No longer accepting applications", "Applied … ago", "Application submitted" → Needs Attention (C14);
- a button whose label starts with "Easy Apply" → click it; wait (≤ 8 s) for the Easy Apply dialog (a `role=dialog` that holds the application form) → stage "form";
- an apply control without "Easy Apply" (for example "Apply", or a link off LinkedIn) → **do not click**; Needs Attention, class `external_ats`, "external ATS, not yet supported";
- a cookie banner → click its reject/decline control ("Reject", "Decline", "Reject all", "Only necessary");
- none of these → one Jev `choice` over the visible, guard-allowed buttons ("Which control starts the application?" + `none`); `none` or confidence < 0.6 → Needs Attention `navigation`.

Easy Apply dialog, advance:
- click the dialog button whose label matches the advance allowlist;
- wait (≤ 8 s) until the dialog changes: the step heading, the progress value, or the field set;
- an inline error (`[role=alert]`, `.artdeco-inline-feedback--error`) or no change → `stuck` → the existing `broken_form` attempt;
- no allowlisted button and a refused "Submit application" → final step → gate → park.

Tabs: a tab opened by the job tab while on LinkedIn is junk → close it. Never close the job tab. Never close any tab at exit.

## Widgets in P2
`fill_page` loses the page-goal fallback. An answer that cannot be done with click, type, select, toggle or upload stays empty and is listed in the note. If the field is required → Needs Attention `broken_form` ("widget not supported yet"). P4 adds the widget handlers.

## Tasks
- T1 Golden tables, **before any change**: run the old observer on every fixture page and save `tests/golden/tables/<fixture>.json`.
- T2 Build `assistant/driver/` (Keep / change / delete).
- T3 `assistant/browser.py` and `FakeBrowser`; port every `FakeMCP` test.
- T4 Guard v2 and its tests.
- T5 `navigate.py` and its tests. New fixtures: an Easy Apply dialog (Next → Review → "Submit application"); an external "Apply" job page; a cookie banner with "Confirm my choices"; a form whose Next is `type=submit`; a Greenhouse-like page whose submit reads "Apply now!"; a form inside a `<div class="consent">` whose submit reads "Submit" (must be refused).
- T6 Parity: the new observer's tables match the golden tables on every fixture — the same controls (role, name, value, checked, options) in the same order; extra fields and longer strings are allowed. Then delete the wheel, the patch, the hooks, `contract_check.py` and the goal prompts, and run `pip uninstall jev-ultrafast-mcp` in the venv.
- T7 Tripwire on all fixtures: zero POSTs; every refused label and structure is refused; Enter is never sent; the exemption works only as specified.

## Acceptance criteria
- A1 The offline suite, tripwire and parity pass.
- A2 `grep -r jev_ultrafast_mcp` finds only `DISCOVERY.md` and `assistant/driver/LICENSE`.
- A3 No `browser_goal` and no text helper: `llm_inference_logs.json` holds only LLM inference and fallback-decider calls.
- A4 The live gate passes.
- A5 Docs: build spec v4.0 (§3 driver, §4 guard v2, §5.1 navigation); `recore/HANDOVER.md` written.

## Live gate (the user runs)
1. `python -m assistant run --no-record --limit 3`.
2. Check:
   - one "Allow" click for the run;
   - each Easy Apply job: the program clicks "Easy Apply", fills the first step, and clicks Next by itself; it ends parked, or at Needs Attention / broken_form with a clear reason;
   - each job with an external "Apply": Needs Attention `external_ats`, and the Apply control was not clicked;
   - after the program exits, every job tab is still open, and none of your other tabs changed;
   - nothing was submitted.
