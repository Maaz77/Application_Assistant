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

## First real run: all seven jobs in Needs-Attention (runs/20260923-224945)

Seven real jobs, recorded. From `calls.jsonl`, a replay of one answer-engine call, and the pages opened live:

| Job | What happened | Cause |
|---|---|---|
| Linda AI (Easy Apply) | "Job search safety reminder" after Easy Apply; the rule clicked **Review job post** (it matched `^review`), which closes the pop-up; 12 loops | fixed label rules; the right choice, **Continue applying**, is a link |
| Digital Mfg Ireland (Easy Apply) | pages 1–3 filled correctly; LinkedIn redrew the radio group after the toggle (e221 → e231, checked), the read-back by the old ref said "not set", the job was refilled twice and given up | read-back tied to refs |
| Toast (Greenhouse form on the careers page) | a "Cookie consent" modal covered the form; every typed value `occluded` | the cookie rule knew only Reject all / Only necessary / Accept all; Toast says "I do not accept" |
| Mastercard (Phenom) | the job page's job-alert box ("Please enter your email address") was filled as the application; "Apply Now" (a 6-step form, no account) never clicked | "has a field → a form" |
| Genesys (Workday) | the entry click hit LinkedIn re-rendering (`detached`, then `page_changed`); given up after one retry, 3 s in | timing. Behind it Workday asks to **create an account**: a real blocker |
| The Flex ×2 (Ashby) | "answer engine output invalid" | qwen3.7-flash spent all 8,192 output tokens reasoning (`finish_reason: length`): an empty answer, then a cut-off one |

Five of the seven were the rule-based driver guessing wrong about what was on screen; the browser layer itself (observe, click, type, upload, the aa1–aa6 fixes) worked every time.

## The browser agent drives (user decision, 2026-09-23)

The user chose the package's own goal agent (`browser_goal`) over our own agent loop or Stagehand, and a mid-tier model.

- **The decision model cannot be chosen.** OpenRouter's `/api/alpha/decisions` serves only TypeSafe's model: `jev-latest` resolved to `typesafe/jev-1.13-20260917`; `openai/gpt-5.4-mini`, `google/gemini-3.6-flash`, `anthropic/claude-haiku-4.5` and `deepseek/deepseek-v4-flash` answer "does not exist". It is fast and cheap (0.5 s, about $0.00002 per decision) and picked **Continue applying** on the Linda AI pop-up with 0.97 confidence.
- **The mid-tier model is the text helper**, which writes each value the agent types. `deepseek/deepseek-chat` took up to 12 s a field (Toast: 7–38 s per goal); `openai/gpt-4.1-mini` answered the same three fields exactly in about 1 s each. `gemini-3.6-flash` was as good; `claude-haiku-4.5` did not return plain JSON.
- **Navigation is the agent's, one action per goal** (`prompts/navigate_goal.md`, `max_steps=1`): cookie banners, pop-ups, job pages, start dialogs. The page is read again after every action, so the agent can never click through form steps we have not filled. Live fixture test: with several steps per goal it took a one-question first step for "not the form yet" and walked Next → Review → Submit (refused).
- **Navigation stops in code** when an uncovered page looks like an application (a file input, an empty required field, or three visible fields) and has Next / Review / Submit. When the agent says "the form is here" on a page that does not look like one (the Mastercard job-alert box), it is asked once more with a hint; a second answer stands.
- **Every Apply the agent picks comes back to the program**: the server refuses it (B1), and `entry.confirm_click` makes it only for an entry or guest label, with no field holding a value, and never for a button that submits a form with fields. The new read-only probe `ENTRY_SUBMITS` lists those: Toast's own submit is "Apply now!", next to an "APPLY NOW" link that starts the application. A stale click is re-read and retried up to three times (Genesys).
- **Advancing is the agent's too** (`prompts/next_step_goal.md`, `max_steps=1`, asked again while it only scrolls or waits). A refused Submit/Send/Apply means the last step, as before.
- **"Covered"** means a modal named like a cookie or consent pop-up, or a form field the observer marks covered. The page behind an application dialog is covered too, but that dialog is the form (the fixture's `div` dialog, unlike LinkedIn's `<dialog>`, shows its background as covered).
- **Unchanged, in code:** the answers and their checks, the resume upload, direct typing of short answers, the blockers, Google sign-in, the final gate, the alarms, the records.

