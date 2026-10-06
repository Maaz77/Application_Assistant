# P4 review — `recore/p4-easy-apply-reliability` vs `recore/P4_easy_apply_reliability.md`

Reviewed 2026-10-04 against `main`. Diff: 5 files, +153/-9
(`DISCOVERY.md`, `assistant/fill.py`, `assistant/navigate.py`, `assistant/widgets.py`, `tests/test_fill_loop.py`).

> **Status, 2026-10-04 (later the same day).** B1, B2 and B3 below are **fixed** in the working tree (nothing
> committed). Each fix carries a regression test that was verified to fail against `main`'s code and pass against
> the fix (`git stash` on `assistant/`, re-run, restore — 9 of the new tests fail on the old code).
>
> Offline gate after the fixes: **405 passed, 51 skipped** (`main` was 391 — 14 new tests), replay **8**,
> tripwire **44**, exit 0. `graphify update .` run. `DISCOVERY.md` carries four new entries.
>
> **Two things the fixes assume and the fixtures cannot settle** — check both on the first live run:
> - typeahead suggestions are matched by `e.dialog == el.dialog`. If LinkedIn renders the option list in a portal
>   outside the modal, `opts` is always empty and every typeahead becomes a `broken_form`.
> - `widgets.handle` looks the field up by `q.ref` on the re-read page. If a re-render replaces the combobox node
>   outright, that lookup misses and the field is reported rather than filled. (`type_long` re-maps by question
>   text for exactly this case; the typeahead does not yet.)
>
> **Still outstanding:** T1 checkbox groups / numbers / dates / phone country code, T3 validation messages, T2
> read-back, T4 refused-label set, T5 settle-hash polling, the `REQUIRED_EMPTY` probe extension, and all of
> **A3** (spec v4.2, HANDOVER failure → fix table). **A2 remains unmet and unrunnable** — see the live-gate
> section. The findings below are kept in full, since the reasoning is the record.

**Verdict: not correctly done.** The branch implements roughly one of the six P4 tasks, and both behaviour
changes it does make are defective. A third defect in pre-existing code, which P4's own new typeahead path now
walks straight into, will by itself keep the ≥ 7/10 live gate from passing. Each is confirmed by a probe below.

---

## Blocking defects

### B3 — read-back rejects a *correct* typeahead pick (CONFIRMED, likeliest gate-killer)

A typeahead almost never echoes the answer verbatim: pick "Milan" and LinkedIn's input shows
"Milan, Lombardy, Italy". `fill._fuzzy_holds` (pre-existing, `fill.py:338-348`) does exact, phone-digit-suffix and
strip-punctuation comparison — none of which is a prefix or containment test:

```
'Milan'    vs 'Milan, Lombardy, Italy'            -> holds=False
'Hybrid'   vs 'Hybrid (3 days onsite)'            -> holds=False
'Dublin'   vs 'Dublin, County Dublin, Ireland'    -> holds=False
'Milan'    vs 'Milan'                             -> holds=True
```

So the read-back in `fill_page` reports a mismatch on a correctly filled typeahead, re-plans, fails again, and
raises `_Refill` → `broken_form`. P4 added a typeahead handler without extending the read-back to the labels a
typeahead actually produces, so every City-style field becomes a Needs Attention. T1 names City as its own
example. This is pre-existing code, but P4 is the change that routes traffic into it.

### B1 — `mismatches()` radio fallback is not scoped to the radio group (CONFIRMED)

`assistant/fill.py:363-366`. When a radio's `option_ref` is gone after a re-render, the fallback scans
**the whole page** for any checked toggle whose name equals the answer:

```python
if q.option_ref:  # ref gone after React re-render
    label = pages.norm_label(q.answer or "")
    return any(e.role in TOGGLES and bool(e.checked) and pages.norm_label(e.name) == label
               for e in p.elements)
```

LinkedIn pages carry several Yes/No groups whose option labels are identical, so one checked "Yes" anywhere
makes **every** question answered "Yes" report as filled. Probe (two of three groups genuinely empty, only `g3`
checked):

```
ground truth: 2 of 3 radio groups are EMPTY -> expect 2 mismatches
mismatches() reported: 0 []
VERDICT: BUG CONFIRMED (false 'filled')
```

**Accurate cost.** On the Linda AI run that motivated this, the retry was `3/3 ops ok`, so there the fallback
happens to answer correctly. It bites when some toggles in a multi-group page fail: the read-back T1 requires is
then defeated, and nothing downstream catches it — `pages.gate()`'s `required_empty` probe
(`probes.py:43`) queries only `input,select,textarea`, so it is **blind to LinkedIn's `div role=radio` groups**.
The failure surfaces later and misdiagnosed: `advance` → `"stuck"` → a blind whole-page refill → a gate stop,
instead of a precise `broken_form` naming the field. Blocking because it removes the only check on the P4 path.

