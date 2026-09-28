# Live test plan (you run these)

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
- In each `<job folder>/jev_inference_logs.json`: no request has more than **3** attempts. Count the entries for one
  judgment — three identical `State` values in a row is the retry ladder, and a fourth would be a bug.
- `llm_inference_logs.json`: no `:free` model anywhere in it.
- Nothing was submitted; each job's tab is still open.
- If a provider did fail: the run stopped with one clear reason (`■ run stopped: …`), the exit code is 3
  (`echo $?`), and that job has **no** folder move, **no** tracker change and **no** `job.md` note.

Known and expected on these settings: every job ends in Needs Attention `load_failure` at the entry decision, because
kev-0.8b cannot classify a real LinkedIn posting (`recore/HANDOVER.md`). That still passes this gate.

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
