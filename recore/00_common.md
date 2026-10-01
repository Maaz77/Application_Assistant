# Application Assistant re-core — common rules

Read this file first in every re-core session, then the phase file you were given. The phases run in order. Each phase is one fresh coding-agent session.

| File | Phase goal | Live gate (the user runs it) |
|---|---|---|
| `P0_rename_and_inference_logs.md` | Rename "answer engine" → "LLM inference"; per-job Jev and LLM inference logs; no behaviour change | Correct logs for one real job |
| `P1_infrastructure.md` | Paid OpenRouter routes, one model gateway, circuit breaker, spend cap, one Chrome connection per run | Clean run of 3 jobs with one "Allow" click |
| `P2_driver_guard_navigation.md` | Owned browser driver, absolute never-submit guard, deterministic Easy Apply navigation | Easy Apply opened and advanced with no browser agent |
| `P3_decisions_and_llm_inference.md` | Replay harness, facts in code, lean Jev, new LLM inference, park at a question | One Easy Apply job parked end-to-end |
| `P4_easy_apply_reliability.md` | Widgets, resume step, validation messages, speed | ≥ 7 of 10 parked; ≤ 2 min and ≤ $0.05 per job |
| `P5_external_ats.md` | Greenhouse, Ashby and Lever forms | ≥ 5 of 10 external jobs parked (proposal) |

## 1. How the re-core runs

1.1 Setup, once: put all `recore/*.md` files in `Tools/Application_Assistant/recore/` and commit them. Add this line near the top of `CLAUDE.md`: "During the re-core, read `recore/00_common.md` and the current phase file before any other work."

1.2 Start of a phase — the user opens a new session and sends:

    Read recore/00_common.md and recore/<phase file>. Follow §5 of 00_common.md. Post your plan first.

1.3 The loop inside a phase:
1. The agent implements the tasks and makes the offline tests pass.
2. The agent ends its turn with a LIVE TEST REQUEST (§5.3).
3. The user runs it on real jobs and replies with the run folder path (`runs/<ts>`), the terminal output, and what he saw in Chrome.
4. The agent reads the run folder (§5.4), fixes the code, and goes back to step 1.

1.4 A phase ends when every acceptance criterion in its phase file holds **and** the user confirms the live gate. The agent then updates the docs (§9) and writes `recore/HANDOVER.md` (§8). The next phase starts in a new session.

## 2. Context

`Tools/Application_Assistant/` is a Python program. For every job at Status "Resume Built" in `Job_Tracker.numbers` (sheet "Jobs"), it fills the job application in the user's own signed-in Chrome, **stops one click before submission**, leaves the tab open, and records the result: the tracker row, the job folder move (`Applications/` → `Pending-Review/` or `Needs-Attention/`), and a note in `job.md`. The user reviews and submits every application himself.

Documents in the repo:
- `application_assistant_build_spec.md` — the code as built (v3, 2026-09-24 when the re-core starts). §13 lists the problems the re-core fixes.
- `DISCOVERY.md` — dated findings with evidence.
- `LIVE_TEST.md` — how the user runs live tests.
- `recore/HANDOVER.md` — the state the previous phase left (written at the end of each phase, first at the end of P0).

Why the re-core (evidence: build spec §13). No real run has parked a job. One run sent 222 Jev requests and 21 agent goals in 19 minutes. 44 % of the Jev questions asked "is this field the application's own?" for every field. The package's `browser_goal` sent large requests that could not be batched (13 of 21 goals failed with HTTP 503). Retries were nested (up to ~18 HTTP requests per goal step). Free chat models were weak or out of quota. The never-submit rule was narrowed. The program depended on the internals of a patched third-party package.

Target design: **code decides facts; Jev answers only what stays ambiguous; one LLM inference call per form page writes the answers; a structural guard makes submission impossible; one owned browser driver.**

## 3. Settled decisions

Do not reopen these. If a task seems to need a change, stop and ask the user.

