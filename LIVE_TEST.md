# Live test plan (you run these)

## P5 re-core gate (2026-10-07)

> **Status (2026-10-07):** run over five live `--no-record` passes. Real Ashby forms parked (2/2 in the last full
> run); the embedded-Greenhouse (Toast) and résumé-upload-re-render cases were fixed from the live evidence. The
> user **accepted this as the gate** (no full 10-job recorded run). **Prerequisite for re-running on D14:**
> `config.toml` now has `chat_route = "openrouter"`, so `OPENROUTER_API_KEY` needs purchased credit (a run
> otherwise stops at preflight with HTTP 402); reverting to `chat_route = "vercel"` is the alternative.

External ATS (Greenhouse, Ashby, Lever) by one general loop — no host adapters. An external "Apply" is followed
off LinkedIn, the ATS tab is adopted, and the same pipeline fills it and parks one click before submit. Other
hosts are classified: an account wall (Workday/iCIMS/Taleo/SuccessFactors) → `signup`/`credentials`, a captcha →
`captcha`, a page with no readable application form → `unsupported_ats` (host named). T2 (cross-origin frame
attach) is deferred — this gate is hosted forms.

**Before you start**
- Start the System One route (`./run_kev_server.command` if the route is `local`), Chrome signed in to LinkedIn.
- Line up ~10 **Resume Built** jobs whose LinkedIn control is "Apply on company website" (external), with a mix
  of Greenhouse / Ashby / Lever and at least one other host. The run reports each host, so this also measures the
  real host mix.
- There is **no Lever capture** in the repo. If you want the Ashby/Lever fixtures checked against a real page,
  capture one each first (read-only, writes nothing):
  ```bash
  .venv/bin/python -m assistant capture "https://jobs.lever.co/<company>/<id>"
  ```

**1. Requeue the earlier external jobs, dry-run the queue**
```bash
.venv/bin/python -m assistant requeue --class external_ats
.venv/bin/python -m assistant run --dry-run
```

**2. A not-recorded pass first (nothing written)**
```bash
.venv/bin/python -m assistant run --no-record --limit 5
```
Check: each external job hands off to the ATS tab and the LinkedIn tab closes; a Greenhouse/Ashby/Lever form is
filled and parked (or parked-at-question with the questions noted); an unsupported host / account wall / captcha
ends Needs Attention with the right class and its tab is left open; **nothing was submitted** (no POST in
`browser_actions.jsonl`); **exactly one tab per job** is open after the run.

**3. The recorded gate (10 external jobs)**
```bash
.venv/bin/python -m assistant run --limit 10
```
**Pass when:** ≥ 5 of 10 Greenhouse/Ashby/Lever jobs are **Pending Review**; unsupported hosts and account walls
are Needs Attention with the right class (`unsupported_ats` names the host); there are **zero** submissions; there
is **exactly one tab per job**, still open after the run.

Send back: the run folder path (`runs/<ts>`), the terminal output, and what you saw in Chrome.

## P2 re-core gate (2026-09-29)

Owned browser driver, absolute never-submit guard, deterministic Easy Apply navigation. **Before you start**,
make sure at least one queued job is a real LinkedIn **Easy Apply** posting (an "Apply on company website" job is
now reported `external_ats` and is not driven).

```bash
./run_kev_server.command                                    # System One route is "local": start it first
.venv/bin/python -m assistant preflight                     # one "Allow remote debugging?" click
.venv/bin/python -m assistant run --no-record --limit 3
```

Check:
- **one** "Allow" click for the whole run (one CDP connection, D13);
- each Easy Apply job: the program clicks "Easy Apply", fills the first step, and clicks Next **by itself** (no
  browser-agent goal); it ends parked, or at Needs Attention / `broken_form` with a clear reason;
- each job with an external "Apply": Needs Attention **`external_ats`**, and the Apply control was **not** clicked;
- after the program exits, **every job tab is still open**, and none of your other tabs changed;
- **nothing was submitted** (no POST); `browser_actions.jsonl` shows no submit/apply click that came back `ok`.
- Watch items (report if seen): a resume "Upload resume" button refused as a submit; a step wrongly judged final;
  an already-applied or closed posting misread because of the sidebar.

Send back: the run folder path (`runs/<ts>`), the terminal output, and what you saw in Chrome.

## P1 re-core gate (2026-09-28)

