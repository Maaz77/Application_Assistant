# Application Assistant v2 — Build Spec for the Coding Agent

Spec v2 · 2026-09-22 · derived from `application_assistant_v2_plan.md` Draft 2 (IDs in parentheses point there). v2: the wrapper calls the package's public functions directly — no MCP client, no stdio (§3).

**You build** a Python program, *the wrapper*, in `Tools/Application_Assistant/`. For every job at Status "Resume Built" it fills the application in the user's own signed-in Chrome through the `jev-ultrafast-mcp` package — its public functions, called directly in Python — **stops one click before submission**, leaves that tab open, and records the result. This file is complete: where it differs from the plan, this file wins, and the plan's decisions are closed.

## 0. Rules for you — read first

1. Never submit an application, and write no code path that can.
2. Never open LinkedIn or any real job site. Develop and test only against local fixture pages in a throwaway Chrome (§9). The user runs every live test himself (T10); his `capture` files (§2) are your only real-page inputs.
3. Only `entry.py` may send `"confirm": true`. No code may send `keys` Enter/Return, `type` with `submit: true`, or an `eval` that is not a constant in `probes.py`.
4. Never write `Profile.md`, the real tracker or real job folders. Use temp copies.
5. Import tracker I/O from `Tools/Reconcile/reconcile.py`; do not change its behaviour.
6. Pin `jev-ultrafast-mcp==0.1.5`. Never commit `.env`.
7. If the server behaves differently from §3, stop and report. Do not work around it.
8. §11 lists what you must ask the user. Everything else is decided here.
9. Only `jev.py` imports `jev_ultrafast_mcp`, and it calls only the public `server.browser_*` functions. Never import or call `BrowserManager`, `Session`, `policy`, `safety` or other internals.

## 1. What the finished program does

Inputs: `Applications/{Jobid}_{Company}_{JobTitle}/` holding `job.md` (its Overview has `**LinkedIn URL:** <url>`) and exactly one `Amin_*.pdf` (the tailored resume); `Profile.md` (background, Fit Preferences, `## Scratch Pad`); `Job_Tracker.numbers`, sheet "Jobs", columns include "Status", "Job URL", "Notes".

1. **Preflight** — any failure: exit 1, zero writes. Config valid; key present; the package imports after `apply_env()` (§3); `browser_doctor` shows `text_model`, `uploads`, `js_eval` true; LinkedIn probe passes (§6.1); `browser_doctor` then shows `connected: true`.
2. **Journal recovery** (§8.4).
3. **Queue** — rows with Status "Resume Built", joined to folders by LinkedIn job ID. Folder without a row: process it and add a row. Row without a folder, or folder whose row has another status: report, skip.
4. **Per job, strictly in sequence** (§5): open the LinkedIn URL in a new tab → entry → page loop → at the final pre-submit step: park (tab untouched), record (row "Pending Review", folder → `Pending-Review/`), release the tab. A blocker after ≤ 2 attempts, or a required question with no answer in the files: note in `job.md`, folder → `Needs-Attention/`, row "Needs Attention" + Notes, tab left open and released.
5. **Close-out** — report, one terminal line per job, exit code (§8.5).

Always: upload the tailored resume · every value traces to `Profile.md`, `job.md` or the resume text (or a kept LinkedIn pre-fill) · never ask the user during a run · never close a job tab · tracker row and folder move written together, journaled.

## 2. Project setup

```
Tools/Application_Assistant/
  pyproject.toml     same packaging tool as Tools/LinkedIn_Job_Scrapper (Poetry if it uses it); Python ≥ 3.11
  config.toml  .env.example  .gitignore (.env, runs/)  run_application_assistant.command  README.md
  prompts/answer_engine.md  prompts/page_goal.md
  assistant/  __main__.py cli.py config.py jev.py guard.py probes.py entry.py pages.py fill.py
              answers.py google_signin.py blockers.py tabs.py records.py tracker.py report.py
  tests/      fixtures/*.html  golden/  captured/  conftest.py  test_*.py
  runs/<YYYYMMDD-HHMMSS>/  report.md run.jsonl journal.jsonl calls.jsonl answers/ shots/ tracker-backup.numbers
```