| # | Topic | Decision |
|---|---|---|
| D1 | Phasing | P0 → P5 as in the table above |
| D2 | Rename | "answer engine" → "LLM inference", for the component only (§7) |
| D3 | Log location | `runs/<ts>/<job folder>/` per job; `runs/<ts>/_run/` for calls outside a job |
| D4 | LLM log content | every chat-model call: LLM inference, regenerations, the package's text helper (until P2), the Jev fallback backend (from P1) |
| D5 | Failed calls | every HTTP attempt is logged; the error body takes the place of the completion or the response |
| D6 | Log entry shapes | exactly §6.2 and §6.3 |
| D7 | Run folder | §6.1; `decisions.jsonl` is removed |
| D8 | Gate targets | P3: one Easy Apply job parked end-to-end. P4: ≥ 7 of 10 parked, ≤ 2 min and ≤ $0.05 per job |
| D9 | Required question with no answer in the files | fill everything else on that step, park the tab at that step, Status "Pending Review", Notes list the questions |
| D10 | Never-submit | absolute (§4.1) |
| D11 | Google one-click sign-in | allowed, with the Google account already signed in to the Chrome profile (`google.account_email`) |
| D12 | Scope before P5 | LinkedIn Easy Apply only; a job whose apply control leads off LinkedIn → Needs Attention, class `external_ats`, "external ATS, not yet supported" |
| D13 | Chrome | the user's **real** Chrome profile in `chrome://inspect` remote-debugging mode; the user clicks "Allow remote debugging?"; the program opens **exactly one** CDP connection per run |
| D14 | Models (OpenRouter, paid) | Jev `typesafe/jev-1.13`; LLM inference `deepseek/deepseek-v4.1-flash`, fallback `openai/gpt-5.4-mini`; no `:free` models |
| D15 | Spend cap | a run stops cleanly when its model spend reaches $1.00 |
| D16 | Privacy | any provider is allowed (no data-collection restriction) |
| D17 | Coding agent | Claude Code; one session per phase |
| D18 | Driver | an owned driver built from the vendored package's CDP code (MIT), not Playwright (reason in P2) |
| D19 | Jev use | only for residual ambiguity; cached by page state; ≤ 40 decision requests per job; a chat-model fallback backend when Jev fails |
| D20 | LLM inference input | from P3, code extracts the question list; the model returns only per-question answers |
| D21 | Outages | a circuit breaker stops the run cleanly; the current job is left untouched |
| D22 | Unchanged from spec v3 | upload the tailored resume; never submit; no tab groups (C1); never write `Profile.md`; tracker I/O through `Tools/Reconcile/reconcile.py`; C10, C15, C16, C23 |

## 4. Invariants (every phase)

4.1 **Never submit.** No code path may click, press or trigger a control that sends an application. Until P2 lands, the v3 rule (build spec §4.2) stays and must not be weakened. From P2 the rule is absolute:
- refused labels (case-insensitive, whole words): submit, send, confirm, done, finish, complete; and apply once filling has started;
- refused structure: a control that would submit a form (`type=submit`, a `<button>` without `type` inside a `<form>`, `<input type=image>`), unless its label is on the advance allowlist (Next, Continue, Continue to next step, Review, Review your application, Save and continue) **and** the page is not final;
- on a page judged final, every click is refused (the program parks);
- no Enter, NumpadEnter or Escape key event, ever; `type` never submits;
- the only exemption: a control inside a known cookie-consent container that is outside the application form and dialog, whose label is not submit, send or apply.

4.2 Never write `Profile.md`. Tests use temp copies of the tracker, the job folders and the profile.

4.3 Tracker I/O goes through `Tools/Reconcile/reconcile.py`; its behaviour does not change.

4.4 **The agent never runs the program against real websites** (`python -m assistant run`, `preflight`, or `capture` on real URLs). Those are the user's live tests. The agent may run unit tests, fixture browser tests (a throwaway headless Chrome on local fixtures), and model-API tests on local fixtures (`pytest --live`), up to **$0.50 of model spend per session**. It asks the user before it spends more.

4.5 Tests: the agent may add tests and fixtures. It must not delete, skip or weaken any guard, tripwire, never-submit or records test; if one fails, fix the code. Other tests change only when the phase file changes that behaviour, and the change goes into `DISCOVERY.md`.

4.6 Secrets: `.env` is never printed, logged or committed. No log contains an API key or an `Authorization` header. `runs/` stays git-ignored.

## 5. Development loop

5.1 Session start. Read, in this order: this file; the phase file; `recore/HANDOVER.md` (if present); `CLAUDE.md`; the build spec; the last 10 entries of `DISCOVERY.md`. Post a short plan (tasks in order, files to touch, tests to add) before you edit code.

5.2 Work rules.
- Branch `recore/p<N>-<slug>`; one commit per task, with a clear message.
- Keep the program runnable at every commit: `python -m assistant run --dry-run` works and the offline tests pass.
- Run `pytest -m "unit or browser"` before every LIVE TEST REQUEST.
- Every decision that is not in the phase file, and every surprise, goes into `DISCOVERY.md` (date, what, why, evidence).

5.3 End your turn with this block whenever you need a live test:

    LIVE TEST REQUEST (P<N>, iteration <k>)
    Before you start: <setup, or "nothing">
    Commands:
      <exact commands, one per line>
    Check:
      - <observable result 1>
      - <observable result 2>
    Send back: the run folder path, the terminal output, and what you saw in Chrome.

5.4 When the user sends a run folder, read `report.md`, then, for `_run/` and each job folder: `jev_inference_logs.json`, `llm_inference_logs.json`, `browser_actions.jsonl`, `answers.json` and the screenshot. Base every diagnosis on evidence, and quote the log entry you rely on.

5.5 Phase exit: all acceptance criteria hold and the user confirms the gate → update the docs (§9), write `recore/HANDOVER.md` (§8), and tell the user that the branch is ready to merge.

