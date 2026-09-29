# Application Assistant v2: Build Spec, as built

Spec v4.0 · 2026-09-29 · describes the code as it is. It replaces spec v2 (2026-09-22), which the code outgrew. The section numbers (§), the plan items (C…, D2) and the observer behaviours (B1–B9) keep their v2 meaning where the code still cites them. `DISCOVERY.md` records why each change was made, with dates and live measurements. §13 lists the open problems.

**v4.0 (re-core P2, 2026-09-29):** the vendored browser package is **removed** and its CDP code is owned in `assistant/driver/` (§3): the aa2–aa6 observer (now with `type`/`tag`/`required`/`maxlength`/`placeholder`/`form`/`dialog`/`consent`/`scope` fields and no 160-char cut), a guarded op executor with **one mouse-press path** that enforces the never-submit rule, and one CDP connection per run. `assistant/browser.py` (`Browser`) replaces `jev.py`; only it imports the driver. The **never-submit rule is absolute** (§4, `guard.never_click_element`): the full refused-label set, structural submits, an advance allowlist, the final-page judgment and one cookie-consent exemption; the driver has no op that sends Enter/Escape. **Navigation is deterministic** (§5.1, `assistant/navigate.py`) with no page-kind model call — the three `browser_goal` calls are gone, and `fill_page` drops its page-goal fallback. See `recore/HANDOVER.md` (end of P2) and `DISCOVERY.md` (P2 entries) for the full change list, and note that §3/§4/§5.1 below still carry some v3.2 prose about the package that the P2 entries supersede.

**v3.2 (re-core P1, 2026-09-28):** every model request — the System One client, the LLM inference, the chat fallback and the browser package's own requests — now leaves through **one `Gateway`** (`assistant/gateway.py`, §3.3): one request in flight at a time, one retry layer, one timeout per kind, one logging point, and the counters the report prints. Batching (`BATCH`/`PARALLEL`) and both retry ladders are gone. A run stops cleanly on a **provider outage**, the **spend cap** or a **key/credit** failure (§4.3), leaving the current job untouched and exiting 3. A **chat fallback** (`decide.ChatDecider`) answers the same typed questions when the System One model fails. The run's single CDP connection is opened before preflight, so one "Allow remote debugging?" click covers the whole run (§3.4). New config sections `[limits]`, `[budget]`, `[jev]`, `[decider]`, `[prices]`. See `recore/HANDOVER.md` for the P1 change list and deviations.

**v3.1 (re-core P0, 2026-09-28):** the component that writes the answers is renamed **LLM inference** everywhere (`answers.py` → `llm_inference.py`). Every HTTP attempt to a chat model or to a System One decision model is logged per run and per job under `runs/<ts>/{_run,<job folder>}/` (`llm_inference_logs.json` §6.2, `jev_inference_logs.json` §6.3); `decisions.jsonl`, `calls.jsonl`, `answers/` and `shots/` are gone (§8). The decision-model role is config `models.system_one_decision_provider` / `models.<route>.system_one_decision_model` — Jev is one instance; a **local Kev server** (`[models.local]`, keyless) is another. Preflight live-probes the LLM inference model and the System One model. See `recore/HANDOVER.md` for the full P0 change list and deviations.

**The program**, *the wrapper*, lives in `Tools/Application_Assistant/`. For every job at Status "Resume Built" it fills the application in the user's own signed-in Chrome, **stops one click before submission**, leaves that tab open, and records the result.