Dependencies: `jev-ultrafast-mcp==0.1.5`, `httpx`, `pydantic>=2`, `pypdf`, `python-dotenv`, `pytest`; `numbers-parser` only if `reconcile.py` uses it. (`mcp` arrives as a dependency of the package; the wrapper never imports it.)

CLI (`python -m assistant …`):
- `run [--dry-run] [--no-record] [--job URL] [--limit N]` — `--dry-run`: no browser, no writes, prints the queue; `--no-record`: fills and parks, writes no tracker, folder or `job.md`.
- `preflight` · `tripwire` (runs the fixture tripwire suite).
- `capture URL` — for the user: opens URL in his Chrome, saves `browser_observe(mode="full", include_json=True)` output, page text and a screenshot to `tests/captured/<host>-<ts>/`. Read-only: no clicks, no typing.

`config.toml` (pydantic model; unknown keys are an error):

```toml
[paths]
base = ""                          # REQUIRED — holds the five entries below (§11)
applications = "Applications"
pending_review = "Pending-Review"
needs_attention = "Needs-Attention"
profile = "Profile.md"
tracker = "Job_Tracker.numbers"
tracker_sheet = "Jobs"

[browser]
cdp_url = "http://127.0.0.1:9222"
chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"   # tests only
max_actions = 2000
max_pages_per_job = 15

[models]                           # OpenRouter slugs
answer_engine = ""                 # REQUIRED (§11)
text_helper = "deepseek/deepseek-chat"
jev = "jev-latest"

[policy]
prefill = "keep-if-silent"         # or "strict"
free_text_max_chars = 1500

[google]
account_email = ""                 # empty → every Google sign-in is a blocker (§11)
```

`.env`: `OPENROUTER_API_KEY=` — the only secret.

## 3. jev-ultrafast-mcp 0.1.5 — interface

**Load and call** — `jev.py` is the only module that imports the package. Verified with the 0.1.5 wheel in a clean venv: the functions are plain synchronous callables, `browser_doctor()` runs, and importing `jev_ultrafast_mcp.config` does not load the server.

```python
from jev_ultrafast_mcp.config import DEFAULT_DENY_PATTERNS   # safe: does not load the server
apply_env()                                                  # see below
from jev_ultrafast_mcp import server   # AFTER apply_env(): Config.from_env() and the browser manager run at import (server.py l.61–63)
text = call("browser_open", url=u, session=s)                # keyword args; every function returns str
```

`call(name, **kwargs)`: runs `getattr(server, name)(**kwargs)` on one dedicated worker thread (one call at a time, B7), waits with the timeout below, and logs to `calls.jsonl` (key redacted). On timeout: write the report, then `os._exit(3)` — a blocked call cannot be cancelled, and `os._exit` skips the exit hook, so the current job's tab stays open. The import registers the package's tab-closing exit hook in the wrapper's own process (B3), so release (§4.5) stays necessary.

`apply_env()`: in the wrapper's own `os.environ`, delete every key starting with `JEVMCP_`, `TYPESAFE_`, `TEXT_MODEL_`, `OPENROUTER_` (a stray `TYPESAFE_API_KEY` overrides the OpenRouter key, `config.py` l.97), then set:

| Variable | Value |
|---|---|
| `JEVMCP_MODE` / `JEVMCP_CDP_URL` | `attach` / `browser.cdp_url` |
| `JEVMCP_FOREGROUND` / `JEVMCP_MAX_ACTIONS` | `0` / `browser.max_actions` |
| `JEVMCP_ALLOW_UPLOADS` / `JEVMCP_ALLOW_JS` | `1` / `1` |
| `JEVMCP_CONFIRM_PATTERNS` | `",".join(DEFAULT_DENY_PATTERNS + TRANSMIT)` — import `DEFAULT_DENY_PATTERNS` from `jev_ultrafast_mcp.config`; assert no pattern contains `,` or `;` (the value is split on both) |
| `TYPESAFE_BASE_URL` / `TYPESAFE_MODEL` | `https://openrouter.ai/api/alpha/decisions` / `models.jev` |
| `OPENROUTER_API_KEY` / `TEXT_MODEL_API_KEY` | the `.env` key, both |
| `TEXT_MODEL_BASE_URL` / `TEXT_MODEL` | `https://openrouter.ai/api/v1` / `models.text_helper` |

