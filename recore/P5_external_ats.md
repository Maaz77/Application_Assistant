# P5 — External ATS: Greenhouse, Ashby, Lever

Read `recore/00_common.md` and `recore/HANDOVER.md` first.

**Proposed gate (the user may change it at the start of the session):** ≥ 5 of 10 external jobs on Greenhouse, Ashby or Lever parked; every other external job classified correctly.

## Goal
Follow LinkedIn's external "Apply" to Greenhouse, Ashby and Lever forms, and fill them with the same pipeline: code facts → LLM inference → guard → park. Other ATSs stay Needs Attention, with their host named.

## Tasks
- T1 Hand-off: on a job page with an external "Apply", click it (allowed from P5), adopt the new tab as the job tab, and close the LinkedIn job tab (one tab per job). A host that is not supported → Needs Attention `unsupported_ats`, with the host; leave that tab open for the user.
- T2 Frames: the driver attaches to the job tab's child frames (`Target.setAutoAttach` with `flatten: true`, same-origin and cross-origin), runs the observer in each frame, and prefixes refs with the frame (`f2:e14`). Greenhouse forms embedded in company pages need this. Delete the `browser_open(src)` iframe hop.
- T3 Adapters `assistant/ats/greenhouse.py`, `ashby.py`, `lever.py`. Each one covers: detection by host and DOM; the form scope; field extraction details (custom selects, "Attach" resume buttons, EEO/demographic sections → C16: only from the Scratch Pad); required markers; the final submit control (for example "Submit application", "Apply now!"), which guard v2 refuses → final step.
- T4 Greenhouse cross-check (optional): `GET https://boards-api.greenhouse.io/v1/boards/<board>/jobs/<id>?questions=true` returns the question list with required flags; compare it with the extraction.
- T5 Sign-in: Google one-click (D11) through the existing `google_signin` flow. Any other account wall (Workday, iCIMS, Taleo, SuccessFactors) → Needs Attention `signup` or `credentials` (build spec §6.3).
- T6 `requeue --class external_ats`: move the jobs recorded earlier as "external ATS, not yet supported" back to the queue.
- T7 Fixtures: Greenhouse-like (hosted, and embedded in an iframe), Ashby-like, Lever-like — from `tests/fixtures/ats/` and from pages the user captures with `python -m assistant capture <URL>`.

## Acceptance criteria
- A1 The offline suite, tripwire (including each ATS's final button) and replay pass.
- A2 The live gate passes.
- A3 Docs: build spec v5.0; `recore/HANDOVER.md` written.

## Live gate (the user runs)
1. `python -m assistant requeue --class external_ats`, then recorded runs over 10 external jobs.
2. Pass when ≥ 5 of 10 Greenhouse/Ashby/Lever jobs are "Pending Review"; unsupported hosts and account walls are Needs Attention with the right class; there are zero submissions; there is exactly one tab per job, open after the run.