P1 changed how the program talks to models and to Chrome. This gate checks the **traffic and the failure handling**,
not decision quality: with `kev-0.8b` a job still stops at the entry decision, and P1 says the jobs do not have to
park. Nothing here needs a paid key.

**Before you start**

1. Start the local Kev server: `./run_kev_server.command` (the System One route is `local`, so no key and no quota).
2. Chrome open with remote debugging on 9222, signed in to LinkedIn.
3. Chrome → Settings → Performance → Memory Saver → "Always keep these sites active": add `linkedin.com`, so Chrome
   does not discard a parked tab.
4. `.env` needs `AI_GATEWAY_API_KEY` (the chat route is `vercel`). No OpenRouter credit is needed on these settings.

**1. Preflight — one "Allow" click**
```bash
.venv/bin/python -m assistant preflight
```
- The line `Chrome will ask "Allow remote debugging?" — click Allow (waiting up to 180 s).` prints **before** any
  model probe, and Chrome asks **once**.
- Then the ✓ lines, ending with "tab release works".

**2. Three jobs, not recorded**
```bash
.venv/bin/python -m assistant run --no-record --limit 3
```

**3. Check, in `runs/<ts>/`**
- **One** "Allow remote debugging?" click for the whole run — not one per job.
- `report.md` Summary has:
  - `Highest number of requests in flight: 1`;
  - `Model spend: $…` under $1.00 (on these settings it is cents: the System One model is local and free, and only
    the Vercel chat calls cost anything);
  - `Model requests: N System One, M LLM inference (A attempts, F failed)` with `A` at most `3 × N + 3 × M`.
- In each `<job folder>/jev_inference_logs.json`: no request has more than **3** attempts. The program asks several
  different judgments about the same page (classify, fill, read-back), so repeated `State` values are normal — what
  would be a bug is **four or more entries in a row with the same `State` *and* the same questions**. One extra group
  of up to three is allowed for the single goal retry.
- `llm_inference_logs.json`: no `:free` model anywhere in it.
- Nothing was submitted; each job's tab is still open.
- If a provider did fail: the run stopped with one clear reason (`■ run stopped: …`), the exit code is 3
  (`echo $?`), and that job has **no** folder move, **no** tracker change and **no** `job.md` note.

Known and expected on these settings: every job ends in Needs Attention `load_failure` at the entry decision, because
kev-0.8b cannot classify a real LinkedIn posting (`recore/HANDOVER.md`). That still passes this gate.

**Check the queue before you trust a later gate.** P3's and P4's targets count *parked* jobs, and a job with no
Easy Apply control can never park. In the P1 gate run two of the three queued jobs carried "Apply on company
website" (an external ATS, D12) and only one was an Easy Apply posting — so "3 jobs" tested one. Open each queued
job in Chrome and confirm it shows **Easy Apply**, or the number is meaningless. `run --dry-run` lists the queue.

**Run on 2026-09-29: passed** (`runs/20260929-094752`). One "Allow" click; highest in flight 1; 6 requests in 6
attempts; $0.0002; no `:free` model; nothing submitted; tabs left open; nothing recorded. 3 jobs, 0 parked, 3
Needs Attention `load_failure` — as expected above. Details in `DISCOVERY.md` (2026-09-29).