**Functions** (`server.browser_*`) — keyword arguments only; all except `browser_doctor` take `session` (default `"default"`). `contract_check.py` compares these signatures with `inspect.signature`. Timeouts: 60 s, `browser_goal` 300 s.

| Function | Parameters |
|---|---|
| `browser_doctor` | — (JSON with `connected`, `text_model`, `js_eval`, `uploads`, …) |
| `browser_open` | `url`, `hint=""` |
| `browser_observe` | `include_json=False`, `include_text=True`, `mode="auto"\|"full"\|"delta"` |
| `browser_act` | `ops`, `dry_run=False`, `observe_after=True`, `stop_on_error=True` |
| `browser_assert` | `checks` |
| `browser_goal` | `goal`, `max_steps=20`, `verify=None`, `verbose=False` — no `url` in the 0.1.5 wheel |
| `browser_tabs` | `action="list"\|"new"\|"switch"\|"close"`, `index=-1`, `target_id=""`, `url="about:blank"` |
| `browser_close` | `shutdown_browser=False` |

`browser_macro` and `browser_sessions` are not used in v1.

**Ops** (`browser_act`), exact keys:

```
{"op":"click","ref":"e12"}                                   # "confirm": true only from entry.py
{"op":"type","ref":"e7","text":"…","clear":true,"submit":false}
{"op":"select","ref":"e9","value":"Italy"}
{"op":"toggle","ref":"e3","state":true}
{"op":"upload","ref":"e5","path":"/abs/path/Amin_X.pdf"}
{"op":"keys","key":"Tab"}                                    # never Enter / Return
{"op":"wait_for_load","timeout_ms":20000}    {"op":"wait_for_text","text":"…","timeout_ms":8000}
{"op":"screenshot","path":"/abs/path/p3.jpg","full":true}
{"op":"eval","js":"<a constant from probes.py>"}
```

**Checks** (`browser_assert`): `url_matches{pattern}` · `url_contains{text}` · `title_matches{pattern}` · `text_contains{text,regex}` · `text_absent{text}` · `element_exists{role,name}` · `element_gone{ref}` · `value_equals{ref,value}` · `checked{ref,state}` · `count_at_least{role,min}` · `js{expr}` (boolean only).

**Verified behaviour** (read in the 0.1.5 source):
- **B1** Confirmation rules run on every `click`, Jev's included (goal ops carry no `role`). A blocked click returns error `needs_confirmation`; the goal ends `status: failed:needs_confirmation` with the target label in its trace. If `verify` then passes, the server rewrites the status to `done` → always scan the trace.
- **B2** Only `click` (rules) and `type` into secret-looking fields are checked. `keys` Enter and `type … submit: true` are not.
- **B3** At process exit, the package's `atexit` hook closes every session's current tab, attach mode included → release (§4.5).
- **B4** `browser_tabs` can address any tab by target ID.
- **B5** Text helper: needs `TEXT_MODEL_API_KEY`; sees the goal text, the field, the page title and the first 6,000 chars of page text; rejects values > 2,000 chars; no value → the goal returns an error.
- **B6** Element table: name, label and value truncated to 160 chars; no `required` flag.
- **B7** One browser, one session at a time.
- **B8** `browser_goal` text starts `goal: …` / `status: …` / `steps: N`, then trace lines. Status: `done` · `failed:<error>` · `stopped: hit max_steps=N`. A result without a `status:` line is an error → failed.
- **B9** `browser_doctor` may report `connected: false` until a tool first needs the browser.

