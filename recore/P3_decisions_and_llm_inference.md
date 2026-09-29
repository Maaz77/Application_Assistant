# P3 — Replay harness, facts in code, lean Jev, new LLM inference → the first parked job

Read `recore/00_common.md` and `recore/HANDOVER.md` first.

## Goal
1. A replay harness that re-runs recorded jobs offline.
2. Facts come from code; Jev answers only residual ambiguity (D19).
3. LLM inference receives a question list extracted by code and returns only answers (D20).
4. Park at an unanswered required question (D9).
5. **Gate: one LinkedIn Easy Apply job parked end-to-end** (D8).

## T1 — Replay harness (do this first)
- `browser_actions.jsonl` must hold every observe/act call with its **full** result (table, text, probes), so each page can be rebuilt.
- `tests/replay/`: `ReplayBrowser` returns the recorded results in order. `ReplayGateway` answers model requests from the run's `jev_inference_logs.json` and `llm_inference_logs.json`, matched on the full request content (Jev: `State` + questions; LLM: `messages` + `parameters`).
- Modes: `strict` — a request missing from the logs fails the test; `live` — a miss calls the real API (`--live` only, within the session budget) and saves the new response into the fixture.
- `python -m assistant replay <run folder>/<job folder>` prints the decisions, the requests per page, and the funnel.
- Funnel per job (also rows in the report Summary): opened → Easy Apply dialog → steps filled → advanced → final reached → parked (final | at question) or Needs Attention class.
- Copy the user's P2 gate runs into `tests/replay/fixtures/<name>/` as the first fixtures.

## T2 — Facts in code (delete the matching Jev questions)

| Fact | Code rule | Jev question deleted |
|---|---|---|
| the application's fields | controls whose `scope` is the Easy Apply dialog | `field_<ref>` (44 % of v3 questions) |
| required | the element's `required` field + the `REQUIRED_EMPTY` probe | — |
| placeholder value | an empty value; text matching `^(select( an option)?\|choose\|please select\|--)` (case-insensitive); or the first disabled option | `placeholder` |
| read-back | compare each field's value after filling with the intended value (whitespace/case normalized; selects by option label; radios and checkboxes by `checked`) | topic `readback` |
| settle | two equal observe hashes 300 ms apart, at most 5 s | the per-read `loading` judgments |
| signed out | URL rules (build spec §6.1) + a LinkedIn sign-in form | — |
| closed / applied | LinkedIn texts (P2) | `closed`, `applied` on LinkedIn |
| captcha | the `CAPTCHA_PRESENT` probe | kind `captcha` on LinkedIn |
| final step | a refused "Submit application" in the dialog and no advance button | — (confirmed by Jev, see T3) |
| resume / cover-letter input | a file input or "Upload resume" button under a "Resume" heading; a "Cover letter" heading | `resume_input`, `cover_<i>` when code finds exactly one |

## T3 — What Jev still answers (one request per page state)
- `must_not_generate` and total-vs-specific years for the LLM inference answers (build spec §7.4): one request per form page;
- option mapping, **only** when an answer does not exactly match a visible option: `choice` over the options plus `none`;
- the final-step check before parking: `noul` "Is this the last step before the application is sent?" ≥ 0.5, together with the code rule;
- `kind` (build spec §6.5 PAGE_KINDS), only on a page that is neither a LinkedIn job page nor the Easy Apply dialog;
- the resume/cover-letter input when code finds zero or several candidates.

Rules:
- **Cache** judgments by page-state hash = sha256(URL + ordered control signatures (role, name, value, checked) + page-text hash). The same hash reuses the judgment.
- Ask only the questions the current step needs, all in one request.
- **Budget:** `jev.max_requests_per_job = 40`, Jev and fallback-decider requests together. Above it → Needs Attention `decision_budget`.
- Delete the per-read `judge` of every snapshot, the `settle` judgments, and the `readback`, `questions` and `fill` topics that code now covers. `plan_fill` remains only for option mapping.

## T4 — New LLM inference (D20)
- `extract_questions(page)` (code): for each application field or field group in the dialog — `id` (the ref, or a hash of the label for a group), `question` (the full label or legend), `kind` (text | longtext | number | select | radio | checkbox | combobox | date | file), `options` (visible labels), `required`, `current_value`, `maxlength`.
- Request: `prompts/llm_inference.md`, rewritten but keeping every rule of build spec §7.3 (facts only from the sources, with one exact quote; `generated` only for motivation/description questions; `computed` only for total years; the never-guess list; the pre-fill rules; a choice is exactly one option; the phone gets the local number) + JSON `{"questions": [...], "sources": {"profile": ..., "job": ..., "resume": ...}}`.
- Strict output schema: `{"answers": [{"id", "answer", "source", "quote", "relies_on"}]}`, with `source` ∈ profile | job | resume | generated | computed | linkedin-prefill | null.
- One call per dialog step, plus at most one regeneration for `generated` answers. Keep every check of build spec §7.4 (quotes, choices, generated, computed recompute, pre-fill, keep-if-silent). `answers.json` holds the checked result.

## T5 — Park at an unanswered required question (D9)
If, after the checks, a required field has no valid answer and no kept value:
- fill every other field of the step; do **not** advance;
- run the gate without the `REQUIRED_EMPTY` rule for the listed fields; take the screenshot;
- record: tracker Status "Pending Review", Notes `Answer before you submit: <q1>; <q2>`; a `job.md` entry `## <date time> — Pending Review (parked at a question)` with the step, the questions and what was filled; in the report, under Parked with "(at a question)", and the questions under "Questions for your Scratch Pad";
- move the folder to `Pending-Review/`, as for any park.

## T6 — Tests
- Replay: the P2 gate runs replay in `strict` mode with zero network calls.
- Each code fact of T2 on fixtures; the cache (the same hash sends no new request); the budget stop.
- `extract_questions` on the Easy Apply fixtures (labels, groups, options, required).
- Park-at-question end to end on fixtures, with temp records.
- Request counts on the Easy Apply fixture flow: ≤ 5 Jev requests per page; ≤ 1 LLM inference call per page (+ ≤ 1 regeneration).

## Acceptance criteria
- A1 The offline suite, tripwire and replay pass.
- A2 The T6 request counts hold offline.
- A3 **Live gate: at least one Easy Apply job parked at the final step and recorded correctly.**
- A4 Docs: build spec v4.1 (§6 and §7 rewritten); `recore/HANDOVER.md` written.

## Live gate (the user runs)
1. `python -m assistant run --limit 3` — a recorded run (use Easy Apply jobs).
2. For at least one job:
   - the tab shows the Easy Apply review step with "Submit application" visible and not clicked;
   - the tracker row is "Pending Review", the folder is in `Pending-Review/`, and `job.md` has the note;
   - `report.md`: ≤ 5 Jev requests per page on average and ≤ 40 per job; ≤ 1 LLM inference call per page (+ regenerations); time and cost per job.
3. A job parked at a question lists only questions that `Profile.md` and its Scratch Pad do not answer.
4. Send back the run folder; the agent adds it to the replay fixtures.
