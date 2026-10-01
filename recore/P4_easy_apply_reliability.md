# P4 — Make LinkedIn Easy Apply reliable

Read `recore/00_common.md` and `recore/HANDOVER.md` first.

## Goal (D8)
On 10 real Easy Apply jobs: ≥ 7 are "Pending Review" (at the final step, or at a question that `Profile.md` and its Scratch Pad do not answer); on average ≤ 2 min and ≤ $0.05 per job; nothing submitted.

## Method
Evidence first. Each failure in a gate run becomes a replay fixture, then a fix, then a passing replay test. Keep the table "failure → fixture → fix → test" in `recore/HANDOVER.md`.

## Tasks
- T1 Widget handlers (`assistant/widgets.py`), each with fixtures and a read-back:
  - typeahead/combobox (for example City): type the value; wait ≤ 3 s for `role=option` items; click the option equal to the value, else the one that starts with it; several close candidates → Jev `choice`; none → leave empty and note it;
  - radio groups and single checkboxes: click the option's label or input; verify `checked`;
  - checkbox groups (multi-select);
  - native `<select>`: select by option label;
  - numbers: whole numbers when the hint says so; respect the range in the hint ("between 0 and 99");
  - dates: type in the format of the placeholder; a calendar-only picker → leave empty and note it;
  - phone: country-code select + local number (build spec §7.3).
- T2 Resume step (D22): if a resume card with the tailored file name exists, select it; else upload the PDF through the file input, then verify that the card with that name is selected. Cover letter: C15.
- T3 Validation messages: read the dialog's inline errors, map each one to its field, and refill once when the message states the format (whole number, range, a required selection). Still wrong → `broken_form`.
- T4 Dialog safety: never click the dialog's close (X), "Dismiss", "Discard", or anything in the "Save this application?" dialog. If that dialog appears, stop the job with no clicks: Needs Attention `dialog_closed`.
- T5 Speed: no fixed sleeps; wait on the settle hash with short timeouts; target ≤ 2 min per job.
- T6 Optional, only if the user gives a HAR file of one manual Easy Apply submission: a network tripwire on the job tab while the program drives it — block the submission request seen in the HAR and raise the alarm. Switch it off when the tab is parked, so that the user's own submission works.

## Acceptance criteria
- A1 The offline suite, tripwire and replay pass, including every new failure fixture.
- A2 The live gate passes, counted over the final code.
- A3 Docs: build spec v4.2; `recore/HANDOVER.md` with the failure → fix table.

## Live gate (the user runs)
1. Recorded runs over 10 Easy Apply jobs: `python -m assistant run --limit N`; after a fix, `python -m assistant requeue` to retry the Needs Attention jobs.
2. Pass when ≥ 7 of 10 are "Pending Review" as defined in the goal; the report shows on average ≤ 2 min and ≤ $0.05 per job; there are zero submissions; every job tab is open after the run.