**Unknown until T1** — find out, record in `tests/golden/` and `DISCOVERY.md`: the element-table JSON shape (keys for ref, role, name, label, value, options, checked, frame) and the role vocabulary; the `browser_assert` result format; whether `eval` returns its value; how a hidden `<input type=file>` appears and which ref `upload` needs; what `browser_tabs` `new` returns and whether it switches the session.

## 4. Safety layer

```python
TRANSMIT_STRONG = [r"\bsubmit", r"\bsend\b", r"\bapply\b"]
TRANSMIT_WEAK   = [r"\bconfirm\b", r"\bdone\b", r"\bfinish", r"\bcomplete\b"]
TRANSMIT        = TRANSMIT_STRONG + TRANSMIT_WEAK       # re.I, re.search on the element label
ADVANCE_RE = r"^\s*(next|continue|review|save and continue|save & continue)\b"   # and not TRANSMIT
ENTRY_RE   = r"^\s*(easy apply|apply)\b"
```

- **4.1 Guard.** Every `browser_act` call goes through `jev.act()`, which calls `guard.check(op, table)`. Raise `GuardError` for: `keys` containing Enter/Return (also inside `keys[]`); `type` with a truthy `submit`; `eval` whose `js` is not identical to a `probes.py` constant; `click` whose target label matches TRANSMIT; any op with `confirm` unless called with the `ENTRY` token that only `entry.py` imports.
- **4.2 Entry click** (`entry.py`, the only `confirm: true`): the label matches ENTRY_RE, no form field on the page holds a value, and the URL matches `/jobs/view/` or the page has no form fields.
- **4.3 Advance.** Exactly one button matches ADVANCE_RE and not TRANSMIT → the wrapper clicks it. None or several → one goal "Go to the next step of this application." (transmit stays blocked, B1). An advance that leaves the same fields on screen counts as a failed attempt.
- **4.4 Final step.** Page flags `has_transmit and not has_advance`; or a goal ends `failed:needs_confirmation` on a TRANSMIT_STRONG label. A block on a TRANSMIT_WEAK label is not final (date pickers use "Done"): read-back decides; still wrong after one retry → blocker "widget needs a '<label>' click".
- **4.5 Release(session).** `browser_tabs(action="new")` → switch the session to that scratch tab → `browser_close(session)`. Run it after every record, and in a `finally` for every job session on every exit path.
- **4.6 Alarms** → stop the run at once, exit 3: a goal trace shows a successful click on a TRANSMIT label; or, after the wrapper acted on a job, the page text matches `(your )?application (was )?(submitted|sent)|thank(s| you) for (applying|your application)`.
- **4.7 `probes.py`** — constants only, read-only, JSON results:
  - `REQUIRED_EMPTY` (eval): label and type of every `[required]` / `[aria-required=true]` input, select or textarea still empty (file: `files.length == 0`; radio/checkbox group: none checked);
  - `MAXLENGTHS` (eval): label → `maxLength` for inputs and textareas with `maxLength > 0`;
  - `IFRAME_SRCS` (eval): the absolute `src` of every iframe;
  - `CAPTCHA_PRESENT` (assert `js`): a reCAPTCHA, hCaptcha or Turnstile iframe or container exists.

  Unit test: no probe contains a DOM assignment, `.submit`, `.click`, `dispatchEvent`, `fetch`, `XMLHttpRequest` or `location`.

## 5. Per-job algorithm

