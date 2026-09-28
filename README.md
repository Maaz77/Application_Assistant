# Application Assistant v2

For every job at Status **Resume Built**, this fills the application in your own signed-in Chrome and **stops one click before submission**. It leaves that tab open for you to review and submit, and records the result. It never submits anything.

- A parked job gets tracker Status **Pending Review**, and its folder moves to `Pending-Review/`.
- A job it can't finish gets **Needs Attention** with a one-line Notes entry, and its folder moves to `Needs-Attention/`. The reason and the open questions are appended to its `job.md` under `# Application Assistant`.
- Either way, the job's browser tab stays open.

Build spec (v2): [application_assistant_build_spec.md](application_assistant_build_spec.md). Server findings and decisions: [DISCOVERY.md](DISCOVERY.md).

## How it works

Three pictures: what happens to one job, what happens on one form page, and which file does what. The names on the right are the files in `assistant/` that do each step.

**The System One decision model** (`models.system_one_decision_provider`: a **Kev** server on this Mac, or TypeSafe's **Jev** through Vercel AI Gateway or OpenRouter — one API, so only the address changes) makes the decisions, in two places:
- **The browser agent** is the jev package's goal agent (`browser_goal`). The decision model looks at the page and picks each click, one action at a time.
- **The page decisions** are typed questions the code asks it in `decide.py`, all of them for a page in one call (about 0.3 s, $0.0003). What is this page? Is a pop-up in the way? Which fields belong to the application? Which upload takes the resume? Is there an error message? The code branches on the answers, with thresholds set in `decide.THRESHOLDS`.

"Jev" in the pictures below means whichever of the three the config names; the questions, the answers and the thresholds are the same. The model decides and the code keeps only what must not be left to a model: the answers come only from your files, the resume is uploaded by the code, and one fixed rule in `guard.py` (no model call): a button or link labelled "Submit" or "Send" is never clicked, and one labelled "Apply" is not clicked once the form is being filled.

### One job, start to finish

```text
 preflight: key, Jev answers, Chrome, LinkedIn signed in ............ cli.py
  │  (a failure stops the run here: exit 1, nothing written)
  ▼
 the queue .......................................................... records.py
     Applications/<job>/   job.md (with the LinkedIn URL) + resume Amin_*.pdf
     Job_Tracker.numbers   the job's row says "Resume Built"
  │
  ▼  for each job, one at a time
 open the job's LinkedIn posting in its own tab ..................... cli.py
  │  (a company site that opens in a new tab is followed) ........... tabs.py
  ▼
  ┌─ the page loop ────────────────────────────────────────────────── fill.py
  │  read the page; Jev judges it in one call ....................... pages.py
  │    "application submitted" ───────► ALARM: stop the whole run (exit 3)
  │    captcha / sign-up / password ──► one retry where possible, else ✗
  │    "Sign in with Google" ─────────► pick your account ........... google_signin.py
  │  is the application form on screen, with nothing over it? (Jev)
  │    no: the browser agent takes ONE action, then the page is read again
  │      cookie banner ───────────────► decline optional cookies
  │      pop-up ──────────────────────► continue the application
  │      job page ────────────────────► Apply (the agent's own click)
  │    yes: fill the page (next picture); the agent clicks Next
  └─ repeat until the last step: Submit, Send or Apply, never clicked
  │
  ▼
 final check: nothing required is empty, the resume is on the page,
 no error message and a Submit button (Jev) ......................... pages.py
  │
  ▼
 ✓ PARKED            the tab stays open on the Submit page for you to review
 ✗ NEEDS ATTENTION   at any step where it cannot go on; that tab stays open too
  │
  ▼
 record the result .................................................. records.py
     tracker   Status: Pending Review, or Needs Attention
     folder    moved to Pending-Review/ or Needs-Attention/
     job.md    a note: what happened, and any open questions
     (--no-record skips all three)
 write runs/<date-time>/report.md ................................... report.py
```

- **"The form is on screen"** is Jev's answer: the page is an application step (or its last step) and nothing covers it. If the agent says it has reached the form where Jev does not see one, such as a job-alert box, the agent is asked once more.
- **A pop-up over the form**, as Jev judges it (a cookie or consent dialog, or a covered field), sends the page back to the agent, even halfway through the form.
- **The agent's Apply** on a job page is its own click. Once the form is being filled, an Apply (or any Submit or Send) is refused, and that refusal marks the form's last step.
- **A decision that cannot be made** (Jev unreachable, out of credits) sends the job to Needs Attention with class `decision`. It is never guessed.

### One form page

```text
 the page's questions + Profile.md + job.md + the resume's text
  │
  ▼
 the LLM inference: one model call, reasoning off ................... answers.py
 for each question: an answer, its source, and a quote that proves it
  │
  ▼
 check every answer against the files (check_answers) ............... answers.py
     is the quote really in the source file?
     is the chosen option really on the page?
     does a computed total, like years of experience, add up?
     Jev: may this question get a written answer? total or tool years?
          is the value on the page a placeholder like "Select…"?
     an answer that fails a check is dropped
  │
  ▼
 fill the page ...................................................... fill.py
     1  upload the resume
     2  type the long answers, and the ones written for you
     3  tick, select and type the rest directly, without a model
     4  anything left, like a custom dropdown: a page goal (a model clicks)
     5  a required question with no answer → ✗ NEEDS ATTENTION; the question
        goes into the report and job.md, for your Profile.md Scratch Pad
     6  read the page back (an option the page redrew is found by its label);
        a value that did not stick gets one retry, else ✗
```

### The code

```text
 python -m assistant   run · preflight · capture · tripwire · requeue
  │
 cli.py ................... the commands and preflight; runs the jobs one by one
  ├─ config.py ............ config.toml, and the two keys from .env
  ├─ records.py ........... the queue before the jobs, the record after each one
  │   └─ tracker.py ....... reads and writes Job_Tracker.numbers
  ├─ report.py ............ runs/<date-time>/report.md, and the exit code
  ├─ blockers.py .......... how a job can end, and the one-retry rule
  └─ fill.py .............. the page loop: the agent's steps, and filling a page
      ├─ pages.py ......... reads a page, and Jev's judgment of it; the gate
      ├─ answers.py ....... asks the model, checks its answers ──► OpenRouter
      ├─ decide.py ........ asks typed questions ──► the System One route (Kev here, or Jev)
      ├─ google_signin.py . Google one-click sign-in
      └─ tabs.py .......... which tab is whose; leaves each job's tab open
  │
  ▼  every browser call goes through
 jev.py ................... the only file that imports the browser package
  ├─ guard.py ............. never clicks Submit or Send, nor Apply once filling started;
  │                         no script except a probe
  └─ probes.py ............ small read-only scripts that measure the page
  │
  ▼
 jev-ultrafast-mcp 0.1.5+aa6, patched, in vendor/ ──► your Chrome, port 9222
 (its goal agent picks each click with the same decision model, on the same route)
```

Also in this folder:
- `prompts/`: what the models are told. `llm_inference.md` is used by `answers.py`; `navigate_goal.md`, `next_step_goal.md` and `page_goal.md` are the browser agent's goals.
- `vendor/`: the patched browser package, and the patch itself.
- `tests/`: see [Tests](#tests).
- `runs/`: one folder per run, see [Running](#running).
- `assistant/contract_check.py`: checks that the package's functions still match what `jev.py` calls.

## Setup (once)

1. **Python environment.** The sibling tools use Poetry, but on this Mac Poetry's pyenv shim is broken, so a plain venv is used:

   ```bash
   cd Tools/Application_Assistant
   /opt/homebrew/bin/python3 -m venv .venv
   .venv/bin/pip install vendor/jev_ultrafast_mcp-0.1.5+aa6-py3-none-any.whl httpx "pydantic>=2" pypdf python-dotenv numbers-parser pytest
   ```

   The browser package is a **patched 0.1.5** kept in `vendor/`, with fixes found on the live sites:
   - content inside `display: contents` wrappers becomes visible (LinkedIn's job card);
   - only the topmost modal dialog is read (Easy Apply, and its "Save this application?" prompt);
   - custom-styled `opacity: 0` radios, checkboxes and file inputs are listed;
   - ARIA radios and checkboxes report their state;
   - the resume can be uploaded through a file-chooser button (LinkedIn has no file input).

   The changes are in `vendor/jev_ultrafast_mcp-0.1.5+aa6.patch`, and preflight refuses to run on the stock package. Once Poetry works again, `poetry install` does the same from `pyproject.toml`.

2. **Keys.** Create `.env` in this folder. It is git-ignored. The decision model on the `local` route needs none
   of them; the LLM inference and the text helper always need their route's key.
   - `OPENROUTER_API_KEY=<key>`: the LLM inference and the text helper when `models.chat_route = "openrouter"`.
   - `AI_GATEWAY_API_KEY=<key>`: Jev through Vercel AI Gateway (`models.system_one_decision_provider = "vercel"`), and the chat models when `models.chat_route = "vercel"`. Vercel serves requests only once a card is on file for the team, which also unlocks its free credits. With `system_one_decision_provider = "openrouter"` the OpenRouter key pays for Jev instead, and with `"local"` nothing pays for it.
   - `KEV_API_KEY=<key>`: only when you started the local Kev server with `KEV_API_KEY` set (a Kev server on `127.0.0.1` is open by default and ignores the header).
 Keep some credit on the account: each form page costs one LLM inference call (about $0.001), plus the browser agent's decisions (about $0.00002 each) and the text helper's typed values. `tests/test_model_access.py` shows the key, the account's credit and whether a model answers. An account that has never bought credits gets 50 free-model requests a day across all free models, and rotation cannot get past that: HTTP 429 "free-models-per-day" from every model. $10 of credit raises it to 1,000 a day.

3. **Config.** `config.toml` is already filled in:

   | Key | Value |
   |---|---|
   | `paths.base` | the repo root |
   | `models.chat_route` | who serves the LLM inference and the text helper: `openrouter` (the free models below) or `vercel` (Vercel AI Gateway, paid from its credit, for when OpenRouter's free quota is out). Both take the same chat/completions request |
   | `models.openrouter.llm_inference` | five free OpenRouter models, tried in turn (`rotation.py`): answers each form page from your files (sent with reasoning off). A call starts at the model that answered last; one that is out (rate-limited, overloaded, timed out, wrong output) hands over to the next at once. A 429 with a short `Retry-After` (30 s or less) is waited out once. When none answers, the job goes to Needs Attention with every model's reason. A single ID also works |
   | `models.openrouter.text_helper` | four free OpenRouter models, rotated the same way: types the values the browser agent enters |
   | `models.vercel.llm_inference`, `models.vercel.text_helper` | `mistral/mistral-small` (about 5 s and $0.0013 a page), then `mistral/mistral-nemo` (cheaper, but about 60 s a page). Vercel limits a new team to 5 requests a minute per model |
   | `models.<route>.system_one_decision_model` | the decision model of the route in use, for the browser agent and every page decision (`decide.py`). Each route names it its own way: `kev-latest` on a local Kev server, `typesafe-ai/jev` on Vercel, `typesafe/jev-1.13` on OpenRouter |
   | `models.system_one_decision_provider` | `local` (a Kev server on this Mac: no key, no quota, and nothing leaves the machine), `vercel` (Vercel AI Gateway's TypeSafe-compatible API) or `openrouter` |
   | `models.local` | the Kev server: `base_url` (`http://127.0.0.1:8009`), `system_one_decision_model` (`kev-latest`, the name the server answers to), `state_chars` (12000: Kev was trained on short states and loses accuracy on long ones) and `timeout` (120 s: one pass on an Apple GPU is seconds) |
   | `google.account_email` | `maaz1377.aa@gmail.com` |

   Unknown keys are an error.

4. **Chrome with remote debugging on port 9222**, signed in to LinkedIn (and Google, for the one-click rule). Either:
   - open `chrome://inspect/#remote-debugging` in your normal Chrome and turn remote debugging on, or
   - start a dedicated profile and sign in to LinkedIn and Google there once:

     ```bash
     "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222 --user-data-dir="$HOME/.chrome-applications"
     ```

   The assistant *attaches* to this Chrome through the jev package, which runs inside the assistant's own process. It never quits Chrome, and never closes a job tab.

5. **The decision model on this Mac** (only while `models.system_one_decision_provider = "local"`, which is what
   `config.toml` ships with). [Kev](https://github.com/jaredpalmer/kev) is a family of small decision models that
   serve TypeSafe's own System One API, so the assistant reaches them exactly as it reaches Jev. It needs
   [uv](https://docs.astral.sh/uv) and git; the first start downloads the checkpoint and its Qwen base model into
   `~/.cache/huggingface`, and the server then runs on MLX (Apple Silicon).

   ```bash
   ./run_kev_server.command          # clones/updates ~/kev, then serves kev-0.8b on http://127.0.0.1:8009
   ```

   Leave that window open while the assistant runs. **Kev-0.8B is the size for a 16 GB Mac**; `KEV_MODEL=jaredpalmer/kev-4b ./run_kev_server.command` is more accurate but is a 32 GB machine in Kev's own table. What this buys and what it costs:

   | | Jev (Vercel / OpenRouter) | Kev-0.8B here | Kev-4B (32 GB Mac) |
   |---|---|---|---|
   | Accuracy on questions it was not trained on (Kev's README) | 0.857 | 0.648 | 0.817 |
   | Cost and quota | about $0.00002 a decision, a key, a rate limit | none | none |
   | Speed | about 0.3 s a page | hundreds of ms a pass, on your own GPU | slower, and it shares 16 GB with Chrome |
   | Your pages | leave the machine | never leave the machine | never leave the machine |

   A smaller model means more jobs in `Needs-Attention/`, not a wrong application: every threshold in
   `decide.THRESHOLDS` still has to be met, and a decision that cannot be got is never guessed. `models.local.state_chars`
   (12000) keeps each page short, because Kev was trained on states of up to 384 tokens and loses accuracy on long ones.
   To go back to Jev, set `system_one_decision_provider = "vercel"` (or `"openrouter"`) in `config.toml`.

6. Check everything:

   ```bash
   .venv/bin/python -m assistant preflight
   ```

   On the local route preflight first reads `GET /v1/models` and prints which checkpoint the server loaded, on
   which backend; if the server is not running it says so and how to start it, and writes nothing.

## Running

```bash
.venv/bin/python -m assistant run --dry-run
```

```bash
.venv/bin/python -m assistant run --no-record --limit 1
```

```bash
.venv/bin/python -m assistant run --limit 3
```

Or double-click `run_application_assistant.command`; arguments pass through.

To try jobs again after a run, put the Needs-Attention ones back in the queue: their folders move back to `Applications/`, their Status returns to Resume Built, and the "Needs Attention: …" line the run added to Notes is removed. `job.md` keeps its notes as the job's history.

```bash
.venv/bin/python -m assistant requeue
```

`--job URL` requeues only that job. The tracker is backed up to `runs/<time>-requeue/` first.

| Flag | Effect |
|---|---|
| `--dry-run` | No browser, no writes: prints the queue and anomalies. |
| `--no-record` | Fills and parks, but writes no tracker row, folder move or `job.md` note. |
| `--job URL` | Only that job. |
| `--limit N` | At most N jobs. |

Each run writes `runs/<YYYYMMDD-HHMMSS>/`:

| File | Contents |
|---|---|
| `report.md` | parked jobs, jobs needing attention, queue anomalies, and questions for your Scratch Pad |
| `answers/` | every answer with its source and quote |
| `shots/` | a screenshot of each parked page |
| `calls.jsonl` | every `browser_*` call to the jev package, key redacted |
| `journal.jsonl` | the record events |
| `tracker-backup.numbers` | the tracker as it was before the run |

**Exit codes:**

| Code | Meaning |
|---|---|
| 0 | all parked |
| 1 | preflight failed; nothing was written |
| 2 | at least one job needs attention |
| 3 | the run was stopped (a safety alarm, LinkedIn signed out, the tracker changed on disk, or a folder clash) |

## How it stays one click short of submitting

1. **One fixed rule** (`assistant/guard.py`, user decision 2026-09-24), with no model call: a button or link labelled **Submit** or **Send** is never clicked, and one labelled **Apply** is not clicked once the form is being filled. Before that, "Apply" / "Easy Apply" is how the agent starts the application. The browser package applies the rule to every click, the agent's and the program's; a refused click comes back as `needs_confirmation`, and in the form it marks the last step, where the job is parked. Nothing in the program ever sends `confirm`.
2. **Deliberately narrow.** Buttons labelled "Done", "Finish" or "Confirm", and a form that submits on Enter, are not covered by the rule.
3. **The program's own ops** run only read-only scripts from `probes.py`, never an upload onto a Submit, Send or Apply control, and never `confirm`.
4. **Alarms stop the run** (exit 3) if a Submit or Apply click ever goes through anyway, or confirmation text appears on the page (Jev's answer, or the fixed tripwire pattern).

`python -m assistant tripwire` proves the rule on local forms that end in Submit, Send or Apply. `--live` also has the model try to submit them, which costs a few model calls.

## Known limits (jev-ultrafast-mcp 0.1.5+aa6)

See [DISCOVERY.md](DISCOVERY.md):
- Inputs without a `type` attribute are invisible to the server, so such a required field ends in Needs-Attention.
- Chrome asks you to **allow** each new remote-debugging connection in `chrome://inspect` mode. Preflight waits up to 180 s for you to click Allow; after that the run stops with exit 3.
- Workday and similar sites need an account. The program tries your Google one-click sign-in; if the site then asks to register or accept terms, the job goes to Needs-Attention.
- Read-only date pickers can't be set.
- A custom dropdown shows its options only once opened, so an answer for it can't be checked against the page and is dropped ("answer not on the page").
- Jev reads text only, and is trained mainly on English; pages in other languages are judged less accurately.
- Script results are capped at 200 characters, so probes return counts plus the first items.

## Tests

```bash
.venv/bin/pytest -m unit -q
```

```bash
.venv/bin/pytest -q
```

```bash
.venv/bin/pytest -q --live
```

- `-m unit`: no browser, no network.
- Plain `pytest`: adds `browser` tests, run against a throwaway headless Chrome on port 9223 and local fixture pages only. `tests/test_model_access.py` also runs here and calls OpenRouter.
- `--live`: adds `live_model` tests. They call the configured routes — the chat models over the network, and the decision model where `models.system_one_decision_provider` points, so on the `local` route the Kev server must be running — still against local fixtures and saved pages only, and need `.env` for the chat key. They include the end-to-end `process()` runs and `tests/test_decisions_live.py`, where Jev judges the fixture pages, pages saved from real sites, and the six pages of the run that went wrong on 2026-09-23.
- Offline, the decision model's questions are answered by `tests/rule_decider.py`, a stand-in built from the rules the program used before Jev. The program itself never uses those rules.

Real pages for the classifier tests come from `capture` (see [LIVE_TEST.md](LIVE_TEST.md)).