The committed test `test_mismatches_holds_after_radio_rerender` cannot catch this — it uses a single radio group.
`Element.group` exists, and `llm_inference._group_radios` already groups by `group` → `name` → `scope`; the
fallback must carry that group key from plan time and scan only within it.

### B2 — typeahead clicks the first suggestion on no exact match (CONFIRMED)

`assistant/widgets.py:_typeahead`:

```python
match = next((e for e in opts if norm_label(e.name) == target), opts[0])
```

The spec says: equal to the value, **else the one that starts with it**; several close candidates → Jev `choice`;
**none → leave empty and note it**. The code clicks `opts[0]` — an arbitrary suggestion — and `return True`
unconditionally, whether the click succeeded or not. There is no read-back of the selection itself.

Probe (answer `"Hybrid"`, suggestions `["Remote", "Hybrid (3 days onsite)"]`): it clicked ref `e3` = option
**"Remote"**. **Scope of that evidence:** the run parked, but only because `tests/fake_browser.py` does not write
a clicked option back into the combobox (`act` has no `click`-updates-`value` path, `fake_browser.py:146-183`), so
the field kept the typed "Hybrid". On real LinkedIn the input would then read "Remote", and B3 would turn it into
a `broken_form`. So the confirmed defect is the wrong pick and the missing read-back, **not** a silent wrong park.

Also in the same function:
- **options are not scoped.** Every `role=option` on the page is collected, not just this combobox's or the
  dialog's. `Element.dialog` exists for scoping. A stale or unrelated listbox can be clicked.
- **the `type` result is discarded** (`stop_on_error=False`, return ignored), so a `target_changed` on the type
  still proceeds to click an option.
- **the page is stale.** `widgets.handle` is called with the `p` captured *before* the `direct` ops ran — the same
  re-render hazard B1 is about.

---

## Spec coverage