```
process(job):
  pdf = the only match of job.dir/"Amin_*.pdf", else needs_attention("resume PDF missing or ambiguous")  # no browser yet
  S = job.key; baseline = all tab target IDs
  browser_open(job.linkedin_url, session=S); e = classify_entry()                          # §6.1
  closed | applied → needs_attention (no retry); signed_out → StopRun (exit 3, job untouched)
  entry click (§4.2); a new tab opened → switch S to it, close the LinkedIn tab
  filled = False; pages = 0
  loop:
    p = observe(mode="full", include_json=True) + probes + flags (§6.5)
    blocker → attempt 2 per §6.3, else needs_attention
    Google page or sign-in wall offering Google → google_signin() (§6.2), else needs_attention
    cookie banner → click "Reject all" | "Only necessary" | "Accept all"
    no fields, ENTRY button (ATS job page) → entry click (§4.2); continue
    no fields, IFRAME_SRCS holds a form → navigate the application tab to that src (attempt), else blocker
    if p.has_fields and not filled:
        a = answer_engine(p)                                    # §7
        fill(a)       # 1 upload (C25) · 2 wrapper types generated/computed long text · 3 one goal with ANSWERS
        if a.uncovered_required: needs_attention(D2, questions) # after filling the rest; no advance, no retry
        read_back(a)  # mismatch → one retry of step 3 → blocker "field would not accept its value"
        filled = True; continue
    if p.has_transmit and not p.has_advance: break              # final step
    advance() (§4.3); filled = False; pages += 1
    pages > max_pages_per_job → blocker "no final step after N pages"
  gate() (§6.4); fail → one retry → needs_attention(<failed check>)
  screenshot → record Pending Review (§8) → release(S) → close junk tabs (not in baseline, not the application tab)
```

`needs_attention(...)`: `job.md` note, row "Needs Attention" + a one-line Notes, folder → `Needs-Attention/`, tab left open, release, close junk tabs, next job.

## 6. Page rules (`pages.py`, deterministic)

- **6.1 LinkedIn job page.** Signed out = URL contains `/login`, `/authwall`, `/checkpoint` or `/uas/`. Closed = "No longer accepting applications". Applied = `Applied \d+ \w+ ago` or "Application submitted". Else Easy Apply / Apply via ENTRY_RE. Preflight probe: open `https://www.linkedin.com/feed/` in session `preflight`; pass if the URL still contains `/feed` and no signed-out marker; then `browser_close(session="preflight")`.
- **6.2 Google** (`accounts.google.com`; no model acts there). Click the button matching `(sign in|continue) with google` → click the chooser element containing `google.account_email`, exactly once → back on the site. Blocker if: a password or 2-step prompt; consent text ("wants to access your Google Account", "Allow"); a second Google click is needed; after the return the text matches `create (an |your )?(account|profile)|terms of (use|service)|complete (your )?registration`.
- **6.3 Blockers.** Attempt 2 = the failing step again from a fresh page.

| Class | Cue | Attempt 2 |
|---|---|---|
| captcha | `CAPTCHA_PRESENT`, or text `verify you('\| a)re human\|are you a robot` | wait 5 s, reload |
| signup | "create account" / "sign up" / "register" with a password field | click a link matching `apply without an account\|continue as guest` |
| credentials | password field or 2-step prompt, no Google path | none |
| broken form | validation text stays, a value is refused, advance changes nothing | reload, refill the page |
| load failure | timeout, HTTP ≥ 400, blank page | reopen from the LinkedIn URL |

- **6.4 Gate** — all must hold: `REQUIRED_EMPTY` is empty; the page text does not match `this field is required|is required\.|please (enter|select|fill|provide)|invalid (value|format|email|phone)`; the resume file name (first 30 chars) is on the page; a TRANSMIT button still exists; no alarm text (§4.6); every typed generated text passes `value_equals(ref, text[:160])`.
- **6.5 Flags.** `has_fields`: an element with a form role (textbox, combobox, listbox, checkbox, radio, spinbutton, searchbox — adjust to the T1 role vocabulary) or a file input. `has_transmit`: a button or link matching TRANSMIT; on a page without fields, ENTRY_RE labels do not count. `has_advance`: a button matching ADVANCE_RE and not TRANSMIT.

## 7. Answer engine (`answers.py`)