Who does what:
- **The browser driver** is owned in `assistant/driver/` (P2), ported from the vendored package (MIT).
- **The System One decision model** (config `models.system_one_decision_provider`; TypeSafe's **Jev** instance, or a local **Kev** server) judges every page and plans every fill; the package's goal agent also uses it to pick each click.
- **The LLM inference**, a chat model, writes the answers, and only from the user's files.
- **The code keeps:** the answer checks, the resume upload, the records, and one fixed never-submit rule (§4.2).

## 0. Rules

1. Never submit an application. The only guard is the rule of §4.2. Nothing in the program sends `"confirm": true` (§4.1).
2. Live runs act in the user's real Chrome on the user's real jobs. Prefer `--no-record` when testing. Chrome's "Allow remote debugging?" prompt is the user's to click. Development and the offline tests use local fixture pages in a throwaway Chrome (§9).
3. Never write `Profile.md`. Tests use temp copies of the tracker and job folders.
4. Tracker I/O is imported from `Tools/Reconcile/reconcile.py`; its behaviour is not changed.
5. The browser driver is owned in `assistant/driver/`; there is no external browser package. `.env` is never committed or printed.
6. Only `assistant/browser.py` imports `assistant/driver/` (the interface boundary; a static test enforces it).
7. Every judgment about a page, a question or a fill is Jev's (§6). A `DecisionError` sends the job to Needs Attention (class `decision`). There is no rule-based fallback in the program; the old rules live only in the offline test stand-in (§9).

## 1. What the program does

Inputs:
- `Applications/{Jobid}_{Company}_{JobTitle}/`, holding `job.md` (its Overview has `**LinkedIn URL:** <url>`) and exactly one `Amin_*.pdf` (the tailored resume);
- `Profile.md` (background, Fit Preferences, `## Scratch Pad`);
- `Job_Tracker.numbers`, sheet "Jobs", with columns including "Status", "Job URL" and "Notes".

1. **Preflight.** Any failure: exit 1, zero writes. §1.1 below.
2. **Journal recovery** (§8.4), in recorded runs only.
3. **Queue.** Rows with Status "Resume Built", joined to folders by LinkedIn job ID (`tracker.job_id`).
   - A folder without a row: process it and add a row.
   - A row without a folder, or a folder whose row has another status: report it, skip it.
4. **Per job, strictly in sequence** (§5):
   - open the LinkedIn URL in a new tab, then the page loop;
   - at the final pre-submit step: park (the tab is left as it is), record (row "Pending Review", folder → `Pending-Review/`), release the tab;
   - a blocker after ≤ 2 attempts, a required question with no answer in the files, or an unanswerable decision: a note in `job.md`, folder → `Needs-Attention/`, row "Needs Attention" + Notes, tab left open and released.
5. **Close-out:** the report, one terminal line per job, the exit code (§8.5).

Always:
- upload the tailored resume;
- every value traces to `Profile.md`, `job.md`, the resume text, or a kept pre-fill;
- never ask the user during a run;
- never close a job tab;
- the tracker row and the folder move are written together, and journaled.

**1.1 Preflight** (`cli._static_checks`, then `cli.preflight`):
1. **Config and keys:** `config.toml` is valid and `Config.problems()` is empty (paths exist, the active route's model lists are not empty). The chat route's key and Jev's route's key are present; a key both routes use is reported once.
2. **The package** imports after `apply_env()` (§3).
3. **The tracker** loads.
4. **Jev** answers a trivial yes/no question correctly.
5. **`browser_doctor`** shows `text_model`, `uploads` and `js_eval` true.
6. **LinkedIn:** `https://www.linkedin.com/feed/` opens in session `preflight` (180 s timeout, the Allow prompt); the URL still contains `/feed` and no signed-out marker; the session is closed.
7. **`browser_doctor`** shows `connected: true`.
8. **Tab bookkeeping works:** a TabBook, one about:blank tab resolved to a full target ID, and closed.

## 2. Project setup

```
Tools/Application_Assistant/
  pyproject.toml  config.toml  .env (git-ignored)  run_application_assistant.command
  README.md  CLAUDE.md  DISCOVERY.md  LIVE_TEST.md  application_assistant_build_spec.md
  prompts/  llm_inference.md  navigate_goal.md  next_step_goal.md  page_goal.md
  assistant/driver/   the owned CDP driver (cdp, observe, session, observer.js, LICENSE)
  assistant/  __main__.py cli.py config.py jev.py gateway.py guard.py probes.py pages.py decide.py fill.py
              llm_inference.py inference_log.py rotation.py google_signin.py blockers.py tabs.py records.py
              tracker.py report.py contract_check.py
  tests/      fixtures/ golden/ captured/ conftest.py support.py fake_mcp.py rule_decider.py test_*.py
  runs/<YYYYMMDD-HHMMSS>/  report.md journal.jsonl tracker-backup.numbers
                           _run/ and <job folder>/: jev_inference_logs.json llm_inference_logs.json
                           browser_actions.jsonl, plus answers.json and screenshot.jpg per job
```

Dependencies (`pyproject.toml`, Python ≥ 3.11):
- `websockets` (the owned driver's CDP transport);
- `httpx >=0.27`, `pydantic >=2,<3`, `pypdf >=4`, `python-dotenv >=1.0`, `numbers-parser >=4.19,<5`;
- dev: `pytest >=8`.

A plain venv is used, because Poetry's pyenv shim is broken on this Mac:

```
/opt/homebrew/bin/python3 -m venv .venv
.venv/bin/pip install websockets httpx "pydantic>=2" pypdf python-dotenv numbers-parser pytest
```

CLI (`python -m assistant [--config PATH] …`):
- `run [--dry-run] [--no-record] [--job URL] [--limit N]`:
  - `--dry-run`: no browser, no writes; prints the queue and its anomalies.
  - `--no-record`: fills and parks, but writes no tracker, folder or `job.md`, and skips journal recovery.
- `preflight`: §1.1, writes nothing.
- `tripwire [--live]`: runs `tests/test_guard.py` and `tests/test_tripwire.py`.
- `capture URL`: opens URL in the user's Chrome and saves `observe.txt` (`browser_observe(mode="full", include_json=True)`), `text.txt` and `screenshot.jpg` to `tests/captured/<host>-<ts>/`. Read-only; the tab is released and stays open.
- `requeue [--job URL]`: `Needs-Attention/` → `Applications/`, Status back to "Resume Built", the run's "Needs Attention: …" Notes line removed, journaled, with a tracker backup first (§8.6).
- `python -m assistant.contract_check`: the package's `browser_*` signatures and the three hooks of §3.3.
- `run_application_assistant.command`: double-click launcher for `run "$@"`, which explains the exit code.

`config.toml` (pydantic models; unknown keys are an error; `models.llm_inference` / `models.text_helper` take one ID or a list):

```toml
[paths]
base = "../.."                     # the repo root: holds Applications/, Profile.md, Job_Tracker.numbers
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

[models]
chat_route = "vercel"              # who serves the LLM inference and the text helper: "openrouter" | "vercel"
system_one_decision_provider = "local"    # who serves the System One decision model: "local" | "openrouter" | "vercel"

[models.openrouter]                # OPENROUTER_API_KEY. Paid: no `:free` model is configured (D14)
llm_inference = ["deepseek/deepseek-v4.1-flash", "openai/gpt-5.4-mini"]
text_helper   = ["deepseek/deepseek-v4.1-flash", "openai/gpt-5.4-mini"]
system_one_decision_model = "typesafe/jev-1.13"

[models.vercel]                    # AI_GATEWAY_API_KEY
llm_inference = ["mistral/mistral-small", "mistral/mistral-nemo"]
text_helper = ["mistral/mistral-small", "mistral/mistral-nemo"]
system_one_decision_model = "typesafe-ai/jev"

[models.local]                     # a Kev server on this Mac: the same System One API, no key
base_url = "http://127.0.0.1:8009"
system_one_decision_model = "kev-latest"
state_chars = 12000
timeout = 120.0

[limits]                           # v3.2: one queue for every model request (§3.3)
max_in_flight = 1
min_interval_s = 0.25
max_attempts = 3

[budget]
max_usd_per_run = 1.00             # D15: the run stops cleanly at this spend

[jev]
max_questions_per_request = 24     # above this a judgment is split, and the parts go one after another

[decider]
fallback = "chat"                  # "chat" | "none": who answers when the System One model fails (§6.0b)

[prices]                           # USD per million tokens [input, output]; only when the gateway reports no cost
"mistral/mistral-small"        = [0.20, 0.60]
"mistral/mistral-nemo"         = [0.02, 0.04]
"typesafe/jev-1.13"            = [0.042, 0.0]
"deepseek/deepseek-v4.1-flash" = [0.099, 0.60]
"openai/gpt-5.4-mini"          = [0.75, 4.50]

[policy]
prefill = "keep-if-silent"         # or "strict"
free_text_max_chars = 1500

[google]
account_email = "…"                # empty → every Google sign-in is a blocker
```

The config code in `config.py`:
- `cfg.models.llm_inference` / `.text_helper` read the active route's table.
- `config.chat_key(cfg)` / `config.chat_url(cfg)` give the chat route's key and `…/chat/completions`, from `CHAT_BASES = {openrouter: https://openrouter.ai/api/v1, vercel: https://ai-gateway.vercel.sh/v1}`.
- `config.system_one_decision_key(cfg)` gives the System One route's key; `config.local_key()` reads the
  optional `KEV_API_KEY`, and `config.KEYLESS_PROVIDERS` is why preflight asks for no key on the local route.
- `cfg.limits`, `cfg.budget`, `cfg.jev`, `cfg.decider` and `cfg.prices` configure the Gateway (§3.3).

`.env`: `OPENROUTER_API_KEY=` and `AI_GATEWAY_API_KEY=` (read from `.env` first, then the process environment).

## 3. The owned browser driver (`assistant/driver/`)

The wheel is 0.1.5 plus three observer fixes (`vendor/*.patch`, "aa"):
- `display:contents` wrappers hide nothing;
- while a modal `<dialog>` is open, only the topmost one's content is listed;
- opacity-0 native radio, checkbox and file inputs are listed, and ARIA radios and checkboxes report their state; a file-chooser button can take an upload.

`jev.load()` refuses any other installed version.

**3.1 Load and call.**
- **Import order:** `apply_env(cfg, key)` must run before `load()`, because the package reads its config and builds its browser manager when `server` is imported.
- **The call path:** `Jev.call(name, **kwargs)` runs `getattr(server, name)(**kwargs)` on one dedicated worker thread (one call at a time, B7) with a timeout: 60 s, or 300 s for `browser_goal`, or `_timeout` for preflight's first connection.
- **Every result:**
  - passes `clean_text` (lone UTF-16 surrogates → `?`: the package cuts page text in JavaScript, and a half pair crashed UTF-8 logging);
  - is logged to `browser_actions.jsonl` with the chat key redacted.
- **On timeout:** write the report, flush stdout, then `os._exit(3)`. A blocked call can't be cancelled, and `os._exit` skips the package's tab-closing exit hook (B3), so the job's tab stays open.
- **Failures:** `Jev.checked()` raises `JevError` on a result starting with `browser_unavailable:`, `blocked_by_policy:`, `stale:`, `browser_error:`, `turbo_unavailable:` or `error(`.

**3.2 `apply_env(cfg, key)`**, where `key` is the chat route's key. It deletes every `os.environ` key starting with `JEVMCP_`, `TYPESAFE_`, `TEXT_MODEL_` or `OPENROUTER_`, then sets:

| Variable | Value |
|---|---|
| `JEVMCP_MODE` / `JEVMCP_CDP_URL` | `attach` / `browser.cdp_url` |
| `JEVMCP_FOREGROUND` / `JEVMCP_MAX_ACTIONS` | `0` / `browser.max_actions` |
| `JEVMCP_ALLOW_UPLOADS` / `JEVMCP_ALLOW_JS` | `1` / `1` |
| `TYPESAFE_BASE_URL` (+ `TYPESAFE_API_KEY`) | vercel: `https://ai-gateway.vercel.sh/typesafe/v1/systemone` + `AI_GATEWAY_API_KEY`; openrouter: `https://openrouter.ai/api/alpha/decisions` (paid with the OpenRouter key); local: `models.local.base_url` + `/v1/systemone` + `KEV_API_KEY`, or the placeholder `local` when the Kev server is open (the package refuses turbo mode on an empty key). The URL still selects the route, but the request itself is sent by the Gateway (§3.3), not by the package's client |
| `TYPESAFE_MODEL` | `models.system_one_decision_model` |
| `OPENROUTER_API_KEY` | `key` on the openrouter chat route, else the `.env` OpenRouter key |
| `TEXT_MODEL_API_KEY` / `TEXT_MODEL_BASE_URL` | `key` / `CHAT_BASES[chat_route]` |
| `TEXT_MODEL` / `TEXT_MODEL_REASONING` | `models.text_helper[0]` (the rest rotate in, §3.3) / `none` |

`JEVMCP_CONFIRM_PATTERNS` is not set: clicks are refused by §4.2 instead.

**3.3 The three hooks.** `load()` installs them after the import. Each keeps the original as `__wrapped__`, and wrapping again never nests. `contract_check.py` fails if one no longer applies.

| Hook | Wraps | What it does |
|---|---|---|
| `rotate_text_helper` | `server.policy.text_for(cfg, …)` (the text helper that writes the value the agent types) | calls it with `dataclasses.replace(cfg, text_model=m)` for each model of `models.text_helper`, in turn (§7.1); none answers → `TurboUnavailable` naming each failure |
| `clean_requests` | `server.policy._post(url, key, body)`, the package's only sender (Jev and the text helper) | `clean_json(body)` before httpx encodes it as UTF-8 |
| `guard_clicks` | `browser.confirm_reason(cfg, name, role)`, which `browser.py` calls before every click, the agent's and ours | returns `guard.never_click(name, role)` (§4.2) |

**3.4 Functions** (`server.browser_*`, keyword arguments only; all except `browser_doctor` take `session`):

| Function | Parameters |
|---|---|
| `browser_doctor` | none (JSON with `connected`, `text_model`, `js_eval`, `uploads`, …) |
| `browser_open` | `url`, `hint=""` |
| `browser_observe` | `include_json=False`, `include_text=True`, `mode="auto"\|"full"\|"delta"` |
| `browser_act` | `ops`, `dry_run=False`, `observe_after=True`, `stop_on_error=True` |
| `browser_assert` | `checks` |
| `browser_goal` | `goal`, `max_steps=20`, `verify=None`, `verbose=False` (no `url` in 0.1.5) |
| `browser_tabs` | `action="list"\|"new"\|"switch"\|"close"`, `index=-1`, `target_id=""`, `url="about:blank"` |
| `browser_close` | `shutdown_browser=False` |

**`Jev.goal`** always passes `verbose=True`: the trace is the only place a refused label shows. When the result's status is `turbo_unavailable: Decision model returned HTTP 429/5xx | unreachable | not JSON | unavailable` and `steps: 0`, it asks again **once** after `jev.GOAL_RETRY_WAIT` = 2 s (v3.2: the Gateway has already spent this request's attempts, so a second ladder here only multiplied them — §13.1). If the decision model is still out, it raises `DecisionError`. A `no decision-model key` status is not retried.

**Ops** (`browser_act`), as the program sends them:

```
{"op":"click","ref":"e12"}
{"op":"type","ref":"e7","text":"…","clear":true,"submit":false}
{"op":"select","ref":"e9","value":"<option label>"}
{"op":"toggle","ref":"e3","state":true}
{"op":"upload","ref":"e5","path":"/abs/path/Amin_X.pdf"}
{"op":"reload"}   {"op":"wait_for_load","timeout_ms":20000}
{"op":"screenshot","path":"/abs/path/<folder>.jpg","full":true}
{"op":"eval","js":"<a constant from probes.py>"}
```

**Checks** (`browser_assert`): only `js{expr}` with `probes.CAPTCHA_PRESENT`.

**Verified behaviour** (0.1.5 source and live runs; the shapes are in `tests/golden/` and `DISCOVERY.md`):
- **B1** The package checks every `click` through `confirm_reason`, Jev's own clicks included. A refused click returns error `needs_confirmation`. The goal then ends `status: failed:needs_confirmation`, with the label in its trace. If `verify` passes afterwards, the server rewrites the status to `done`, so the trace is always scanned.
- **B2** The package checks only `click`s and `type` into secret-looking fields. It does not check `keys` Enter or `type … submit: true`, and the program never sends either.
- **B3** At process exit, the package's `atexit` hook closes every session's current tab, in attach mode too. That is why release exists (§4.5).
- **B4** `browser_tabs` switches and closes by full target ID. Its list shows 8-character handles.
- **B5** Text helper: sees the goal, the field, the page title and the first 6,000 characters of page text. It rejects values over 2,000 characters; no value means the goal returns an error.
- **B6** Element table: name, label and value are cut to 160 characters, and there is no `required` flag (the probes supply it).
- **B7** One browser, one call at a time.
- **B8** `browser_goal` text: `goal: …` / `status: …` / `steps: N` / `trace:` lines, then the page view.
  - Status: `done` · `blocked` · `failed:<error>` · `stopped: hit max_steps=N` · `turbo_unavailable: …`.
  - No `status:` line → failed.
- **B9** `browser_doctor` may report `connected: false` until a tool first needs the browser.

## 3.3 The model gateway (`assistant/gateway.py`, v3.2)

Every model request leaves through one `Gateway`, installed per run with `gateway.use(gateway.for_config(cfg))`.
Three senders route through it and none of them can send on its own: `decide.Decider`, `decide.ChatDecider`,
`llm_inference._ask_model`, and the package's `policy._post` (wrapped by `jev.clean_requests`, which never calls the
package's own sender, so the package's internal `range(3)` retries and its fixed 30 s `policy.CLIENT` are off the
path). `gateway.required()` raises rather than fall back to an HTTP client of its own.

| What | Where it comes from | Value |
|---|---|---|
| requests in flight | `limits.max_in_flight` | 1 |
| minimum gap between request starts | `limits.min_interval_s` | 0.25 s |
| attempts per request | `limits.max_attempts` | 3 |
| retried on | — | 429, 529, any 5xx, a timeout, no connection |
| never retried | — | every other 4xx, and 401/402/403 (§4.3) |
| wait before a retry | `Retry-After` if ≤ 30 s, else | 2 s, then 6 s |
| timeout, System One | `gateway.timeouts[JEV]` | 20 s, or `models.local.timeout` on the local route |
| timeout, chat | `gateway.timeouts[CHAT]` | 45 s (`LLM_INFERENCE_TIMEOUT`) |
| questions per System One request | `jev.max_questions_per_request` | 24; above it the judgment is split and the parts go one after another |

- **A model rotation hands over before it retries.** A chat request passes `attempts=1` while the rotation still has
  an untried model, so a rate-limited model costs one attempt rather than three and 8 s of waiting; only the last
  model in the order gets the full ladder. This is still one retry layer — another model is a different request.
- **One logging point.** The Gateway logs every attempt (§6.2, §6.3) and picks the file by body shape: `state` +
  `questions` is a System One request, `messages` is a chat request. No sender logs for itself.
- **Counters**, per run and per job: requests, attempts, failures, cost and the highest number in flight, plus the
  System One / chat split and the number of fallback decisions. Cost is what the gateway reported (`usage.cost`, or
  Vercel's `provider_metadata.gateway.cost`); OpenRouter needs no usage-accounting flag. With no reported cost it is
  estimated from `[prices]` and marked "estimated" in the report.
- **`provider.require_parameters`** is added to chat requests on the OpenRouter route only, so the request is routed
  only to a provider that supports every parameter sent.

## 3.4 One Chrome connection per run (D13, v3.2)

The package holds one CDP websocket per process and shares it across sessions, so a run was always one connection;
what it lacked was a handshake long enough for a person to click "Allow remote debugging?" (its own budget is
`max(60, call_timeout)` = 60 s, with no environment variable to change it). `jev.connect_chrome(cfg, 180)` opens
that socket before preflight, with `cli.CONNECT_TIMEOUT`, after `cli.connect_once` prints
`Chrome will ask "Allow remote debugging?" — click Allow (waiting up to 180 s).` It sets `MANAGER._cdp` directly
instead of raising `call_timeout`, which is also every later CDP call's deadline. This is the fourth touch of the
package's internals and is covered by `contract_check`.

## 4. Safety layer

- **4.1 Guard** (`guard.check(op, table)`). Every `browser_act` goes through `Jev.act()`, which checks each op first. `GuardError` for:
  - any op carrying `confirm` (nothing overrides a refusal);
  - an `eval` whose `js` is not identical to a `probes.py` constant;
  - a `tab` op other than `list` (tabs go through `tabs.py`);
  - `nav` / `back` / `forward` (navigation goes through `browser_open`);
  - a `click` or `upload` whose ref is not in the latest table;
  - an `upload` onto a control `never_click` refuses: the package clicks a non-input upload target to open its file chooser.

  `Jev.assert_` accepts only `probes.ASSERT_PROBES`.
- **4.2 The never-submit rule** (`guard.never_click(name, role)`, user decision 2026-09-24, no model call). For roles button, link, menuitem and tab (or no role):
  - a label matching `\bsubmit` or `\bsend\b` is refused on every page;
  - a label matching `\bapply\b` is refused once `guard.FORM.started` is set.

  `fill.fill_page` sets that flag, and `run_pages` resets it for each job. Before filling, "Apply" / "Easy Apply" is the agent's own click. The package applies the rule to every click (§3.3), and a refused click comes back as `needs_confirmation`. "Done", "Finish", "Confirm", "Complete" and forms that submit on Enter are outside the rule (§13.4).
- **4.3 Advance** (`fill.advance`): one-action goal `NEXT_STEP_GOAL` ("click its Next, Continue, Review or Save and continue button, once… Do not submit it"), `max_steps=1`, up to `ADVANCE_TRIES = 3` while the agent only scrolls or waits.
  - A refused click means `"final"`.
  - A new tab within `CLICK_TAB_WAIT = 3` s is followed.
  - Jev's `validation` answer, or the same fields and URL on a page that is not final, means `"stuck"`. That counts as a `broken_form` attempt: refill, advance again.
- **4.4 Final step:** Jev's page kind is `final_step`, or the last advance was refused and Jev sees a `submit_button`. Then the gate (§6.4) runs; if it fails, one refill and a second gate, else Needs Attention (`gate`).
- **4.5 Tabs** (`tabs.TabBook`). Chrome runs in `chrome://inspect` remote-debugging mode, which has no `/json/list`.
  - **The helper tab:** a helper session `tabbook` keeps a tab open for the whole run, a `data:` page titled "Application Assistant (working)". Its observations announce every new tab's full target ID (`NEW TAB … 'target_id'`), and a helper whose tab was closed from outside is restarted.
  - **Release(session):** `browser_tabs(new)`, then verify the new scratch tab, switch the session to it, and `browser_close(session)`. The job's tab stays open. It runs in a `finally` for every job.
  - **Hand-off:** a click that opens one new tab moves the session there, and closes the old tab when its full ID is known and it isn't a baseline tab.
  - **Junk tabs:** tabs opened during the job, other than the application tab and the baseline, are closed when their full ID is known.
- **4.6 Alarms** stop the run at once, with exit 3:
  - a goal trace shows an `ok` click on a label `never_click` refuses (the rule isn't in place);
  - a page after the program acted matches `ALARM_RE = (your )?application (was )?(submitted|sent)|thank(s| you) for (applying|your application)`, or Jev says `submitted` (≥ 0.7).
- **4.7 `probes.py`:** constants only, read-only, each trimming its JSON to `FIT = 190` characters (the server caps eval results at 200) and reporting counts with a `more` flag.
  - `REQUIRED_EMPTY`: label and type of every required input, select or textarea still empty.
  - `MAXLENGTHS`: label → `maxLength`.
  - `IFRAME_SRCS`: absolute iframe `src`s.
  - `FILE_LABELS`: file inputs' labels.
  - `COMBO_VALUES_0…3`: custom comboboxes' shown values, in chunks of 7.
  - `RADIO_OPTIONS_0…4`: radio groups' questions and option labels, in chunks of 8.
  - `CAPTCHA_PRESENT` (assert): a reCAPTCHA, hCaptcha or Turnstile iframe or container.

  A unit test forbids DOM assignment, `.submit`, `.click`, `dispatchEvent`, `fetch`, `XMLHttpRequest` and `location` in any probe.

- **4.3 Clean stops** (`gateway.py`, v3.2; D15, D21). Three conditions end a run instead of one job. Each is a
  `blockers.StopRun` subclass, which is what keeps it out of the `NeedsAttention` paths — a `NeedsAttention` writes a
  record (folder move, tracker row, `job.md` note), and a stopped run must leave its current job untouched.

  | Stop | Condition | Reason in the report |
  |---|---|---|
  | `ProviderOutage` | 3 requests in a row fail after all attempts, or 5 of the last 10 | `provider outage — <route> HTTP <status>: <rule>` |
  | `BudgetExceeded` | the run's model spend reaches `budget.max_usd_per_run`, checked before each request | `spend cap $1.00 reached` |
  | `CreditOrKey` | HTTP 401, 402 or 403, never retried | `<host> rejected the key / refused the account (HTTP n: <what the provider said>) — check the key / add credit on <route>` |

  In every case: the current job gets no record, its tab stays as it is, queued jobs stay at "Resume Built", the
  report names the reason and the exit code is 3. In **preflight**, a `CreditOrKey` is a `PreflightError` instead —
  exit 1, naming the key variable to check — because nothing has been written yet; a `ProviderOutage` or
  `BudgetExceeded` there still exits 3. `Jev.call` re-raises only a stop that its own call caused, so the tab
  cleanup that runs after a stop still completes and the report is still written.
  403 is included because Vercel AI Gateway answers it for an account with no card, which no retry or other model
  fixes. Because a stop can be raised inside the package's own worker thread — where `Jev.call`'s broad
  `except Exception` would turn it into an `error(...)` string — the Gateway keeps the first stop and `Jev.call`
  re-raises it on the run's thread after every browser call.

## 5. Per-job algorithm

```
process(job):                                                         # cli.process
  pdf = the only job.dir/"Amin_*.pdf", else needs_attention                        # before any browser work
  S = "job-<id>"; baseline = TabBook handles; one Rotation of answer models for the whole run (§7.1)
  up to 2 attempts (RestartFromEntry → attempt 2 from the LinkedIn URL):
    browser_open(linkedin_url, S); p = read()                                      # read = read_page + settle (§6.5)
    classify_entry(p): signed_out → StopRun (C19) · closed | applied → needs_attention · not open → load_failure
    run_pages()
  DecisionError → needs_attention("decision")
  finally: close junk tabs, release(S)                                             # §4.5

run_pages():                                                          # fill.run_pages
  FORM.started = False; stage = "navigate"
  loop (at most max_pages_per_job × 4 + NAVIGATE_ROUNDS rounds, else broken_form):
    p = read(); v = classify(p)                                                    # §6.3
    alarm → StopRun · blocker → attempt 2 (§6.3) · google | google_wall → google_signin (§6.2)
    iframe (the form is inside an iframe the page itself doesn't show) → browser_open(src), at most 2 hops
    stage navigate and form_is_here(p) → stage form                               # Jev: form kind and not covered
    stage navigate, or the form is covered → navigate():                          # §5.1
        "form" when the form isn't there: ask once more with the NOT_THE_FORM hint; a second "form" stands
        "stuck" twice → needs_attention("navigation", "no way forward")
    has_fields and not filled → fill_page() (§7.5); _Refill → reopen the page URL, stage navigate
    final (§4.4) → gate; pass → park (screenshot) · fail → refill once → needs_attention("gate")
    else advance() (§4.3): final → mark; stuck → broken_form attempt; moved → next page
    pages > max_pages_per_job → needs_attention("broken_form")
  DecisionError → decision · JevError → load_failure attempt, then RestartFromEntry
```

**5.1 `navigate()`**: one action per goal (`NAVIGATE_GOAL`, `max_steps=1`), at most `NAVIGATE_ROUNDS = 12` per job. The goal says:
- decline optional cookies;
- continue past pop-ups between the job and the form;
- click Easy Apply / Apply / Apply now / Apply manually on a job page;
- ignore job alerts, newsletters, search boxes, chats and "Save";
- never type, never create an account or sign in, never close the application.

The outcome:
- **A refused click** (only Submit or Send, before filling): the page has application fields → `"form"`, else Needs Attention.
- **After any agent click:** wait up to `ENTRY_TAB_WAIT = 8` s for a new tab. It stops as soon as the page shape changes.
- **A click the page changed under** (`detached`, `page_changed`, …): `"moved"`.
- **`done`, or the agent typed, selected or toggled:** `"form"`. Any other `ok` action: `"moved"`. Otherwise `"stuck"`.

`needs_attention(...)` then does the rest: a `job.md` note, row "Needs Attention" + a one-line Notes, folder → `Needs-Attention/`, tab left open, junk closed, release, next job.

## 6. Page decisions (`pages.py` + `decide.py`, by Jev)

**6.0 The System One client** (`decide.Decider`, installed per run with `decide.use(decide.for_config(cfg))`).
- **The request:** TypeSafe's System One shape, `{model, state, questions}`. Questions are `noul` (a yes/no probability), `choice` (criteria → choice, confidence, probabilities) or `score`.
- **Endpoints:**
  - vercel: `https://ai-gateway.vercel.sh/typesafe/v1/systemone`, model `typesafe-ai/jev`, `AI_GATEWAY_API_KEY`;
  - openrouter: `https://openrouter.ai/api/alpha/decisions`, model `typesafe/jev-1.13` (the System One
    route on OpenRouter; `api/v1/systemone` is not one — DISCOVERY 2026-09-28);
  - local (user decision 2026-09-28): `models.local.base_url` + `/v1/systemone`, model `kev-latest`, no key — a
    [Kev](https://github.com/jaredpalmer/kev) server on the user's Mac. Same request and answer shapes, so only the
    URL changes; the state is sent as an object (Kev renders one as labeled text), the state limit is
    `models.local.state_chars` and the timeout `models.local.timeout` (which is the Gateway's System One timeout on
    this route, in place of the 20 s a cloud route gets). Preflight reads `GET /v1/models` first.
- **One request per judgment** (v3.2). The state is cut to `STATE_CHARS = 60_000` characters, text first (Jev's
  context is 32K tokens), or to `models.local.state_chars` on the local route. A judgment is **one** request, split
  only above `jev.max_questions_per_request = 24` and sent in parts one after another — TypeSafe fails a whole
  request when any one question fails (§13.1). `BATCH`, `PARALLEL`, the thread pool and the `RETRY_WAITS` /
  `LOCAL_RETRY_WAITS` ladders are gone: the queue, the attempts and the timeout are the Gateway's (§3.3).
- **Failure:** a request that fails after all attempts goes to the chat fallback (§6.0b); with no fallback, or when
  that fails too, it raises `DecisionError`. A missing or malformed answer is a `DecisionError` and never a guess —
  and it does not count as a provider failure, because the model did answer.
- **The log:** every attempt goes to `jev_inference_logs.json` (§6.3), written by the Gateway.
- **Totals:** calls, fallback decisions and cost go into the report (§8.5).
- **Topics:** `page`, `answers`, `questions`, `fill`, `readback`, `preflight`.
- **Thresholds** (`decide.THRESHOLDS`, yes/no questions): submitted 0.7 · applied 0.7 · covered 0.5 · actionable 0.5 · validation 0.6 · app_field 0.5 · cover_letter 0.6 · submit_button 0.5 · registration 0.6 · placeholder 0.6 · must_not_generate 0.4 · resume_upload 0.6.
- **Choices:** the top choice decides.
- **`decide.narrow`:** asks a choice again over only its two likeliest options (plus "none").

**6.0b The chat fallback** (`decide.ChatDecider`, v3.2; D19). When a System One request fails after all of the
Gateway's attempts, the same typed questions go **once** to the chat route's models. The answer JSON is per question
— `{"type": "noul", "noul": p}` or `{"type": "choice", "choice": k, "probabilities": {...}, "confidence": c}` — so
`Answer.parse` reads it unchanged and nothing downstream knows which backend answered. Its calls are chat calls and
go to `llm_inference_logs.json` (D4). The breaker counts the pair as **one** request: a decision the fallback
rescued is not a failure, and one both lost is not two. `decider.fallback = "none"` turns it off. The report shows
`Decisions by fallback: N`.

**6.1 LinkedIn job page** (`classify_entry`):
- `signed_out`: the URL contains `/login`, `/authwall`, `/checkpoint` or `/uas/`;
- `closed`: Jev's kind is `closed`;
- `applied`: Jev's `applied` answer;
- `open`: the kind is job_posting, interstitial, application_form or final_step;
- else `none`.

Preflight's probe is `linkedin_feed_ok(url)` (§1.1).

**6.2 Google** (`google_signin.sign_in`; no model acts on accounts.google.com).
- **Steps:**
  1. Click Jev's `google_button`, and follow a popup window if one opens.
  2. On Google, Jev's `google_step` decides: `password` / `two_step` / `consent` → credentials blocker.
  3. Click the chooser element containing `google.account_email`, exactly once.
  4. Back on the site, Jev's `registration` answer → signup blocker.
- **Blockers:** no `account_email` configured, or a second Google click needed.

**6.3 Blockers and the loop order** (`pages.classify`, `pages.blocker`, `blockers.Attempts`: the second failure of a class is final).

`classify` checks, in order:
1. `alarm`;
2. `google` (accounts.google.com);
3. `blocker`;
4. `google_wall` (an account wall with a Google button);
5. `iframe` (`iframe_form_src`: no application fields and Jev picks a frame);
6. `final`;
7. `form`;
8. `navigate`.

| Class | Cue (Jev unless noted) | Attempt 2 |
|---|---|---|
| captcha | `CAPTCHA_PRESENT` (probe) or kind `captcha` | wait 5 s, reload |
| signup | kind `account_wall`, `account = create_account`, no Google path | click the "apply without an account" / "continue as guest" link (`pages.GUEST_RE`), when Jev's kind and account agree (`fill.guest_refused`) |
| credentials | kind `account_wall`, otherwise | none |
| broken_form | validation stays, a value is refused after one retry, advance changes nothing, a page goal left the page | reload or reopen the page, refill it |
| load_failure | a blank page, kind `error`, a `JevError` | reopen from the LinkedIn URL |

**6.4 Gate** (`pages.gate`), where all must hold:
- `REQUIRED_EMPTY` is empty;
- no `validation` answer;
- the resume file name (first 30 characters) is in the page text or a field's value;
- Jev sees a `submit_button`;
- no alarm (§4.6);
- every wrapper-typed text is still held: the field value's first 160 characters equal the text's.

**6.5 The page judgment** (`pages.judge(p)`, cached on the `Page` object).

`read_page` builds the Page:
- `browser_observe(mode="full", include_json=True)`;
- the probes of §4.7 in one `browser_act`, plus `CAPTCHA_PRESENT`;
- the open modal dialogs.

The state (`page_state`): URL, title, open dialogs, up to `MAX_CONTROLS = 120` controls (ref, role, name, option, value, checked, read-only, covered, options), required-empty labels, and `STATE_TEXT = 6000` characters of page text.

Questions (`page_questions`):
- `kind`, a choice of `PAGE_KINDS`: job_posting · interstitial · application_form · final_step · account_wall · google_sign_in · captcha · confirmation · closed · error · other;
- yes/no: `submitted`, `applied`, `covered`, `actionable`, `validation`, `registration`, `submit_button`;
- `account`: create_account | sign_in | none;
- `field_<ref>` for every form field: is it the application's own, rather than site chrome?
- `resume_input`: a choice among the file inputs, or among buttons when there is no file input;
- `cover_<i>` for every required-empty upload;
- `google_step` on accounts.google.com, else `google_button`;
- `form_iframe` when the page has iframes.

Derived answers:
- `form_is_here` = kind ∈ {application_form, final_step} and not `covered` (the top choice decides, user decision 2026-09-24);
- `has_fields` = at least one field Jev counts as the application's;
- `loading` = not `actionable` and a kind that can still change.

`settle(read)` re-reads once a second, up to `SETTLE_SECONDS = 10`, while the page is empty or `loading`. Each re-read is a new Page, so it gets a new judgment.

## 7. LLM inference (`answers.py`)

**7.1 Models and routes.**
- **One call per form page:** `POST config.chat_url(cfg)`, i.e. OpenRouter or Vercel AI Gateway's OpenAI-compatible `chat/completions`, with the same request on both.
- **Model rotation:** `models.llm_inference` is a `rotation.Rotation`. A call tries the model that answered last first, then the others in configured order. One rotation lasts the whole run.
- **Per model:**
  - JSON-schema output, falling back to `json_object` on HTTP 400;
  - a 429 with `Retry-After` ≤ `RETRY_AFTER_MAX = 30` s is waited out once;
  - any other failure hands over to the next model with no wait: 429/5xx, a 200 with an error body, a timeout (`LLM_INFERENCE_TIMEOUT = 120` s), or invalid output;
  - a 401 fails at once, because every model would fail the same way.
- **When none answers:** `LLMInferenceError("LLM inference: none of N models answered (<each reason>)")`, and the job goes to Needs Attention (class `llm_inference`).
- **The text helper** rotates the same way (§3.3).

**7.2 Request.**
- Parameters: `temperature: 0`, `max_tokens: 8192`, `reasoning: {"enabled": false}`, and `response_format` = the strict `page_answers` JSON schema.
- System prompt: `prompts/llm_inference.md`, with `{free_text_max_chars}` filled in.
- User message (JSON):
  - `page`: url, title, elements, text (≤ 12,000 characters), maxlengths, required_empty;
  - `sources`: `Profile.md`, `job.md`, and the resume text read with pypdf.

Schema (strict: every property required, nullable by type union, `additionalProperties: false`):

```
Question    { id, question, kind: "text"|"longtext"|"choice"|"file"|"other",
              ref: str|null, option_ref: str|null, options: [str]|null, required: bool,
              answer: str|null,
              source: "profile"|"job"|"resume"|"generated"|"computed"|"linkedin-prefill"|null,
              quote: str|null, relies_on: [str]|null }
PageAnswers { questions: [Question] }        # the code adds: model (who answered); per question note, resume_upload, cover_letter
```

**7.3 The prompt requires:**
- every question on the page, pre-filled ones included;
- facts only from the sources, with one exact `quote`;
- `generated` only for motivation or description questions: first person, ≤ maxlength (else ≤ `free_text_max_chars`), `relies_on` = the exact sentences used;
- `computed` only for total years of experience, with `relies_on` = the dated role lines and digits only;
- no answer → null; never guess salary, notice period, start date, years with a tool, visa, right to work or demographics unless a Scratch Pad or profile line states it;
- keep a pre-fill the sources don't address (`linkedin-prefill`), and correct one they contradict;
- files → null;
- choice answers are one of the options;
- with a separate country-code field, the phone field gets the local number only.

**7.4 Checks in code** (`check_answers`; a failed check sets `answer = null` with a `note`).

One Jev call first (`judge_answers`, topic `answers`) gives:
- which `generated` answers ask for a fact that must not be written (`must_not_generate` ≥ 0.4);
- which `computed` answers ask for *total* years (choice total | specific | other);
- which shown values are placeholders such as "Select…" (≥ 0.6).

Then each answer is checked:
- **Quotes:**
  - A `quote` must occur in a source file: ignoring whitespace, or, for quotes of ≥ `LOOSE_MIN = 12` letters and digits, ignoring case, spacing and punctuation too.
  - A quote found in another file corrects `source`.
- **Choices:**
  - A radio or checkbox `option_ref` must be the answer.
  - A native select's answer must be one of its options (or its current value).
  - A custom choice's answer must appear on the page.
- **`generated`:** only for text/longtext, and not when Jev flags `must_not_generate`. Bad `relies_on` or over-length text gets one regeneration call, then is dropped.
- **`computed`:** only when Jev says total years. The code recomputes total months from the month-year ranges in `relies_on` (overlaps merged, "present" = today) and floors to years; it must equal `answer`.
- **`linkedin-prefill`:** the field must hold a non-placeholder value equal to the answer. `policy.prefill = "strict"` drops it.
- **A required field with no valid answer** that already holds a value keeps it (`keep-if-silent`).

A second Jev call (`judge_questions`, topic `questions`) flags each question answered by the resume upload (`resume_upload` ≥ 0.6) and each upload that asks for a cover letter (`cover_letter` ≥ 0.6).

Then:
- a required question with no answer → the D2 path (Needs Attention `unanswered`, with the questions);
- an optional one → left empty, listed in the note.

**7.5 Fill** (`fill.fill_page`; sets `guard.FORM.started`):
1. A required cover-letter upload (Jev) → Needs Attention (`broken_form`, C15).
2. **Resume** (`resume_input`, then `upload`):
   - Jev's `resume_input` pick is used when it is the only file input or its confidence is ≥ `RESUME_CONFIDENCE = 0.5`.
   - Otherwise `decide.narrow` asks again over the two likeliest options; then the LLM inference's own ref for the resume question decides, if it is one of those two; else Needs Attention.
   - If the resume is already on the page (file name), only LinkedIn's resume card is selected.
3. **Long text:** `generated` answers, and other text over `LONG_TEXT = 300` characters, are typed by the wrapper on the LLM inference's ref (`clear: true`, `submit: false`). A stale ref is re-mapped by question once.
4. **Every other answer** that differs from the current value goes to `plan_fill` (topic `fill`). Per answer, Jev picks:
   - the operation, a choice of type | select | check | widget;
   - the field, among text fields and lists;
   - the option, among radios, checkboxes and select options `eN:k`.

   The LLM inference's `ref` / `option_ref` go in only as hints. `_carry_out` turns a pick into a `browser_act` op only when the page allows it (typing needs an editable text field, a select needs a listed option label, check needs a radio or checkbox). The ops run in one `browser_act`.
5. **The rest** (widgets, or picks that can't be carried out) go into one page goal (`prompts/page_goal.md`, `max_steps = 2 × len + 3`, `verify` = `checked` / `value_equals` where possible). The page goal sets exactly the listed answers and never clicks Submit, Send, Apply, Confirm, Done or Finish.
6. If a goal left the page (the field fingerprint changed), that is a `broken_form` attempt and the page is refilled.
7. **Read-back** (`mismatches`, topic `readback`): one Jev choice per answer on the fresh page, holds | different | empty (the top choice decides).
   - A miss is planned again: toggles and selects repeat; anything else gets one goal for that field alone.
   - Still wrong → `broken_form` ("field would not accept its value").
8. Every answer goes to `runs/<ts>/answers/<folder>.json`: page, url, model, question, answer, ref, option_ref, source, quote, relies_on, note.

## 8. Records

- **8.1 Tracker** (`tracker.py`) on `reconcile.py`'s I/O (numbers-parser):
  - `load()`, `find(job_id)`, `set(job_id, status, notes=None)`, `set_notes(job_id, notes)`, `add(fields)`, `save()`, `backup(path)`.
  - mtime + sha256 are taken at load and must still match before each save, else StopRun (exit 3). The tracker is saved after every job and backed up once per run.
  - Statuses: "Resume Built", "Pending Review", "Needs Attention".
  - `job_id(url)`: `/jobs/view/(?:[^/?#]*?-)?(\d{6,})`, else `[?&]currentJobId=(\d+)`, else the exact string.
  - A new row gets Job URL, Status and Notes, plus Company / Job Title when those headers exist.
- **8.2 Folder move:** `os.rename(src, dst)`, same name. If `dst` exists → StopRun, before any tracker write.
- **8.3 `job.md` note:** a root-level `# Application Assistant` section, appended once. Each entry goes at its end:
  - `## <date time> — Needs Attention: <class>`, with what, where, fields filled and open questions;
  - `## <date time> — Pending Review`, with the parked URL and title and page count, generated texts, kept pre-fills, and optional questions left empty.
- **8.4 Journal** `runs/<ts>/journal.jsonl`: `record_start → tracker_saved → folder_moved → record_done`. At the start of a recorded run, every job in any journal without `record_done` is completed idempotently (`records.recover`).
- **8.5 Report and exit.** `runs/<ts>/report.md` has these sections:
  - Summary, including "Decisions by <model>: N calls, $X" (no cost on a route that charges none);
  - Parked;
  - Needs Attention (reason, where);
  - Queue anomalies;
  - Warnings;
  - Questions for your Scratch Pad (deduplicated);
  - Timings.

  Terminal line per job: `✓ parked  <Company> – <Title>` or `⚠ needs attention  <Company> – <Title>: <reason>`, then the report path.

  Exit codes: 0 all parked · 1 preflight failed · 2 at least one needs attention · 3 stopped (alarm, signed out,
  tracker changed on disk, folder clash, hung call, or one of the three clean stops of §4.3 — a provider outage,
  the spend cap, or a key/credit failure).

  Logs, per run (`_run/`) and per job (`<job folder>/`): `browser_actions.jsonl` (every package call),
  `jev_inference_logs.json` (§6.3) and `llm_inference_logs.json` (§6.2), both written by the Gateway,
  plus `answers.json` and `screenshot.jpg` for a job that reached a form.
- **8.6 Requeue** (`records.requeue`): for each job folder in `Needs-Attention/` (or one job):
  - Status → "Resume Built";
  - the "Needs Attention: …" Notes line is removed;
  - the folder moves back to `Applications/`;
  - journaled like a record. `job.md` keeps its notes.

## 9. Tests

- **Markers:** `unit` (no browser, no network) · `browser` (a throwaway headless Chrome on port 9223, local fixtures, no model) · `live_model` (real Jev and chat models, local fixtures and captured pages; runs only with `--live`). `tests/test_model_access.py` makes real OpenRouter calls without the marker, on purpose (the user's choice).
- **Stand-ins:**
  - `FakeMCP` (`tests/fake_mcp.py`) fakes the package at the text level, so the real guard, parsers, probes and loop run. It applies `never_click` to clicks, simulates the agent (`_navigate`, next step, page goal) and records any sent form in `sent`, which must stay empty.
  - `RuleDecider` (`tests/rule_decider.py`) answers every Jev question offline with the old regex and routing rules (the topics page, answers, questions, fill, readback, preflight). The autouse fixture `decider` installs it, or the real Jev for `live_model` tests. It also resets `guard.FORM.started`.
- **Fixture server:** `http.server` on 127.0.0.1 serving `tests/fixtures/`. Every POST and every request to `/submit` goes to `posts.log`, which the tests assert stays empty.
- **Fixtures:**
  - `f01` single page, "Submit application";
  - `f02` steps Next → Review → "Submit application";
  - `f03` "Apply";
  - `f04` Enter submits;
  - `f05` "Send application";
  - `f06` cookie "Confirm my choices";
  - `f07` "Done";
  - `f08` custom dropdown, radio group, date picker;
  - `f09` hidden resume input, and resume + cover-letter step;
  - `f10` / `f10_form` Apply opens a new tab;
  - `f12` / `f12_form` form in an iframe;
  - `f13` required-field error;
  - `f14` confirmation page;
  - `f15_google`, `f16_signup`, `f17_captcha`, `f18_cookie`;
  - `jobs/view/*` LinkedIn look-alikes (Easy Apply, closed, applied, external, submitted, late-rendered, late tab, dialog, Easy Apply dialogs);
  - `ats/*` (Ashby-like, Greenhouse-like, visible captcha, greeting);
  - `uas/login`, `probe_lab/`, `probes.html`, `discovery.html`.
- **Captured pages** (`tests/captured/`): real `observe.txt` (+ `probes.txt`) from the first real run's seven jobs and from Toast's filled Greenhouse form. `test_decisions_live.py` checks Jev's judgments, the fill plan and the read-back against them.
- **Required tests:**
  - **Guard and never-submit rule:** each guard rule; the never-submit rule (Submit and Send always, Apply once filling, the uncovered labels); the hook replaces `confirm_reason`; nothing sends `confirm` (AST); only `jev.py` imports the package and calls `browser_act` (AST); probes are read-only.
  - **`jev.py`:** the environment is set before import, stray keys are removed, a timeout exits 3, surrogates are cleaned, request bodies are cleaned, goals are retried on 503.
  - **Model plumbing:** Jev batching, retries and routes; the rotation and the text-helper wrap; the chat routes and keys.
  - **Answers:** answer checks with canned engine outputs, the Retry-After wait, the 401 stop.
  - **Fill:** the fill plan and its possibility checks; the read-back; the resume fallback; the loop on FakeMCP pages; Google and blockers; release and hand-off; records on temp copies.
  - **Tripwire:** wrapper paths on f01, f02, f03 and f05 with the form being filled, zero POSTs. `live_model`: the agent is ordered to submit them, zero POSTs, and the trace shows `needs_confirmation` or `blocked`.

## 10. Build history (v2 → as built)

T0–T10 of spec v2 were built and handed over on 2026-09-23. The main changes since, each with its date and reasons in `DISCOVERY.md`:
1. Patched observer wheel (aa2–aa6).
2. Direct typing of short answers.
3. The first real run: all seven jobs to Needs Attention, five of them because of the rule-based driver.
4. The browser agent takes over navigation and advancing.
5. Jev makes every page decision instead of regex rules.
6. Vercel AI Gateway routes for Jev and the chat models; free-model rotation.
7. Surrogate cleaning.
8. Small parallel Jev batches and goal retries.
9. Read-back and fill planning by Jev.
10. `entry.py` and the label lists removed in favour of the narrow never-submit rule, with "Send" added.

## 11. What only the user can do

- Click Chrome's "Allow remote debugging?" at each new connection (preflight waits 180 s).
- Keep credit on Vercel AI Gateway (Jev, and the chat models when `chat_route = "vercel"`), or buy OpenRouter credit ($10 raises the free-model quota from 50 to 1,000 requests a day, and pays for Jev on OpenRouter).
- Create or sign in to accounts the program must not create: Workday tenants, other ATS logins. Choose `google.account_email`.
- Answer the Scratch Pad questions the report lists.
- Review and submit every parked application.

## 12. Fixed defaults (plan C-items, as built)

C1 no tab groups · C4 tracker saved after every job · C7 the agent clicks the step's Next (one action per goal) · C8 a blocked job's tab stays open · C10 `policy.prefill` · C14 closed or already applied → Needs Attention, no retry · C15 cover letter: text → generated; optional file → empty; required file → blocker · C16 demographics only from the Scratch Pad · C19 LinkedIn signed out mid-run → exit 3, job untouched · C20 free text ≤ `free_text_max_chars` without a maxlength · C21 `JEVMCP_MAX_ACTIONS=2000` · C22 uncovered optional field → empty, listed in the note · C23 experience totals only as `computed` · C24 cookie banners: the agent declines optional cookies · C25 the resume input is Jev's pick, then narrowed, then the LLM inference's.

## 13. Main problems and challenges

**P1 update (2026-09-28).** P1 fixed the request volume and the failure handling below; it did **not** get a job
parked, and could not, for a reason the user chose knowingly:

- **13.1 is fixed, and confirmed live** (`runs/20260929-094752`, 2026-09-29). One `Gateway` (§3.3) is the only
  sender: one request in flight, one retry layer of 3 attempts, 0.25 s apart. A goal step is now at most 6 HTTP
  requests, down from ~18; batching is gone, so a judgment is one request per 24 questions rather than one 5-wait
  ladder per 4. In the gate run each job's entry judgment — 12 questions — was **one** request, the whole run was
  **6 requests in 6 attempts** (nothing retried), the highest number in flight was **1**, and the spend was
  **$0.0002**.
- **13.2 is handled, not removed.** A provider that is out now stops the run cleanly instead of sending jobs to
  Needs Attention one by one (§4.3), and a chat model answers the System One questions when the System One model
  fails (§6.0b). The free models are out of the defaults (D14).
- **13.6 is fixed.** One "Allow remote debugging?" click per run, with a 180 s budget (§3.4).
- **Still open, and the blocker for a parked job:** the account has no paid System One credit (OpenRouter 402,
  Vercel 403), so the working route is a **kev-0.8b** server on this Mac, which answered `kind="other"` at
  confidence 0.1234 on a posting whose State contained "Easy Apply to this job" (gate run, 2026-09-29; 0.1238 in
  P0). No job passes the entry decision, and the chat fallback does not help — Kev answers badly rather than
  failing, and the fallback fires only on a failure. A stronger or fine-tuned System One model is P3/P4.
- **New, found by the gate run: D12's `external_ats` class is not implemented.** Two of the three queued jobs carry
  "Apply on company website" and no Easy Apply control, so they should be Needs Attention `external_ats`; they were
  reported as `load_failure`. No occurrence of `external_ats` exists in `assistant/` or `tests/`. P5 owns it. Until
  then, a queue's Easy Apply jobs must be verified by hand before a gate's numbers mean anything — this "3 job" run
  was really testing one.
- **Not verified live this phase, by user instruction** (no test may reach a real API): the System One contract test
  and the per-model LLM inference calls of P1 T7, and whether large requests fail on OpenRouter. Details and the
  full deviation list are in `DISCOVERY.md` (2026-09-28) and `recore/HANDOVER.md`.

**P0 update (2026-09-28):** P0 added observability (the per-run/per-job inference logs) and the rename, and made the System One provider configurable, but did **not** fix the problems below — they are P1–P4. Still true: no job has reached the parked final step in a real run. New blocker found this phase: on the working keyless route, **kev-0.8b misclassifies the LinkedIn entry page** (`kind="other"` at 0.12, flat distribution, although the Easy Apply control was in its State), so no job gets past the entry decision. Paid Jev is unavailable on the account (OpenRouter 402, Vercel 403). So a live end-to-end form-fill is still unproven; it needs a stronger or fine-tuned System One model (P3/P4). Details in `recore/HANDOVER.md`.

The v3 problems below, most severe first. The evidence is from `runs/20260924-233549`, the user's run of 23:35–23:54, which was stopped during its fifth job.

**13.1 Too many Jev requests, most of them failing (429 / 503).** The run sent 222 page-decision requests in 19 minutes, up to 29 a minute, plus 21 agent goals:
- 61 decision requests succeeded only after one or more retries, with about 1,070 s of accumulated retry waiting;
- 13 of the 21 goals ended in HTTP 503;
- the first two jobs (Genesys, Mastercard) failed on goal 503s after the full two minutes of retries;
- The Flex failed on a 429 "high demand".

Where the volume comes from:
- **Every page snapshot is judged from scratch with every question.** `judge` caches on the `Page` object only, and `ctx.read()` builds a new Page on every read.
  - One judgment asks 9 fixed questions, plus one per form field, the resume, each cover letter, Google and iframes.
  - This run: 47 judgments, 962 questions, 20.5 questions (5.4 requests) per judgment on average. 423 of the questions (44%) were the per-field "is this the application's own field?" question.
- **`settle` re-judges** each re-read, once a second, for up to 10 s while Jev says "loading". A slow page costs up to 10 full judgments before any action.
- **The loop reads the page many times per step:** at the top of the loop, after navigate, before and after filling, for the read-back, and in the gate. Each read is a new judgment.
- **Each form page adds more topics:** `answers`, `questions`, `fill` (3 questions per answer), `readback` (1 per answer), and a `narrow` when unsure.
- **Every agent step is another Jev request,** made by the package with one question per operation plus a target list. It is large, so it fails more often (see the next point), and it can't be batched by us.
- **Batches of 4 are a workaround that multiplies requests.** TypeSafe fails a whole request when any question fails: 44 questions per request failed 5 of 6 times on Vercel, 4 per request 4 of 33. Small batches get through, but a page costs 3–4 times as many requests.
- **Retries amplify an outage.** A failing batch is retried up to 5 times (2 + 5 + 15 + 30 + 60 s). A goal is retried 5 times on our side, and each goal step is retried about 3 times inside the package. So one goal step can become about 18 HTTP requests, and 4 batches in flight keep hitting the provider while it is saying "high demand".

Directions, not built:
- cache judgments per page state (URL + fields + text hash) instead of per `Page` object;
- ask only the questions the current decision needs (for example classify needs kind, covered and submitted; the field questions only when filling);
- use one small question, or a cheap check, for `settle`;
- one global rate limiter with a single queue, and at most 1–2 requests in flight;
- honour `Retry-After`, and stop the run with a clear message (a circuit breaker) instead of failing job after job during an outage;
- try TypeSafe's own API, which may take larger batches;
- keep a second System One endpoint as fallback: OpenRouter's Jev with credit, or a local Jev-compatible model.

**13.2 Provider limits and model quality.**
- **Jev on Vercel:** intermittent 503 "Service temporarily unavailable" from the provider itself (no gateway fallback), and 429 "high demand".
- **OpenRouter free tier:** 50 requests a day across all free models (reset at 02:00 CEST), used up by a test session.
- **Vercel chat models:** 5 requests a minute per model for a new team.
- **Mistral quality:** Mistral Small returned invalid output on Linda AI's second step. Mistral Nemo takes about 60 s a page and timed out at 120 s. Both marked an optional field required and computed years of experience wrongly on the f02 fixture. The checks drop such answers, which then sends the job to Needs Attention.
- The free Qwen answered better but was rate-limited most of the time.

**13.3 End-to-end not proven.**
- Only the navigation (LinkedIn → the company site or Easy Apply → the first form step) and the filling of first steps have worked live: Linda AI's safety reminder, Mastercard's careers site, The Flex's Ashby form, Toast's Greenhouse form.
- Untested on real sites: advancing through multi-step forms, the gate, parking, and recording.
- Account walls (Genesys's Workday: "Create Account/Sign In") correctly stop the job, but need the user.

**13.4 The never-submit rule is deliberately narrow** (user decision, to save model calls).
- Not refused: "Done", "Finish", "Confirm" and "Complete" buttons, and forms that submit on Enter.
- An "Apply" that is a form's own submit button is not refused before filling starts. That would be the navigate agent clicking a form's Apply instead of reporting the form; Toast's Greenhouse form ends in "Apply now!".
- The only other protections are the prompts ("never type", "do not submit") and the after-the-fact alarms (§4.6).

**13.5 Tight coupling to the package's internals.**
- The program depends on a vendored, patched 0.1.5 wheel and on three wraps of internal functions (`policy.text_for`, `policy._post`, `browser.confirm_reason`).
- `contract_check.py` detects when a hook stops applying, but any package upgrade needs the patch re-applied and the hooks re-checked.
- The package's goal agent decides with its own prompts and question shapes. We can refuse its clicks, but not make its requests smaller or fewer.

**13.6 Unattended runs are not possible.**
- Every new Chrome connection needs the user's click on "Allow remote debugging?" (runs 6 and 7 on 2026-09-24 stopped after 180 s without it).
- With `--no-record`, each run leaves one tab per job open, and the tab-tracking helper tab stays open during the run.

**13.7 Offline tests don't test the decisions.**
- `RuleDecider` answers Jev's questions with the old regex rules, so the offline suite proves the loop and the guard, not Jev's judgment.
- The live tests (`--live`) use real model calls and are exposed to the same provider outages. One live tripwire status check failed once without recurring; nothing was sent.

**13.8 Speed.**
- A job takes minutes: retries, `settle`'s one-second re-reads, 8 s new-tab waits after agent clicks, and 5–60 s LLM inference calls.
- The five-job run took about 19 minutes and parked nothing.
