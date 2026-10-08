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

## Live runs on the seven requeued jobs (2026-09-24, Vercel route, `--no-record`)

OpenRouter's free quota was used up (reset 02:00 CEST), so `chat_route = "vercel"` was used. Every problem found was fixed and re-run:

- **A lone UTF-16 surrogate ended the run.** LinkedIn postings use styled letters (U+1D400…), and the package cuts page text in JavaScript, which counts UTF-16 units. A cut can leave `\ud835` alone.
  - Writing `calls.jsonl` raised `UnicodeEncodeError`, which ended the run on job 1.
  - Inside the package, every `browser_goal` failed, because httpx encodes request bodies as UTF-8.
  - Fix: `jev.clean_text` on everything the package returns, and `jev.clean_requests`, which wraps `policy._post` (the package's one sender, checked by `contract_check`).
- **Jev on Vercel fails often, and more often the more questions a request carries.** The 503 "Service temporarily unavailable" comes from the provider (`typesafe-ai`, no fallback), and it seems one failing question fails the whole request.
  - Measured on a captured page: 44 questions per request failed 5 of 6 times, 11 per request 7 of 12, 4 per request 4 of 33.
  - `decide.BATCH = 4`, 4 batches in flight, each retried on its own with `RETRY_WAITS = (2, 5, 15, 30, 60)`. The same three captured pages then judged 6 of 6, in 0.4–53 s.
- **The agent's goals got 503 too.** The package retries for only about 1.5 s, and the job was reported as "no way forward".
  - `Jev.goal` now asks again after each `RETRY_WAITS` step when the decision model was unavailable and no step was taken. Still out, it raises `DecisionError` (class `decision`).
  - Mastercard got through five 503s this way and reached its apply page.
- **Mistral Nemo on Vercel is slow.** A realistic page (5.3K tokens in) took 58.6 s against the answer engine's 60 s timeout, and Linda AI and Digital Manufacturing timed out.
  - Mistral Small took 5.2 s for $0.0013 a page. It is now first in `[models.vercel]`, with Nemo as its fallback, and `ENGINE_TIMEOUT = 120`.
- **Genesys (Workday):** "Apply Manually" leads to step 1 of 6, "Create Account/Sign In". The job stops with `credentials`, which is correct: accounts and passwords are the user's. Once the user signs in to Genesys's Workday in Chrome, the next run continues from "My Information".
- **Linda AI's Easy Apply** (a first-run failure): the agent clicked "Continue applying" on LinkedIn's safety reminder, reached the form, filled step 1 (email, country code, phone) with Mistral Small, and clicked Next.
- **The "stale tab" before the first job** (user report) is the TabBook helper's tab, opened before the first job and kept for the whole run to learn full tab IDs. It now shows a page titled "Application Assistant (working)" instead of about:blank.
- **Resume upload on Ashby (The Flex, both jobs).** The page has an "Upload file" button (autofill), a file input "Resume", and an "Upload File" button for that same input. Jev picked the input, but split its confidence with the button (0.4 < `RESUME_CONFIDENCE`), so the job went to Needs Attention. The resume question now offers only file inputs when the page has any; buttons are offered only when there is none (LinkedIn's "Upload resume").
- **Select options named by their own ref (Toast, Greenhouse).** The model answered "Are you currently based in Ireland?" with option `e31:3` ("No") of a native select. No element carries that ref, so the field went to a page goal (SELECT, 5 steps), and the read-back found nothing to check, although the page showed `current: "No"`. The result was "field would not accept its value". `fill._select_option` now resolves `eN:k` to the select and its option label, for the direct `select` op and for the read-back. The answers log now records `ref` and `option_ref`.
- **Chrome's "Allow remote debugging?"** Runs 6 and 7 (16:57 and 16:58) stopped in preflight after 180 s: nobody clicked Allow. Earlier runs that day connected, so the prompt came back for these connections. It is the user's to click.

## Page work moved from rules to Jev (user decision, 2026-09-24)

The user's aim: as little rule-based browser work and as few rule-based guards as possible. From the research report on Jev browser agents (jev-ultrafast, jkudish/jev-browser, hunch, CUA-S1-FORMS) the order was: the field check, the unsure-answer fallback, filling, then the guards (user choice: Jev alone, no fixed never-submit floor).

1. **Read-back** (`fill.mismatches`): one Jev choice per field on the fresh page, "holds / different / empty". Jev's top choice decides. It replaces the rules per kind of control (`_holds`, `_option_now`, option refs, the combobox page-text rule). On Toast's filled Greenhouse page (captured as `run-20260924-toast-filled`) Jev read "No" in the select at 0.98, and the phone widget's "+393519358813" for the typed "351 935 8813" as "holds" at 0.92–0.95. As a yes/no question, the phone sat at 0.36–0.54 around the threshold.
2. **Unsure resume pick**: no fixed stop at confidence 0.5. Jev is asked again over its two likeliest picks (`decide.narrow`). If it is still unsure, the answer engine's ref for the resume question settles it, when it is one of the two. On The Flex, offering only file inputs (see above) already gave 0.90–0.95.
3. **Filling** (`fill.plan_fill`, jev-ultrafast's speculative fan-out): per answer, Jev picks the operation (type / select / check / widget), the field and the option, in one round. The answer engine's refs go in only as hints. The code checks only that the pick can be carried out (typing needs an editable text field, checking needs a radio or checkbox, selecting needs a listed option), then acts with the answer engine's exact text. `direct_op`'s routing by control kind is gone. Live: the right operation and target on Toast (including two answers with no hints) and on Digital Manufacturing's radios, 2.9 s for 6 answers.

`tests/rule_decider.py` answers the new `readback` and `fill` questions with the old rules, so the offline tests keep running.

## Never-submit: one narrow rule, no model calls (user decision, 2026-09-24)

The user first chose Jev alone for the never-submit decision (no fixed floor). Asking Jev on every click would cost a model call per click, so the user replaced that with: handle only buttons labelled "Submit" or "Apply" that come after filling the application form.

- **The rule** is `guard.never_click(name, role)`: a button or link whose label says "Submit" is refused on any page, and one that says "Apply" once `fill_page` has started (`guard.FORM.started`, reset at each job's `run_pages`).
  - `jev.guard_clicks` replaces the package's `browser.confirm_reason`, which `browser.py` calls before every click, the agent's and ours. So one function covers both, and `JEVMCP_CONFIRM_PATTERNS` is no longer set.
  - `contract_check` fails if the hook stops applying.
  - Uploads count as clicks: the package clicks a non-input upload target to open its file chooser.
- **Removed:**
  - `entry.py` (`confirm_click`, the `ENTRY` token, Jev's `starts_application`, the no-field-holds-a-value check, the retries);
  - the `ENTRY_SUBMITS` probe;
  - the label lists (Send / Confirm / Done / Finish / Complete);
  - the Enter-key and `type submit` refusals;
  - the package's default payment and deletion patterns.
- **How the loop works now:**
  - Before the form, "Apply" / "Easy Apply" is the agent's own click, followed by a wait for a new tab or dialog.
  - A click that fails because the page changed under it (Genesys's re-render) counts as movement, not "stuck".
  - The guest link of a sign-up wall is a plain click.
  - In the form, a refused click marks the last step.
- **Not covered, by decision:** "Send application", "Done", "Finish", "Confirm", Enter-submitting forms. An "Apply" that is a form's own submit button *before* filling starts is not refused either: that would be the navigate agent clicking a form's Apply instead of reporting the form (Toast's Greenhouse form ends in "Apply now!").
- **Tests:** the tripwire walks f01–f03 (Submit application / multi-step Submit / Apply) with the form being filled, and nothing is sent. Live, the real agent was told to submit those forms 11 times per run; it was always refused or stopped, and nothing was sent in two runs. One earlier status check failed once without recurring, and nothing was sent in that run either.
- **"Send" added** (user decision, same day): "Send" (`\bsend\b`) is refused on every page, like "Submit". A Send button is never needed to start an application; "Sender" and "Sending" don't match. The tripwire now also walks f05 ("Send application"). Live, the agent was told to click it and was refused. Across 9 further live tripwire runs (up to 14 checks each), every attempt was refused and nothing was sent.

## Rename "answer engine" → "LLM inference" (P0 re-core, 2026-09-27)

The re-core (00_common §7) renames the component formerly called the "answer engine" to "LLM inference" everywhere in code, config, prompts, tests and docs. Names for answer *values* keep "answer". Old entries above keep the old name by decision; this entry records the rename.

Renamed:
- Module `assistant/answers.py` → `assistant/llm_inference.py`; prompt `prompts/answer_engine.md` → `prompts/llm_inference.md` (content unchanged); all imports updated.
- `AnswerEngineError` → `LLMInferenceError`; `ENGINE_TIMEOUT` → `LLM_INFERENCE_TIMEOUT`.
- Config: `[models.openrouter].answer_engine` / `[models.vercel].answer_engine` → `.llm_inference`; `ChatModels.answer_engine` field and `Models.answer_engine` property → `llm_inference`; `config.problems()` check. An old `answer_engine` key now fails validation with `models.<route>.answer_engine was renamed to models.<route>.llm_inference` (a `model_validator` on `Models`, marked `rename-guard`).
- Needs-Attention class `answer_engine` → `llm_inference` (`fill.py`).
- Prose "answer engine" → "LLM inference" in the report, terminal text, comments, README.md, CLAUDE.md, LIVE_TEST.md and the build spec.

Kept (00_common §7 — these name the answer value or its action, not the component): `Question.answer`, `PageAnswers`, `check_answers`, `judge_answers`, `judge_questions`, `answers.json`, "unanswered", and the function/parameter names `call_engine`, `answer_page`, `answer_fn`.

No behaviour change: the offline suite reproduces `tests/golden/p0_baseline/` (T6, `test_baseline_p0.py`), and `test_rename.py` asserts no component-name token survives.

## Where the upstream provider sits in a chat response (live, 2026-09-27)

One `mistral/mistral-small` call to `https://ai-gateway.vercel.sh/v1/chat/completions` (5 tokens out) showed that Vercel's OpenAI-compatible chat response has no top-level `provider` or `provider_metadata`. The routing record sits inside the message, at `choices[0].message.provider_metadata.gateway.routing`. That record has `finalProvider` (the provider that served the call after any fallbacks; here `mistral`), plus `resolvedProvider`, `modelAttempts[].providerAttempts[]` (with each attempt's `statusCode`) and `fallbacksAvailable`. Cost is at `provider_metadata.gateway.cost` in the same place, and also at `usage.cost`. OpenRouter puts the upstream in a top-level `provider` field. `inference_log._provider` reads OpenRouter's `provider` first, then Vercel's `finalProvider`, and logs just the gateway when neither is there (error bodies, the TypeSafe API's top-level `provider_metadata` without `routing`).

## The package's `policy._post` retries internally (P0 T3.3, 2026-09-27)

00_common §T3.3 asks whether the vendored package retries by calling `_post` again (each attempt visible to our wrapper) or inside `_post` (only the last visible). Evidence: `jev_ultrafast_mcp/policy.py` `_post(url, key, body)` is `for attempt in range(3): ... if status in {429, 529, 503} and attempt < 2: sleep; continue`. So it retries **inside** `_post` and returns only the last attempt's body (or raises `TurboUnavailable`).

Consequence for the logs: `jev.clean_requests` wraps the whole `_post`, so for the package's own calls (the goal agent's Jev decisions, option picks and the text helper) `jev_inference_logs.json` / `llm_inference_logs.json` record **one entry per `_post` call — the last attempt only**, not the intermediate 429/503 retries. Our own senders (`decide.Decider._one`, `llm_inference._ask_model`) log every attempt, because their retry loops live in our code. A failed `_post` raises rather than returning an error body, so the wrapper catches it, logs the attempt with the exception text in place of the response, and re-raises.

## Jev moved to OpenRouter System One; per-route Jev model (user decision, 2026-09-28)

Vercel AI Gateway's Jev now returns **HTTP 403** for the account: "Free tier users do not have access to this model. Upgrade to paid credits…". Preflight stops there (`the decision model (Jev) does not answer: decision model HTTP 403`), so no live run could proceed.

The user supplied a TypeSafe-compatible System One model on OpenRouter — `respan/span-01-lite:free`, reached at `https://openrouter.ai/api/alpha/decisions` (same request/answer shapes: `state`, `questions` typed noul/choice/score; `answers[…]["noul"|"choice"|"probabilities"]`). Changes:

- **Endpoint fix.** `decide.ENDPOINTS["openrouter"]` was `https://openrouter.ai/api/v1/systemone`, which is not the System One route; corrected to `https://openrouter.ai/api/alpha/decisions` (already what `jev.agent_route` and the package's no-key hint use). Removed the unused `decide.SYSTEMONE` constant.
- **Per-route Jev model, like llm_inference.** `ChatModels` gains a `jev` field, so each `[models.<route>]` table names that route's Jev model; `Models.jev` is now a property selecting by `models.jev_route` (parallel to `chat`/`chat_route`). config.toml: `[models.openrouter].jev = "respan/span-01-lite:free"`, `[models.vercel].jev = "typesafe-ai/jev"`, and `jev_route = "openrouter"`. `config.problems()` now also flags an empty Jev model for the active route.
- **Key.** On `jev_route = "openrouter"`, Jev uses `OPENROUTER_API_KEY` (via `config.jev_key`), so `.env` must have it.

Deviations, recorded per 00_common §3/§5.2:
- **P0 is "no behaviour change"**, but this changes the decision model, route and endpoint. It is a user-directed fix to unblock the P0 live gate, kept minimal (config + one endpoint string + the offline tests that pinned the old endpoint/model). T6 is unaffected (it runs offline on RuleDecider; the LLM inference request bodies it snapshots do not involve Jev).
- **D14** named Jev `typesafe/jev-1.13` on OpenRouter and "no `:free` models". The user overrode both. Caveat from earlier findings: OpenRouter free models are rate-limited (≈50 requests/day/account) and can answer 429/overloaded, so this route may be flaky under load; a paid System One model would be steadier.

## Preflight live-probes the LLM inference model (user decision, 2026-09-28)

Preflight probed Jev live but only static-checked the LLM inference model (key present, model list non-empty), so a configured-but-dead or out-of-credit chat model passed preflight and failed later mid-job. The user asked preflight to probe the LLM inference engine live, like Jev.

`cli._probe_llm_inference(cfg)` now calls `llm_inference.call_engine` once with a trivial empty page over the real strict-`json_schema` path (`config.chat_url`, the run's `Rotation` of `models.<chat_route>.llm_inference`, `PROBE_TIMEOUT = 60 s`). It raises `LLMInferenceError` when no configured model answers; `preflight()` turns that into a `PreflightError` ("the LLM inference model does not answer: …") and otherwise prints `✓ LLM inference <model> answers (via <route>)`. The probe logs to `_run/llm_inference_logs.json` like any other chat call.

Deviation: this adds a preflight step (behaviour change), out of P0's "no behaviour change". It was the fixed-scope item flagged for P1 (`P1_infrastructure.md`), pulled forward at the user's request. No offline test change (no test calls `preflight()`); the probe itself is unit-tested via a monkeypatched `call_engine`. Cost: one extra chat call per preflight.

## Config names Jev by its role, not the instance (user decision, 2026-09-28)

"Jev" was used as if it were the decision-model role in config and code, but Jev (`typesafe-ai/jev`, `typesafe/jev-1.13`) is one *instance* of a System One decision model; another is `respan/span-01-lite`. Renamed the role, keeping Jev where it means the actual instance/package.

- config keys: `models.jev_route` → `models.system_one_decision_provider`; `[models.<route>].jev` → `[models.<route>].system_one_decision_model`.
- code: `ChatModels.jev` → `system_one_decision_model`; `Models.jev` property and `Models.jev_route` → `system_one_decision_model` / `system_one_decision_provider`; `config.jev_key` → `config.system_one_decision_key`; readers in `decide.for_config`, `jev.env_values`/`agent_route`, `cli` (preflight/report/`_static_checks`); prose in config.toml, config.py, decide.py, README.md, CLAUDE.md, the build spec, and the tests.
- Kept as the Jev instance/package: the `jev.py` module, `Jev` driver class, `JevError`, `jevlib`, `jev_ultrafast_mcp`, `JEVMCP_*`, the model-id values (`typesafe-ai/jev`, `typesafe/jev-1.13`), and the browser worker thread name. `decide.adapt_questions_for` still keys on `"jev"` in the model id (Jev takes native structured questions). DISCOVERY's earlier entries keep the old names.

Not a P0 goal (naming), done at the user's request alongside the earlier LLM inference work. 237 unit pass; contract_check + dry-run clean.

## The System One decision model can run on this Mac: Kev (user decision, 2026-09-28)

The user asked for the System One decision model to run locally instead of on a paid key, on a 16 GB M3 Mac, naming
[github.com/jaredpalmer/kev](https://github.com/jaredpalmer/kev). Read: Kev's README, `kev/api.py` and `kev/serve.py`
at `main` on 2026-09-28.

**What Kev is.** Small decision models (0.8B, 4B, 9B, 27B: LoRA + a pointer head on Qwen3.5/3.8 bases) that serve
**TypeSafe's own System One API**: `POST /v1/systemone` with `{state, model, questions}` in and typed `answers` out,
plus `GET /v1/models`, `POST /v1/systemone/{permute,separate}` and the `x-typesafe-request-id` header. The server
binds to `127.0.0.1`, is **open by default**, and requires `Authorization: Bearer <KEV_API_KEY>` only when started
with that variable set. On Apple Silicon `uv sync --extra serve` installs MLX and the server uses it (bf16).

**Why it drops in.** Its request models (`kev/api.py`) take exactly what we and the package already send:
`state` is `str | dict | list | int | float | bool | None`, question `instructions` the same, and choice `criteria`
is `dict[str, JSONContent]` with 1–255 options — so the package's nested criteria (`{ref: {element, current_value,
context}}`, up to 120 refs) and our structured `instructions` need no flattening (`adapt_questions_for` is only for
OpenRouter's respan models). Objects are rendered as **labeled text** (`api.render`), so a dict state reads better,
and costs fewer tokens, than the JSON string the OpenRouter route needs.

**Checked offline against Kev's own schema.** A stand-in server built on `kev/api.py`'s pydantic models
(`SystemOneRequest`) answered both callers: `decide.Decider` and the package's own goal agent (`policy.choose`,
with a real `Observation`). 0 rejections (422), state sent as an object, `model: "kev-latest"`.

**What changed here.**
- `[models.local]` in config.toml (`base_url`, `system_one_decision_model = "kev-latest"`, `state_chars`, `timeout`)
  and `system_one_decision_provider = "local"`, the third value of that key. `config.LocalKev`;
  `Models.system_one_decision_model` now reads the named table by attribute.
- `config.local_key()` (`KEV_API_KEY`), `config.KEYLESS_PROVIDERS`: `cli._static_checks` no longer demands a key for
  a keyless route, and `run_preflight` says "no key needed".
- `decide.endpoint(cfg)` (base_url + `/v1/systemone`), `Decider(url=, state_chars=, timeout=, retry_waits=,
  keep_object_state=)`, `fit_state(state, limit, keep_object)`, `decide.server_card()` (preflight's `GET /v1/models`,
  which prints the loaded checkpoint and backend) and `decide.start_hint()` (how to start a server that is down).
- `jev.agent_route` points `TYPESAFE_BASE_URL` at the server. The package refuses turbo mode on an empty key
  (`policy.available`, `policy.choose`), so an open server gets the placeholder `TYPESAFE_API_KEY=local`, which it
  ignores. `jev.clean_requests(string_state=False)` leaves the state an object on this route, and
  `jev.set_call_timeout` replaces the package's fixed 30 s `policy.CLIENT` with `models.local.timeout`.
- `run_kev_server.command`: clones or updates `~/kev`, `uv sync --extra serve`, serves `KEV_MODEL` (default
  `jaredpalmer/kev-0.8b`) on `KEV_PORT` (8009).

**The cost of it, from Kev's own numbers.** Accuracy on sources a model was not trained on: Jev 0.857, Kev-4B 0.817,
**Kev-0.8B 0.648** — and 4B and 9B are "32 GB Mac" in Kev's table, so 0.8B is the size for 16 GB. Kev was trained on
states of up to 384 tokens and loses accuracy on long ones (Kev-9B answers 0.556 of questions buried in 1k–6k tokens),
which is why `models.local.state_chars` cuts our state to 12000 characters (~3000 tokens) from the cloud route's
60000. At a 5% error budget Kev automates 0.45–0.57 of decisions against Jev's 0.70, and its calibration temperature
is fitted in distribution, so `decide.THRESHOLDS` (set for Jev) are not measured for it. Expect more jobs in
`Needs-Attention/`, not wrong answers: a decision that cannot be got is still never guessed.

**Deviations and open items.**
- Only the decision model moves. The LLM inference and the text helper still need `models.chat_route`'s key: Kev
  serves no chat/completions endpoint.
- The thresholds were not re-fitted for Kev; that needs the user's own labelled pages.
- `jev_inference_logs.json` already records every request and answer (`inference_log.log_jev`), which is the training
  file shape `kev.train` takes (`{state, questions, label}` per line): a fine-tune on the logged Jev answers is the
  way back to Jev-level accuracy on these pages, and Kev's README measures 0.804 → 0.904 for one such run.
- Not verified against a real Kev server (no Apple Silicon and no weights in this container): the wire contract was
  verified against Kev's own request models, and the first real check is `preflight` on the user's Mac.

## One gateway for every model request (P1 T2, 2026-09-28)

P1's evidence (build spec §13.1): one run sent 222 System One requests in 19 minutes, with four batches in flight
and nested retry loops that could reach ~18 HTTP requests for a single goal step. The cause was three senders that
each had their own queue, retries and timeout:

| Sender | Queue | Retries | Timeout |
|---|---|---|---|
| `decide.Decider` | batches of `BATCH = 4`, `PARALLEL = 4` in flight | `RETRY_WAITS` = 2, 5, 15, 30, 60 s | 30 s |
| `llm_inference._ask_model` | none (a model rotation) | one `Retry-After` wait | 120 s |
| the package's `policy._post` | none | `range(3)` on 429/529/503, inside one call | 30 s, fixed in `policy.CLIENT` |

**Worst case before:** a goal step asked the package's `_post`, which tried 3 times; our `Jev.goal` asked the whole
goal again after each of 5 `RETRY_WAITS`; a judgment of 16 questions went out as 4 batches side by side, each with
its own 5-wait ladder. 3 × 6 = 18 HTTP requests per goal step, and 4 concurrent connections per judgment.

**Worst case now:** `Gateway.send` is the only sender. One request, at most `limits.max_attempts = 3` attempts,
one at a time (`limits.max_in_flight = 1`), at least `limits.min_interval_s = 0.25` apart. A goal that took no step
is asked again once (`jev.GOAL_RETRY_WAIT`), so a goal step is at most **6** HTTP requests, down from ~18, and a
judgment of any size is at most 3 requests per 24 questions instead of one ladder per 4 questions. The package's own
`range(3)` and its `policy.CLIENT` are no longer on any path: `clean_requests` answers every `_post` from the
Gateway and never calls the package's `original`. `jev.set_call_timeout` was deleted with it — the local route's
timeout is now `gateway.timeouts[JEV]` from `models.local.timeout`.

**Batching is gone.** `BATCH`, `PARALLEL` and the `ThreadPoolExecutor` are deleted. A judgment is one request, split
only above `jev.max_questions_per_request = 24` and sent in parts one after another. Whether large requests fail on
OpenRouter is **not re-measured this phase**: the account has no System One credit there (402), and the working route
is a Kev server on this Mac, which takes what it is given. The 2026-09-24 Vercel evidence (44 questions per request
failed 5 of 6 times, 11 per request 7 of 12, 4 per request 4 of 33) is why the split exists at all; 24 is P1's number,
and it stays unverified against a cloud provider until one is reachable.

**A model rotation is not a retry layer, and hands over first.** With three attempts per request, a rate-limited
model would cost 3 attempts and 8 s of waiting before the rotation tried a model that was free. So a chat request
passes `attempts=1` while the rotation still has an untried model, and only the last model in the order gets the full
ladder. This is still one retry layer: the next model is a different request, not a retry of this one. Evidence that
it matters: `tests/test_answers.py` pins the old live behaviour (a 429 on `a:free` hands over to `b:free` at once,
2026-09-24), and that behaviour is preserved.

**Logging moved to one place.** `log_jev` in `Decider._one`, `log_llm` in `call_engine` and
`jev._log_package_request` all logged their own attempt; with the Gateway sending, every one of them would have
double-logged. The Gateway is now the single logging point and classifies by body shape, the test
`jev._log_package_request` used (`state` + `questions` = System One, `messages` = chat). `_log_package_request`
survives only for the no-Gateway path a test uses when it drives the package directly.

**OpenRouter needs no usage-accounting flag.** Its docs (usage accounting, read 2026-09-28) say "Full usage details
are now always included automatically in every response", and `usage: {include: true}` and
`stream_options: {include_usage: true}` "have no effect". Cost is at `usage.cost`. So `[prices]` is only a fallback
for a gateway that reports nothing, and a cost derived from it is marked "estimated" in the report.

**`provider.require_parameters` is OpenRouter-only.** Added to chat requests on that route so OpenRouter routes only
to a provider that supports every parameter sent, instead of one that silently drops `response_format` or
`temperature`. Vercel AI Gateway has no such field, so the flag is gated on the route. This is the one intended
change to `tests/golden/p0_baseline/model_bodies.json`; the golden was patched rather than regenerated, so any other
drift would still fail T6.

## Three clean stops, and why they are StopRun subclasses (P1 T3, 2026-09-28)

`ProviderOutage` (3 requests in a row fail, or 5 of the last 10), `BudgetExceeded` (`budget.max_usd_per_run`,
checked before each request) and `CreditOrKey` (401/402/403, never retried) all subclass `blockers.StopRun`.

That choice is the whole design. `cli.run` already turns `StopRun` into exit 3 with the report written and **no**
record for the current job, which is what D21 asks for. The alternative — a new exception family — would have had to
survive five `except` sites that convert model failures into `NeedsAttention` (`cli.py` at the job level, `fill.py`
in four places), and a `NeedsAttention` **writes a record**: it moves the folder, updates the tracker row and adds a
`job.md` note. A breaker that recorded the job it stopped on would be worse than no breaker.
`tests/test_clean_stop.py` asserts the outcome on temp copies of the tracker and the folders, including the contrast
case (a `NeedsAttention` job still is recorded).

**403, not only 401 and 402.** P1 names 401 and 402. Vercel AI Gateway answers **403** for an account with no card
("Free tier users do not have access to this model", 2026-09-28), which is the same condition: not cured by waiting,
not cured by another model. Treating it as a generic failure would spend three attempts and then trip the breaker
with the wrong reason, so 403 is `CreditOrKey` too, and the provider's own message is carried into the stop reason.

**A stop raised on the package's thread.** `Jev.call` catches `Exception` broadly and turns it into an
`"error(...)"` string, so a stop raised inside `policy._post` would be swallowed there and the job would be recorded
as Needs Attention. The Gateway therefore keeps the first stop (`gateway.tripped`) and `Jev.call` asks for it after
every browser call, raising it on the run's own thread. The flag is also what makes the text helper's rotation stop
trying further models after a stop.

**A stop during preflight**, corrected later the same day: it depends which stop. A `ProviderOutage` or
`BudgetExceeded` is a stop (exit 3, with a report). A `CreditOrKey` is a **`PreflightError`** — exit 1, naming the
key variable from `config.KEY_NAMES` — because P1 T1 asks for exactly that ("a 401 or 402 fails preflight with
'check OPENROUTER_API_KEY / add credit on OpenRouter'") and because "preflight failed, nothing written" is the
accurate description of that outcome. The same condition inside the job loop stays a `CreditOrKey` stop with exit 3.

## The chat fallback, and the verdict it shares (P1 T4, 2026-09-28)

`decide.ChatDecider` answers the same typed questions with the chat route's models, returning the shapes
`Answer.parse` already reads, so nothing downstream knows which backend answered. Its calls are chat calls and land
in `llm_inference_logs.json` (D4).

Two things were not obvious:

1. **The verdict belongs to the pair.** T4 says the breaker counts a request as failed "only when both fail". The
   fallback's own `send` therefore defers its verdict as well (`defer_verdict`), and the `Decider` reports one
   verdict for the pair. Without that, one failed judgment fed the breaker twice and three consecutive rescued
   decisions tripped an outage although every decision had been answered.
2. **There is no nesting to deadlock.** `Gateway.release()` was written for the case where the fallback sends while
   the System One request still holds the only queue slot. It cannot happen: `send` frees the slot before it
   returns, so the fallback's send is a separate request. `release()` was deleted as dead code, and the test that
   found it stayed, because a regression there would hang rather than fail.

## A model rotation is one request for the breaker (P1 T2/T3, 2026-09-29)

Found by review, after the first implementation. `llm_inference._ask_model` reported a verdict per **model**, so a
rotation handing over fed the breaker one failure every time it did. On the shipped settings that trips a false
outage: `[models.vercel]` lists two models and Vercel allows 5 requests a minute **per model**, so a busy
`mistral-small` with `mistral-nemo` answering produces failure, success, failure, success — which reaches
"5 of the last 10 failed" while every page was in fact answered. The strict-`json_schema` → `json_object` downgrade
on HTTP 400 has the same shape: one failure per page on any model that rejects a strict schema.

The fix is the rule already applied to the chat fallback (T4): the logical request is "answer this page", so
`_ask_model` defers its verdict and `call_engine` reports one — success when any model answered, failure only when
none did. `tests/test_gateway.py` covers both directions (12 alternating calls trip nothing; three calls where no
model answers count 3 and trip on the third), and both were verified by reintroducing the bug.

The package's own text-helper rotation (`jev.rotate_text_helper`, wrapping `policy.text_for`) has the same shape and
is **not** fixed: our wrapper sees only the individual `_post` calls and cannot tell which belong to one logical
call. P2 removes the package from the send path.

## How many CDP connections a run opens (P1 T5, 2026-09-28)

**Answer: one per process, already.** `jev_ultrafast_mcp.browser.BrowserManager.cdp` (0.1.5+aa6, browser.py
l.908–926) builds `self._cdp` lazily on first use and every session shares it; `browser_close(session)` closes that
session's tab and leaves the socket open, and only `shutdown_browser=True` detaches, which this program never asks
for. So preflight, the `tabbook` helper and every job session were always one websocket, and the single "Allow
remote debugging?" prompt was already the only one.

**What was actually wrong was the handshake budget.** `attach_chrome` is called with
`open_timeout=max(60.0, cfg.call_timeout)` and `call_timeout` defaults to 30 s with **no environment variable** to
change it, so a person had 60 s to find and click "Allow", not the 180 s `cli.CONNECT_TIMEOUT` promised — and the
connection happened as a side effect of preflight's first `browser_open`, after the model probes had already run.

`jev.connect_chrome(cfg, 180)` now opens that one socket explicitly, before preflight, and `cli.connect_once` prints
P1's line first. It sets `MANAGER._cdp` directly rather than raising `call_timeout`, because `call_timeout` is also
the default deadline of every later CDP call, and a 180 s default would turn a hung call into a three-minute stall.
Reaching into `MANAGER._cdp` is a fourth touch of the package's internals, so it is listed in `contract_check`'s
scope alongside the other three.

## P1 deviations, and what the live gate can and cannot prove (2026-09-28)

User decisions this phase, all recorded here because they override P1's own text:

1. **The System One provider stays `local` (Kev on this Mac), not paid OpenRouter Jev.** P1 T1 and D14 assume
   `typesafe/jev-1.13` on OpenRouter. The user chose to keep the keyless local route. Consequence, already known
   from P0: kev-0.8b answers `kind="other"` at 0.12 on a real LinkedIn posting, so no job passes the entry decision
   and **A3 (the live gate's "3 jobs") cannot be met this phase**. The chat fallback does not rescue it — Kev
   answers badly rather than failing, and the fallback fires only on a failure. P1 itself says "the jobs do not have
   to park in P1", so the gate is reduced to what T2–T6 can show: one "Allow" click, highest-in-flight = 1, no
   request over 3 attempts, cost under $1, and a clean stop if a provider fails.
2. **The chat route stays `vercel`** (`mistral/mistral-small`, `mistral/mistral-nemo`), not P1's
   `deepseek/deepseek-v4.1-flash` + `openai/gpt-5.4-mini` on OpenRouter. The paid OpenRouter models are configured
   in `[models.openrouter]` and no `:free` model remains anywhere (T7), but that table is not the default.
   `[prices]` therefore carries the mistral prices as well.
3. **No test may reach a real API** (user instruction). P1 T7's `--live` items — the System One contract test and one
   LLM call per configured model on the `f02` fixture — are **not implemented**. Consequences:
   - **A1's "the `--live` contract tests pass" is dropped.** Every new test runs against a local `http.server` or an
     injected sender; the offline suite costs $0.
   - **P1 T1's endpoint question is not settled.** T1 asks for one endpoint at
     `https://openrouter.ai/api/v1/systemone` for both senders, contract-tested live. On the local route both senders
     already share one URL (`models.local.base_url` + `/v1/systemone`), so the task's goal holds; but the OpenRouter
     value stays `https://openrouter.ai/api/alpha/decisions`, which is what the 2026-09-28 entry above measured
     (`api/v1/systemone` is not the System One route). Build spec §3.2 line 320 still said `api/v1/systemone` for
     that route and has been corrected to match the code.
   - `tests/test_model_access.py` still has no `live_model` marker, by the earlier user decision recorded in
     `CLAUDE.md`; it is the one file a plain `pytest` would spend money through. It was left untouched, and the
     phase's command is `pytest -m "unit or browser"`.

## P1 live gate: the infrastructure passes, and the queue is not what it looked like (2026-09-29)

`runs/20260929-094752`, the user's own run of `run --no-record --limit 3` with the Kev server up. Every P1 check in
`LIVE_TEST.md` holds:

| Check | Observed |
|---|---|
| the "Allow" line before any model probe | printed first (T5) |
| highest number of requests in flight | **1** |
| model spend | **$0.0002**, reported (not estimated) |
| attempts | **6 requests, 6 attempts** — no request retried once |
| `:free` models | none; `mistral/mistral-small` (vercel/mistral) and `mistral/mistral-nemo` (vercel/deepinfra) |
| log key sets | exactly §6.2 and §6.3; no key string in either |
| submission | no click, no `needs_confirmation`, no submit anywhere — only read-only `eval` probes |
| job tabs | left open (the logged `browser_close` calls are `TabBook.release`'s own scratch tabs) |
| `--no-record` | honoured: 7 folders still in `Applications/`, `Pending-Review/` and `Needs-Attention/` empty |

**The batching change is visible.** Each job's entry judgment — 12 questions, 4 `choice` and 8 `noul` — went out as
**one** System One request. Before P1 that was three batches of four, side by side, each with its own five-wait
ladder. Three jobs took 11 s of browser work in total.

**Two findings, neither a P1 regression.**

1. **kev-0.8b, confirmed again.** Linda AI's State contains `"Easy Apply to this job"` and the model still answered
   `kind="other"` at confidence **0.1234**, with a flat distribution over eleven options (`other` 0.2031,
   `job_posting` 0.1652, `application_form` 0.1577). Genesys 0.1539, same shape. Identical to the P0 measurement
   (0.1238), so the cause is model capacity, not anything P1 changed.

2. **Two of the three jobs were never Easy Apply jobs.** Genesys and Mastercard carry `"Apply on company website"`
   and no Easy Apply control at all: by **D12** they should be Needs Attention class `external_ats`, "external ATS,
   not yet supported". They were reported as `load_failure` instead, for two reasons that compound:
   - `external_ats` **is not implemented anywhere** — no occurrence in `assistant/`, `tests/` or the build spec,
     although D12 is a settled decision. P5 is the external-ATS phase; this is a pre-existing gap, recorded here
     because this run is the first evidence of it reaching the user as a wrong reason.
   - even implemented, it would not have fired: `pages.classify_entry` branches on the System One `kind`, and with
     kev answering `other` every posting falls to the same `none` → `load_failure` path.

   **Consequence for the gates that follow:** a queue of "3 jobs" was really testing **one** Easy Apply job. The
   P3 and P4 targets (one job parked end-to-end; ≥ 7 of 10 parked) need a queue verified to be Easy Apply before
   its numbers mean anything.

**Left as it is, by user decision (2026-09-29):** `Counters.row()` does not pluralise, so the Timings rows read
"1 attempts". Cosmetic; not fixed this phase.

## P2 T1: golden observer tables captured before the driver port (2026-09-29)

`tests/capture_tables.py` runs the current vendored observer over every `tests/fixtures/**/*.html` page and
writes `tests/golden/tables/<fixture>.json` (38 files). This is the frozen parity baseline the owned driver
(`assistant/driver/`, P2 T2) is graded against in T6; the branch is `recore/p2-driver-guard-navigation`, off `main`
(P1 is already in main).

- **Scope: `tests/fixtures/` only, not `tests/captured/`.** The fixtures are the pages the browser tests actually
  serve and re-observe headless, so they are what the new observer can be graded against. `tests/captured/` holds
  live-site HTML/observe snapshots for reference, not served fixtures. (Decision — the phase file says only "every
  fixture page".)
- **Normalization: each element is `{ref, role, name, value, checked, options:[label…]}` in table order.** These are
  the fields T6 parity compares ("the same controls … in the same order"). The observer's other fields and any
  longer strings are allowed to differ, so they are not stored.
- **T6 must compare strings by prefix, not equality.** The current observer cuts name/label/value to 160 chars
  (build spec B6); the owned driver will not (P2 removes the cut). The golden holds the cut strings, so parity
  checks `new.startswith(golden)` after stripping any trailing ellipsis.
- Counts sanity-checked: `f12`, `f17_captcha`, `4012345605-submitted` have 0 controls (expected);
  `probe_lab/display_contents.html` lists its 3 children through a `display:contents` wrapper, confirming the aa
  patch is in the baseline.

## P2 T2: owned browser driver in `assistant/driver/` (2026-09-29)

Ported `cdp.py`, `observe.py`, `session.py` (from the package's `browser.py`), `observer.js` and `assertions.py`
from jev-ultrafast-mcp 0.1.5+aa6 (MIT; `assistant/driver/LICENSE` kept). Parity holds on all 38 fixtures
(`tests/test_parity.py`): the same controls in the same order as the golden capture. Changes from the vendored code:

- **One guarded mouse-press path** (`Session._press`). Every click — a click op, a toggle, a file-chooser upload,
  and the focus click of a `type` — runs an injected `refuse_click(descriptor)` on the element's **live** descriptor
  (`window.__jevMcp.descriptor`, added to `observer.js`) before dispatching. This is the never-submit defence in
  depth (00_common §4.1): even a `type` on `<input type=submit>` is refused, which the vendored code allowed (it
  checked only `click`, via `confirm_reason`). The check reads the live node, not `self.last`, which is stale after
  the first op of a batch.
- **No `keys` op and no `type … submit` Enter branch**; `_dispatch_keys`/`KEY_SPECS`/`MODIFIERS` are deleted. The
  driver has no way to send Enter / NumpadEnter / Escape.
- **Ops kept:** click, type, select, toggle, upload, reload, wait_for_load, screenshot, eval (the §3.4 keep-list).
  `hover`/`scroll`/`wait`/`wait_for_*`/`nav`/`back`/`forward` and the `tab` op are gone; tab actions are `Session`
  methods (`switch_tab`/`close_tab`), navigation is `Session.navigate`.
- **`DriverTimeout`** (a `DriverError` subclass) replaces the `os._exit(3)` path: a hung CDP call raises and the
  process stays alive. `JevError` → `DriverError`.
- **A tab opened by the page** is found through `Target.targetCreated` openers (`Cdp.openers`, targetId→openerId,
  never cleared even when `events` is), and `attach_chrome` now calls `Target.setDiscoverTargets` so those events
  arrive. The vendored `tabbook` helper/scratch tab is gone; nothing is closed at process exit (no `atexit`).
- **New element fields** from `observer.js`: `type`, `tag`, `required`, `maxlength`, `placeholder`, `form`,
  `dialog`, `consent`, `scope`. The 160-char cut on name/label and the 300-char cut on value are gone (a 4000-char
  sanity bound); the eval result's 200-char cap is now 256 KB. Whitespace is still collapsed, so the driver's
  strings prefix-match the golden.
- **`Settings`** (constructor, from `config.toml`'s `[browser]`) replaces `JEVMCP_*` / `Config.from_env`. Macros,
  the `browser_goal` support, `launch_chrome` and the domain envelope are dropped. Only `assistant.browser` imports
  the package (T3).
- Known: `websockets`' sync `connect()` prints a `DeprecationWarning` (used without a context manager, as in the
  vendored code); functional, left for now.

## P2 T4: guard v2 — the absolute never-submit rule (2026-09-29)

`guard.never_click_element(el, page)` is the element-level rule the owned driver enforces in its press path;
`guard.looks_final(elements)` is the deterministic final-page judgment. Both are added **alongside** v1
(`never_click` / `check`), which `jev.py` still uses until T3 removes it, so the offline suite stays green
(`tests/test_guard.py` unchanged, 36 tests; `tests/test_guard_v2.py` new, 16 tests).

- **Absolute (00_common §4.1):** refuses a label matching `\b(submit|send|confirm|done|finish|complete)\b`
  everywhere; `apply` once `FORM.started`; a structural submit (`type=submit`, a `<button>` without `type` inside a
  `<form>`, `<input type=image>`) unless its label is on the advance allowlist
  (`^(next|continue|continue to next step|review|review your application|save and continue)\b`) **and** the page is
  not final; and every click on a final page.
- **Behavior change from v3:** done / finish / complete / confirm are now refused. v3 deliberately allowed them
  (`test_the_rule_covers_only_submit_and_apply`); v2 makes the rule absolute as P2 requires.
- **Exemption:** a cookie-consent control (observer.js emits `consent`, the matched container) outside any
  application form and the application dialog, whose label is not submit/send/apply — so "Confirm my choices",
  "Accept all", "Reject all" in a banner are allowed. A `<div class="consent">` is **not** a known container
  (`consent=""`), so a "Submit" inside it is refused (T5 fixture).
- **`looks_final` avoids v3's circularity.** v3 inferred final *from* a refused click; now the driver refuses
  submits itself, so final is read from the table: a submit-like control present and no advance control. This
  "page is judged final" definition is a design choice (other heuristics are possible); it is deterministic and
  table-only, and is flagged for the user to confirm at the live gate.
- The driver never imports guard: `Browser` injects `never_click_element` as the `refuse_click` callable and passes
  the page state (T3), keeping job state out of the driver.

## P2 T5: deterministic LinkedIn Easy Apply navigation (`assistant/navigate.py`, 2026-09-29)

`navigate.enter` and `navigate.advance` replace the three `browser_goal` calls, with **no page-kind model call**
(kev answers `other` at 0.12 on a real posting, so the whole `judge().kind` path fails). Proven end-to-end offline
on `4012345610-easy-dialog` (`tests/test_navigate.py`, 6 tests): Easy Apply → dialog → Contact → Resume → Questions
→ Review → final, with **zero POSTs**.

- **Entry from facts, in order:** an Easy Apply button (label starts "Easy Apply") → click and wait ≤8 s for the
  dialog; else closed text ("No longer accepting applications") → Needs Attention `closed`; else applied text
  ("Applied … ago" / "Application submitted" / "See application") → `applied`; else a bare "Apply" link/button →
  `external_ats` (D12, first implementation of the class); else a cookie reject control → click it; else one Jev
  `choice` over guard-allowed buttons ("Which control starts the application?"), `none`/confidence < 0.6 →
  `navigation`. Easy Apply is checked first because a closed/applied posting has no Easy Apply button, and applied
  is checked before any §4.6 alarm (which applies only after the program acts — "Application submitted" would
  otherwise StopRun a merely-already-applied job).
- **Dialog scope by the `dialog` field**, not "all listed elements": robust whether LinkedIn uses a real
  `<dialog showModal>` (aa2 lists only its content) or a `div role=dialog` (page buttons stay listed). `advance`
  and finality look only at elements with a non-empty `dialog`.
- **Advance** clicks the allowlisted button and waits ≤8 s for the dialog signature (its fields + buttons) to
  change; an inline error (matched in the dialog text, since the observer does not list `role=alert`) or no change
  → `stuck` → the existing `broken_form` attempt. No advance button left → `final` (only a refused Submit remains).
- Reads with `pages.read_page` directly, never `pages.settle` (settle calls `judge()`/kev).
- **Live-gate watch item (top-card scoping):** entry text/controls are matched page-wide; the fixtures have no
  "similar jobs" sidebar, but a real posting does (its cards carry their own "Applied"/"Easy Apply" badges).
  Checking the Easy Apply *button* first covers the common case; confirm on a real posting.

## P2 T3/T6: Browser facade, package removed, test migration (2026-09-29)

- **`assistant/browser.py` replaces `jev.py`.** `Browser` wraps the owned driver, runs `guard.check`, injects
  `guard.never_click_element` into the driver press path, renders the driver's structured results into the text
  formats the callers parse, and logs to `browser_actions.jsonl`. `Table`/`Element`/`Option`/`split_json` moved
  here. Only `browser.py` imports `assistant.driver` (static test).
- **One `Browser` (one CDP connection) per run** (cli.py), fixing a latent bug the review caught: each `Browser`
  owns a `BrowserManager`, so building three of them (run/preflight/capture) would have given three "Allow" prompts
  (breaks D13). cli now builds one and `connect_once(browser)` opens the single socket.
- **`DriverTimeout` subclasses `CdpError`** so the op executor and observe/settle polls still swallow a
  mid-navigation eval timeout; `run_pages` catches it before `DriverError` and raises `StopRun` (a hung browser
  call is exit 3, matching CLAUDE.md).
- **`never_click_element` scopes the label/apply/final rules to click-role elements.** A required textbox
  "Confirm email address" or checkbox "I confirm…" reaches `_press` via type/toggle and must stay fillable; the
  structural rule still applies to every element, so `<input type=submit>` is refused whatever its role reads as.
- **Tabs are reported only when this tab opened them** (`opener_id == session.target_id`), so junk-closing never
  touches a tab the user opened; `TabBook.release` is now `Browser.forget` (drop bookkeeping, leave the tab open),
  and there is no exit hook — a tab the driver opened survives the process (D13).
- **Test migration.** The old browser_goal navigation/fill tests could not be ported verbatim (the behaviour is
  gone). The equivalent coverage moved to `tests/test_navigate.py` (real driver on the Easy Apply fixtures) and a
  rewritten `tests/test_fill_loop.py` (`tests/fake_browser.py`: a fake driver injected as `Browser(manager=…)`, so
  the real facade + guard + renderers + probe parser run offline). `test_tripwire`'s goal cases became a direct
  submit-click refused by the press path; `test_guard`'s `guard_clicks` test became "the driver refuses the press,
  no keys op"; `test_rules` kept `Attempts` + the job-card judgment and dropped the Google/sign-up/external-ATS
  entry tests (P5). Deleted: `test_jev`, `test_jev_browser`, `test_text_helper_live`, `test_baseline_p0`
  (+`golden/p0_baseline`), `fake_mcp.py`, `discover.py`, `release_child.py`. Dropped individual tests:
  `test_the_browser_agent_follows_the_same_route` (env_values), the `clean_requests`/`rotate_text_helper`
  package-sender tests, and `test_clean_stop`'s sticky-re-raise/real-Jev cleanup tests (no model sender on the
  browser thread in P2, so the P1 threading bug cannot occur). **Result: 371 offline tests pass.**
- **Package removed (T6).** `pip uninstall jev-ultrafast-mcp`; `websockets` is now a direct dependency (the driver's
  CDP transport). Deleted `vendor/` and the stale `golden/calls.jsonl` / `doctor_*.json`. `git grep jev_ultrafast_mcp`
  finds only `DISCOVERY.md` and `assistant/driver/LICENSE` (after the README + build-spec scrub).

## P2 live gate, iteration 1 — the entry still went through the kev kind gate (`runs/20260929-160256`, 2026-09-29)

First live run: all three jobs ended `load_failure` "no Easy Apply / Apply button on the LinkedIn job page", each
after **one** System One call. The reason was a missed rewire: `cli.process` still called `pages.classify_entry(p)`
— which asks kev `judge().kind` — **before** `run_pages`, so `navigate.enter` (the deterministic entry, the whole
point of P2) never ran. kev answered `kind="other"` on every real posting, so `classify_entry` returned `none` and
the job died before navigation. This is exactly the gate-critical coupling the P2 plan warned about; the offline
tests missed it because they drive `run_pages` directly, not `cli.process`.

**Fix:** `cli.process` now keeps only the deterministic signed-out URL check and calls `run_pages` straight away;
`navigate.enter` decides Easy Apply / closed / applied / external ATS with no page-kind model call.
`pages.classify_entry` stays as a pages helper (still unit-tested) but is no longer on the entry path.

**Safety held even so:** `browser_actions.jsonl` for all three jobs shows only read-only `eval` probes — **no click,
type or upload, and no POST**. The run was safe; it just never started an application. Re-run pending.

## P2 live gate, iteration 2 — the "Job search safety reminder" modal over Easy Apply (`runs/20260929-161017`, 2026-09-29)

With the entry fix, the deterministic path worked: **Genesys and Mastercard → `external_ats`** in 1 s each with
**0 System One calls** (the Apply control was found by label, never clicked), and **Linda AI is a real Easy Apply
job** — the program found "Easy Apply to this job", clicked it (`e52`, ok), and waited for the dialog.

New finding: clicking Easy Apply put up a LinkedIn **"Job search safety reminder"** modal (`! dialog open: Job
search safety reminder [modal]`, one button "Dismiss", `dialog:"<dialog>"`) **over** the Easy Apply form. aa2 lists
only the topmost modal, so `wait_for_dialog` saw a dialog with no form fields and timed out after 8 s →
`navigation` "clicked Easy Apply but no application dialog appeared". `browser_actions.jsonl`: the Easy Apply click
plus read-only probes only — **no type/upload, no POST**; the reminder was never dismissed.

**Fix:** `navigate.wait_for_dialog` now clicks through an interstitial — a dialog that is open but has no form
fields — using `INTERSTITIAL_RE` (continue applying / continue / got it / i understand / dismiss / ok), once per
control, then looks again for the form underneath. Guarded by `never_click_element` like any click (none are
submit/structural; "Continue applying" is allowed pre-fill).

## P2 live gate, iteration 3 — the reminder fix clicked "Dismiss" (cancel), not "Continue applying" (2026-09-29)

A subagent re-ran the live test (`runs/20260929-162226`) and inspected the real tabs with the Chrome tools. Result:
never-submit airtight (exactly two clicks in the whole run — the Easy Apply entry and one modal button — and **0**
type/upload/POST; Genesys + Mastercard clicked nothing and are correctly `external_ats`, verified against the live
"Apply on company website" → Workday/Phenom pages). But Linda AI still failed, and the iteration-2 fix was the
cause: the "Job search safety reminder" modal's buttons in DOM order are **Dismiss, report it, Review job post,
Continue applying** (from the iteration-2 log, which polled the fully-rendered modal for 8 s), and
`_interstitial_control` returned the **first** `INTERSTITIAL_RE` match — "Dismiss" — which **cancels** the
application (obs after the click showed the bare job page, no form). A regex alternation does not rank alternatives;
first DOM match wins.

**Fix (iteration 3):** `PROCEED_RE` matches only `continue applying` / `continue to next step` / `continue` — never
`dismiss` / `review job post` / `report it`. If only a cancel control is showing (the modal renders "Dismiss" a beat
before "Continue applying"), `wait_for_dialog` keeps polling for the proceed control instead of clicking the wrong
one, and each successful interstitial click resets the wait so the form has time to render. Offline test renamed
`test_easy_apply_safety_reminder_is_passed_with_continue_applying` with the real four-button modal (Dismiss first,
Continue applying last); the test fails if "Dismiss" is clicked.

## P2 live gate, iteration 4 — `FORM.started` not reset on the broken_form re-entry (`runs/20260929-215143`, 2026-09-29)

The "Continue applying" fix worked: Linda AI went Easy Apply → **Continue applying** (e41) → the real **"Apply to
Linda AI"** form dialog opened with its fields (Email, Phone country code, Mobile phone number, Next), and
`fill_page` **ran and typed the phone (e45)**. But the mobile-phone field did not "hold" its value on read-back, so
`fill_page` raised `_Refill` (broken_form attempt 2). `run_pages` re-opened the posting and re-entered — **but did
not reset `guard.FORM.started`** (set True by `fill_page`), so the re-entry `navigate.enter` clicked "Easy Apply"
with the form-being-filled flag still set and the driver **refused** it ("the program never clicks Apply once the
form is being filled"). No dialog reopened → `wait_for_dialog` timed out → the job reported `navigation` "clicked
Easy Apply but no application dialog appeared" — a bogus symptom hiding the real cause (the phone field).

Never-submit held throughout: the only clicks were Easy Apply, Continue applying, and the *refused* re-entry Easy
Apply; two `type` ops into the phone field; no submit, no POST.

**Fix:** both re-entry paths in `run_pages` (a `_Refill`, and "the Easy Apply dialog is not open") now reset
`guard.FORM.started = guard.FORM.final = False` before reopening the posting, so a re-entry is a clean posting entry
where Easy Apply is clickable again. Regression test `test_refill_reenters_and_easy_apply_stays_clickable`: a
field that will not hold must end `broken_form` (the field), never `navigation`, and the re-entry Easy Apply must
not be refused. `FakeSession` gained a `navigate()` (the reopen path was never exercised offline before).

**Open for the next iteration (P4-ish):** why the mobile-phone value did not hold (a `tel` input should keep
"351 935 8813" verbatim; the country code is a separate combobox). Either the read-back judge (kev) wrongly said
"different", or LinkedIn reformats the value. After this fix, Linda AI should report a clear `broken_form` on the
phone field (an acceptable P2 gate outcome — "parked, or broken_form with a clear reason") rather than `navigation`;
the phone widget itself is P4. Re-run pending.

## P2 live gate, iteration 5 — both fixes confirmed; read-back false-flagged a verbatim field (`runs/20260929-220759`, 2026-09-29)

A second subagent run confirmed both prior fixes work live: Linda AI goes Easy Apply → **Continue applying** → the
real "Apply to Linda AI" form → fills email + phone country code + phone → and after a `_Refill` the re-entry Easy
Apply is **allowed** (the `FORM.started` reset works). Outcome is now `broken_form — field would not accept its
value: 'Mobile phone number*' (after 2 attempts)`, **not** the bogus `navigation`. Never-submit airtight: only Easy
Apply + Continue applying (×2) clicks, four types into the phone, **zero** submit/upload/POST; `Next` was present but
never clicked.

**But the phone value actually held** — the program's own DOM observations and the read-back's own `State` show
`e45 = "+39 351 935 8813"`, byte-for-byte the answer. The failure was the **read-back judge**: `kev-0.8b` answered
`held_0="different"` at **0.3987** over `holds` **0.2348** (empty 0.3665), four times, on a verbatim-correct field.
A model-calibration miss, not value-didn't-stick and not a validation error.

**Fix:** `fill.mismatches` decides the exact-match case in code — a field whose value already equals the answer (or
a toggle whose option is checked) **holds without a model call**; the read-back is asked only for fields the page
reformatted. This is the re-core target ("code decides facts; Jev answers only what stays ambiguous") and removes
the kev false-negative for every verbatim field. Unit test
`test_mismatches_holds_a_verbatim_field_without_asking_the_model`.

**Secondary (Profile data, not code):** the answer `"+39 351 935 8813"` repeats the `+39` already in the country
code `e44 "Italy (+39)"`. It does not cause this failure, but LinkedIn may reject a `+39`-prefixed number when a
`+39` country code is set, at `Next`. Worth storing the national number in `Profile.md`.

**P2 gate status: met** — driver, guard v2 and deterministic navigation work live; nothing is submitted;
Genesys/Mastercard `external_ats`; Linda AI reaches and fills the Easy Apply form. Whether it now *parks* end-to-end
(P3's bar) depends on LinkedIn accepting the phone at `Next`.

---

### 2026-10-04 — P4 T1a: React re-render invalidates radio option_ref (root cause of P3 broken_form)

**Evidence:** `runs/20261001-214422/4470454940_Linda-AI_Founding-Software-Engineer/browser_actions.jsonl`
line 112: `toggle e150,e152,e154 → 0/3 ops ok (target_changed)`.
Line 116 (mismatch retry): same toggles → `3/3 ops ok` ✓.
Yet `broken_form` still fires. `answers.json` shows `opt_ref: e150` (Yes, education), `opt_ref: e152` (Yes,
onsite), `opt_ref: e154` (Yes, Ireland).

**Root cause:** LinkedIn's radio groups re-render after each toggle, assigning new DOM refs.
`mismatches.holds()` looked up the old `option_ref` (e.g. `e150`) in the post-re-render page, found it gone, and
returned `False` — a false mismatch that triggered `broken_form` even though all three toggles succeeded.

**Fix:** `mismatches.holds()` now has a label-scan fallback: when `q.option_ref` is set but the ref is gone from
the page, scan for any checked toggle whose `name` matches `q.answer`. Correct because LinkedIn re-renders radio
groups in place; the labels stay identical, only the refs change.
Unit test: `test_mismatches_holds_after_radio_rerender`.

---

### 2026-10-04 — P4 T1b: typeahead combobox (no listed options) filled via type→poll→click

LinkedIn Easy Apply uses comboboxes that type-filter their options dynamically (no static options in the DOM until
the user types). `_code_how` returns "select", `_carry_out` returns None (no options), the item becomes a
widget_item. `widgets._typeahead` types the answer, polls for `role=option` elements to appear, then clicks the
best label match. `widget_poll_secs=3.0` is injectable (set to 0 in unit tests).
Unit test: `test_typeahead_widget_fills_combobox`.

---

### 2026-10-04 — P4 T4: "Save this application?" dialog detected without clicking

LinkedIn shows a "Save this application?" overlay when the Easy Apply dialog closes mid-fill (e.g. accidental close
or session timeout). The correct action is to surface it as `NeedsAttention("dialog_closed", ...)` without clicking
either button; the user decides whether to save or discard. Check added in `navigate.wait_for_dialog` (entry-time
detection) and in `fill.run_pages` (mid-loop detection).
Unit test: `test_save_application_dialog_raises_dialog_closed`.

---

### 2026-10-04 — P4 T1a correction: the re-render read-back must be scoped to one radio group

**Supersedes the P4 T1a entry above.** That fix was right about the cause (a re-render invalidates `option_ref`)
and wrong about the remedy. Its label scan ran over `p.elements`, the whole page:

```python
return any(e.role in TOGGLES and bool(e.checked) and pages.norm_label(e.name) == label
           for e in p.elements)
```

LinkedIn renders a Yes/No question as a `div role=radio` group whose accessible **name** is the question and
whose **label** is the option (`llm_inference._radio_q`, the `same_name` branch). A page therefore carries several
groups whose option labels are identical, so one checked "Yes" made **every** question answered "Yes" report as
held. Measured on the three-group shape of the Linda AI page: 2 of 3 groups empty, `mismatches()` returned 0.

That is worse than the failure it replaced. A false `broken_form` writes a record and stops; a false *hold*
removes the only check on the P4 fill path and lets the run advance with empty required fields. Nothing
downstream catches it either: `probes.REQUIRED_EMPTY` queries `input,select,textarea`, so it never sees a
`div role=radio` group at all, and the failure resurfaces later as `advance → "stuck"` and a blind refill.

**Fix:** `llm_inference.checked_option(question, p)` re-derives the grouping with the same `_group_radios` /
`_radio_q` the extraction used, finds the group whose question text matches, and returns **that group's** checked
label. `fill.mismatches` compares it to the answer. The question text is a sound join key because `_merge_answers`
merges the model's reply by `id` and overwrites only `answer`/`source`/`quote`/`relies_on` — it never rewrites
`question`, so the extracted text survives the model round-trip.

It **fails closed** on ambiguity: `_group_label` falls back to `context` or `"Select one"` when a group key is
opaque, and HANDOVER records a "Select one" question on Linda AI page 2, so two groups can carry the same text.
`checked_option` returns None unless exactly one group matched, and the caller treats None as not held.
Tests: `test_mismatches_are_scoped_to_one_radio_group` (the decisive one — three groups, one checked),
`test_mismatches_holds_after_radio_rerender`, `test_mismatches_catch_the_wrong_option_in_the_right_group`.

**Still open:** `REQUIRED_EMPTY` remains blind to `[role=radio]` groups. Extending that probe needs live Chrome
to verify and was left out while the CDP endpoint is unreachable (see the entry below).

### 2026-10-04 — P4 T1b: the read-back rejected a *correct* typeahead pick

A typeahead expands the label it accepts: pick "Milan" and the field holds "Milan, Lombardy, Italy". `_fuzzy_holds`
compared exact, phone-digit-suffix and punctuation-stripped forms — no prefix test — so a correctly filled City
became a mismatch, was re-planned, failed again and raised `_Refill` → `broken_form`:

```
'Milan'  vs 'Milan, Lombardy, Italy'          -> holds=False
'Dublin' vs 'Dublin, County Dublin, Ireland'  -> holds=False
```

This is pre-existing code, but T1b's typeahead is what routes traffic into it, so every City-style field would
have become a Needs Attention and the ≥ 7/10 gate could not have passed.

**Fix:** accept a held value that starts with the answer **at a token boundary**
(`nh.startswith(na) and not nh[len(na)].isalnum()`), so "Milan" holds "Milan, Lombardy, Italy" but not "Milano",
and "1" does not hold "10". Test: `test_fuzzy_holds_accepts_an_expanded_typeahead_label` (7 cases).

With that in place, `fill.mismatches` is also the read-back for the typeahead *selection*: a wrong suggestion
leaves a value that does not prefix-match the answer, so no separate verification is needed in `widgets.py`.

### 2026-10-04 — P4 T1b correction: the typeahead picked the first suggestion

`_typeahead` fell back to `opts[0]` when no label equalled the answer, which fills the application with a wrong
value. Measured: answer "Hybrid", suggestions `["Remote", "Hybrid (3 days onsite)"]` → it clicked **Remote**.
It also collected every `role=option` on the page with no dialog scoping, discarded the result of its own `type`
op (so a `target_changed` still went on to click), and returned True whether the click succeeded or not.

**Fix (T1 as specified):** equal to the answer → the only suggestion that starts with it → `decide.choice` among
the suggestions that start with it → otherwise False. "Close candidate" is read at face value as *starts with the
answer*: a suggestion that does not is never offered to the model, so an unrelated list blocks rather than being
guessed at. False means not filled, and `fill_page` decides the cost — an optional field is left empty and noted,
a required one is a `broken_form`. Never a silent wrong value. Options are
scoped by `Element.dialog`, the `type` and `click` results are both required to be `1/1 ops ok`, and
`fill.fill_page` now re-reads the page before calling `widgets.handle`, because the `direct` ops it just ran are
themselves a re-render. Tests: `test_typeahead_picks_the_suggestion_that_starts_with_the_answer`,
`test_typeahead_without_a_matching_suggestion_is_a_broken_form`.

`tests/fake_browser.py` now writes a clicked `role=option` back into its combobox, as a real one does. Without
that the fake kept the typed text, and a test could not tell a correct pick from a wrong one.

### 2026-10-04 — Preflight reports an unreadable `DevToolsActivePort` as missing

The P4 live gate could not be run from the agent's process context. Port 9222 is listening (the user's own Chrome
with the `chrome://inspect` toggle) but `/json/version` and `/json/list` answer `HTTP 404 len=0`, which is the
Chrome 144+ WebSocket-only server already recorded above. `cdp._from_active_port` is then the only route, and

```
read FAILED: PermissionError [Errno 1] Operation not permitted:
  '/Users/maaz/Library/Application Support/Google/Chrome/DevToolsActivePort'
```

The file exists (mode 644, 59 B) — macOS TCC protects `~/Library/Application Support`, and the user's own
terminal holds that access, which is why earlier runs passed ("7 of 7 checks").

`cdp.py:198-200` catches `OSError`, and `PermissionError` is an `OSError`, so preflight then asserts "no
`DevToolsActivePort` file was found in the usual browser data directories" when the file is present and merely
unreadable. That points at re-toggling `chrome://inspect`, which is already on. Distinguishing absent from
`EPERM` in that message would make this blocker self-diagnosing. Not fixed here: it is a diagnostic, and the fix
belongs with someone who can verify it against a reachable Chrome.

**Unverified live.** Suggestions are matched with `e.dialog == el.dialog`, which assumes LinkedIn renders the
option list inside the modal's subtree. If it renders in a portal outside the dialog, `opts` is always empty and
every typeahead becomes a `broken_form`. The fixtures set `dialog="d"` on their options, so they cannot settle
this — check it on the first live run. For the same reason `widgets.handle` looks the field up by `q.ref` on the
re-read page; if a re-render replaces the combobox node outright, that lookup misses and the field is reported
rather than filled (`type_long` re-maps by question text for this case; the typeahead does not yet).

### 2026-10-06 — Fixed: the browser endpoint needs no `DevToolsActivePort` and no HTTP API

The 2026-10-04 entry above assumed the EPERM on `DevToolsActivePort` was the agent's process context, and that
"the user's own terminal holds that access". **That was wrong.** The user hit the same failure from their own
terminal, so preflight could not reach a Chrome that was plainly serving:

```
$ lsof -nP -iTCP -sTCP:LISTEN | grep 9222
Google  55661 maaz  96u  IPv4  TCP 127.0.0.1:9222 (LISTEN)

$ curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9222/json/version
404

$ .venv/bin/python -c "...attach_chrome('http://127.0.0.1:9222', data_dirs=chrome_data_dirs())"
ChromeLaunchError: Cannot reach a CDP endpoint at http://127.0.0.1:9222. Nothing answered /json/version, and
no DevToolsActivePort file was found …
```

macOS 27 / Chrome 154: `stat` on `~/Library/Application Support/Google/Chrome/DevToolsActivePort` succeeds
(mode 644, 59 B, owner `maaz`, no flags) while `open` raises `PermissionError` errno 1 for any process without
Full Disk Access — including the user's own shell. `xattr` fails the same way. So both of `attach_chrome`'s
discovery paths were dead at once, and no amount of re-toggling `chrome://inspect` could help.

**The endpoint was reachable the whole time.** `ws://127.0.0.1:9222/devtools/browser` — the browser endpoint with
**no target id** — upgrades and serves the full protocol:

```
$ curl -i -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' \
       -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' http://127.0.0.1:9222/devtools/browser
HTTP/1.1 101 WebSocket Protocol Handshake

Browser.getVersion     → Chrome/154.0.8037.92, protocolVersion 1.3
Target.setDiscoverTargets → {}
Target.getTargets      → 24 targets
```

**Fix (`cdp.py`).** `attach_chrome` grew a third discovery step, after `/json/version` and
`DevToolsActivePort`: `browser_ws(endpoint)` → `ws://host:port/devtools/browser`. No file is read and no HTTP API
is needed, so it is immune to both causes. Two details keep it honest:

- **It is gated on evidence, not on an exception.** `_from_json_version` now returns `(ws_url, serving)`, where
  `serving` is True when *anything* answered on the port — including the 404 — and False only when the connection
  itself failed. Step 3 runs only when `serving`, so a dead port still raises `ChromeLaunchError` at once instead
  of hanging on a websocket handshake against nothing. `urllib.error.HTTPError` has to be caught before
  `URLError`, which it subclasses, or a 404 reads as "nothing listening".
- **It is last, not first.** `DevToolsActivePort` names the exact browser id and is the documented route, so a
  readable file still wins; step 3 is the rescue. Discovery stays I/O-cheap: it opens no socket of its own, it
  only *names* the URL that the one existing `Cdp(...)` call then connects to, so the run still costs exactly one
  "Allow remote debugging?" click.

Full Disk Access is therefore **not** required any more, and the swallowed `PermissionError` in
`_from_active_port` is now harmless rather than fatal. The old error message also lied twice — `/json/version`
*had* answered, and the file *was* found — so it was replaced with one that only fires when nothing is listening
at all.

Tests: `tests/test_attach.py` (9, `unit`) pins the ladder's order and its guard, including the live case —
port serving, file present, `read_text` raising EPERM — and that a dead port never reaches step 3. The HTTP side
is a local one-request server and `Cdp` is monkeypatched, so no browser and no network are touched. Verified
against the user's real Chrome: `attach_chrome` now returns `ws://127.0.0.1:9222/devtools/browser`, 24 targets.

### 2026-10-06 — A correct answer dropped by the quote check; the resume text leaves the sources (user decisions)

**Symptom.** Linda AI, run `20261006-220620`, page 3: "Have you completed the following level of education:
Bachelor's Degree?" was left empty, the required radio stayed unset, and the job ended
`broken_form — the Easy Apply step did not advance (after 2 attempts)`. The question went to the Scratch Pad
list in the report as if the sources did not answer it. They did.

**The model answered it correctly, twice.** Both answers were discarded by the quote check, not by the model.
`answers.json` for that question, both rows:

```
attempt 1   answer: null   source: resume    note: "quote not found in the sources"
attempt 2   answer: null   source: profile   note: "quote not found in the sources"
```

`llm_inference_logs.json` entries 2 and 3 both carry `{"id": "r_e147", "answer": "Yes", ...}`.

**Why both citations failed — and why it was not a formatting problem.** `quoted_in` already ignores case,
spacing and punctuation through `_loose`, so `**`, `·` and `–` all normalise away. The failures were an
insertion and an omission *inside* the quote, which no normalisation can repair, because the test is for a
contiguous substring:

| | the source says | the model wrote |
|---|---|---|
| attempt 1 (resume PDF) | `Shiraz University — B.Sc. in Computer Engineering (GPA: 3.74/4) 2016 - 2021` | `… (GPA: 3.74/4) · 2016 – Jun 2021` — **spliced `Jun`** in from Profile.md's range |
| attempt 2 (Profile.md line 122) | `**M.Sc. … (majoring Artificial Intelligence)** (Final Grade: 100/110) · Sep 2022 – Dec 2025` | `M.Sc. … (majoring Artificial Intelligence) · Sep 2022 – Dec 2025` — **elided `(Final Grade: 100/110)`** |

Measured:

```
quote loose in line loose?                            False
drop "(Final Grade: 100/110) " from the line, then?   True
```

So "hand it Profile.md instead of the PDF" would **not** have fixed this question: attempt 2 was already pure
Profile.md. Also worth noting that `Bachelor` appears 0 times in Profile.md and 0 times in the resume, so the
model had to infer B.Sc. ⇒ Bachelor's Degree — which it did, both times.

**Decision 1 (user, 2026-10-06): a `choice` answer is not asked for a quote.** A quote is the only evidence free
text can carry, so it stays mandatory there. A choice answer already has to be one of the page's *own* options —
the branches after the quote check require exactly that, and for this radio group it would have passed — which is
stronger evidence than a self-reported citation. `check_answers` therefore only drops a missing or unverifiable
quote when `q.kind != "choice"`. The citation is still recorded, with a `quote not verbatim in <source>` note, so
an unverified one is visible in `answers.json` and the report rather than silently accepted. Rejected
alternatives: segmenting the quote on `· — – ( ) , ;` and requiring every segment (fixes this, but lets a quote
be assembled from two distant real lines); citing by line number (strongest, but moves the prompt, the JSON
schema and the check at once).

Tests: `test_an_option_answer_survives_a_citation_that_is_not_verbatim` pins the exact elided Profile.md line;
`test_free_text_still_needs_a_verbatim_quote` and `test_an_option_answer_off_the_page_is_still_dropped` pin the
two halves of the guarantee that must not move. Both of the new behaviour tests fail against the old code.

**Decision 2 (user, 2026-10-06): the resume's extracted text is no longer a source.**
`cli.process` now builds `Sources(profile=profile, job=job.job_md.read_text(), resume="")`. `job.resume_pdf()`
still runs, because the file itself is what gets uploaded; only `resume_text(pdf)` is gone from the model's
input, and with it the second differently-worded copy of the same facts that attempt 1 spliced from. The prompt
now says `resume` is normally empty and to answer from `profile` and `job`. `tests/replay/harness.py` was changed
the same way, or a replay would not reproduce a real run. Cost of this: a fact only the PDF holds (the B.Sc. GPA
3.74/4, exact role dates) can no longer be cited at all, so it has to live in `Profile.md` to be answerable.

**Decision 3 (user, 2026-10-06): Profile.md gets plain-language degree lines.** Four lines added to its
`# Scratch Pad`, phrased the way LinkedIn asks, so one whole line is a quotable sentence and no inference from
"B.Sc." is needed:

```
- Have you completed the following level of education: Bachelor's Degree? Yes — B.Sc. in Computer Engineering, Shiraz University, completed Jun 2021.
- Have you completed the following level of education: Master's Degree? Yes — M.Sc. in Computer Science and Engineering, Politecnico di Milano, completed Dec 2025.
- Have you completed the following level of education: Doctorate / PhD? No.
- Highest level of education completed: Master's Degree (M.Sc.).
```

**Not verified live.** Replay cannot confirm this end to end: the fixture's prompts predate the P4 edits, so
`python -m assistant replay runs/20261006-220620/4470454940_Linda-AI_Founding-Software-Engineer` misses on the
first LLM request (`LLM request not in fixture`), and dropping the resume from the sources changes the prompt
hash again. The next live run on this job is what settles it — re-queue it first
(`python -m assistant requeue --job <url>`).

### 2026-10-06 — `run --job` that matches nothing was silent and exited 0

**Symptom.** `python -m assistant run --job 4470454940` printed all seven preflight ticks, opened no tab, wrote
an empty report and exited 0:

```
✓ LLM inference answers: mistral/mistral-small, mistral/mistral-nemo (via vercel)
✓ Kev server on http://127.0.0.1:8009: jaredpalmer/kev-0.8b on mps via mlx (bfloat16)
… 5 more ✓ …
Report: runs/20261006-231622/report.md        # Parked 0 · Needs Attention 0 · Queue anomalies 0
```

**Cause.** `--job` is applied by `cli._queue` *after* `records.build_queue`, so it filters the queue instead of
bypassing it. Job 4470454940 was in `Needs-Attention/` from the previous run, which means no folder in
`Applications/`, which means it was never in the queue to be filtered. `for job in q.jobs:` then iterated zero
times. Nothing printed, because the terminal only ever reported per-job results, and `Report.exit_code()`
returned `EXIT_OK` — "all parked" — for a run that parked nothing.

Three separate defects in one symptom:

1. **No message.** An empty queue, from any cause, said nothing on a real run.
2. **Anomalies were invisible.** `build_queue`'s anomaly lines reached the terminal on `--dry-run` only. A run
   that skipped every job for a status mismatch looked identical to a run with nothing to do.
3. **Exit 0 was a lie.** A script or wrapper checking the exit code could not tell this from a clean run.

**Fix.** `cli._report_queue(cfg, tracker, q, job_url)` is called by both `dry_run` and `run` right after the
queue is built. It prints one `⚠ queue anomaly:` line per anomaly, and when `--job` matched nothing it prints
why and returns the reason. `_why_not_queued` looks the job id up in the other two folders and then in the
tracker, so the message names the way out rather than the symptom:

| Where the job actually is | What it now says |
|---|---|
| `Needs-Attention/` | `its folder is in Needs-Attention/ — put it back in the queue first: python -m assistant requeue --job <id>` |
| `Pending-Review/` | `its folder is in Pending-Review/ — it is already parked, waiting for you to submit it` |
| row at another status | `its tracker row says Status 'Interviewing', and only 'Resume Built' is queued` |
| row but no folder | `its tracker row is 'Resume Built' but it has no folder in Applications/` |
| neither | `no folder in Applications/ and no tracker row has that job id — check the URL` |

`run` stores that reason in the new `Report.nothing_matched`, which `exit_code()` turns into **exit 1** and
`markdown()` prints as `- **No job matched `--job`:** …`. Setting a field rather than returning early was
deliberate: the run's existing tail still writes the report and closes the tab book, so the report is no longer
empty and nothing in the cleanup path is skipped.

The check stays **after** `_queue`, which costs a preflight that the user has already paid for by then. It
cannot move earlier: `records.recover` runs between preflight and the queue, and an interrupted `requeue` is
recovered by moving a folder back *into* `Applications/`, so a queue built before recovery could reject a job
that recovery is about to restore. `--dry-run` is the free way to check a `--job` first, and it now reports an
unmatched one exactly as a real run does, exit code included.

Tests (`tests/test_cli.py`, 7 new, `unit`): one per row of the table above, one that the happy path still exits
0 and prints no warning, one that anomalies now reach the terminal on a plain run, and one on
`Report.exit_code()` / `markdown()` directly. Six of the seven fail against the old code; the seventh is the
happy-path regression guard, which passes against both on purpose.

Verified live on the user's own workspace:

```
$ .venv/bin/python -m assistant run --dry-run --job 4470454940
Queue (0 jobs):
✗ --job 4470454940: no queued job has that id — its folder is in Needs-Attention/ — put it back in the
  queue first: python -m assistant requeue --job 4470454940
exit=1

$ .venv/bin/python -m assistant run --dry-run --job 9999999999
✗ --job 9999999999: no queued job has that id — no folder in Applications/ and no tracker row has that
  job id — check the URL
exit=1

$ .venv/bin/python -m assistant run --dry-run --job 4470918779
Queue (1 job):
  • The Flex – Senior Software Engineer  https://www.linkedin.com/jobs/view/4470918779  [4470918779_…]
exit=0
```

## 2026-10-07 — P5: external ATS by one general loop, no host adapters (decision + findings)

**Decision (user, 2026-10-07).** Follow LinkedIn's external "Apply" to the ATS form and fill it with the same
pipeline, but **not** with three hardcoded host adapters (Greenhouse/Ashby/Lever) as the phase file's T3 drafted.
The user asked for a smarter, more general approach — "the LLM/KEV decides what to do on the page, the driver
acts". An Opus brainstorm (clean context) and a reviewer pass agreed: the generic pipeline already reads all
three hosts (the P2 observer solved grouped file labels, react-select `current`, opacity:0 radios; P3's
`extract_questions`→answer→`plan_fill`→read-back→`gate` is host-neutral), so the only LinkedIn-specific thing is
the *navigation shell* of `run_pages` (modal-dialog scoping). P5 adds one thin shell for full-page / multi-step
forms and keeps everything else. **Supersedes D12** (external Apply was Needs Attention `external_ats`).

Also decided: gate ≥ 5 of 10; **skip T4** (the Greenhouse boards-API cross-check — DOM extraction already yields
required markers); **defer T2** (cross-origin frame attach) — LinkedIn's external "Apply" opens the ATS's own
**hosted** top-document form (`job-boards.greenhouse.io`, `jobs.ashbyhq.com`, `jobs.lever.co`), which the
top-level observer reads in full; the captures show `iframe_srcs.long = 0` there. The one-line `browser_open(src)`
hop stays for a genuinely embedded form (we park before submit, so the hop's usual submit-coupling downside does
not apply). Frame-attach becomes justified only if a live run shows an embedded form whose token-bearing src is
trimmed by the `IFRAME_SRCS` ~170-char cap.

**How it works.** `navigate.enter` now raises the control-flow signal `GoExternal` (blockers.py) instead of
`NeedsAttention("external_ats")`; `cli.process` catches it and runs `external.run_external`, which hands the job
off to the ATS tab and loops: `pages.read_page` → `pages.classify` (the previously-unused `Verdict` dispatcher,
now revived) → fill/advance/gate/park. `run_external` mirrors `run_pages`' full exception mapping, because it is
caught through `process()`'s `except GoExternal:` (a sibling of its `except RestartFromEntry:`), so anything that
escaped would crash the whole run rather than becoming a per-job outcome.

**Two safety predicates external hosts need and LinkedIn does not.**
1. `external.looks_like_application(p)` — fill only a real application form, not a page that merely has fields.
   Signal: a résumé/CV upload control is present, OR both an identity name field and an email field are among the
   real application fields. A lone email box (a job-alert sign-up) fails. This closes the Mastercard mis-fill
   (2026-09-23 entry): previously "any non-chrome field ⇒ a form". A page with fields that fails this →
   `unsupported_ats` with the host, and **nothing on it is clicked**.
2. The pre-fill apply click (`external._safe_apply`) is allowed only on a page with no form yet, for a control
   that is not a form's own control and that the guard already allows. On LinkedIn a pre-fill "Apply" is never a
   submit; on an arbitrary ATS it can be (Toast's "Apply now!", 2026-09-23). The apply-click permission is kept
   separate from `looks_like_application`, so that predicate is never load-bearing for never-submit.

**Final-submit detection.** `looks_final`/`_submit_like` do not match the label "apply", so a Greenhouse/Toast
page ending in "Apply now!" was never judged to have a submit and failed the gate with "no submit button". New
`pages.forward_submit(p)` counts an apply-labelled forward control as the submit, so such a page parks. Kept out
of `_submit_like` on purpose: adding "apply" there would make a LinkedIn posting's top-card "Apply" turn every
posting into `final_step`. The guard still refuses the control — it is never clicked.

**Driver: `Session.adopt()` parity.** Adopting a background tab (the external hand-off) skipped
`Emulation.setDeviceMetricsOverride`, `setFocusEmulationEnabled` and `addScriptToEvaluateOnNewDocument`, so React
inputs in the adopted tab were treated as unfocused/zero-size and silently dropped typed values (read-back
mismatch → `broken_form` on every external form). `adopt()` now mirrors `_attach_page`'s capability setup.

**Hand-off through a "You are leaving LinkedIn" interstitial.** LinkedIn can cover the posting with a "you are
leaving to apply on the company website" dialog whose "Continue" opens the ATS tab; the fixture
`4012345608-dialog` reproduces it. `external._hand_off` polls after the apply click: hand off as soon as a new
tab appears, click a leaving/Continue control when one is up (never Cancel, never "Continue with <provider>"),
or return when the form opens in this tab (a same-tab navigation). The immediate (`4012345604-external`) and
delayed-tab (`4012345607-late-tab`) cases keep working.

**Ashby autofill-input.** Ashby's application lists an "Autofill from resume" file input (`e9`, empty name)
*before* the real "Resume" input (`e15`) — capture `jobs-ashbyhq-com-20260923-150513`. `_code_resume` returned
`named[0]` in DOM order, so if the autofill input's group label carried "resume" the résumé went to the parser.
Fixed generally: `pages._code_resume` drops any file input matching `autofill|parse|populate` when a non-autofill
file input is also present.

**T6 `requeue --class <cls>`** filters Needs-Attention by the class a run recorded, read from the tracker Notes
line (`records.recorded_class`, authoritative) with the `job.md` heading as fallback.

**Tests.** `test_external_apply_is_needs_attention` / `test_external_apply_is_external_ats` are rewritten to
assert `GoExternal` (phase-mandated behaviour change, 00_common §4.5). New: `test_external.py`
(`looks_like_application`), the external cases of `test_process_browser.py` moved into the offline browser suite
(deterministic now — no `browser_goal`) plus an unsupported job-alert-box case, `test_tripwire.py`
`test_each_ats_final_button_is_refused_by_the_driver` (A1: Greenhouse/Lever "Submit application", Toast "Apply
now!", Ashby "Submit Application"), `test_pages_unit.py` `forward_submit` and the autofill-input pick, and
`test_records.py` for `requeue --class`. No Lever capture exists in the repo (`lever_like.html` is authored from
Lever's known form); a live Lever run still needs a user `capture`.

## 2026-10-07 — P5 live runs (the user authorized live testing) and the fixes they forced

Two live `run --no-record` passes in the user's real Chrome (Kev local System One, Vercel/mistral chat). The
never-submit rule held in both: across every `browser_actions.jsonl` the only `ok` clicks were start-of-apply
controls ("Apply on company website", "Easy Apply", Ashby "Apply for this Job"), Next/Review, and résumé uploads
— no Submit/Apply-submit click, no ALARM, and `--no-record` wrote nothing.

**Run 1 (`runs/20261007-092704`) — crashed on Toast.** Observing the freshly-adopted Toast tab ran
`__jevMcp.readState` while `document.body` was still null, and `textOf`'s `document.createTreeWalker(document.body)`
threw `TypeError: ... parameter 1 is not of type 'Node'`. That `CdpError` was raised in `external._hand_off`,
which runs in `run_external`'s outer `try` where there was no `except DriverError`, so it escaped `process()` and
aborted the whole run with a traceback. Ashby ended `unsupported_ats` because the loop read `/application` while
it still said "Fetching application form" and gave up before the form rendered. Linda AI (Easy Apply) parked.

**Fixes:** (a) `observer.js` `textOf` returns `''` when `document.body` is null (helper version 13→14 so it
re-injects); (b) `run_external` gained an outer `except DriverError` → NeedsAttention `load_failure`; (c)
`external._wait_for_form` waits (bounded 12 s) for a form's fields before concluding `unsupported_ats`.

**Run 2 (`runs/20261007-093653`) — no crash; 2 parked, 4 Needs Attention.**
- **Parked:** Linda AI (Easy Apply); **The Flex – Senior Full-Stack (Ashby)** — the first external ATS parked
  end to end, on the real `jobs.ashbyhq.com/.../application`.
- **Toast (Greenhouse) → `unsupported_ats`, misclassified.** "Apply on company website" lands on
  `careers.toasttab.com`, whose only field is a site-search box inside a `<form>` → the observer gives it
  `scope="<form>"`. `pages.code_app_fields`/`_code_kind` short-circuited on `scope` (a LinkedIn Easy-Apply-dialog
  concept) before the searchbox/site-chrome exclusion, so the careers page read as an `application_form` and the
  loop gave up instead of clicking its "Apply now" link (clean, `scope`/`form` empty) to the Greenhouse form.
  **Fix:** honour `scope` only on linkedin.com; off LinkedIn use the form-fields-minus-site-chrome rule. Also
  `external._click_forward` now hands off when the forward click opens the form in a new tab.
- **The Flex – Senior SE (Ashby) → `llm_inference`.** It reached the Ashby form (late-render fix worked) but the
  chat model failed: `mistral-small` returned valid JSON that the answer checks rejected — it answered the
  **demographic** radios ("Prefer not to say", source=profile, `quote:null`) — then the fallback `mistral-nemo`
  ReadTimeout'd ×3 (151 s). Root: the demographic radio groups were labeled with Ashby's compound UUID group id
  (`_group_label` ran its cosmetic `-`/`_`→space transform on a key that slipped past `_UUID_RE`), so
  `MUST_NOT_GENERATE` (C16) did not recognise them and the model tried to answer. **Fix:** `_OPAQUE_ID_RE`
  catches a UUID anywhere in the group key → the label falls back to the element context or "Select one".
- **Genesys (Workday) → `unsupported_ats`** (a sign-in wall with no readable form — acceptable: a non-target host,
  correctly not parked).
- **Mastercard → `navigation` "no way to start".** The LinkedIn entry snapshot held only nav chrome — no
  Apply/Easy-Apply control was present at observe time (a LinkedIn top-card render-timing edge, not external ATS).
  Still open; affects one job.

**Reliability note (user's config, not a code bug):** the run used `mistral-small` + `mistral-nemo` on Vercel,
not D14's `deepseek`/`gpt` models; `mistral-small` under-quotes and `mistral-nemo` times out. The model choice is
the user's. **Gate note:** the current queue has only 3 target-host jobs (Ashby×2, Greenhouse×1) and no Lever, so
the ≥5/10 gate cannot be reached with it; it needs ~10 external Resume-Built jobs mixing Greenhouse/Ashby/Lever.

### Runs 3–5 and the Toast live inspection (same day)

**Run 3 (`runs/20261007-093653`-era re-run).** UUID labels fixed (now "Select one"); The Flex SE (Ashby)
parked. Two new findings: The Flex Full-Stack hit `broken_form` on the résumé upload (`page_changed`: Ashby
re-rendered the form around the upload) — fixed by retrying the upload once after a re-observe
(`fill.upload_resume`, mirroring `type_long`). Toast still `unsupported_ats`.

**Run 4 (`runs/20261007-100753`).** 3 parked (Easy Apply + both Ashby); the upload-retry fix confirmed live (The
Flex SE hit `page_changed`, retried, parked). Toast still `unsupported_ats`. Reconciling the Toast logs offline
(rebuilding `pages.classify` on the saved observes) kept giving `navigate` while live gave the form branch — an
unresolvable gap from logs alone. Added `pages.settle` on each external read first (Toast's careers page loads in
stages: blank → a transient blocker-looking state → the real page); that did not resolve Toast.

**Toast — live inspection (Claude in Chrome, user-authorized).** Opening the real careers URL showed the cause:
the Greenhouse application form is **embedded inline on the same careers page** at `#applynow` (a `<form>` with
"Legal First Name", a "Resume" file input and an "Apply now!" submit), **not** a cross-origin iframe, and it
renders a beat after load, below the fold. The loop's early snapshot saw only the nav, a site-search box and an
"Apply now" anchor (`href="#applynow"`). **Fix:** `external._wait_for_form` now waits for a complete
`looks_like_application` (résumé, or name+email) rather than any field, and the `form`/`final`-not-application
branch calls it before `unsupported_ats`. Re-reading only — never a click or a scroll-trigger — so it cannot
submit (the never-submit line the advisor drew: an "Apply now" on a fields page can be a JS submit). Fixture
`ats/toast_embedded.html` reproduces the late-rendered embed.

**Run 5 — stopped at preflight.** Per the user's decision the chat route was switched to `openrouter` (D14:
`deepseek/deepseek-v4.1-flash` + `openai/gpt-5.4-mini`). Preflight stopped with OpenRouter **HTTP 402
"Insufficient credits. This account never purchased credits"** (exit 1, nothing written — the clean key/credit
stop worked). So D14 is unvalidated live until the user adds OpenRouter credit (or confirms the key's account);
reverting `models.chat_route` to `vercel` is the one-line alternative, at the cost of the mistral under-quote/
timeout. The Toast embedded-form fix is therefore proven offline (new fixture + test) but not yet live.

**Net external-ATS result:** the generic loop parks real Ashby forms reliably (2/2 in run 4), and standard hosted
Greenhouse/Lever via fixtures; the embedded-careers-page Greenhouse (Toast) is fixed offline; Workday/Mastercard
are correctly non-parked (account wall / no apply control at observe time). The user accepted this as the gate
(no full 10-job live gate), recorded in `recore/HANDOVER.md`.

## One local provider for the chat models: FreeLLMAPI (user decision, 2026-10-07)

The user replaced the paid cloud chat routes with a **FreeLLMAPI** router
([github.com/tashfeenahmed/freellmapi](https://github.com/tashfeenahmed/freellmapi)) running on this Mac at
`http://127.0.0.1:31415/v1`, keyed by `FREELLMAPI_KEY` in `.env`. It aggregates the free tiers of ~34 providers
behind one OpenAI-compatible `/v1`, and a router picks a live provider per request. OpenRouter and Vercel AI
Gateway are removed from the code, not kept as a fallback: `models.chat_route`, `[models.openrouter]`,
`[models.vercel]`, `models.system_one_decision_provider`, `OPENROUTER_API_KEY` and `AI_GATEWAY_API_KEY` are all
gone, and a config still naming one is rejected with a message saying where its keys moved.

The existing sender was kept. `llm_inference.call_engine` already speaks OpenAI `chat/completions` over `httpx`
through the Gateway, which is the one retry layer, queue and log (P1 T2). Adding the `openai` SDK would have put a
second retry layer inside one Gateway request (`max_retries` defaults to 2) and sent through its own client, which
`tests/test_no_bypass.py` seals `httpx.post` against. "Via the OpenAI API" is therefore read as the wire format,
which the code already spoke — only the base URL, the key and the model IDs changed.

### Verified live, 2026-10-07

- **Auth is required even locally.** `GET /v1/models` without a key is **HTTP 401**; with it, 200. So
  `FREELLMAPI_KEY` is the one key a run needs, and 401 stays in `gateway.CREDIT_STATUS` (a clean stop — no other
  model on a rejected key would do better).
- **`auto` is unusable for this program.** The router's own `auto` model ignores `response_format`: asked for a
  strict `json_schema`, it answered **HTTP 200 with prose** (routed to `zai-org/GLM-5.2`). The code only falls back
  to `json_object` on HTTP 400, so a 200-with-prose fails every page as `ModelUnavailable("output invalid")`. Only
  concrete model IDs whose catalogue entry lists `response_format` honour the strict schema —
  `deepseek-v4-flash` returned `{ "n": 7 }` to the same request. `config.toml` and `test_model_access.py` both
  now forbid `auto`.
- **`reasoning: {"enabled": false}` is OpenRouter's spelling and is ignored here** — passed through, and GLM-5.2
  still spent 645 of 696 completion tokens reasoning. The catalogue lists `reasoning_effort`, and `"none"` does
  switch it off (kimi-k3: 0 reasoning tokens). `llm_inference.REASONING_EFFORT = "none"` replaces `REASONING`.
- **No cost is reported.** `usage` has no `cost` field, and there is no `provider_metadata`. So `_reported_cost`
  returns None, `_estimated_cost` returns 0.0 for a model with no `[prices]` entry — i.e. all of them — and
  `[prices]` is now empty. Every request is costed at **$0**, which is right for a free provider but means
  `[budget] max_usd_per_run` no longer guards anything. Kept, because a paid route may return.
- **`POST /v1/systemone` is HTTP 404.** The router serves chat only, so the **Kev** server on this Mac
  (`http://127.0.0.1:8009`) is now the *only* System One decision route. `decide.ENDPOINTS`, the `route=` argument
  and `adapt_questions_for`/`respan_questions` (which existed for OpenRouter's `alpha/decisions` models) are gone,
  and `keep_object_state` is always on.
- **The quota that runs out belongs to a platform, not a model ID.** A 429 body reads `All models exhausted: 1
  route checked (1 rate-limited or on cooldown) … Soonest reset ~22h. 3 models skipped: no key configured for
  their platform.` Two IDs served by the same platform share one limit, so rotating between them buys nothing.
  The configured rotation spreads over four platforms — groq, google, huggingface (two), nvidia.
- **The router reports who actually served the request** at `_routed_via: {"platform", "model"}`, e.g.
  `{"platform": "groq", "model": "qwen/qwen3.8-27b"}`. `inference_log._provider` now logs
  `freellmapi/<platform>`; without it a chat entry could not say which free tier answered. `gateway_of` also had
  to learn to tell the two loopback servers apart by path, or a chat failure and a Kev failure would both be
  labelled `local` in the outage and rejected-key messages.

### Models measured against the real strict `ANSWER_SCHEMA` and system prompt (a 6-question page)

| Model | Platform | Time | Verdict |
|---|---|---|---|
| `qwen3.8-27b` | groq | 1.2 s | ✓ |
| `gemini-3.5-flash` | google | 2.5 s | ✓ |
| `kimi-k3` | huggingface | 9.1 s | ✓ |
| `deepseek-v4-flash` | huggingface | 11.1 s | ✓ |
| `deepseek-v4-pro-0813` | huggingface | 21.0 s | ✓ |
| `glm-5.2` | huggingface | 32.2 s | ✓ (close to the 45 s chat timeout) |
| `qwen3.8-2.4t-a95b` | huggingface | 34.0 s | ✓ (close to the 45 s chat timeout) |
| `muse-glimmer-30b` | nvidia | 42.5 s | ✓ (at the timeout) |
| `agnes-2.5-flash` | nara | 40.0 s | ✓ but returned an empty cover-letter answer |
| `laguna-s-2.1` | nara | 11.8 s | ✗ answered, but not to the schema ("output invalid") |
| `gemini-3.8-flash`, `minimax-m3` | — | — | ✗ 429, free tier exhausted |

`models.freellmapi.llm_inference` is the five fastest on four platforms:
`["qwen3.8-27b", "gemini-3.5-flash", "kimi-k3", "deepseek-v4-flash", "muse-glimmer-30b"]`.

### Preflight now requires one model, not all of them

A free tier goes in and out of quota minute by minute: `gemini-3.6-flash` answered a sweep and returned 429 a
minute later, and the huggingface tier was exhausted (`~22h` to reset) by an afternoon of testing. The old
`_probe_llm_inference` failed preflight if **any** configured model failed, which was right when every model was
paid — a 429 then meant something was wrong. On rotating free tiers it would fail almost always, while a run only
ever needs one model per page, which is what `rotation.Rotation` provides. The probe still asks every model and
now returns `(answered, out)`; it raises only when none answers, and preflight prints the ones that are out:

```
LLM inference answers: qwen3.8-27b, gemini-3.5-flash, muse-glimmer-30b (via the FreeLLMAPI router); out of
quota, the rotation will skip: kimi-k3 (HTTP 429 …), deepseek-v4-flash (HTTP 429 …)
```

Measured end-to-end the same day: 3 of 5 models answered, the Kev card read
`jaredpalmer/kev-0.8b on mps via mlx (bfloat16)`, a trivial decision came back `noul 0.922`, and the gateway
counters were `1 System One, 5 LLM, 10 attempts, 2 failures, $0`.

### Corrections and two defects found in review, same day

- **The relaxed preflight probe was still defeated by the D21 breaker.** `call_engine` reports one *failed request*
  per model that cannot answer, so probing five models fed five verdicts into the run's Gateway. Three failures in
  a row is a `ProviderOutage` — and three configured models being out of quota at once is ordinary here, with two
  of the five sitting next to each other — which exited 3 instead of reporting them, *and* then skipped every
  model after the third because the trip is sticky. Each model is now probed on a Gateway of its own
  (`cli._probe_llm_inference`), which is also what the question means: "can this model answer?", independent of
  the others. One request cannot trip a fresh breaker (D21 needs three in a row), so no outage can arise there at
  all. The probe's Gateway inherits the run's `post` and `sleep` — building it with the default sender instead made
  it ignore an injected one and reach the network for real, which the first attempt at this fix did.
  `CreditOrKey` and `BudgetExceeded` are deliberately **not** caught: they name the key or the cap and must still
  reach preflight. Covered by `test_models_out_of_quota_do_not_trip_the_runs_breaker` and
  `test_a_rejected_key_during_the_probe_still_reaches_preflight`.
- **`decide.ChatDecider._one` was still sending `reasoning: {"enabled": False}`.** The chat fallback was missed when
  `llm_inference` moved to `reasoning_effort`. It now sends `decide.REASONING_EFFORT = "none"`, spelled out in
  `decide` rather than imported from `llm_inference`, which imports `decide`.
- **The chat fallback re-verified on the models that now lead the rotation** (it had only been checked on kimi-k3
  and deepseek-v4-flash, both since out of quota): `qwen3.8-27b` 0.4 s and `gemini-3.5-flash` 5.1 s both returned a
  correct `noul 1.0` / `choice "stop"` on a confirmation page.
- **`muse-glimmer-30b` passes the strict json_schema path but fails the fallback's `json_object` path**: HTTP 502
  `All 1 routed attempt(s) failed with upstream provider errors (format_ignored ×1)`. It is last in the rotation,
  and the fallback rotates on, so this costs a handover rather than a decision. Worth remembering before promoting
  it.
- **Unverified, stated for honesty:** the "huggingface" platform for `kimi-k3` and `deepseek-v4-flash` is inferred
  from an earlier `_routed_via` reading, not observed in the table above; and `deepseek-v4-flash` was never
  confirmed to honour `reasoning_effort: "none"` (its probe returned no `usage` detail either way). Neither
  changes the configuration, because the rotation covers both.

### Quota exhaustion can arrive as HTTP 413, not only 429 (live, 2026-10-07)

After a day of testing, `qwen3.8-27b` stopped answering with:

```
HTTP 413 "The request is too large for every available candidate's context/token window. Reduce the
prompt/history size or enable a larger-context model. All models exhausted: 1 route checked (1 prompt too
large for the model) … Soonest reset ~21h."
```

This is **not** about request size. A one-word prompt (`"say OK"`, `max_tokens` 2000) gets the identical 413. The
groq route's daily *token budget* is spent, so the router reports the remaining window as too small for any prompt
and dresses quota exhaustion as 413. The "Soonest reset ~21h" is the giveaway. Do not react to this by lowering
`llm_inference.PAGE_TEXT_MAX` or `MAX_TOKENS` — nothing is wrong with the request.

The handling is already right and needs no change: `gateway._retryable(413)` is False and 413 is not in
`CREDIT_STATUS`, so the model fails once, is not retried, and the rotation hands over to the next platform. The
same end-to-end probe confirmed it — `qwen3.8-27b` (413), `kimi-k3` and `deepseek-v4-flash` (429) all reported as
out, `gemini-3.5-flash` and `muse-glimmer-30b` answering, the run's Gateway untripped with zero counters, and the
Kev decision returning `noul 0.922`. So the entry in the table above stands as measured (1.2 s when it had
budget); it simply has none today.

## Cost accounting removed (user decision, 2026-10-07)

With both model servers on this machine and neither reporting a price, every request was costed at $0 and the
spend cap guarded nothing: `_reported_cost` returned None, `_estimated_cost` returned 0.0 for a model with no
`[prices]` entry — and `[prices]` was empty — so `BudgetExceeded` could never be raised. The user asked for the
whole layer to go rather than keep it inert.

Removed:

- `gateway.py`: `BudgetExceeded`, the budget check at the top of `send`, `_reported_cost`, `_estimated_cost`,
  `CHARS_PER_TOKEN`, `_charge`, the `budget` and `prices` constructor arguments, and `Counters.cost` /
  `Counters.estimated` (so `Counters.row()` no longer ends in a money figure).
- `config.py` / `config.toml`: the `Budget` model, `Config.budget`, `Config.prices`, and the `[budget]` and
  `[prices]` tables. The config is strict, so an old file naming either is now an error — which is the intended
  signal, not a regression.
- `decide.py`: `_cost` and `Decider.cost`.
- `report.py`: the "Model spend" line and the `, $X` suffix on the decisions line; `Report.decisions` is now an
  `int` (the call count) instead of a `(calls, cost)` tuple.

**Two clean stops remain, not three** (`ProviderOutage`, `CreditOrKey`). Every "three clean stops" reference in
`CLAUDE.md`, `README.md` and the build spec, including the exit-code-3 lists and the §4.3 table, was updated. The
build spec's `[prices]` and `[budget]` blocks are gone and its TOML block was checked table-for-table against the
real `config.toml` — they match exactly.

A local variable in `Gateway._attempts` named `budget` held a *timeout*, not money; it is now `timeout_s`, since
the name only made sense while a money budget existed next to it. `fill.jev_budget` / `_check_jev_budget` are a
per-job **request count** cap (`jev.max_requests_per_job`), unrelated to money, and are untouched.

Two tests now assert the absence rather than the behaviour: `test_counters_count_requests_attempts_and_failures`
checks `not hasattr(gateway.run, "cost")` and that `row()` carries no `$`, and `test_the_report_names_no_money`
checks that no `$`, "spend" or "cost" reaches `report.md`. Re-adding a paid route means re-adding this layer.

## The chat transport moves to the `openai` SDK (user decision, 2026-10-07)

**This reverses the decision recorded earlier today** ("The existing sender was kept… 'Via the OpenAI API' is
therefore read as the wire format"). The user asked again, with the router's own Python example, for the chat
calls to go through the official SDK. That is their call; the earlier reasoning is left above as the record of
what was weighed.

What changed is only the **transport inside the Gateway**, not the architecture. `gateway._post` dispatches on the
URL path — `/chat/completions` to `_openai_post` (the SDK), anything else to `_httpx_post` — so all three senders
(`decide.Decider`, `decide.ChatDecider`, `llm_inference._ask_model`) are untouched, the injectable
`post(url, body, headers, timeout)` contract is unchanged, and `Gateway.send` is still the only place a model
request leaves the program. Kev's `POST /v1/systemone` stays on httpx because it is not an OpenAI endpoint.

Dispatch is by **path**, not body shape: `_openai_post` derives the SDK's `base_url` by stripping
`/chat/completions`, which is only valid when the suffix is there, and it matches the rule `gateway_of` already
uses.

### Four things the SDK forced, each verified live

- **`max_retries=0`.** `openai.DEFAULT_MAX_RETRIES` is 2 and `_base_client.request` has its own retry loop, so the
  default would put three HTTP requests inside *each* of the Gateway's three attempts. `[limits] max_attempts = 3`
  would have meant nine. `test_the_sdk_sender_does_not_retry_on_its_own` asserts one HTTP hit per attempt.
- **Return the raw wire JSON, never `parse().model_dump()`.** The typed model adds its own unset fields
  (`refusal`, `audio`, `tool_calls`, `service_tier`) to every logged response, and — decisively — it cannot carry
  the router's non-standard `_routed_via`, which `inference_log._provider` reads to name the platform that
  actually answered. It also cannot represent the HTTP 200 with an error body and no `choices` that
  `llm_inference._ask_model` deliberately treats as a failed model. `with_raw_response` plus
  `raw.http_response.json()` keeps this sender's contract byte-identical to `_httpx_post`'s.
- **Carry `Retry-After` off the exception.** The SDK raises `APIStatusError` where HTTP returns a status, so the
  header has to be read from `exc.response` and put into the body where `_retry_after` looks for it. Without this
  the Gateway's "wait exactly as long as the provider asked" degrades silently to the fixed `BACKOFF` ladder.
- **Map the exceptions back to statuses.** `APIStatusError` keeps its status and body; `APIError` (which covers
  `APIConnectionError` and `APITimeoutError`) becomes status 0, the same shape `_httpx_post` returns for a
  transport failure. `Gateway._attempts` only catches `httpx.HTTPError`, so an uncaught SDK exception would have
  crashed the run instead of being retried.

### The SDK does not use httpx — it uses httpx2

`openai` 3.26.0 depends on **`httpx2`** (pydantic's client, 2.13.0) and its `_client` is a
`SyncHttpxClientWrapper(_DefaultHttpxClient, httpx2.Client)`; the only transport call in `SyncAPIClient` is
`self._client.send`. So `tests/test_no_bypass.py`'s existing seal could never have caught it: with `httpx.post`,
`httpx.Client.post` **and** `httpx.Client.send` all sealed, a real SDK request still reached the local router and
returned `'OK'`. Only `monkeypatch.setattr(httpx2.Client, "send", forbidden)` stops it.

The file now seals both stacks, and `test_the_seal_itself_catches_the_openai_sdk` asserts the seal catches the SDK
— so if the SDK changes transport again, that test fails loudly instead of leaving every other test in the file
passing vacuously. The project now carries two HTTP stacks (`httpx` + `httpx2`, plus `anyio`, `jiter`, `sniffio`),
which is the real cost of this change.

### Coverage

The SDK path had none as first written, because every existing test injects `post=`. `tests/test_gateway.py` now
drives `_openai_post` against the real local `http.server` fixture (which ignores the path, so the same server
serves both senders via a new `chat_url`): wire-JSON passthrough including `_routed_via`, no self-retry,
`Retry-After` honoured, 401 → `CreditOrKey`, a non-JSON error body, a closed port → status 0, and `/v1/systemone`
staying on httpx.

## Model choice belongs to the router, not to this code (user decision, 2026-10-07)

**This corrects an error made earlier today.** The entry above claims the router's `"auto"` ignores
`response_format` and answers prose at HTTP 200, and on that basis `config.toml` pinned five concrete model IDs,
`CLAUDE.md` said "never configure `auto`", and two tests asserted `auto` was absent. **That finding was wrong.**
It came from a bad test: a toy `{"n": integer}` schema with the bare prompt `"n=7"` and no system message. The
router picked GLM-5.2, which replied in prose — which says something about that prompt, not about `auto`.

Re-tested with the program's real system prompt and the real strict `ANSWER_SCHEMA` on a 5-question page, three
trials per strategy, **all twelve returned schema-valid answers**:

| Routing | Trials | Verdict |
|---|---|---|
| `auto` | 4.8 / 6.0 / 5.2 s | ✓ 3/3 |
| `auto:fast` | 6.2 / 6.4 / 4.8 s | ✓ 3/3 |
| `auto:smart` | 14.0 / 5.2 / 6.2 s | ✓ 3/3 |
| `fusion` | 78.1 / 40.2 / 31.9 s | ✓ 3/3, but far over the 45 s chat timeout |

`decide.ChatDecider`'s `json_object` path on `auto`: 4.4 / 3.0 / 2.3 s, three for three.

The decisive evidence is not the pass rate but *when* it passed: `auto` answered in ~5 s while **four of the five
pinned IDs were 429 or 413**, their free tiers spent. Routing around an exhausted tier is exactly what the router
is for, and a hand-written list in `config.toml` cannot do it — the list could only name tiers that were already
dead. The second rotation layer was duplicating the router's own job and doing it worse.

So `models.freellmapi.llm_inference = "auto"`. A list is still accepted and `rotation.Rotation` still works, for a
caller that deliberately pins several concrete IDs; it is simply not what is shipped. `config.py`'s docstring,
`CLAUDE.md`, the `llm_inference` comment about 200-with-prose, and both tests are corrected:
`test_the_shipped_config_lets_the_router_choose_the_model` now asserts the config names a routing mode, and
`test_model_access.py` checks `response_format` support only for a concrete ID, since a routing mode's catalogue
entry carries no `supported_parameters` (the router picks the model per request).

Two tests in `test_cli.py` had to stop leaning on the shipped config, which now has one entry: they build their
own pinned multi-model config (`pinned_cfg`), because the per-model probe behaviour they protect is still real for
anyone who pins a list.

### The preflight skip line is now one short reason per model

Preflight printed five nested `LLM inference: none of 1 models answered (<model>: HTTP 429 …)` strings on one
line, repeating each model name and wrapping into an unreadable paragraph. `cli._why` strips that wrapper — the
probe asks one model at a time, so the caller already prints the name — and keeps the status plus the first 90
characters of what the provider said. The full text stays in `llm_inference_logs.json`. With `auto` configured
there is usually nothing to skip at all; preflight's whole LLM check is now one line in ~1.6 s.

## 2026-10-07 — `run` hung after preflight: pages.settle counted iterations, not wall-clock

A recorded `run` (P6 FreeLLMAPI chat route) printed the preflight ✓ lines then appeared to hang. A faulthandler
stack (`faulthandler.dump_traceback_later(150, exit=True)` around `cli.main(['run','--no-record','--limit','1'])`)
caught the main thread in `external.run_external` → `pages.settle` → `pages.read_page` → `session.observe`, on the
Genesys Workday "Apply with LinkedIn" gadget page (`applywithlinkedin.myworkdaygadgets.com/awli/`, blank:
0 elements, 0 text). 16 observes of that page; last observe 4123 ms.

**Root cause.** `pages.settle` bounded its re-read loop by a counter (`waited += 1.0` per iteration, `waited <
seconds`), not by wall-clock. A `read()` is cheap on a fast page but costs ~8 s on this one — `read_page` does two
observes (the element table, then `captcha_present`'s observe), and each pays `session.observe`'s 4 s
client-render wait because the gadget page has body children but no actionable elements. So a persistently
`unsettled` slow page ran ~10 loops of ~9 s ≈ **90 s per settle call**, and `run_external` calls settle on every
loop iteration (the P5 settle-on-each-read change surfaced the latent bug). Genesys alone burned minutes before
its `load_failure` raised, so the whole run looked stuck. Not an infinite hang — a dead CDP socket would raise
`DriverTimeout` (caught → StopRun) — purely slowness compounding.

**Fix.** `pages.settle` loops against a `time.monotonic()` deadline, so the real time spent is capped by `seconds`
(10 s) regardless of read cost. Tests: the existing budget-give-up test drives a fake clock (each sleep ticks 1 s);
a new `test_settle_is_bounded_by_wall_clock_not_read_count` proves a slow, always-unsettled read yields ~3 reads in
a 10 s budget, not 10. **Confirmed live:** `run --no-record --limit 3` finished on its own in ~97 s (exit 2) —
Genesys now gives up in 48 s (`load_failure: blank page (after 2 attempts)`) instead of hanging, Mastercard
`navigation` in 2 s, Linda AI **parked** in 19 s; no submit, no ALARM, report written.

## 2026-10-08 — three defects from run 20261008-094723

### Linda AI: a required LinkedIn radio group was invisible to every required check

`Linda AI – Founding Software Engineer` came back `broken_form — the Easy Apply step did not advance (after 2
attempts)` after parking cleanly for the previous ten runs. The reported symptom is a dead end; the page text in
`browser_actions.jsonl` (actions 62 and 72, the two `click e154 → Review`) names the cause:

```
Apply to Linda AI 3/4 pages Additional Questions
Have you completed the following level of education: Bachelor's Degree?* Yes No
Are you comfortable working in an onsite setting?* Yes No This field is required
Are you legally authorized to work in Ireland?* Yes No   Back Review
```

`Review` did nothing because LinkedIn was refusing it: the onsite radio group was still empty.

**Why it was empty.** `answers.json` for that run: `"Are you comfortable working in an onsite setting?" →
answer: null, source: "generated", note: "generated text not allowed for this question"`. The free router
answered a `choice` question with prose, `judge_questions` dropped it (correctly), and the question was left
unanswered. In the ten previous runs the same router answered `Yes`/`No` from the profile — so this is **model
variance exposing a latent gap, not a code regression**.

**Why the gap.** An unanswered *required* question raises `ParkedAtQuestion` and the job parks with the question
noted. This group was classified **optional**, so it went to `ctx.optional_empty` and the loop advanced. All three
required signals miss a LinkedIn radio group at once:

- the radios are `DIV role=radio`, so `e.required` is false and there is no `aria-required`;
- `observer.js` derives `required` partly from `/\*/.test(name)`, but the aria-label is the bare question —
  LinkedIn puts the asterisk in a separate span of the label text, not in the accessible name;
- `probes.REQUIRED_EMPTY` queries `input,select,textarea`, so it returned `{"n": 0, "items": []}` on this page
  (visible in the action log) and `_is_required`'s third branch had nothing to match.

**Fix.** `llm_inference._starred_in_text(name, p)`: true when the page text shows `<name>*` (or `<name> *`).
It is OR-ed into the radio-group `required` only — deliberately **not** into `_is_required` generally, because a
text field's label is routinely a substring of another one's (`Name` inside `Last Name*`), which would mark
optional fields required and park jobs that fill fine today. Tests in `test_answers.py`:
`test_a_starred_radio_group_is_required_even_when_the_dom_says_otherwise` (built from the live page-3 elements,
asserting first that the probe and the observer both see nothing), `test_the_asterisk_rule_does_not_leak_into_
text_fields`, and `test_an_unanswered_starred_radio_group_is_an_uncovered_required_question`.

**Expected new outcome:** `⏸ parked — 1 answer needed`, with the onsite question on the Scratch Pad. That is the
distinct park-at-question label from 6975e36, not `✓ parked`.

### Toast: a cookie-consent modal `<dialog>` made the whole form inert

`Toast – Software Engineer II, IQ Grow` failed with `field would not accept its value` on eight fields, twice.
Every `type` op reported `occluded: occluded` while one line in the same observation explained it:

```
! dialog open: Cookie consent [modal]
  x type e21 → Legal First Name (required)  occluded: occluded
  + select e31 → No  33ms
```

`careers.toasttab.com` opens a **native modal `<dialog>`** named `Cookie consent` over its embedded Greenhouse
form. A modal dialog makes the rest of the page inert (HTML: "blocked by a modal dialog"), so the press path's
hit test refuses every click and focus-click; `select` still worked, which is why one op in eight succeeded and
made the failure look like a field-level problem.

Three things kept the existing cookie handling from firing:

- the observer's `consent` marker keys off `CONSENT_SELECTOR` (OneTrust, Cookiebot, Didomi, Usercentrics,
  TrustArc, Quantcast). Toast's dialog is in none of them, so `consent` was `''` on all six of its controls;
- `COOKIE_REJECT_RE` (`reject|decline|only necessary|refuse`) does not match Toast's `I do not accept`;
- `external.run_external` never called `cookie_reject` at all — only `navigate.enter` did, on the LinkedIn side.

**Fix.** `navigate.consent_modal(p)` is true when **every** open modal dialog name matches
`cookie|consent|privacy preference|gdpr` — `all`, so the Easy Apply modal (`Apply to <company>`) is never
mistaken for one, alone or alongside a consent dialog. `cookie_reject` accepts a control that is either in a
known CMP container or inside such a modal; `COOKIE_REJECT_RE` gained `refuse|necessary (cookies) only|(i) do
n(o)t accept`. `decline_consent(ctx, p)` is the one click path, used by `navigate.enter` (replacing its inline
step 5), by the `run_external` loop right after `settle`, and by `external._hand_off`.

The hand-off call fixes a second, latent bug found while testing: `pages.judge().covered` is already true for a
consent dialog, so `form_is_here` is false and `_hand_off`'s same-tab branch would poll for 24 s and raise
`navigation: the apply click did not open the external application` on a form that was right there. Toast only
escaped it by arriving through the new-tab branch.

Toast's reject button is `tag=BUTTON type=button form=""`, so `guard.never_click_element` already allows it: no
guard change was needed, and `tripwire` still passes (48 tests). `ponytail:` only reject/decline labels are
matched — a consent wall offering just *Accept* and *Close* would still stick; add `close` when a live site needs
it. Tests: `test_pages_unit.py` (selection, the Easy Apply dialog, the CMP container still winning) and
`test_external.py::test_a_consent_modal_is_declined_before_the_external_form_is_filled`, which drives the whole
`run_external` loop over a fake Toast and fails without the fix. `tests/fake_browser.py` now renders
`FakePage.modal` into the observation's `overlays`, so `Page.dialogs` is reachable from the fake at all (the field
was declared and unused).

### The Flex: "two tabs per job" — no cause found by code reading; logging added

The user reports two tabs left open on the Flex application. Code reading does not support a leak:
`process()` takes `baseline = book.handles()` **before** `browser.open`, so the job's LinkedIn tab is not in the
baseline, and `tabs.hand_off` switches to the adopted ATS tab and then closes the old one. The run artifacts
cannot settle it either, because `browser.close_tab_id` was the one browser call that wrote **no** line to
`browser_actions.jsonl` — the Flex logs show the `tabs switch` (action 11) and nothing after it.

`close_tab_id` now logs (`"tool": "close_tab"`). The competing explanation to rule out live is that the two
tabs are the two *different* Flex postings (`.../The-Flex/d9457005-…` and `.../The-Flex/59e8ac69-…`), whose
titles are nearly identical — both jobs parked in this run, and D13 keeps a parked job's tab open on purpose.

## 2026-10-08 — run 20261008-143810 (after the three fixes): what it settled and what it exposed

```
⚠ needs attention  Genesys: load_failure: blank page (after 2 attempts)
⚠ needs attention  Mastercard: no way to start the application
✓ parked           Linda AI – Founding Software Engineer
⏸ parked — 2 answers needed  The Flex – Senior Software Engineer
⚠ needs attention  Toast: field would not accept its value: 'Are you currently based in Ireland? (required)'
⏸ parked — 3 answers needed  The Flex – Senior Full-Stack Product Engineer
✓ parked           Digital Manufacturing Ireland
```

**The consent fix works.** `click e53 → I do not accept` (action 22), and the next delta shows the page coming
back to life — `reachable=10/48` and a run of `(now reachable)` markers. The fill then reported **8/8 ops ok**
where the previous run reported `1/8 ops ok (stopped early)` with seven `occluded: occluded`.

**The Flex "two tabs" is two jobs, not a leak.** `close_tab` now logs, and every external-ATS job in this run
logs exactly one: Genesys 1, The Flex (SSE) 1, Toast 1, The Flex (Full-Stack) 1; the three LinkedIn-only jobs log
0, as they should. Action 11 switches to the ATS tab and action 12 closes the LinkedIn tab
(`closed tab 16A8DED68E4D128BB49A4684B3A3AC1B`). The two Ashby tabs left open are the two *different* Flex
postings (`d9457005-…` and `59e8ac69-…`), both parked, both waiting for the user to submit — which is D13 working
as designed, not a bug.

**Linda AI parked.** The router answered the onsite question `No` from the profile this time, so the new
required-radio rule was not the thing that saved it; the rule is the net for the next time the router returns
prose. Both runs agree the question is answerable from Profile.md.

### Toast, second defect: a native `<select>`'s read-back could never pass

With the form finally fillable, one field remained: `'Are you currently based in Ireland? (required)'`, failing
twice while its own op succeeded — `+ select e31 → No  33ms` followed by `= no change (48 elements)`. The
observation explains it:

```json
{"ref": "e31", "role": "combobox", "name": "Are you currently based in Ireland? (required)",
 "value": "3e6420fe23fa0dd5632749f038b51ae0", "current": "No", "tag": "SELECT",
 "options": [{"ref": "e31:1", "label": "", "selected": false},
             {"ref": "e31:2", "label": "Yes", "value": "81d4831b…", "selected": false},
             {"ref": "e31:3", "label": "No",  "value": "3e6420fe…", "selected": true}]}
```

The field **was** set correctly. `answers.json` has `ref: "e31", option_ref: "e31:3"`, and `fill.mismatches`'
`holds` starts with `by_ref.get(q.option_ref)` — but a `<select>`'s `<option>` is not a top-level element, it
lives in the combobox's own `options` list, so that lookup always misses. `holds` then took the
`if q.option_ref:` branch and asked `checked_option(q.question, p)`, which looks for a **checked radio** in that
question's group. A `<select>` has none, so `holds` returned False for every select the plan drove through an
`option_ref` — the read-back could not pass for this shape at all. It was simply invisible before, because the
seven occluded siblings failed first and the error listed all eight together.

**Fix.** Inside that branch, resolve the option ref's owner (`q.option_ref.partition(":")[0]`) and, when it has
options, compare against the selected option's **label**, else `current`. Not `value` — Greenhouse makes the
option values opaque hashes. The radio-group fallback stays for its real case (a ref gone after a re-render).
Test: `test_fill_loop.py::test_a_native_select_reads_back_from_the_control_not_the_option`, built from the live
element above; it fails without the fix and still catches a genuinely unset choice.

## 2026-10-08 — live validation of run 20261008-143810: the tab question answered properly, and three more defects

A read-only inspection of the user's own Chrome (CDP getter-only JS; a census before and after returned the
identical 17 pages, nothing clicked, typed or closed) confirmed the two parked LinkedIn jobs and both Flex
applications, and settled the tab question with better evidence than the action log could give.

### "Always two tabs on the application" is real, and it is cross-run accumulation

Within one run there is no leak — exactly one tab per job, and no `linkedin.com/jobs/view/4470918779`,
`4470932445` or `safety/go` tab is left anywhere. Across runs there is: the user's Chrome held **14 job tabs =
7 jobs × 2 runs**, attributed by target id (Ashby/Toast/awli, whose ids are in each run's `browser_actions.jsonl`)
and by `trackingId` (Linda, DMI, whose ids are in each run's `report.md`). The earlier conclusion in the previous
section — "the two Flex tabs are the two different Flex postings" — was **wrong**: there are **four** Flex tabs,
two postings × two runs.

This is not cosmetic. The two tabs for one posting **disagree**: the stale 094723 Flex Senior-SWE tab held
`Age = "27"`, typed by the user; this run's tab has `Age` empty. Both carry a complete, submittable form, and
LinkedIn would refuse a second Easy Apply but Ashby and Greenhouse would not. D13 ("a job's tab is never closed")
is right about the tab the run just parked; it had no notion of the tab the *previous* run parked.

**Fix.** `tabs.TabMemory` (a `job.key → target_id` map at `runs/open-tabs.json`, gitignored, written in
`process`'s `finally` next to `close_junk`) plus `tabs.TabBook.close_stale`, called right after the job's own
tab is opened. Two narrow rules, because the two kinds of leftover look different:

- **`remembered`** — the exact id the last run released for this job. The only way to recognise an adopted ATS
  tab, whose URL carries no job id.
- **`/jobs/view/<job_key>` in the URL** — recognises a LinkedIn posting tab with no stored state, so the fix
  works on the very first run after it lands rather than from the second.

The tab this run is driving is never closed, a missing or corrupt memory file is an empty memory (losing it
costs one extra tab, never a job), and a close that fails is ignored because the user may have closed it
already. Only ids this program recorded, or postings carrying this job's own id, are ever touched.
`runs/open-tabs.json` was seeded from 143810's action logs for the four external jobs, so the next run closes
those too; its ids cross-check against the inspection exactly (`02965C797E96…`, `DE84413D2156…`,
`639C204FA478…`, `F38E987A4A98…`). Tests: `tests/test_tabs_memory.py`.

The ten tabs the 094723 run left are not in the memory and are left for the user to close by hand — one of them
holds input they typed, so closing it is their call, not the program's. `linkedin.com/feed` is the user's own
tab: `cli.py` opens one as the `preflight` scratch session and closes it in a `finally`, and its id is in
neither run's log.

### Mastercard is a closed posting, not a navigation failure

The live page reads *"Not currently accepting applications"* and has **no** apply control in the DOM at all —
every `<button>` on it is LinkedIn chrome. `_CLOSED_RE` / `navigate.CLOSED_RE` only knew *"No longer accepting
applications"*, so the job fell through to `_entry_choice` and came back as `navigation: no way to start the
application`, which reads as something a requeue could fix. Both patterns now accept
`(no longer|not currently) accepting applications`, so the job gets the `closed` reason code it deserves.

### Genesys: the run navigated to the "Apply with LinkedIn" widget's own host

The run did reach the right destination. Ordered URLs from its action log: the posting →
`linkedin.com/safety/go?url=…genesys.wd1.myworkdayjobs.com/…JR112303-1` → **the Workday posting itself** →
`applywithlinkedin.myworkdaygadgets.com/awli/`. That last hop is `pages.classify`'s `iframe` verdict:
`_FORM_IFRAME_RE` matches the host on `apply`, and `_NON_FORM_IFRAME_RE` did not exclude it, so `run_external`
called `browser.open` on it.

It can never render there. That host is the *embedded* Apply-with-LinkedIn widget, configured entirely by its
query string; opened top-level and bare it has `location.search == ""`, `document.referrer == ""`,
`body.innerText.length == 0`, zero interactive elements, and requests its own script with
`apiKey=undefined&renderV3=null&applyUrl=` pointing back at the gadget. `load_failure: blank page` was an
accurate reading of a page with nothing on it. `_NON_FORM_IFRAME_RE` now excludes
`applywithlinkedin|myworkdaygadgets|talentwidgets`, so the hop is refused and the Workday posting stays the
current page. Whether Workday itself can then be driven is a separate, open question.

### Still open (user decisions, not yet changed)

- **A `profile`-sourced answer whose quote does not support it is accepted.** `check_answers`' own docstring
  only requires the quote to appear in one of the three source files, never that it entails the answer. Live
  consequences: Linda AI's `Are you comfortable working in an onsite setting? → No` cites
  `quote: "Address: Via Padova, Milano, MI, Italy, 20132"`; `What is your level in English → Fluent/Native`
  while Profile.md says *"English: C1 Level (TOEFL iBT 101)"*; `Gender → Male` and `Marital Status` are chosen
  although Profile.md says nothing about either, and the **two Flex forms contradict each other** —
  `Marital Status` is `Single` on one and `I prefer not to say` on the other, and the job-type question gets
  opposite answers. The two free-text answers are honestly labelled `source: generated`; it is the
  `profile`-sourced *choices* that are unsupported.
- **The "Follow <company>" checkbox is left `checked=true` while being reported as an open question.** Both
  LinkedIn jobs: `answers.json` records `source: "linkedin-prefill", note: "pre-fill not kept"` and the report
  lists it under "Questions for your Scratch Pad" with `A: ___`, but nothing unchecked it, so LinkedIn's default
  stands and submitting follows the company. Either state is defensible; reporting it as open while leaving it
  checked is not.
- **`answers.json` records a resume-card pick that never took effect.** Linda AI logs
  `"Select one" → "Amin_The-Portfolio-Group_AI-Engineer-Gen-AI-RAG.pdf"` and DMI logs a NearTech resume, both
  stale cards from other companies chosen out of a 25-card list because `job.md` carries a generic
  `Matched Resume:` line. The live pages show the **correct** tailored PDFs, because the deterministic upload
  superseded the pick — so the outcome is right and only the record is misleading. Anyone auditing
  `answers.json` alone would conclude the wrong resume went out.

## 2026-10-08 — run 20261008-152050: all three re-tested jobs changed verdict

```
⚠ needs attention  Genesys: signup: site asks to create an account; guest link not usable:
                   no 'apply without an account' / 'continue as guest' link
⚠ needs attention  Mastercard: LinkedIn says this job is no longer accepting applications
✓ parked           Toast – Software Engineer II, IQ Grow
```

- **Toast parks.** The `<select>` read-back fix was the last thing in its way.
- **Mastercard gets the `closed` reason code** instead of `navigation: no way to start the application`.
- **Genesys gets much further.** Its action log is now: posting → click *Apply on company website* → the
  Workday posting → click *Apply* → click **Apply Manually** → `…/apply/applyManually`, where it stops at
  `signup: site asks to create an account; guest link not usable`. The `awli` hop is gone; the remaining
  obstacle is real and correct — Workday wants an account and the program never creates one.
- **Tab accounting is right.** Six stale tabs closed, three new ones opened, net −3:
  Genesys closed the seeded `F38E987A4A98…` (the dead `awli` tab) and the stale posting tab `702D1DE6409C…`;
  Toast closed the seeded ATS tab `639C204FA478…` and a stale posting tab `5BDF8737E0CA…`; Mastercard closed
  **both** `AEA95B61BA1B…` and `5443265B5E3F…` — the two tabs the inspection could not attribute, found by the
  `/jobs/view/<job_key>` rule with no stored state, which is exactly the case that rule exists for.

### Why the ungrounded answers happened: the questions had no text at all

The live inspection reported *"answers with no Profile.md support"*. The artifacts show the cause, and it is
deterministic rather than a model-quality problem. Every one of the seven suspect answers on both Flex forms
was to a question whose recorded label is **`Select one`**:

```
'Select one' -> 'Male'                | profile | 'Amin Abbaszadeh'
'Select one' -> 'Single'              | profile | 'Amin Abbaszadeh'
'Select one' -> 'Male'                | profile | 'abbaszadehmohammadamin@yahoo.com'
'Select one' -> 'I prefer not to say' | profile | 'abbaszadehmohammadamin@yahoo.com'
'Select one' -> 'Fluent/Native'       | profile | 'English: C1 Level (TOEFL iBT 101)'
```

Ashby groups these radios under a compound-UUID id with `label`, `context` and `scope` **all empty**, so
`_group_label` fell through to its placeholder. The model was asked seven questions all titled "Select one"
and answered them from whatever was at hand — the candidate's own name, then their email. It is also why the
two forms **contradict each other** (`Single` vs `I prefer not to say`, and opposite job-type answers): the
model was guessing which question it was being asked, independently, twice.

**Fixes (user decision 2026-10-08: strict — an answer that cannot be checked is left to the user).**

1. ~~`_label_from_text(options, p)` recovers the question from the page text.~~ **Written, then deleted
   the same day — see the reviewer section below.** It matched the FIRST occurrence of the option run, so
   two unlabelled Yes/No groups both took the first group's question: a felony question would have been
   answered, confidently and under the user's name, as a visa-sponsorship question, and written into
   `answers.json` under the wrong label. That is worse than the bug it fixed, and the refusal in (3)
   cannot catch it — a recovered label is no longer the placeholder.
2. Such a group's placeholder now names its own options —
   `Select one (Single / Married / I prefer not to say / Married with kids)` — because the earlier reports
   listed `Q: Select one — A: ___` on the Scratch Pad, which told the user nothing.
3. `check_answers` **refuses** an answer to a question that still carries only the placeholder. Those
   questions are `required: false` on Ashby, so the job still parks; they land on the Scratch Pad instead of
   going out as guesses.
4. A **contact detail is not evidence for a non-contact question**. Linda AI answered
   *"Are you comfortable working in an onsite setting?"* with `No` — on an Ireland role — citing
   `Address: Via Padova, Milano, MI, Italy, 20132`. `_contact_quote` matches a quote that is an email, a phone
   number, or a line announcing itself as one (`Address:`, `Phone:`, `Location:`), and the answer is dropped
   unless the question is itself a contact field. Deliberately narrow: a labelled question quoting an ordinary
   sentence is untouched, which is what keeps *"Are you legally authorized to work in Ireland? → No"* and the
   Bachelor's-degree answer working.

### The "Follow <company>" checkbox is declined, and a checkbox can finally be unticked

**Falsified twice. The decline was first inert (four sites decide what a toggle answer means), and once
that was fixed the click itself proved unsafe — it tears the Easy Apply dialog down. The box is now left at
LinkedIn's default and the question is never created. See the 192511/193650 sections below.**
`decline_follow_the_company` answers a `^follow\b` checkbox as unchecked, `source: computed`, after all the
checks — so it leaves the Scratch Pad and submitting no longer follows the company silently (user decision
2026-10-08). That exposed a second gap: `fill._carry_out`'s `check` branch hard-coded `"state": True`, so a
toggle could only ever **tick** a box and the decline could not have been carried out. It now unticks when a
**checkbox or switch** is answered `no/false/off/unchecked/decline/none`. A radio keeps `state: True`
unconditionally, because a radio's `option_ref` already *is* the option to pick — "No" there means click the
No radio, not clear it.

## 2026-10-08 — reviewer pass on `0362f98..90cebe8`: three of the last commit's five changes were wrong

An architect review of the five fix commits found **never-submit intact** — no path by which
`navigate.decline_consent` can click a transmitting control; `guard.REFUSE_LABEL_RE` and the structural-submit
rule block every shape `COOKIE_REJECT_RE` can reach, including the adversarial `"Decline and submit
application"`, which the reviewer checked directly. Both suites pass when run independently.

It also found a clean pattern worth recording: **the three commits with a live run behind them each landed
clean; the one without a live run (`90cebe8`) carried three defects**, two of which a run against the same
three jobs would have shown in its first minute. Unit tests written from captured data proved the mechanism
and missed the integration.

### `_label_from_text` relabelled the wrong question — deleted

`low.index(run)` takes the **first** occurrence of the option run, so two unlabelled Yes/No groups both get
the first group's question:

```
text: "Do you require visa sponsorship to work in Ireland? Yes No
       Have you ever been convicted of a felony? Yes No"
group 1 -> 'Do you require visa sponsorship to work in Ireland'   unlabelled=False
group 2 -> 'Do you require visa sponsorship to work in Ireland'   unlabelled=False
```

A second shape cut mid-question: with an unrelated element named `work`, *"Are you legally authorized to work
in Ireland?"* became `'in Ireland'`. Both defeat the placeholder refusal, because a recovered label is not the
placeholder any more — so instead of refusing, the program answers a legal question it has mislabelled.

A one-line guard (`if low.count(run) != 1: return ""`) fixes the first shape and not the second. The function
was 26 lines of heuristic buying exactly one recovered label (`Gender`), while the one-line sibling fallback —
the placeholder naming its own options — delivers the actual user-visible value. **Deleted.** `Gender` now
lands on the Scratch Pad with the rest.

### `close_stale`'s URL rule could close the user's own tab — deleted

`if tid == remembered or (job_key and f"/jobs/view/{job_key}" in url)` — the second disjunct had **no
ownership test**, and `list_tabs()` is `Target.getTargets`: every tab in the user's Chrome. Reading the
posting and then running the tool is exactly how the user queues a job, so that rule would silently close
their own tab, breaking the promise at the top of `tabs.py`. Note the asymmetry the reviewer spotted:
`close_junk` takes `baseline` and honours it; `close_stale` took none.

Passing `baseline` cannot fix it — `cli.py` computes it *before* `browser.open`, so it contains the stale tabs
too and honouring it would make `close_stale` a no-op. The URL rule's only purpose was a one-time migration
for tabs predating `TabMemory`, and that migration completed in run 152050. **Deleted.** `TabMemory` now
records `{id, host}` and `close_stale` re-checks the host, because a target id outlives its page: a tab the
user has since navigated elsewhere is no longer ours to close. That also honours this file's own earlier rule
about a tab the user has typed into — "closing it is their call, not the program's".

### The Follow decline was inert: FOUR places decide what a toggle answer means

The previous section claimed the Follow checkbox was unticked and off the Scratch Pad. **Both claims were
false live**, and the reviewer proved it end to end. `_carry_out` was fixed at the wrong layer:

| Location | Rule | Assumed |
|---|---|---|
| `llm_inference._set_option_refs` | answer `"no"` → `q.answer = None` | toggle = tick |
| `fill._goal_items` | `opt.checked` → skip | toggle = tick |
| `fill._carry_out` | `_UNCHECKED` → untick | **both** |
| `fill.mismatches.holds` | `bool(opt.checked)` | toggle = tick |

So the answer was nulled before `plan_fill` ever saw it, a pre-ticked box was skipped as already done, and a
successful untick read back as a mismatch. The shipped test passed only because it called
`decline_follow_the_company` and stopped — it never called `_set_option_refs`, the very next line in
`answer_page`.

`_UNCHECKED` was also the wrong discriminator: it contained `decline` and `none`, which are the **option
labels** EEO forms use on Greenhouse and Ashby. A checkbox whose own label is `Decline` answered `Decline`
unticked itself, failed read-back and raised a false `broken_form`.

**Fix:** one predicate, `llm_inference.wants_checked(q, el)`, read by all four sites. The own-label test comes
**first**, and that is what makes a radio safe — a radio's answer is always its own option label, so `"No"` on
a Yes/No group means click the No radio, never clear it. Only a toggle answered something else that plainly
means no (`no|false|off|unchecked`) is a clear. Net −4 lines across the four sites, and `_UNCHECKED` is gone.
A fifth gap surfaced while testing it: `mismatches` read a toggle's `value`/`current`, which a checkbox does
not have, so a checkbox addressed by `ref` alone could never read back; it now compares `checked` against
`wants_checked`.

### The contact rule dropped the live answer the `<select>` fix had just made work

`runs/20261008-152050/…Toast…/answers.json` records
`"Are you currently based in Ireland? (required)" → "No"`, quoting
`Address: Via Padova, Milano, MI, Italy, 20132`. The address **is** the right evidence for a "where are you
based" question, and `_CONTACT_Q_RE` had `location` but not `based`, `located` or `reside` — so the next run
would have emptied that required `<select>` and parked Toast at a question. A regression on the one job this
batch had fixed, provable from the run's own artifact.

`_CONTACT_Q_RE` gained `based|reside|resident|residence|located`. `relocat` was considered and left out: an
address is weak evidence for willingness to move, which is the kind of mismatch the rule exists to catch.
The bare-value clause is gone too — `_PHONE_V` fullmatches a résumé date range like `2019 - 2023`, which
would have dropped a years-of-experience answer as a "contact detail", and that clause had no live evidence
behind it. Only a line that announces itself (`Address:`, `Location:`, `Phone:`) counts now.

### `_unlabelled` ate a real question

`startswith` also matched *"Select one option that best describes your race/ethnicity"* — labelled,
answerable, and standard phrasing on Workday and Greenhouse. It now matches the placeholder exactly, or the
placeholder followed by its own option list.

### Two narrower hardenings

- **`consent_modal` could reach into a second dialog.** `e.dialog` is set for *any* ancestor dialog, modal or
  not, while `Page.dialogs` lists only the modal ones. An `aria-modal` consent overlay beside a non-modal
  `div[role=dialog]` application panel let `cookie_reject` return `"Decline this offer"` from the
  **application** panel, which the guard allows (it is not a submit). The modal branch now fires only when at
  most one dialog is contributing elements: if two are, we cannot say which is the consent one, so we decline
  to guess.
- **A refused consent click is no longer reported as a decline.** `decline_consent` returned `True` whatever
  `act` said, and `stop_on_error=False` turns a guard refusal into text rather than an exception. A native
  `<dialog>` consent often uses `<form method=dialog>` with untyped buttons, which the guard reads as
  structural submits — so `run_external` would loop to `"the external page loop is not making progress"`, a
  misleading diagnosis for "the guard refused the consent button". It now reports what `act` carried out,
  takes a `clicked` set so a polling caller does not re-click every pass, and `external._hand_off` checks
  `pages.is_alarm` after it, which every other click in that module already did.

### Trimmed as speculative

`CONSENT_DIALOG_RE` lost `privacy preference|gdpr` and `COOKIE_REJECT_RE` lost
`necessary (cookies )?only`: only `cookie|consent` and `(i )?do ?n[o']t accept` have live evidence, and
`consent` alone is already broad enough to reach an ATS's own *"Candidate Data Processing Consent"* dialog,
which is part of the application rather than a cookie banner.

### Still open from the review

- `browser.close_tab_id` logs **after** the CDP call, so a close that raises is never logged, and
  `close_stale` swallows the failure. A failed close is still not auditable.
- `observer.js`'s overlay list is `.slice(0, 3)`, so a fourth visible dialog never reaches `Page.dialogs`.
- US-004's "the tester never clicked anything" is the tester's own account of its own restraint; no
  third-party artifact corroborates it.

## 2026-10-08 — run 20261008-192511: the strict refusal works, and two defects only a run could find

The user authorised `requeue --from both`, discarding five parked forms, so the paths whose only jobs were all
parked could finally be exercised. The run had to be made twice: the first attempt died after two jobs with

```
assistant.driver.cdp.CdpError: Page.navigate: transport closed
  (sent 1011 (internal error) keepalive ping timeout; no close frame received)
```

— the CDP websocket's keepalive ping timed out between jobs. `process()`'s own `browser.open` sits **outside**
`run_pages` and `run_external`, whose `DriverError` handlers would have caught it, so the traceback escaped
`main()` and aborted the whole run. `external.py` already carried a comment saying a CDP error outside the loop
body must be a job outcome rather than a traceback; the same reasoning had never been applied to the open that
**starts** every job. A dead socket needs another "Allow remote debugging?" grant and every later job would fail
identically, so it is now a clean stop — exit 3, which CLAUDE.md already covers as a hung browser call, with the
job left untouched. Any other open failure stays one job's `load_failure`.

### The strict refusal, live

All seven unlabelled Flex groups came back refused, on both forms:

```
'Select one (Male / Female / Prefer not to say)'            -> None  "the form gives this question no label…"
'Select one (Single / Married / I prefer not to say / …)'    -> None
'Select one (Beginner / Intermediate / Fluent/Native)'      -> None
'Select one (Yes, I graduated / No, I never went to …)'      -> None
```

No `Gender → Male` quoting the candidate's own name, and the placeholder naming its options makes the Scratch
Pad readable. Linda AI parked as `⏸ parked — 1 answer needed` on the onsite question — US-001's intended
behaviour, where the first run of the day had reported `broken_form: the Easy Apply step did not advance`.

It also fixed, incidentally, the misleading record noted earlier in this file: Linda AI's resume-card question
is now `Select one (Amin_Accenture_AI-Engineer.pdf / …)` and **refused**, so `answers.json` no longer carries a
stale resume pick that never took effect, while the deterministic upload still attaches the right file.

Both Flex forms kept their legitimately-sourced answers (`Full Name`, `Email`, `Phone`, `nationality`, and on
one of them `Age → 27` and the salary, both `source: profile`). The two forms still differ — one answered Age
and salary, the other left them for the Scratch Pad — which is ordinary model variance on the same profile, not
a rule misfiring.

### Toast was not a regression: the posting had been taken down

`needs attention — no application form on careers.toasttab.com (other)`. The final observation explains it:

```
https://careers.toasttab.com/en-US/jobs/8226021?gh_jid=8226021
text: … The page you are trying to view is no longer available. Check out these resources below: …
```

The job parked cleanly at 15:21 and was gone by 19:25. `unsupported_ats: no application form` reads like a site
the program cannot drive rather than a job that no longer exists, so `_CLOSED_RE` now also matches
`no longer available` and the outcome is `closed`.

### DMI regressed on the Follow checkbox — LinkedIn deletes the control once it is unticked

`field would not accept its value: 'Follow Digital Manufacturing Ireland to stay up to date with their page'
(after 2 attempts)`, which sent a job that had parked cleanly to Needs Attention. The untick itself worked:

```
63 act {'ops': [{'op': 'toggle', 'ref': 'e165', 'state': False}]}   ->  1/1 ops ok  + toggle e165  138ms
64 observe …                                                       ->  no element named "Follow …" at all
```

So `mismatches.holds` found neither `e165` nor a checked member of its group, took the radio-group fallback,
and called a successful untick a refused field. A control that is gone cannot be re-read, the act reported
success, and `pages.gate` independently checks every required field on the final page — so a toggle whose own
`ref` has vanished is no longer treated as a refusal. Scoped by `bool(q.ref)`: a radio group has no ref of its
own, so its existing `checked_option` fallback keeps deciding, and an unanswered group on a page that no longer
lists it is still a mismatch.

Both defects are the same lesson this file has now recorded twice in one day: **the unit test proves the
mechanism, the run finds the integration.** The toggle fix passed seven unit tests, including one driving
`run_pages` over a pre-ticked DIV checkbox, and still broke on the one behaviour no fixture had — LinkedIn
removing the element afterwards.

## 2026-10-08 — run 20261008-193650: unticking "Follow <company>" abandons the application

The vanished-toggle fix held — DMI no longer reports `field would not accept its value` — and revealed what the
read-back had been masking. With the untick now accepted, the job ended as
`dialog_closed: LinkedIn asked to save the application`. The log is unambiguous:

```
64 act {'ops': [{'op': 'toggle', 'ref': 'e165', 'state': False}]}   ->  1/1 ops ok  + toggle e165  159ms
   [delta#46]  ! dialog open: Dismiss Save this application? Save to return to this application later… [modal]
             + e171 btn Dismiss   + e172 btn Discard   + e173 btn Save
```

**That click tears the Easy Apply dialog down.** The driver reports success — it reads `aria-checked`, sees a
difference and clicks — but the click dismisses the dialog and LinkedIn raises "Save this application? … Any
uploaded files will not be saved." Two runs, two different failures, one cause: 192511 saw the control
disappear by the next observe (read as a refused field), 193650 saw the whole dialog go.

Abandoning a filled application to avoid following a company is a bad trade, so **the box is left at
LinkedIn's default and the question is never created**: `extract_questions` skips a checkbox or switch whose
label starts `follow`. Nothing to answer, nothing on the Scratch Pad, nothing to click — which is also a
smaller diff than the `decline_follow_the_company` + `wants_checked` machinery it replaces for this control.
`wants_checked` stays, because it is what makes the other three toggle sites agree.

This reverses the user's stated preference on evidence, not taste, and it is reported to them as such. If
following the company matters, the safe place to undo it is LinkedIn's own company page after submitting.

### Toast's `closed` reason code needed one more step

`_CLOSED_RE` matched, but the job still reported
`unsupported_ats: no application form on careers.toasttab.com (closed)` — because `pages.classify`'s fallback
is `Verdict("navigate", j.kind)`, and `pages.blocker()` had no `closed` case, so the kind travelled as a
*detail* of a navigation verdict. `blocker()` now returns `Blocker("closed", …)`, and since `ATTEMPT2` has no
`closed` entry the first failure is final — right for a job that no longer exists.

### What three runs of the same five jobs taught

Every defect in this whole session was found by a run and missed by the suite, in both directions:

| Claimed from unit tests | What the run said |
|---|---|
| the consent decline works | correct — 8/8 fields where it had been 1/8 |
| the Follow box is unticked and off the report | the box stayed ticked and stayed on the report |
| the Follow box is unticked (after the 4-site fix) | the click closed the dialog and abandoned the application |
| `_label_from_text` recovers a label | it relabels a second group with the first group's question |
| the contact rule only drops mismatches | it dropped the live answer the previous fix had just repaired |
| Toast regressed | the posting had been taken down |

The suite is at 390 unit tests and every one of those six was provable only against the real page.

## 2026-10-08 — run 20261008-194557: every job now ends with an honest outcome

```
⚠ needs attention  Genesys:    signup: site asks to create an account; guest link not usable
⚠ needs attention  Mastercard: LinkedIn says this job is no longer accepting applications
⚠ needs attention  Toast:      closed: the site says this posting is no longer available
✓ parked           Digital Manufacturing Ireland – Manufacturing Computer Vision Engineer
```

with run 192511 covering the other three — Linda AI `⏸ parked — 1 answer needed`, The Flex – Senior Software
Engineer `✓ parked`, The Flex – Senior Full-Stack Product Engineer `⏸ parked — 2 answers needed`.

Genesys's `Where` is now
`genesys.wd1.myworkdayjobs.com/en-US/Genesys/job/Galway%2C-Ireland/…_JR112303-1/apply/applyManually` with the
title `"Create Account"` — the run reaches Workday's own apply flow and stops at an account wall, which is the
correct and final answer for a site that will not take an application without one.

Tab accounting across the run: Genesys 2 closes, Mastercard 1, Toast 2, DMI 1 — each job closing the tab the
previous run left plus, for the external ones, the LinkedIn tab after the hand-off. `runs/open-tabs.json` now
carries `{id, host}` for all seven jobs.

One cosmetic fix on the way out: the external path put `"<cls>: <cue>"` in the message while the reason code
already carries the class, so the report read `Reason: closed — closed: the site says…` and
`Reason: signup — signup: site asks…`. `run_external` now passes the cue alone.

## 2026-10-08 — run 20261008-202134: the chat timeout was under the router's real latency, and a slow free tier read as an outage

```
⚠ needs attention  Linda AI – Founding Software Engineer: LLM inference: none of 1 models answered
                                                          (auto: HTTP 0 APITimeoutError: Request timed out.)
⚠ needs attention  The Flex – Senior Software Engineer:   the same
■ run stopped: provider outage — freellmapi unreachable: 3 requests in a row failed
```

The router was up the whole time, and every request it was blamed for answers when it is given long enough. The
three failed requests are 9 timed-out attempts in the per-job logs — three attempts each at 45 s, which is also
what the timings show (The Flex 146 s = 3×45 + 2 + 6 s of backoff).

Replaying the exact request bodies out of `runs/20261008-202134/*/llm_inference_logs.json` against the live router,
same key, same strict `ANSWER_SCHEMA`, `max_tokens` 8192, `reasoning_effort = "none"` — The Flex's page (33,791
prompt chars = 8,791 prompt tokens, 1,196 completion tokens), nine calls on `auto`, one at a time:

| # | Time | HTTP | Routed to |
|---|---|---|---|
| 1 | 73.0 s | 200 | google / `gemini-3-flash-preview` |
| 2 | 66.1 s | 200 | google / `gemini-3-flash-preview` |
| 3 | 10.4 s | 200 | nvidia / `poolside/laguna-xs-2.1` |
| 4 | 89.2 s | 200 | cloudflare / `@cf/zai-org/glm-4.7-flash` |
| 5 | 60.6 s | **502** | — (the router gave up on its upstream) |
| 6 | 6.3 s | 200 | google / `gemini-3-flash-preview` |
| 7 | 38.6 s | 200 | cloudflare / `@cf/zai-org/glm-4.7-flash` |
| 8 | 12.6 s | 200 | nvidia / `poolside/laguna-xs-2.1` |
| 9 | 67.1 s | 200 | nvidia / `meta/muse-glimmer-30b` |

Linda AI's page (27,141 chars) answered in 2.6 s and The Flex – Full-Stack's (34,981 chars) in 5.4 s, both 200 and
schema-valid, so the page is not what makes a request slow. Nor is the model: pinned to
`gemini-3-flash-preview`, the same 8.8k-token body answered in **6.4 / 7.0 / 9.2 s**, three for three.

### What the router's own log says (`~/Library/Application Support/FreeLLMAPI/logs/freeapi.log`)

The router logs every attempt it makes per request — `start`/`next`/`ok`/`fail` with the platform, the model and a
cumulative `lat=`. It explains both halves of the spread, and neither half is a queue in front of one model:

- **A stalled upstream costs 60 s before the router may fail over.** The 60 s is the router's own per-platform
  chat limit, not Cloudflare's: its bundle holds `CHAT_TIMEOUT_MS = providerTimeoutMs("cloudflare", 6e4)` (and a
  `GLM_47_FLASH_TIMEOUT_MS` special case), and the abort is logged as
  `err="The operation was aborted (cloudflare, chat, 60s)"` — provider, kind, limit. When that limit is reached
  the router tries the next platform, which usually answers at once: the 73.0 s call was
  `a0 cloudflare @cf/qwen/qwen3.8-27b` abandoned at 60.0 s then `a1 google gemini-3-flash-preview` answering in
  13 s, and the 66.1 s call was the same shape with 6 s on the end. Failing over is itself cheap — a spent free
  tier answers 429 or 404 in well under a second.
- **`auto` sometimes picks a genuinely slow model, with no failover involved.** Single-attempt calls:
  `nvidia meta/muse-glimmer-30b` 67.1 s (2,974 output tokens), `nvidia nvidia/nemotron-3-ultra-550b-a55b` 62.8 s
  and 48.8 s, `cloudflare @cf/zai-org/glm-4.7-flash` 38.5 s and 89.1 s (5,078 and 5,478 output tokens).

**So 45 s was not merely short, it was shorter than the router's own failover horizon** — and the run's nine
timed-out attempts prove it. Between 18:21:34Z and 18:29:56Z the log holds **10 starts to
`cloudflare @cf/qwen/qwen3.8-27b` and 1 result**: `9171ab` answered Linda AI's second page in 32.3 s, and the nine
starts after it have no `ok` and no `fail` line at all, three per job, spaced 45 + 2 s and 45 + 6 s apart — this
program's own ladder, hanging up before the router's 60 s limit and so before it could reach the next platform.

The counterfactual is in the same log. `048dee` at 18:35:35Z is the first replay of The Flex's body, the one that
took 73.0 s: the same stalled `@cf/qwen/qwen3.8-27b`, but given time it was abandoned at 60.0 s, failed over to
`google gemini-3-flash-preview` and answered. The failover that would have rescued every one of those nine
requests was the one thing 45 s never let the router start.

**And 45 s came from the wrong workload.** It was measured on the 6-question page of 2026-10-07 (the table above:
1.2 to 42.5 s) and on the 5-question routing trials (`auto` 4.8 / 6.0 / 5.2 s). A real form page is 8.8k prompt
tokens, and **five of the nine calls above crossed 45 s**.

Two things changed:

- **`[models.freellmapi] timeout = 180.0`**, the one chat timeout, reaching every chat request (LLM inference and
  the System One chat fallback) through `gateway.for_config`, exactly as `[models.local] timeout` already reached
  the Kev route. `llm_inference.LLM_INFERENCE_TIMEOUT` is gone: a second source of truth was what made raising the
  Gateway's own 45 s pointless, because `call_engine` passed its constant over the top of it. 180 s is twice the
  slowest answer measured; a caller now passes a timeout only to ask for *less*, as preflight's 60 s probe does.
- **A timeout is no longer reported as an unreachable server** (`gateway._no_answer`). Status 0 covers both, and
  labelling both "unreachable" is what put "freellmapi unreachable" on a report while the router was answering.
  The message now reads `provider outage — freellmapi timed out: 3 requests in a row failed`.

180 s is room for one 60 s stall and a slow answer behind it, not twice the slowest answer. It is not a
guarantee: an earlier call of the same kind spent attempt 1 on a 502 (Cloudflare's 60 s abort, then an OpenRouter
429), had attempt 2 cut at the full 180 s (`[FallbackLoop] client disconnected mid-attempt on
cloudflare/@cf/zai-org/glm-4.7-flash`) and answered on attempt 3 in 62.8 s — 311.7 s in all, with the retry ladder
earning its keep. Raising the knob again is a config edit now.

Verified on the real path, not on the replay: `config.load()` -> `gateway.for_config` -> `llm_inference.call_engine`
with the body that failed the run returned **17 answers in 48.9 s on one attempt** (`CHAT` timeout 180 s, `JEV`
120 s, 0 failures, breaker untripped). At 45 s that request was a failure too.

**There is a router-side fix as well, and it is the one that makes pages fast rather than merely possible.** Two
levers, both on the router and both the user's call:

- **Its per-platform chat limit.** 60 s on Cloudflare is what a stall costs before a failover may start. Lower it
  and every stall fails over sooner; that is the number this program has to wait behind.
- **Which upstreams `auto` may pick.** On this workload they fall into two camps: 0.2-13 s
  (`groq qwen/qwen3.8-27b`, `google gemini-3-flash-preview`, `nvidia poolside/laguna-xs-2.1`) and 38-89 s or
  stalled (`cloudflare @cf/qwen/qwen3.8-27b`, `@cf/zai-org/glm-4.7-flash`,
  `nvidia nemotron-3-ultra-550b-a55b`, `meta/muse-glimmer-30b`). Benching the slow camp puts a form page back at
  5-13 s.

Neither is this program's business, and it must survive either way — which is what the knob is for.

The breaker itself was not touched: three timed-out requests in a row is still a stopped run, but a timeout now
means the request really was stuck rather than merely slower than a number measured on a smaller page. Retrying a
timeout was also left alone — the ladder is 3 attempts, so a genuinely hung route now costs 548 s before the job is
recorded, against 135 s before.

**Two** of the three jobs are in `Needs-Attention/` with an `llm_inference` record: Linda AI and The Flex – Senior
Software Engineer, and `requeue --class llm_inference` brings exactly those back to `Applications/` with
`Status = Resume Built`, leaving Mastercard and Toast (`closed`) and Genesys (`signup`) where they belong. The
third failed request belonged to The Flex – Senior Full-Stack Product Engineer, and it is the request that tripped
the breaker: `StopRun` is raised before any record is written, so that job never left
`Applications/4470932445_The-Flex_Senior-Full-Stack-Product-Engineer` and needs no requeue — the next run picks it
up. Its logs are in the run folder all the same, because the Gateway logs every attempt before anything decides
what it means.

## 2026-10-08 — run 20261008-212102: the websocket keepalive closed a live CDP socket

The first run with the 180 s chat timeout got two jobs in and then stopped on the third:

```
■ run stopped: the browser connection closed: Page.navigate: transport closed
  (sent 1011 (internal error) keepalive ping timeout; no close frame received)
```

A different fault from the one above, and one the longer timeout makes **more** likely rather than less.

`websockets` 17.1 pings every 20 s and closes the socket when no pong arrives within 20 s
(`ping_interval=20`, `ping_timeout=20` in `websockets/sync/client.py`; the close is `keepalive()` in
`sync/connection.py`). Its synchronous reader also implements flow control by **stopping entirely**: the
assembler is built with `pause=self.recv_flow_control.acquire`, and the reader loop holds
`with self.recv_flow_control` around its `socket.recv`, so once `max_queue` frames are buffered unread it
reads nothing at all — ping frames included. A reader that reads nothing cannot answer a ping, so the
keepalive concludes the peer is gone and closes a socket whose peer is fine.

This program drains the CDP socket only inside `Cdp.call`, which recv's in a loop and files every message
that is not its own answer into `events`. Between two calls nothing drains, and the gap between two calls
is exactly where a model request sits: 48.9 s on the page measured above, up to 180 s now. A job page that
keeps emitting CDP events fills 64 frames in that window, the reader pauses, and ~20 s later the keepalive
closes the connection. The `Page.navigate` in the message is only the next call, which found the socket
dead.

`ping_interval=None` on the one `connect` in `assistant/driver/cdp.py`. Nothing is lost: the socket is on
loopback, where there is no NAT or proxy to keep a hole open, and a Chrome that really went away is caught
by `DriverTimeout` on the next call — already a clean stop (exit 3) since
`7a7fdf2`. `max_queue=64` stays, because flow control is not the bug; killing the socket over it was.

## 2026-10-08 — run 20261008-213327: a model that answers without `relies_on` cost three whole pages

Both earlier fixes held: no request was cut at 45 s and no socket was closed by a keepalive. The run reached
the fill pages, and then threw them away:

```
⚠ needs attention  Linda AI – Founding Software Engineer: LLM inference: none of 1 models answered (auto: output invalid)
⚠ needs attention  The Flex – Senior Software Engineer:   the same
■ run stopped: provider outage — model requests: 3 requests in a row failed
```

The three "invalid" completions are in the run's logs and they are **correct answers**. `ModelAnswer` declared
`quote: str | None` and `relies_on: list[str] | None` with no defaults, which in pydantic is required-but-nullable,
and `nvidia meta/muse-glimmer-30b` simply omits a field it has nothing to put in:

| Job | What the model returned | Pydantic |
|---|---|---|
| The Flex – Senior SWE | `{"id": "r_e21", "answer": null, "source": null}` | 27 errors: `answers.0.quote Field required`, `answers.0.relies_on Field required` |
| Linda AI | `{"id": "r_e184", "answer": "Yes", "source": "profile", "quote": "…"}` | 3 errors: `relies_on Field required` |
| The Flex – Full-Stack | `{"id": "r_e20", "answer": null, "source": null, "quote": null}` | `relies_on Field required` |

Every one of them is valid JSON; only the model class rejected them. A missing nullable field and an explicit
`null` mean the same thing to everything downstream, so the four nullable fields of `ModelAnswer` now default to
`None`. Replaying the three logged completions through `ModelResponse` after the change: **17, 3 and 17 answers**,
all three pages recovered. `ANSWER_ITEM_SCHEMA` still asks for all five fields — that is what steers a model which
honours the schema, and the defaults are only what happens when one does not.

No safety was traded away for it. The rule that matters for generated prose is `check_answers`:
`if not q.relies_on or not all(src.any_contains(s) for s in q.relies_on)` — an answer with no citations fails,
is regenerated once and then dropped. `relies_on = None` reaches that rule exactly as an explicit `null` did.

The outage message also stopped being mute. `call_engine` reported its verdict with no `where`, so a rotation
that ran out printed `provider outage — model requests: …`; it now passes
`"<route> no model answered"` and the message names the route.

### Two more router-side numbers, both in this run's evidence

- **The router keeps a 45 s retry budget of its own.** One request came back HTTP 200 carrying
  `{"error": {"message": "All 2 routed attempt(s) failed with upstream provider errors (provider_bad_request ×1,
  timeout ×1) (stopped early: retry time budget 45s exceeded — one failover hop is always allowed)"}}`. So the
  60 s per-platform chat limit is not the only clock inside one request: after 45 s the router stops starting new
  hops, bar one.
- **`cloudflare @cf/zai-org/glm-4.7-flash` outran even 180 s, twice.** Its starts at 19:34:14Z and 19:38:02Z have
  no result line, and the next request begins 180 s later; earlier in the same run it answered in 25.7 s, and in
  the 20:21 run in 89.1 s with 5,478 output tokens. The router carries a `GLM_47_FLASH_TIMEOUT_MS` special case
  for this model. It is the first upstream worth benching in the FreeLLMAPI app.