One call per form page: `POST https://openrouter.ai/api/v1/chat/completions`, `temperature: 0`, `model: models.answer_engine`, `response_format: {"type":"json_schema","json_schema":{"name":"page_answers","strict":true,"schema":…}}`. HTTP 400 on `response_format` → retry once with `{"type":"json_object"}`. Timeout 60 s; one retry on timeout or 5xx. Invalid output after one retry → blocker "answer engine output invalid".

User message (JSON): `page` {url, title, elements (the table JSON), text (≤ 12,000 chars), maxlengths, required_empty} and `sources` {profile: `Profile.md`, job: `job.md`, resume: the resume text via `pypdf`}.

Schema (strict mode: every property required, nullable by type union, `additionalProperties: false`):

```
Question    { id, question, kind: "text"|"longtext"|"choice"|"file"|"other",
              ref: str|null, option_ref: str|null, options: [str]|null, required: bool,
              answer: str|null,
              source: "profile"|"job"|"resume"|"generated"|"computed"|"linkedin-prefill"|null,
              quote: str|null, relies_on: [str]|null }
PageAnswers { questions: [Question] }
```

`prompts/answer_engine.md` must require:
- every question on the page, pre-filled ones included; `question` = the label as shown;
- facts only from the sources; `quote` = an exact sentence from the named source;
- `generated` only for motivation or description questions (why this role or company, "tell us about…", cover-letter text): first person, ≤ maxlength else ≤ `free_text_max_chars`, facts only from the sources, the exact sentences used listed in `relies_on`;
- `computed` only for total years of experience; `relies_on` = the dated role lines;
- no answer in the sources → `answer: null`; never guess salary, notice period, start date, years with a tool, visa, right to work or demographics;
- a pre-filled value the sources do not address → keep it, source `linkedin-prefill`; one they contradict → correct it;
- file inputs: `kind: "file"`, `answer: null`.

Checks in code — `norm` = collapse whitespace and strip; a failed check sets `answer = null`:
- `quote` and each `relies_on` item must be substrings of `norm(source)`;
- `choice` with `options`: every option must occur in the page text or element labels, and `answer` must be one of them (case-insensitive);
- `generated`: only for kind `text`/`longtext`, and never when the question matches `salary|compensation|\bpay\b|notice period|start date|availability|visa|sponsor|right to work|authori[sz]ed|years of experience|gender|ethnic|race|veteran|disabilit`; a bad `relies_on` gets one regeneration first;
- `computed`: recompute total months from the month-year ranges in `relies_on` (merge overlaps; "present" = today), floor to years; it must equal `answer`;
- `linkedin-prefill`: only when the element's current value is non-empty and equals `answer`; with `policy.prefill = "strict"` → null.

Then: required and null → D2 path (Needs-Attention); optional and null → left empty, listed in the note.

Fill mapping:
- `file` → wrapper `upload` into the input whose label or section says resume/CV; a single file input on a resume step → that one; several and none says resume/CV → blocker;
- `generated` and long `computed`/other texts over 300 chars → wrapper `type` on `ref` (`clear: true`, `submit: false`);
- every other non-null answer that differs from the current value → one line in ANSWERS. Empty ANSWERS → no goal.

`prompts/page_goal.md` (`max_steps = 2 × len(ANSWERS) + 3`; `verify` = `value_equals` / `checked` checks for answers with `ref` / `option_ref`):

```
Set each field listed in ANSWERS to exactly its value, on this page only.
Do not change any other field. Do not click Submit, Send, Apply, Confirm, Done or Finish.
Stop when every listed field holds its value.

ANSWERS
- "<question>" → "<answer>"
```

A stale ref → re-observe, re-map by `question`, retry once. Append every question to `runs/<ts>/answers/<folder>.json` (question, answer, source, quote / relies_on).

## 8. Records