| Task | Status | Note |
|---|---|---|
| T1 typeahead/combobox | **partial, defective** | B2. No startswith, no Jev `choice`, no "leave empty and note"; `broken_form` instead. `listbox` not handled. |
| T1 radio / single checkbox | pre-existing (P2/P3) | `_code_how` → `check` → `toggle`. P4 only added the re-render fallback, which is B1. |
| T1 checkbox groups (multi-select) | **absent** | No handler; one `option_ref` per question cannot express multi-select. |
| T1 native `<select>` by label | pre-existing | `_carry_out` select-by-label. |
| T1 numbers (whole, range from hint) | **absent** | No integer or range enforcement in `fill.py`, `llm_inference.py` or `prompts/llm_inference.md`. (`RANGE_RE` there is month-year experience parsing, unrelated.) |
| T1 dates (placeholder format; calendar-only → empty + note) | **absent** | No date handling anywhere. |
| T1 phone (country-code select + local number) | **partial, pre-existing** | `_fuzzy_holds` tolerates a country-code prefix on *read-back* only. No country-code select handler (§7.3). |
| T2 resume step (D22) | pre-existing, **no read-back** | `upload_resume` + `select_resume_card` exist, but `select_resume_card` toggles with `stop_on_error=False` and never verifies the named card is selected. Spec: "then verify". Only `pages.gate()` checks the file name on the final page. |
| T3 validation messages | **absent** | `navigate.inline_error` returns a **bool** only; no message text, no field mapping, no format-aware refill. `advance`→`"stuck"` triggers a blind whole-page refill (pre-existing P2/P3 behaviour). |
| T4 dialog safety | **partial** | Detection added (`save_application_dialog`). But "never click close (X) / Dismiss / Discard" is **not** enforced: `guard.REFUSE_LABEL_RE` is `\b(submit\|send\|confirm\|done\|finish\|complete)\b` — none of the three. No path clicks them today (`PROCEED_RE`/`ADVANCE_RE` don't match), so this is a missing guard rail, not a live bug. |
| T5 speed (no fixed sleeps, settle hash) | **not done** | `_typeahead` uses a fixed `ctx.sleep(0.1)` and a **full `read_page` per poll** — up to ~30 CDP observations per widget. Spec: "no fixed sleeps; wait on the settle hash". |
| T6 HAR tripwire | absent | Optional, and conditional on the user supplying a HAR. Fine. |

### Design note (not a defect)

`save_application_dialog` regexes the whole-page `p.text`. The codebase already has the dialog-scoped signal:
`Page.dialogs` (open **modal** dialogs, `! dialog open: … [modal]`), with a live finding and a passing test
(`test_only_the_topmost_modal_counts`) about exactly this overlay. I checked the false-positive risk and it does
**not** currently bite — `observer.js` `deepVisible` uses `checkVisibility()`, so the closed `<dialog id="save">`
in `tests/fixtures/jobs/view/4012345610-easy-dialog.html` contributes no text. It is a latent precision risk plus
a missed reuse of an existing, more exact helper, not a confirmed bug.

---

## Acceptance criteria

- **A1 (offline suite + tripwire + replay)** — **met.** `pytest -m unit` 298 passed; full suite
  `391 passed, 51 skipped, 2 deselected` (the 51 are `live_model`, which need `--live`); `tests/test_replay.py`
  8 passed; `python -m assistant tripwire` 44 passed, exit 0. Note A1 is met only because the new tests do not
  exercise B1 or B2 — both probes above fail against this same code.
- **A2 (live gate over the final code)** — **not met, and currently not runnable.** A delegated smoke-test
  attempt was **blocked at preflight (exit 1)**; zero jobs were attempted and zero submissions occurred. Verified
  independently:
  - `http://127.0.0.1:9222/json/version` and `/json/list` both answer `HTTP 404 len=0` — the Chrome 144+
    `chrome://inspect` WebSocket-only server already documented at `DISCOVERY.md:74` and `cdp.py:155-158`. So the
    driver correctly falls through to `_from_active_port()`.
  - `~/Library/Application Support/Google/Chrome/DevToolsActivePort` **exists** (mode 644, 59 B) but reading it
    fails with `Operation not permitted` from the agent's process context — macOS TCC protecting
    `~/Library/Application Support`. The user's own Terminal presumably holds that access, which is why earlier
    runs passed (`DISCOVERY.md:109`, "7 of 7 checks").

  **Only the user can clear this**, by running `.venv/bin/python -m assistant preflight` from their own terminal.
  Granting an app Full Disk Access is a macOS security setting and is theirs to decide; nothing in this review
  recommends changing it. Note `README.md:291`'s `--remote-debugging-port=9222` alternative is *not* a remedy —
  it uses a separate `--user-data-dir` that is not signed into LinkedIn.

  Consequence for this review: **no live evidence exists for B1, B2 or B3.** `runs/` has no directory newer than
  the fix commit `502f997` (2026-10-04 20:09) — newest is `20261001-214422` — so the P4 code has never executed
  against a real page. All three defects are confirmed offline only. `run --dry-run` works without a browser and
  still shows job `4470454940` (Linda AI) queued, which is the page that exhibits B1's input condition
  (`answers.json` page 5: three questions answered "Yes", `opt_ref` `e150`/`e152`/`e154`;
  `browser_actions.jsonl`: `0/3 ops ok` with `target_changed` on each).

### B4 — `PermissionError` is reported as "file not found" (found while diagnosing the above)

`assistant/driver/cdp.py:198-200`:

```python
try:
    lines = (directory / "DevToolsActivePort").read_text(encoding="utf-8").splitlines()
except OSError:
    continue
```

`PermissionError` is an `OSError` subclass, so an unreadable file is swallowed and preflight then asserts "no
`DevToolsActivePort` file was found in the usual browser data directories" when the file is present and merely
unreadable. That sends the user to re-toggle `chrome://inspect`, which is already on. Distinguishing absent from
`EPERM` in that message would have made this blocker self-diagnosing. Not a P4 regression — pre-existing — but it
is what made the live gate look like a code failure.
- **A3 (docs)** — **not met.** `application_assistant_build_spec.md` is still **v4.0** (spec asks for v4.2 — and
  v4.1 from P3 was never written either). `recore/HANDOVER.md` is untouched on this branch: it still describes P3
  state, says "build spec v4.1 not written", and has **no "failure → fixture → fix → test" table**, which the
  Method section makes the central artefact of P4.

## Housekeeping

- `README.md` has 231 insertions / 125 deletions **uncommitted** in the working tree — not part of the branch.
  It documents P3-era behaviour and claims spec "v4.0". Commit or discard it before merging.

## Recommended order of work

1. **B3 first** — it is the cheapest fix and the one gating ≥ 7/10: let `_fuzzy_holds` accept a held value that
   starts with, or contains as a token prefix, the answer (a typeahead expands the label it does not replace it).
2. Scope B1 by radio group (carry the group key from `_group_radios`; add a multi-group regression test), and
   extend the `REQUIRED_EMPTY` probe to `[role=radio]` groups so the gate can see them at all.
3. Fix B2: startswith → Jev `choice` → leave empty + note; scope options to the combobox/dialog; honour the
   `type` result; re-read the page before `handle`; read back the selection.
4. T3 (read the actual message text and map it to the field) — this is what turns a blocked `Next` into a fix
   rather than a blind refill, and it is load-bearing for the ≥ 7/10 gate.
5. T1 numbers, dates, phone country code; checkbox groups.
6. T2 read-back; T4 add `dismiss|discard|close` to the refused set; T5 settle-hash polling.
7. A3 docs: spec v4.2 and the HANDOVER failure → fix table.
