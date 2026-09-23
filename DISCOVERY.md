# T1 Discovery — jev-ultrafast-mcp 0.1.5

Run: `.venv/bin/python -m tests.discover` (throwaway headless Chrome on :9223, fixture `tests/fixtures/discovery.html`).
Raw outputs in `tests/golden/`. Source read: `jev_ultrafast_mcp/{server,browser,assertions,safety,config}.py`, `js/observer.js`.

## §3 unknowns — answered

| Unknown | Answer | Golden |
|---|---|---|
| Element-table JSON | `observe(include_json=True)` appends `\n\njson: {...}`: `{"url","title","elements":[…]}`. Element keys: `ref, role, name, value, editable, occluded, checked, current, options, context`. `label` is **not** in the JSON (it equals `name`). `options` (selects only) = `[{ref:"e2:1", label, value, selected}]`, max 40. `current` = selected option label (selects). `context` = enclosing scope text, **only when (role, name) repeats**. No frame key; iframe elements are merged into the same table. | `observe_full_json.txt`, `elements.json` |
| Role vocabulary | `button, link, textbox, searchbox, combobox, listbox, checkbox, radio, switch, tab, menuitem, menuitemcheckbox, menuitemradio, option, treeitem, gridcell, spinbutton, file` (+ any explicit ARIA role in that list). `<textarea>` and `type=email/tel/url/date/password…` → `textbox`; `<select>` → `combobox` (`listbox` if multiple). | `open.txt` |
| `browser_assert` format | Text: `PASS\|FAIL  (<url>)` then one line per check `  ok <type>: <detail>` / `  X  <type>: <detail>`. `js` detail = `json.dumps(value)[:120]`. | `assert.txt` |
| Does `eval` return its value? | **Yes, truncated.** Line `  + eval → <json.dumps(value)[:200]>`. A string result is JSON-encoded twice (`"{\"a\":1}"`). `undefined` and a thrown error both return `null` with `ok`. | `act_eval.txt`, `act_eval_undefined.txt` |
| Hidden `<input type=file>` | Listed with role `file`, name from `aria-label`/label, flagged `»` (off-screen), not `⊘`. `upload` on **that ref** works (`files.length == 1`). The styled button is a plain `<span>` and is not listed. | `act_upload.txt` |
| `browser_tabs new` | Returns `opened new tab` + the tab list. **Does not switch the session** (the `*` stays on the old tab). The list shows 8-char handles (`#5F521ED2`), but `switch`/`close` need the **full** target ID (exact match). The next observe prints the full ID in a `! NEW TAB opened: … target_id':'<full>'` hint; `/json/list` on the CDP port has it too. | `tabs_new.txt`, `tabs_after_new_observe.txt` |

## Verified behaviour (B1–B9)

- B1 ✓ `click` on "Submit application" → `needs_confirmation: matches confirmation rule '\\bsubmit'` (`act_confirm_click.txt`). The goal trace is printed only with `verbose=True`; `mcp.goal()` always sends it.
- B2 ✓ `keys Enter` is not checked (`act_keys_enter.txt`, dry run).
- B3 ✓ At server exit each session's **current** tab is closed. After switching the session to a scratch tab, the form tab survived (`json_list_after_exit.json`).
- B7/B9 ✓ `connected: false` before the first browser call, `true` after.

## Differences from §3 — reported to the user, not worked around

1. **Untyped `<input>` is invisible.** `observer.js` `roleOf()` maps an input by its `type` *attribute*; `<input>` with no `type` gets no role and is dropped from the table. (Discovery: the required "First name" field is missing.) The fill loop cannot target such a field; `REQUIRED_EMPTY` still sees it, so the gate would fail the job.
2. **`eval` output is capped at 200 chars**, and `js` assert details at 120. `REQUIRED_EMPTY`, `MAXLENGTHS` and `IFRAME_SRCS` will truncate on real forms.
3. **`browser_goal` has no `url` parameter** (signature: `goal, session, max_steps, verify, verbose`). §5 never passes one, so there's no impact.