- **8.1 Tracker** (`tracker.py`): `load()`, `find(job_id)`, `set(job_id, status, notes=None)`, `add(fields)`, `save()`. Use `reconcile.py`'s I/O; fallback `numbers-parser` (check the installed API): `Document(path).sheets[sheet].tables[0]`, header in row 0, `cell(r, c).value`, `write(r, c, v)`, `doc.save(path)`. Store mtime + sha256 at load; before each save both must still match, else StopRun (exit 3). Save after every job. Backup at preflight. Statuses: "Resume Built", "Pending Review", "Needs Attention".
  `job_id(url)`: `/jobs/view/(?:[^/?#]*?-)?(\d{6,})`, else `[?&]currentJobId=(\d+)`, else exact string compare. Never match by folder name. New row: Job URL, Status, Notes, plus Company and Job Title when those headers exist (from `job.md` `**Company:**` / `**Job Title:**`, else from the folder name — display only).
- **8.2 Folder move:** `os.rename(src, dst)`, same name. Check that `dst` does not exist before the tracker write; if it exists → StopRun.
- **8.3 `job.md` note:** a line exactly `# Application Assistant` at root level, appended at the end of the file if absent; each new entry goes at the end of that section:

```
## 2026-09-22 14:03 — Needs Attention: <class>
- What: <exact description>
- Where: <url> · "<page title>" · page <n> · <stage>
- Filled before stopping: <n> fields
- Open questions:
  - "<question>" — <kind>; options: <a | b | c>
```

- **8.4 Journal** `runs/<ts>/journal.jsonl`, one line per event `{"t","job","event","status","src","dst"}`: `record_start → tracker_saved → folder_moved → record_done`. At start, after the LinkedIn probe, every job in any `runs/*/journal.jsonl` without `record_done` is completed idempotently: set the status if different, move the folder if not moved, append `record_done`.
- **8.5 Report and exit.** `runs/<ts>/report.md`: Summary · Parked (company · title · URL · folder · generated texts · kept pre-fills) · Needs Attention (reason, where) · Queue anomalies · Questions for your Scratch Pad (deduplicated, `- **Q:** <question> — **A:** ___ _(asked by <Company> – <Title>, <date>)_`) · Timings. Terminal per job: `✓ parked  <Company> – <Title>` or `⚠ needs attention  <Company> – <Title>: <reason>`, then the report path. Exit: 0 all parked · 1 preflight failed · 2 at least one needs attention · 3 stopped mid-run (a hung call included, §3). `calls.jsonl` logs every MCP call and result with the key redacted.

## 9. Tests

- **Markers:** `unit` (no browser, no network) · `browser` (throwaway Chrome, no model) · `live_model` (OpenRouter, local fixtures only; runs only with `--live` and a key in `.env`, after the user agrees).
- **Throwaway Chrome** (session fixture): `<browser.chrome> --headless=new --remote-debugging-port=9223 --user-data-dir=<tmp> --no-first-run --no-default-browser-check about:blank`; `conftest.py` runs `apply_env()` with `JEVMCP_CDP_URL=http://127.0.0.1:9223` before the first import of `jev_ultrafast_mcp.server` — the configuration is fixed per process, so all browser tests share this Chrome. Open tabs: `http://127.0.0.1:9223/json/list`.
- **Fixture server:** `http.server` on 127.0.0.1 serving `tests/fixtures/`; every POST and every request to `/submit` is appended to `posts.log`; the tripwire asserts it stays empty.
- **Fixtures:** `f01` single page + "Submit application" · `f02` modal steps Next → Review → "Submit application" · `f03` final button "Apply" · `f04` Enter in a text field submits · `f05` "Send application" · `f06` cookie banner "Confirm my choices" + "Reject all" · `f07` the only button is "Done" · `f08` custom dropdown, radio group, date picker with "Done", "Add another" section · `f09` resume input hidden behind a styled button; a step with resume + cover-letter inputs · `f10` job page whose "Apply" opens the form in a new tab · `f11` LinkedIn-like job page: Easy Apply / closed / applied · `f12` form in an iframe from a second local origin · `f13` required-field error after advance · `f14` confirmation page "Your application was submitted".
- **Required tests:** every guard rule · probes are read-only · only `entry.py` sends `confirm` (AST) · `jev.py`: env set before the import, stray `TYPESAFE_API_KEY` removed, only `jev.py` imports the package (AST), a timeout exits with code 3 (stub function) · release: a subprocess opens a fixture tab, releases it and exits; the tab is still in `/json/list` with its values — control: without release, the tab is gone (B3) · hand-off on `f10`, baseline tabs untouched · tripwire, wrapper paths on `f01`–`f07`: zero POSTs · tripwire, `live_model`: goal "Submit this application" on `f01`–`f07` → zero POSTs, trace shows `needs_confirmation` · text helper, `live_model`: ANSWERS typed exactly on `f08` · upload on `f09` · answer checks with canned engine outputs · records on temp copies: tracker adapter, journal kill-recovery, `job.md` heading rules, folder-move invariant · classifier on every fixture and every `tests/captured/` page.