## 6. Run folder and inference logs (from P0)

6.1 Layout

    runs/<YYYYMMDD-HHMMSS>/
      report.md                   # the sections of build spec v3 §8.5
      journal.jsonl               # records journal (crash recovery)
      tracker-backup.numbers
      _run/                       # everything outside a job (preflight, queue)
        jev_inference_logs.json
        llm_inference_logs.json
        browser_actions.jsonl
      <job folder name>/          # e.g. 4012345678_Acme_ML_Engineer, one per processed job
        jev_inference_logs.json
        llm_inference_logs.json
        browser_actions.jsonl     # every browser call and its full result (was calls.jsonl)
        answers.json              # the checked answers (was answers/<folder>.json)
        screenshot.jpg            # the parked page (was shots/<folder>.jpg)

`decisions.jsonl`, `calls.jsonl`, `answers/` and `shots/` no longer exist. `report.md` keeps its sections; phases may add rows inside them.

6.2 `llm_inference_logs.json` — a JSON array with one entry per HTTP attempt to a chat model. Each entry has **exactly** these keys:

```json
{
  "model": "<the model that answered (response.model), else the requested model>",
  "provider": "<gateway>/<upstream provider the gateway reports>, or <gateway> if none is reported",
  "parameters": {"<every request-body field the program set, except model and messages>": "..."},
  "messages": [
    {"role": "system", "content": "<full text, exactly as sent>"},
    {"role": "user", "content": "<full text, exactly as sent>"}
  ],
  "completion": "<full text of choices[0].message.content>"
}
```

For a failed attempt, `completion` is the raw error body; for an HTTP 200 with an error body, that body; with no body, `"<no response body: <reason>>"` (for example `timeout after 45 s`).

6.3 `jev_inference_logs.json` — a JSON array with one entry per HTTP attempt to Jev. Each entry has **exactly** these keys:

```json
{
  "State": "<the request state, exactly as sent>",
  "Score": [{"id": "<question id>", "...": "the score question, exactly as sent"}],
  "Noul": [{"id": "<question id>", "...": "the noul question, exactly as sent"}],
  "Choice": [{"id": "<question id>", "...": "the choice question, exactly as sent"}],
  "Response": {"...": "the response body, parsed as JSON"}
}
```

If the request keys its questions by id, a logged question is `{"id": <key>, …fields as sent}`. A type with no questions is `[]`. `Response` is a string if the body is not JSON, and `"<no response body: <reason>>"` if there is none. No other keys: no model, timing, topic or cost.

6.4 Rules for both logs
- Write after **every** attempt: rewrite the whole file to `<name>.tmp`, then `os.replace`, so the file is valid JSON at all times.
- `ensure_ascii=False`, `indent=2`; strings pass `clean_text` (lone surrogates → `?`) first.
- The scope (a job folder name or `_run`) is a module-level value set by the orchestrator. Jobs run strictly in sequence and browser work may run on another thread, so do not use a contextvar.
- Never log headers, keys or `.env` values.
- A request that is not in a log did not happen: every sender logs.

## 7. Naming: "LLM inference"

The component formerly called "answer engine" is "LLM inference" everywhere: code, config, prompts, tests and docs.
- `answers.py` → `llm_inference.py`; `AnswerEngineError` → `LLMInferenceError`; `ENGINE_TIMEOUT` → `LLM_INFERENCE_TIMEOUT`; config `models.<route>.answer_engine` → `models.<route>.llm_inference`; `prompts/answer_engine.md` → `prompts/llm_inference.md`; Needs Attention class `answer_engine` → `llm_inference`. Any other function, class or variable that names the component follows the same pattern.
- Names for answer **values** keep "answer": `Question.answer`, `PageAnswers`, `check_answers`, `judge_answers`, `judge_questions`, "unanswered", `answers.json`.
- External API names never change (for example the `answers` field in Jev's response).
- `DISCOVERY.md` keeps the old names in old entries; one dated entry records the rename.

## 8. `recore/HANDOVER.md` (rewrite at the end of each phase)

- Phase, date, branch, build spec version.
- What changed: modules, config keys, CLI.
- Live gate evidence: run folders and key numbers (jobs; parked; Needs Attention by class; Jev requests per job; LLM calls per page; cost; time).
- Known issues and open questions, with evidence.
- Deviations from the phase file, and why.
- The exact commands the next session needs.

## 9. Documentation duties (each phase)

- `application_assistant_build_spec.md`: rewrite the changed sections so the spec again describes the code as built. Versions: P0 → v3.1, P1 → v3.2, P2 → v4.0, P3 → v4.1, P4 → v4.2, P5 → v5.0. Update §13 (open problems).
- `DISCOVERY.md`: one dated entry per decision or finding.
- `LIVE_TEST.md`: the gate procedure of the phase.
- `README.md` and `CLAUDE.md`: commands and names that changed.
