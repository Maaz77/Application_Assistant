# P0 — Rename to "LLM inference" and add the inference logs

Read `recore/00_common.md` first. **This phase changes no behaviour.** The program must make the same decisions, browser calls and model calls as v3. Only names, file locations and logging change.

## Goal
1. Rename the answer-engine component to "LLM inference" (00_common §7).
2. Log every HTTP attempt to Jev and to any chat model, per run and per job (00_common §6).
3. Move the run artifacts to the per-job layout (00_common §6.1) and remove `decisions.jsonl`.

## Out of scope
Decisions, prompt content (except names), retries, models, routes, the guard, the browser package. The other v3 problems belong to P1–P5.

## Tasks

### T0 — Baseline recording (before any change)
Run the offline suite with `FakeMCP` and `RuleDecider`. Record, per test flow, the ordered decisions (topic, question ids, answers used), the browser calls (function and arguments) and the model request bodies. Save them in `tests/golden/p0_baseline/`. T6 compares against this baseline.

### T1 — Rename (00_common §7)
- `git mv assistant/answers.py assistant/llm_inference.py`; `git mv prompts/answer_engine.md prompts/llm_inference.md`; fix the imports.
- Find every name of the component: `grep -rniE "answer.?engine|AnswerEngine|ENGINE_TIMEOUT" assistant tests prompts config.toml *.md`. Apply the §7 rule. List every rename in the `DISCOVERY.md` entry.
- Config: `[models.openrouter] llm_inference = [...]` and `[models.vercel] llm_inference = [...]`; the code reads `cfg.models.llm_inference`. An old `answer_engine` key fails validation with: `models.<route>.answer_engine was renamed to models.<route>.llm_inference`.
- Needs Attention class `answer_engine` → `llm_inference` (report, `job.md` notes, tracker Notes). `requeue` still removes any old "Needs Attention: …" Notes line.
- Report and terminal text: "answer engine" → "LLM inference".
- Docs: rename in README.md, CLAUDE.md, LIVE_TEST.md and the build spec. Add the dated entry to DISCOVERY.md; old entries stay unchanged.

### T2 — `assistant/inference_log.py`
Standard library only.
- `start_run(run_dir: Path)`; `set_scope(name: str | None)` (None → `_run`); context manager `scope(name)`.
- `log_llm(gateway: str, request_body: dict, response: dict | str | None, reason: str | None)` → one entry of 00_common §6.2.
- `log_jev(request_body: dict, response: dict | str | None, reason: str | None)` → one entry of 00_common §6.3.
- One in-memory list per (scope, file). After each append, rewrite the file atomically (§6.4). One `threading.Lock`. The scope is a module-level value (§6.4).
- `provider`: `f"{gateway}/{upstream}"`, where `upstream` is what the gateway reports in the response (OpenRouter: the top-level `provider` field; Vercel: the provider in `provider_metadata`, if present); otherwise just `gateway`. The gateway comes from the URL host (`openrouter.ai` → `openrouter`, `ai-gateway.vercel.sh` → `vercel`).
- `completion`: `choices[0].message.content` (if it is a list of parts, join the text parts). An HTTP error → the raw body text. An HTTP 200 with an error body → the raw body text. No body → `"<no response body: <reason>>"`.
- `parameters`: the request body without `model` and `messages`.

### T3 — Log every sender (each attempt, retries included)
1. `decide.Decider` posts, every topic, preflight included → `log_jev`.
2. `llm_inference.py` chat calls: every model of the rotation, every regeneration, the HTTP-400 retry with `json_object` → `log_llm`.
3. The package's only sender, `server.policy._post(url, key, body)`, already wrapped by the `clean_requests` hook: extend the wrapper. A body with `state` and `questions` → `log_jev`; a body with `messages` (the text helper) → `log_llm`. Find out whether the package retries by calling `_post` again (then each attempt is logged) or inside `_post` (then only the last attempt is visible — record this in DISCOVERY.md).

### T4 — Run folder (00_common §6.1)
- `cli` creates `runs/<ts>/` and `runs/<ts>/_run/`, calls `inference_log.start_run`, runs preflight in scope `_run`, and wraps each job in `inference_log.scope(<job folder name>)`. The job's folder under `runs/<ts>/` is created when the job starts.
- `calls.jsonl` → `browser_actions.jsonl` in the active scope folder, with the same content (every call and its full result).
- `answers/<folder>.json` → `<job folder>/answers.json`; `shots/<folder>.jpg` → `<job folder>/screenshot.jpg`.
- Remove `decisions.jsonl` and its writer. "Decisions by Jev: N calls, $X" in the report now comes from in-memory counters (the same numbers).
- `report.md` keeps all its sections and wording, except the rename and the new file paths.

### T5 — Tests (add; do not weaken existing ones)
- `test_inference_log.py`:
  - the exact key sets of both entry types;
  - a 50,000-character message and completion are logged in full;
  - Jev questions grouped by type, each with its `id`; empty types are `[]`;
  - failed attempts: a 429 body, a 503 body, a timeout with no body, an HTTP 200 with an error body;
  - each file is valid JSON after every append;
  - scope switching; a call from another thread lands in the active scope;
  - no string matching `sk-`, `Bearer ` or any `.env` value appears in any log.
- `test_rename.py`: nothing matches `answer.?engine|AnswerEngine` in `assistant/`, `tests/` (except this test), `prompts/`, `config.toml`, README.md, CLAUDE.md, LIVE_TEST.md or the build spec; the old config key is rejected with the message of T1.
- Update existing tests only for the new names and paths.

### T6 — No behaviour change
The offline suite reproduces `tests/golden/p0_baseline/` exactly: decisions, browser calls and model request bodies, ignoring the renamed identifiers.

## Acceptance criteria
- A1 `pytest -m "unit or browser"` passes; `python -m assistant run --dry-run` prints the same queue as before.
- A2 T6 holds.
- A3 The rename is complete and documented.
- A4 The live gate passes.
- A5 Docs updated (00_common §9): build spec v3.1; `recore/HANDOVER.md` written.

## Live gate (the user runs)
1. `python -m assistant preflight` → it runs; `runs/<ts>/_run/jev_inference_logs.json` holds the preflight Jev call (successful or failed).
2. `python -m assistant run --no-record --job <one Easy Apply job URL>`.
3. In `runs/<ts>/<job folder>/`:
   - every entry of `jev_inference_logs.json` has exactly `State`, `Score`, `Noul`, `Choice`, `Response`; the State is the full text; failed attempts show their error body;
   - every entry of `llm_inference_logs.json` has exactly `model`, `provider`, `parameters`, `messages`, `completion`; the full prompt and page text are in `messages`, and the full output is in `completion`;
   - `browser_actions.jsonl` exists; `answers.json` exists if a form page was reached; `screenshot.jpg` exists if the job parked.
4. `report.md` has the same sections as before.
5. Nothing was submitted, and the job tab is still open.

The job does not have to park in P0: the v3 problems are still there.