Also fixed from the run:
- the answer engine is sent `reasoning: {"enabled": false}`. The Flex's 43-field page then answered in 21 s with 2,473 tokens; `effort: low` still used all 8,192;
- quotes match ignoring spacing, punctuation and case when they are 12 or more letters and digits long, so "+39 351 935 8813" matches the resume's "+393519358813" (Mastercard's email answer had been dropped for it);
- a radio set by a toggle is found again by its question and option label after a redraw;
- httpx's per-request INFO lines no longer print into the run's output;
- `python -m assistant requeue` puts Needs-Attention jobs back in the queue.

The end-to-end `process()` tests now need the decision model and run with `--live`; all four fixture postings (company site in a new tab, a late tab, a "You are leaving LinkedIn" dialog, an Easy Apply dialog) park with the real agent.

OpenRouter, 2026-09-23 23:40: `/credits` reports $0 bought and $0.17 used, so the account balance is negative. A request reserving 8,192 tokens of `deepseek/deepseek-chat` got HTTP 402 ("can only afford 6447"). Smaller calls still went through.

## Decisions by Jev instead of rules (user decision, 2026-09-24)

The user asked for TypeSafe's Jev (`typesafe/jev-1.13` on OpenRouter) to make the program's decisions in place of the regex branches. Docs read: OpenRouter's [TypeSafe SDK guide](https://openrouter.ai/docs/guides/community/typesafe-sdk) and [System One API reference](https://openrouter.ai/docs/api/api-reference/systemone/submit-a-system-one-request), and TypeSafe's [docs](https://docs.typesafe.ai) (Choice, Noul, State).

- **The API:** `POST https://openrouter.ai/api/v1/systemone` with `{model, state, questions}`. `state` is text or JSON. Question types:
  - `noul`: the probability that a yes/no question is true, with optional `criteria` `{true, false}`;
  - `choice`: up to 255 named options, each with a probability, plus a confidence;
  - `score`: a point on an ordered scale.
  Questions in one call are answered in parallel and independently. The model is text-only, has a 32K context and was trained mainly on English. It costs $0.042 per million input tokens, output is free, and a call takes about 0.3 s. `typesafe/jev-1.13` works on this route and on the goal agent's `/api/alpha/decisions`, so both now use it (`models.jev`).
- **`decide.py`** is the client: it builds questions, splits them into batches of 60, retries once on a network error, 429 or 5xx, logs every call to `decisions.jsonl`, and keeps the thresholds in `THRESHOLDS`. A call that fails raises `DecisionError`, and the job goes to Needs Attention (`decision`). There is no fallback to the old rules.
- **One call per page snapshot** (`pages.judge`, cached on the Page) replaces:
  - the classifier;
  - the posting states (closed, applied);
  - captcha, sign-up and password walls, and error pages;
  - Google's steps and buttons;
  - "covered", "not drawn yet", validation messages and the Submit button;
  - which fields are the application's (site search, language picker, job alert);
  - which upload takes the resume, and which asks for a cover letter;
  - which iframe holds the form.
- **Two more calls per form page** (`answers.judge_answers`, `answers.judge_questions`): must a question never get a written answer, does it ask for total or tool-specific years, is a shown value a placeholder, and is it the resume or a cover-letter question.
- **The agent's Apply** also needs Jev's "this starts the application" (`entry.starts_application`), on top of the guard's label list, the no-value rule and `ENTRY_SUBMITS`.
- **Rules that stay:**
  - the never-submit label lists in `guard.py` (and the server's copy of them);
  - the confirmation-text tripwire, kept beside Jev's "submitted" answer;
  - facts that aren't judgments: URLs and hosts, probe counts, the resume's file name, date arithmetic, quote-in-file checks.
- **Found while testing on real pages:**
  - "Is the page still loading?" was answered from the page title: a LinkedIn posting with only its nav bar drawn got 0.35. The question that works is "besides the site's navigation, is there something to act on for this job?": drawn pages score 0.94 or more, early ones 0.22–0.30.
  - A form behind a cookie banner (f06) can come back as kind "other" with covered = yes. The loop sends it to the agent, which is the right thing to do.
- **Live results (`tests/test_decisions_live.py`, 40 of 40):**
  - on the six pages of runs/20260923-224945: Linda AI's safety reminder is an interstitial (0.98); Mastercard's job page is a job posting and its job-alert box is not an application field; Toast's form is covered by its cookie pop-up; Digital Manufacturing's radio step is a form step; Genesys is open and its language picker isn't a question; The Flex's form is a form with a resume upload;
  - Greenhouse, Ashby, Workday and Nota AI pages captured from real sites;
  - all fixture pages, the posting states, and the answer checks.
- **Offline**, `tests/rule_decider.py` answers the same questions with the old rules, so unit and browser tests run without a network. The program never imports it.
- **2026-09-24:** partway through the live suite, OpenRouter began answering HTTP 402 "Insufficient credits. This account never purchased credits." Each such decision became Needs Attention (`decision`), as designed. The remaining live tests need credits on the account.

## Jev through Vercel AI Gateway; free Qwen for the chat models (user decision, 2026-09-24)

The OpenRouter account has never bought credits, so it serves only `:free` models (checked with the `openrouter` SDK: paid models, including `openrouter/auto`, return 402, while `cohere/north-mini-code:free` answers). Jev has no free tier on OpenRouter. The user added a Vercel AI Gateway key and chose `qwen/qwen3.8-27b:free` for the chat models.

- **Vercel's TypeSafe-compatible API** ([docs](https://vercel.com/docs/ai-gateway/sdks-and-apis/typesafe)): `POST https://ai-gateway.vercel.sh/typesafe/v1/systemone` with model `typesafe-ai/jev` and `Authorization: Bearer $AI_GATEWAY_API_KEY`. It uses TypeSafe's request and answer shapes. Cost is in `provider_metadata.gateway.cost` (a string). Errors look like `{"error": {"message", "type"}}`. `GET /typesafe/v1/models` lists `jev`.
- **Config:** `models.jev_route` (`vercel` or `openrouter`) chooses the endpoint and its key.
  - The page decisions use `decide.for_config`.
  - The browser agent uses `jev.agent_route`. The package takes a non-OpenRouter `TYPESAFE_BASE_URL` as the full endpoint, with `TYPESAFE_API_KEY`.
  - Preflight checks that the gateway key is present.
- **Live, 2026-09-24:** the gateway key lists Jev, but every evaluation returns **403** "AI Gateway requires a valid credit card on file to service requests … add a card and unlock your free credits". Preflight stops with that message.
- **`qwen/qwen3.8-27b:free`** answered 429 "temporarily rate-limited upstream" (shared pool) on every try over several minutes. The answer engine now waits 15, 30, then 60 s after a 429 before it gives up (`RATE_LIMIT_WAITS`). The text helper is sent with reasoning off (`TEXT_MODEL_REASONING=none`). *Replaced by the rotation below.*

## Free models in rotation (user decision, 2026-09-24)

The user asked to pick a handful of the free OpenRouter models and rotate over them on each call: a model that is not available hands over to the next, and when none is available the call raises.

- **The pick.** Each of the 24 free models got the answer engine's real request (strict json_schema, reasoning off), in parallel. Answered in shape:
  - `dots-studio/dots-3-note-preview:free` (3.7 s);
  - `nex-agi/nex-n2.5-mini:free` (1.3 s);
  - `nex-agi/nex-n2.5-pro:free` (the first try timed out at 90 s; a later try answered, but took 54 s for one short text value).

  Kept although they failed this time:
  - `qwen/qwen3.8-27b:free` (the user's choice; 429 upstream);
  - `nvidia/nemotron-3-super-120b-a12b:free` (structured outputs, but overloaded).

  Not usable:
  - Nemotron 3 Ultra and 3.5 Lightning, Laguna S, North Mini Code: no structured outputs; they answered in the wrong shape.
  - Ling 3.0: 400 for both formats.
  - Inkling: 403 "only available on agentic harnesses".
  - Gemma 4: rate-limited, and no structured outputs.
  - GLM 5.2: 33K context is too small for the sources.
- **`rotation.Rotation`.** A call tries the model that answered last first, then the others in configured order. A model that fails is skipped for that call only, with no waiting. A rejected key (401) stops at once, because every model would fail the same way. `NoModelAvailable` names each model's failure.
  - One `Rotation` lasts the whole run (`cli.run`), so later pages skip a model that is out.
  - The answers log records the model that answered each page.
  - `config.toml` lists the models; a single string is still a rotation of one.
- **OpenRouter reports some failures as HTTP 200.** An overloaded provider comes back as 200 with an `error` body (code 503) and an empty choice. `_ask_model` treats an error body as a failure whatever the status.
- **The text helper** is the package's `policy.text_for(cfg, …)`, called with one `TEXT_MODEL` that is read once at import. `jev.rotate_text_helper` wraps it after `load()` and calls the original with `dataclasses.replace(cfg, text_model=m)` for each model in turn. This works only because `server.py` looks up `policy.text_for` at call time; `contract_check` fails if it stops doing that.
- **The account's free quota is a hard stop.** Later the same day, every free model returned 429 "Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free model requests per day". The limit is per account, across all free models, 50 requests a day without credits. The rotation raised with all five reasons, as intended.
- **Jev on Vercel** has now accepted the card (live 2026-09-24).
  - It answers with `gateway.cost = "0"` (the free credit) and `marketCost ≈ $0.0000116` per call.
  - It returned 429 "high demand" and 503 "Service temporarily unavailable" on 7 of 42 live tests with one 2 s retry. `decide.RETRY_WAITS = (2, 5, 15)` brought that to 1 of 49.
  - A decision that still fails sends the job to Needs Attention (`decision`), and `requeue` brings it back.

## Chat models through Vercel AI Gateway (user decision, 2026-09-24)

The user asked to be able to run the answer engine through Vercel AI Gateway once OpenRouter's free quota is used up, with a config setting naming the provider. They gave `mistral/mistral-nemo` as the model.

- **Vercel's OpenAI-compatible endpoint:** `POST https://ai-gateway.vercel.sh/v1/chat/completions` with `Authorization: Bearer $AI_GATEWAY_API_KEY` ([docs](https://vercel.com/docs/ai-gateway/sdks-and-apis/openai-chat-completions)).
  - It takes the same `response_format` (strict `json_schema`) and `reasoning: {"enabled": false}` as OpenRouter, so one code path serves both.
  - Errors come as `{"error": {"message", "type"}}`; cost is in `usage.cost`.
  - The Python `ai` SDK from the user's snippet is async and streaming; the REST endpoint needs no new dependency.
- **Config:** `models.chat_route` (`openrouter` or `vercel`) selects the `[models.openrouter]` or `[models.vercel]` table, the key (`config.chat_key`) and the URL (`config.chat_url`).
  - The answer engine posts there.
  - The package's text helper follows the same route (`TEXT_MODEL_BASE_URL`/`TEXT_MODEL_API_KEY` in `jev.env_values`).
- **Mistral Nemo, live 2026-09-24:**
  - Strict `json_schema` gave a valid answer set (1,165 tokens in, 705 out, $0.000043, 49 s).
  - `json_object` mode used the wrong top-level key (`page_answers`).
  - As the text helper it answered `{"text": "Dublin"}` in 1–7 s.
- **Rate limit:** Vercel returned 429 "this team's limit of 5 requests per minute (per region) was reached. Retry after 22s", with a `Retry-After: 22` header. The limit is per model, and the gateway had also tried the model's second provider. The answer engine now waits out a `Retry-After` of up to 30 s once per model (`RETRY_AFTER_MAX`) before moving on.
- **Answer quality:** Mistral Nemo and Mistral Small are weaker than the free Qwen on the live fill tests.
  - The f02 fixture's second step has an optional "Years of experience" field. Both Mistral models marked it required and computed the total wrong (the code recomputed 1 from the quoted lines), so `check_answers` dropped the answer and the job went to Needs Attention (`unanswered`).
  - The answers test passed on this route; both fill tests (f02, f08) failed on this.
