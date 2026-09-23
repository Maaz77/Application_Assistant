# Live test plan (you run these; T10)

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
