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
