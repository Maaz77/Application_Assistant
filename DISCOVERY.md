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