**If you want the paid route instead** (this is what unblocks a parked job, and is P3/P4's problem): add ≥ $5 of
OpenRouter credit, put `OPENROUTER_API_KEY` in `.env`, and set `system_one_decision_provider = "openrouter"` (and, if
you want one bill, `chat_route = "openrouter"`). Then a 401/402 stops the run with "check the key / add credit",
rather than failing each job in turn.

---

## P0 re-core gate (2026-09-28)

The P0 gate checks the logs and the run-folder layout, not decision quality — the job need not park. Start the local Kev server first (`./run_kev_server.command`), Chrome open on 9222 and signed in.

**1. Preflight**
```bash
.venv/bin/python -m assistant preflight
```
`runs/<ts>/_run/` then holds `jev_inference_logs.json` (the System One probe — exactly `State, Score, Noul, Choice, Response`) and `llm_inference_logs.json` (the LLM inference probe — exactly `model, provider, parameters, messages, completion`; `provider` like `vercel/<upstream>`). No key string in either.

**2. One job, not recorded**
```bash
.venv/bin/python -m assistant run --no-record --job "<a Resume-Built Easy Apply URL>"
```
In `runs/<ts>/<job folder>/`:
- `jev_inference_logs.json` and (if a form page was reached) `llm_inference_logs.json`, with exactly the key sets above; full prompt/page text and full completion.
- `browser_actions.jsonl` exists; `answers.json` if a form was reached; `screenshot.jpg` if it parked.
- No `decisions.jsonl` / `calls.jsonl` / `answers/` / `shots/` anywhere in `runs/<ts>/`.
- `report.md` has its sections; nothing was submitted; the job tab is still open.

Known: with `kev-0.8b` the job may stop at the entry decision (Needs Attention `load_failure`) — that still passes the P0 gate (logs + layout + nothing submitted + tab open).

---

## v3 live test plan (T10 — pre-P0 paths: `answers/<folder>.json`, `shots/<folder>.jpg` are now `<job>/answers.json`, `<job>/screenshot.jpg`)

Everything so far was tested only against local fixture pages. These steps are the first contact with real sites, in order of increasing risk. Stop at the first surprise and send me the run folder.

All commands run from `Tools/Application_Assistant/`, with Chrome open on port 9222 and signed in (README, Setup step 4).

## 1. Preflight

```bash
.venv/bin/python -m assistant preflight
```

Expect six ✓ lines:
- config and key
- tracker
- server capabilities
- LinkedIn signed in
- attached to Chrome

A LinkedIn feed tab opens briefly and closes again.

## 2. Capture a few real forms (read-only)

Pick 3–5 real application pages: one LinkedIn Easy Apply modal (open it yourself first), one Greenhouse, one Lever, and one Workday if you have it. For each:

```bash
.venv/bin/python -m assistant capture "https://boards.greenhouse.io/<company>/jobs/<id>"
```

This waits up to 10 s for the page to draw its content, then saves the element table, page text and a screenshot to `tests/captured/<host>-<ts>/`. It never clicks or types, and the captured tab stays open in your Chrome afterwards. Then run the classifier over them:

```bash
.venv/bin/pytest -q tests/test_pages_unit.py -k captured
```

If a page is misread, add `expected_kind.txt` (`form`, `final`, `blocker`, `iframe`, `ats_entry`, …) to its folder and tell me. Look especially for required fields missing from `observe.txt`: untyped inputs are invisible on 0.1.5.

## 3. One Easy Apply job, not recorded

Choose a job in `Applications/` whose tracker row is **Resume Built** and whose LinkedIn page shows **Easy Apply**.

```bash
.venv/bin/python -m assistant run --dry-run
```

```bash
.venv/bin/python -m assistant run --no-record --job "<its LinkedIn URL>"
```

Check:
- The Easy Apply modal is filled and stops on the step whose button says **Submit application**, and nothing was submitted.
- `runs/<ts>/answers/<folder>.json`: every answer has a real `quote` / `relies_on`.
- `runs/<ts>/shots/<folder>.jpg` shows the parked step.
- The tracker, the folder and `job.md` are unchanged, and the tab is still open.

Close the modal yourself afterwards.

## 4. One Greenhouse or Lever job, not recorded

Same as step 3, with a job whose LinkedIn button is **Apply**, which opens the company site in a new tab. Also check:
- The LinkedIn tab was closed, and the application tab is open and parked.
- Cookie banners were answered "Reject all" / "Only necessary".
- If Google sign-in appeared: `maaz1377.aa@gmail.com` was chosen exactly once, or the job went to Needs-Attention with the reason.

## 5. First recorded run

```bash
.venv/bin/python -m assistant run --limit 3
```

Then check:
- Parked jobs: tracker **Pending Review**, folder in `Pending-Review/`, and a `# Application Assistant` note at the end of `job.md`.
- Needs-Attention jobs: tracker **Needs Attention** with a one-line Notes entry, folder in `Needs-Attention/`, and a note listing the open questions.
- `runs/<ts>/report.md`: copy useful answers from "Questions for your Scratch Pad" into `Profile.md`.
- `runs/<ts>/tracker-backup.numbers` is your undo.

Review and submit each parked tab yourself.

## If something goes wrong

- **Exit 3 with "ALARM"**: stop using the tool and send me `runs/<ts>/calls.jsonl`. This should never happen.
- **A job in Needs-Attention you think should have worked**: send `runs/<ts>/report.md`, `answers/<folder>.json`, and a `capture` of that page.
- **An interrupted run**: the next run completes half-recorded jobs from `journal.jsonl` automatically.