## 10. Tasks — in order; each ends with its tests green

| Task | Build | Done when |
|---|---|---|
| T0 | Scaffold, config loader, `.env.example`, `.gitignore`, CLI skeleton, pytest setup | `python -m assistant --help` works; `pytest -m unit` passes |
| T1 | `jev.py` (`apply_env`, import after the env, `call()` on one worker thread with timeout and `os._exit(3)`, `calls.jsonl`); Chrome and fixture-server fixtures; discovery | `tests/golden/` + `DISCOVERY.md` answer every §3 unknown; pydantic models of the element table exist. If `eval` returns no value: stop and report |
| T2 | `guard.py`, `probes.py`, `tabs.py` | guard, probe, confirm-token, release and hand-off tests pass |
| T3 | Tripwire suite, `tripwire` command | wrapper-path tripwire passes; the `live_model` tripwire and text-helper tests pass once the user enables `--live` |
| T4 | `pages.py` | classifier tests pass on all fixtures |
| T5 | `answers.py`, `prompts/answer_engine.md` | answer-check tests pass |
| T6 | `fill.py`, `prompts/page_goal.md` — loop, upload, typing, goal, read-back, advance, final step, gate; a `FakeMCP` for loop unit tests | loop unit tests pass; `live_model`: `f02` and `f08` parked with zero POSTs |
| T7 | `tracker.py`, `records.py`, journal, `report.py` | records tests pass |
| T8 | `entry.py`, `google_signin.py`, `blockers.py` | rule tests pass |
| T9 | `cli` run, preflight, `capture`, exit codes, terminal lines, `run_application_assistant.command` | `--dry-run` on a temp workspace prints the right queue and writes nothing |
| T10 | `README.md` (setup = plan §19.2) and `LIVE_TEST.md` for the user: `preflight` → `capture` on a few real forms → `--no-record --limit 1` on one Easy Apply job → one Greenhouse or Lever job → first recorded run with `--limit 3` | both files exist; hand over to the user |

## 11. Ask the user — do not guess

- `models.answer_engine`: the OpenRouter slug. Preflight refuses to run while it is empty.
- `paths.base`: the folder that holds `Applications/`, `Profile.md` and `Job_Tracker.numbers` (search the repo first).
- `google.account_email`: the Google account for the one-click rule.
- One real Scratch Pad entry, to match the report's suggestion format.
- Only if discovery fails: the `job.md` Overview line format, or tracker columns other than Status, Job URL and Notes.

## 12. Fixed defaults (plan C-items)

C1 no tab groups · C4 tracker saved after every job · C7 wrapper clicks the single advance button · C8 a blocked job's tab stays open · C10 `policy.prefill` · C14 closed or already applied → Needs-Attention, no retry · C15 cover letter: text → generated; optional file → empty; required file → blocker · C16 demographics only from the Scratch Pad · C19 LinkedIn signed out mid-run → exit 3, job untouched · C20 free text ≤ `free_text_max_chars` without a maxlength · C21 `JEVMCP_MAX_ACTIONS=2000` · C22 uncovered optional field → empty, listed in the note · C23 experience totals only as `computed` · C24 cookie banners: Reject all / Only necessary, else Accept all · C25 resume input by label.
