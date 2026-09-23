# Application Assistant v2

For every job at Status **Resume Built**, this fills the application in your own signed-in Chrome and **stops one click before submission**. It leaves that tab open for you to review and submit, and records the result. It never submits anything.

- A parked job gets tracker Status **Pending Review**, and its folder moves to `Pending-Review/`.
- A job it can't finish gets **Needs Attention** with a one-line Notes entry, and its folder moves to `Needs-Attention/`. The reason and the open questions are appended to its `job.md` under `# Application Assistant`.
- Either way, the job's browser tab stays open.

Build spec (v2): [application_assistant_build_spec.md](application_assistant_build_spec.md). Server findings and decisions: [DISCOVERY.md](DISCOVERY.md).

## Setup (once)

1. **Python environment.** The sibling tools use Poetry, but on this Mac Poetry's pyenv shim is broken, so a plain venv is used:

   ```bash
   cd Tools/Application_Assistant
   /opt/homebrew/bin/python3 -m venv .venv
   .venv/bin/pip install "jev-ultrafast-mcp==0.1.5" httpx "pydantic>=2" pypdf python-dotenv numbers-parser pytest
   ```

   Once Poetry works again, `poetry install` does the same from `pyproject.toml`.

2. **OpenRouter key.** Copy `.env.example` to `.env` and set `OPENROUTER_API_KEY=`. This is the only secret, and `.env` is git-ignored. Keep some credit on the account: each form page costs one answer-engine call plus the page-filling model's decisions.

3. **Config.** `config.toml` is already filled in:

   | Key | Value |
   |---|---|
   | `paths.base` | the repo root |
   | `models.answer_engine` | `qwen/qwen3.8-27b:free` |
   | `google.account_email` | `maaz1377.aa@gmail.com` |

   Unknown keys are an error. If the free model keeps answering HTTP 429 (rate-limited upstream, as it did on 2026-09-23), switch to the paid `qwen/qwen3.8-27b`.

4. **Chrome with remote debugging on port 9222**, signed in to LinkedIn (and Google, for the one-click rule). Either:
   - open `chrome://inspect/#remote-debugging` in your normal Chrome and turn remote debugging on, or
   - start a dedicated profile and sign in to LinkedIn and Google there once:

     ```bash
     "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222 --user-data-dir="$HOME/.chrome-applications"
     ```

   The assistant *attaches* to this Chrome through the jev package, which runs inside the assistant's own process. It never quits Chrome, and never closes a job tab.

5. Check everything:

   ```bash
   .venv/bin/python -m assistant preflight
   ```

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

1. **The wrapper's guard** (`assistant/guard.py`) checks every browser op. It refuses:
   - clicks on Submit / Send / Apply / Confirm / Done / Finish / Complete labels;
   - Enter or Return in any key op;
   - typing with `submit`;
   - any script that isn't a read-only constant in `probes.py`;
   - `confirm`, which only `entry.py` may send. It sends it only for the first Easy Apply / Apply click, and for a sign-up wall's "Apply without an account" / "Continue as guest" link while no field holds a value.
2. **The server's confirmation rule** refuses the same labels for the page-filling model (`needs_confirmation`).
3. **Alarms stop the run** (exit 3) if a model ever clicks such a label successfully, or confirmation text appears on the page.

`python -m assistant tripwire` proves this on local fixture pages. `--live` also runs the model tripwire, which costs a few OpenRouter calls.

## Known limits (jev-ultrafast-mcp 0.1.5)

See [DISCOVERY.md](DISCOVERY.md):
- Inputs without a `type` attribute are invisible to the server, so such a required field ends in Needs-Attention.
- Read-only date pickers can't be set.
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
- Plain `pytest`: adds `browser` tests, run against a throwaway headless Chrome on port 9223 and local fixture pages only.
- `--live`: adds `live_model` tests. They call OpenRouter, still against local fixtures only, and need `.env`.

Real pages for the classifier tests come from `capture` (see [LIVE_TEST.md](LIVE_TEST.md)).