## Other notes

- Radio buttons are named by their own label ("Yes"/"No"). The group question (`<legend>`) appears in `context` only when the name repeats on the page.
- Click-guard roles: `confirm_reason` skips roles other than button/link/menuitem/tab, but op dicts carry no `role`, so every click is checked (B1).

## Decision (user, 2026-09-22): option (a) — stay on 0.1.5

- Untyped `<input>` fields are not fillable; `REQUIRED_EMPTY` still lists them, so the gate sends the job to Needs-Attention.
- Every eval probe returns a plain object trimmed in-page to ≤ 190 chars of Python `json.dumps`: `{n, more, items, …}` (`n` = true count, `more` = items were cut). Non-ASCII in labels becomes `?`, which keeps `\uXXXX` escapes from breaking the length count. `IFRAME_SRCS` leaves out srcs over 170 chars and counts them in `long`.

## Live model (2026-09-23, `jev-latest` via OpenRouter, local fixtures only)

- Goal "Submit this application." on f01–f07: the model answers `BLOCKED` at step 0 every time. Zero POSTs.
- Direct orders: the model also refuses "Submit application" / "Send application" by itself. It attempts "Apply", "Confirm my choices" and "Done", and the server stops each with `status: failed:needs_confirmation` plus `CLICK eN <label> → needs_confirmation` in the trace. B1 holds live.
- The decision model has no key operation (`CLICK, TYPE_TEXT, SELECT, TOGGLE, SCROLL, WAIT`), and TYPE_TEXT never sets `submit`, so an Enter-submits form (f04) cannot be triggered by a goal.
- A cross-origin iframe (f12) is not merged into the element table. The page shows no fields, and `IFRAME_SRCS` lists the src, so the classifier returns `iframe` and §5 opens that src in the application tab.

## Fill findings (T6, 2026-09-23)

- **Custom dropdowns** (`<button role=combobox>` + listbox): the goal sets them (open, click the option), but the table reports `value: ""`, because the server reads a button's `value` property. Read-back accepts the answer label when it newly appears in the page text.
- **Bare radio groups** ("Yes"/"No" with the question only in a `<legend>`): a page goal given only the question clicked **Next** and left the page. Decision (user, 2026-09-23): the wrapper sets radios/checkboxes (`toggle`) and native `<select>`s (`select`) directly by ref. The goal handles only text fields and custom widgets. The read-back retry runs one goal per failed field. A goal that leaves the page is a broken-form attempt: reopen the fill's start URL and refill.
- **Read-only date picker** (f08): the decision model tries TYPE_TEXT, the text helper returns `{"text": null}`, and the server aborts the whole goal (B5). With per-field retries only that field fails, and the job goes to Needs-Attention ("field would not accept its value: 'Earliest start date'"). So f08 does not park on 0.1.5; everything else on the page fills.
- **Answer engine on OpenRouter**: send `max_tokens` (8192); otherwise OpenRouter reserves the model maximum and a low balance gets HTTP 402. `qwen/qwen3.8-27b:free` returned HTTP 429 (rate-limited upstream) on every try on 2026-09-23. It is handled as one wait-and-retry, then a blocker.

## Guest links (user decision, 2026-09-23)

§6.3's sign-up attempt 2 ("apply without an account") collided with the §4.1 guard, which blocks every `apply` label outside the entry click. Decision: the `ENTRY` exception also covers guest links. `entry.guest_click` is the only caller. It requires a sign-up wall (a password field), exactly one link matching `apply without an account|continue as guest`, and no form field holding a value. It sends `confirm` only when the label is a transmit label. The token still never unlocks Submit, Send or other labels.

## Direct calls instead of an MCP client (spec v2, 2026-09-23)

No agent calls the browser tools; our code does. So the wrapper imports the package and calls its public `server.browser_*` functions directly. The separate server process, the stdio pipe and all async code are gone. What changed:

- `assistant/jev.py` is the only module that imports `jev_ultrafast_mcp` (AST test).
  1. `apply_env()` removes every `JEVMCP_` / `TYPESAFE_` / `TEXT_MODEL_` / `OPENROUTER_` variable from `os.environ`, then sets the §3 values.
  2. `load()` then imports `jev_ultrafast_mcp.server`. The package reads its config and builds its browser manager at that import, so the config is fixed per process.
  3. `call(name, **kwargs)` runs one call at a time on a dedicated worker thread (60 s, `browser_goal` 300 s). On a timeout it writes the report and calls `os._exit(3)`. That skips the package's exit hook, so the job's tab stays open.
- The package's tab-closing `atexit` hook now runs in the wrapper's own process (B3). Release is still required. It is verified with a child process: a released tab survives exit with its typed value; the control tab without release is closed.
- `browser_goal` has no `url` parameter (none was ever passed). `assistant/contract_check.py` compares every `browser_*` signature with §3; they match the 0.1.5 wheel.
- `mcp` and `pytest-asyncio` left the wrapper's dependencies. `mcp` stays installed only because the package needs it.
- Tests: `conftest.py` calls `apply_env()` with `JEVMCP_CDP_URL=http://127.0.0.1:9223` before anything loads the server, so all browser tests share the throwaway Chrome. The browser suite went from ~100 s to ~65 s, with no server process per test.
- Unchanged: results are text; parsing, guard rules, release, the gate, records and exit codes are as before.

## First real capture (user, 2026-09-23): two fixes

- **No `/json/list` with `chrome://inspect` remote debugging.** Chrome's own switch serves only the DevTools websocket, so the HTTP endpoint answers 404; the package attaches another way. `tabs.py` used `/json/list` for full tab IDs, so release failed, and a failed release lets the exit hook close the job's tab. Now `tabs.TabBook` uses only `browser_*` calls. A helper session (`tabbook`, on its own about:blank tab) learns the full ID of every tab created after it starts from the `NEW TAB opened … 'target_id':'…'` lines in its `browser_observe` output. Older (user) tabs are known only by 8-char handles and are never switched to or closed. Indexes are never used, because Chrome renumbers them on activation. Before `browser_close`, release checks that the session sits on its own scratch tab. Preflight now tests this on a scratch tab.
- **LinkedIn draws the job card after "load".** The first capture of `jobs/view/4460337345` held only the nav bar and site Search box (`reachable=0/8`, text "0 notifications", `{:memberName}` placeholder). The screenshot a second later showed Easy Apply. `pages.settle()` now re-reads a page that has no real form field (site search excluded), no button we act on, no captcha or alarm text and under 200 chars of text, once a second for up to 10 s. `JobCtx.read()` and `capture` use it. The fixture `jobs/view/4012345606-late.html` draws its Easy Apply after 3 s.

## From the LinkedIn posting to the company form (2026-09-23)

Every job starts at its LinkedIn posting (`job.md` "LinkedIn URL"), never at a form link. Two gaps in following that link to the form are now closed:

- **The company site opens late.** After the entry click, and after the `ats_entry` click on a company job page, `fill.follow_new_tab()` checks for a new tab for up to 8 s. It stops at once when the current tab shows a real form field (Easy Apply's dialog), an advance button or confirmation text, so Easy Apply costs no wait.
- **An interstitial dialog** ("You are leaving LinkedIn … Continue"). The Apply button behind a covering dialog is `occluded` and is no longer offered as a second entry click. The dialog's **Continue** is clicked as a normal advance. Every advance click (button or goal) is now followed by the same new-tab check (3 s), so the site that "Continue" opens is handed off to, and the LinkedIn tab is closed.
- Fixtures `jobs/view/4012345607-late-tab.html` (form after 2.5 s) and `4012345608-dialog.html` (Continue dialog) run through the end-to-end `process()` test, next to the immediate `…04-external`.
- **Not handled — needs a decision:** an interstitial whose button says **"Continue to apply"** (or similar with "apply"). It matches TRANSMIT, so the guard blocks it and only the entry click may press an `apply` label (§4.1–4.2). Such a job ends in Needs-Attention at the gate. If real LinkedIn pages show that label, the entry exception needs to cover it, as it now does for guest links.

## LinkedIn job card invisible to the observer: `display: contents` (live diagnosis, 2026-09-23)

With your permission I ran read-only DOM queries (no clicks, no typing) on `jobs/view/4460337345` in a background tab of your Chrome, then closed that tab:

- The DOM is fine. `body.innerText` has 7,308 chars and 28 buttons, including `<button aria-label="Easy Apply to this job">`. It sits in the main document (no shadow root), has no `aria-hidden` or `inert` ancestor, is visible and 118×32 px at (101, 294) in a 1280×860 viewport, and the tab is `visible` and focused.
- `window.__jevMcp.readState()` (the package's observer) returns 14 elements (nav bar, messaging overlay) and 15 chars of text.
- Cause: the observer's `deepVisible()` runs `checkVisibility({checkOpacity, checkVisibilityCSS})` on every ancestor. Chrome returns false for an element with `display: contents`, which generates no box of its own. On LinkedIn, the 7th ancestor of Easy Apply is `div._342f8125` with `display: contents` (opacity 1, visibility visible), so every control **and all text** inside it are dropped. `textOf()` uses the same test, so the answer engine would see no page text either.
- Reproduced locally: `tests/fixtures/probe_lab/display_contents.html`. The strict-xfail test `test_display_contents_wrapper_is_seen` will turn red when a package version fixes it. 0.1.5 is the latest on PyPI.
- Consequence on 0.1.5: every LinkedIn job ends in Needs-Attention ("no Easy Apply / Apply button"), and the Easy Apply dialog is probably hidden the same way. Needs a user decision (rule 7).

### Resolution (user decision, 2026-09-23): patched wheel `0.1.5+aa1`

- `vendor/jev_ultrafast_mcp-0.1.5+aa1-py3-none-any.whl` is the official 0.1.5 wheel with one fix, `vendor/jev_ultrafast_mcp-0.1.5+aa1.patch`: `deepVisible()` skips the `checkVisibility()` box test for an ancestor whose computed `display` is `contents`. The `aria-hidden` / `inert` checks stay. The observer's helper version goes from 6 to 7 (`observer.js` and `HELPER_VERSION`), so a tab still holding the stock v6 helper gets the fixed one. `__version__` and the wheel metadata say `0.1.5+aa1`. Nothing else changed, and `contract_check` still matches §3.
- `pyproject.toml` pins `0.1.5+aa1` (Poetry path dependency to the wheel). `jev.load()` refuses any other installed version, so preflight fails on the stock package.
- Verified read-only on `jobs/view/4460337345`: 39 elements, 1,041 chars of text, `Easy Apply to this job` found, `classify_entry` = `entry`. Easy Apply was not clicked.
- **Site chrome.** The same page has LinkedIn's footer `Select language` `<select>` (always `en_US`), which made §4.2's "no form field holds a value" refuse the entry click. `pages.real_fields()` (and `has_fields`, and the entry/guest-link rules) now ignores exactly a site search box and a field named `Select language`. Any other field, including one called "Preferred language", still counts.
- Fixture `jobs/view/4012345609-easy-li.html` reproduces the whole page (display:contents card, Search, Select language, same-tab Easy Apply dialog: relocate radio → resume → Review → Submit application). It runs through the end-to-end `process()` test and parks on step 3 with zero POSTs.
- Report upstream: `deepVisible()` in `js/observer.js` treats `display: contents` ancestors as invisible.

## Live session on real sites (user-authorised, 2026-09-23)

Done in your Chrome through the Claude-in-Chrome connector and through the program's own code path. Nothing was submitted. Easy Apply dialogs were closed with Dismiss → Discard. One test file, `Amin_Live-Test_Upload-Check.pdf`, was uploaded to LinkedIn to prove the upload path.

**Preflight** passes against the real Chrome, 7 of 7 checks. A 60 s hang in one run was Chrome's per-client "allow remote debugging" approval in `chrome://inspect` mode (the package's `cdp.py` notes the handshake waits on a person). The timeout path now flushes output before `os._exit(3)`, because the lines were lost.

**LinkedIn Easy Apply**, `jobs/view/4460337345`, five pages:

1. **Contact info.** Email (a select with both addresses), Phone country code (select, Italy +39), Mobile phone number (tel, required, empty), Next.
2. **Resume\*.** Earlier resumes are `DIV role=radio` cards (`aria-label` = file name, `aria-checked`) with Download links. **No `<input type=file>` exists**: "Upload resume" is a plain button that creates the input and opens the native file chooser.
3. **Additional Questions.** Yes/No are `DIV role=radio` whose `aria-label` is the question for *both* options; the option text is their content. Plus "How many years of work experience do you have with C++?" (required, max 20 chars; the spec's "never guess years with a tool" → Needs-Attention unless the Scratch Pad answers it).
4. Not reached.
5. Review → Submit application.

Dismiss opens "Save this application?" as a **second, sibling modal `<dialog>`** with Discard / Save.

**Company sites.**

| Site | What the observer showed | Fix |
|---|---|---|
| Greeting ATS (career.nota.ai) | link-wrapped `<button>Apply</button>` counted twice; a Google Maps iframe won | same-label link+button = one entry; form-looking iframes only |
| Greenhouse (job-boards) | both file inputs named "Attach"; react-select dropdowns keep an empty input; invisible reCAPTCHA | `FILE_LABELS` (group label: Resume/CV, Cover Letter); `COMBO_VALUES_*` (shown value → `current`); `CAPTCHA_PRESENT` counts only visible challenges |
| Ashby | "Apply for this Job" as link+button; form loads after "Fetching application form"; radios at `opacity: 0`; invisible reCAPTCHA | entry dedupe; `LOADING_RE` keeps settle waiting; observer aa3 |
| Workday (NVIDIA) | Apply → Autofill / Apply Manually / Use My Last Application → sign-in wall (Google / LinkedIn / email) | works as designed: "Apply Manually" is the entry; the wall is §6.2 Google or a blocker |

**Package patches** (vendor wheel `0.1.5+aa6`, `vendor/jev_ultrafast_mcp-0.1.5+aa6.patch`, observer helper v12):

- aa1: `display: contents` ancestors hide nothing.
- aa2 + aa4 + aa5: while modal `<dialog>`s are open, only the topmost one is listed or read. The HTML spec makes the rest inert. The topmost is the last one whose own content is hit at its centre, and each snapshot re-checks.
- aa3: a native radio, checkbox or file input's own `opacity: 0` does not hide it.
- aa6: ARIA radios, checkboxes and switches report `checked` from `aria-checked`. `toggle` reads `aria-checked`, so a ticked ARIA box is never un-ticked. `upload` on a non-input target clicks it with `Page.setInterceptFileChooserDialog` and fills the input from `Page.fileChooserOpened` (`DOM.setFileInputFiles` by backendNodeId). Verified live: `upload e158 → Amin_Live-Test_Upload-Check.pdf 1/1 ops ok`, and the new card was selected.

**Program changes from the live session.**

- The entry click checks its result and retries once on a stale ref. LinkedIn re-renders Easy Apply between snapshot and click (`x click e16 detached`), and the unchecked click had silently done nothing. The advance click does the same.
- `follow_new_tab` waits for the page to *change* (a new tab, or different URL, fields or advance buttons). "Any field" isn't enough, because the job page already has one.
- Site chrome: LinkedIn's "Set alert for similar jobs" switch, the nav Search box and the footer "Select language" (exact names).
- `has_transmit` ignores links with long labels, so LinkedIn job cards ending in "Easy Apply" don't mark a final step. The guard still refuses to click them.
- Guard: an `upload` target with a transmit label is refused, because aa6 clicks non-input targets.
- Resume: when a page has no file input, the "Upload …resume/CV/file" button is the upload target. After uploading, the tailored resume's card is selected, and engine answers that pick an older resume card are dropped.
- `RADIO_OPTIONS_*` probes give each radio its option text ("Yes"/"No") in `label` when its name is the question.

**Billing (blocks the model-dependent live steps).** The OpenRouter account has no purchased credit (`total_credits: 0`). The answer engine got HTTP 402 ("can only afford 4561 tokens"), and `qwen/qwen3.8-27b:free` answered HTTP 429 all day. A full fill-and-park run on a real job needs credit, or a free model that is not rate-limited. The model is needed for the answer engine and for page goals on text fields.

## Direct typing for short text answers (user decision, 2026-09-23)

The fill mapping of §7 changes to cut model calls. A text answer for a field the observer marks `editable` (textbox, searchbox, number input; not read-only, not a custom combobox) is typed by the wrapper: `type`, `clear: true`, `submit: false`. Together with the earlier decision (radios/checkboxes `toggle`, native selects `select`), the page goal is now used only for custom widgets: dropdowns without an option list, date pickers, autocompletes. Read-back is unchanged. A typed value the page rewrote or refused (masks, widgets) gets **one goal for that field alone**; a toggle or select is simply repeated.

Measured on local fixtures with the live models (qwen/qwen3.7-flash answer engine):
- f02: **0 page goals** over 3 pages (the answer engine's one call per page only).
- f08: every field set directly except the read-only date picker, which still takes its goals (and still ends in Needs-Attention).

## Answer-engine checks, after a live LinkedIn run (qwen/qwen3.7-flash, 2026-09-23)

On Easy Apply's contact page the model returned:
- the email's option list with a typo ("a bbaszadeh…");
- a phone quote from Profile.md credited to the resume;
- **"Austria (+43)"** for a phone country code prefilled as "Italy (+39)". The table lists only a select's first 40 options, so Italy was not visible to it.

The old checks dropped all three, and the prefilled required fields then counted as unanswered. The checks now judge what an answer would set:
- a quote counts if it is in any of the three files (the `source` is corrected and noted);
- a native select's answer must be one of the field's own options or its current value;
- a custom widget's answer must appear on the page;
- a radio/checkbox answer must match the option it targets (its name or its option label, e.g. "Yes");
- with `policy.prefill = "keep-if-silent"`, a field that already holds a real value (not blank, not "Select…") keeps it when no valid answer is left. It is recorded as `linkedin-prefill`. The Austria pick is dropped and Italy stays.

## Chrome's "Allow remote debugging?" prompt

In `chrome://inspect` mode Chrome can hold a new debugging connection until a person allows it. Preflight now prints a hint and gives its first browser call 180 s (`CONNECT_TIMEOUT`). Later calls keep the 60 s limit.

## Live end-to-end on LinkedIn Easy Apply with the models (2026-09-23)

`run --no-record` on job 4460337345 (temp workspace, test resume `Amin_Live-Test_Solas-IT-Recruitment.pdf`, answer engine qwen/qwen3.7-flash):

1. Contact: email and country code kept (quoted from the files; Italy (+39) intact). Phone **typed directly** as the local number `351 935 8813` (prompt rule 11).
2. Resume: uploaded through the file chooser (aa6); the engine's "Resume\*" question counts as covered once the resume is in place (`is_resume_question`).
3. Additional Questions: "Bachelor's Degree?" → Yes, set **directly** on the ARIA radio. "Years of work experience with C++?" → **Needs-Attention**, listed under "Questions for your Scratch Pad".

**Model calls: the answer engine once per page; zero page goals, zero text-helper calls.**

Hardening from this run:
- The tab helper's own tab was closed from outside mid-run, and `release()` crashed the run with no report. The helper now restarts itself. A tab it cannot name is switched to by verified position and is never closed by us. Release failures become report **Warnings**.
- `computed` is enforced as *total* years only (`TOTAL_YEARS_RE`); a years-with-a-tool question (`TOOL_YEARS_RE`: "experience … with/in/using X") never gets a computed answer. The model had offered total years for the C++ question.
