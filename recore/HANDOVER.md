# Re-core HANDOVER — end of P0

- **Phase:** P0 — rename "answer engine" → "LLM inference"; per-run/per-job Jev and LLM inference logs; per-job run folder. No behaviour change was the phase rule; several infrastructure changes were pulled in at the user's request (see Deviations).
- **Date:** 2026-09-28
- **Branch:** `recore/p0-rename-and-inference-logs`
- **Build spec:** v3.1

`recore/` did not exist; the user chose not to copy the phase files into the repo, so this HANDOVER.md is the only file in `recore/`. The phase files live in `~/Downloads/` (`00_common.md`, `P0_…` … `P5_…`).

## What changed

**Modules**
- `assistant/answers.py` → `assistant/llm_inference.py`; `prompts/answer_engine.md` → `prompts/llm_inference.md` (content unchanged). `AnswerEngineError` → `LLMInferenceError`, `ENGINE_TIMEOUT` → `LLM_INFERENCE_TIMEOUT`. Value/action names kept "answer" (`Question.answer`, `PageAnswers`, `check_answers`, `judge_answers`, `judge_questions`, `answers.json`, `call_engine`, `answer_page`).
- **New** `assistant/inference_log.py` (stdlib only): `start_run`, `set_scope`, `scope`, `gateway_of`, `log_llm` (§6.2), `log_jev` (§6.3). Module-level scope (not a contextvar, §6.4); atomic rewrite per append; strings scrubbed of lone surrogates.
- `decide.py`: `log_jev` in the retry loop; `decisions.jsonl` and its writer removed; state sent as a string (OpenRouter) / object (local Kev); `respan_questions` flattening for non-Jev decisions models; `adapt_questions_for`; `endpoint`/`start_hint`/`server_card` and a route-aware `Decider` (url, state limit, timeout, retries from the route) for the Kev commit.
- `llm_inference.py`: `log_llm` per HTTP attempt (json_schema, json_object fallback, transport error).
- `jev.py`: `clean_requests` extended to log the package's own System One / text-helper calls and to string-ify / flatten their bodies on the OpenRouter route; the goal agent points at the local Kev server on the "local" route.
- `cli.py`: `inference_log.start_run`; per-job `inference_log.scope`; `browser.calls_log` → `<scope>/browser_actions.jsonl`; `answers/<f>.json` → `<job>/answers.json`; `shots/<f>.jpg` → `<job>/screenshot.jpg`; `_probe_llm_inference`; `preflight()` is a generator that probes LLM inference, then the System One model, then Chrome.
- `report.py`: Jev counts from in-memory counters (no `decisions.jsonl`); names the decision model; omits a zero cost.
- `run_kev_server.command`: clones/updates `~/kev` and serves `kev-0.8b`.

**Config keys** (`config.toml`, strict)
- `models.<route>.answer_engine` → `models.<route>.llm_inference` (old key rejected with a message).
- `models.jev` / `models.jev_route` → `models.<route>.system_one_decision_model` / `models.system_one_decision_provider` (per-route, like `llm_inference`).
- New provider `"local"` with `[models.local]`: `base_url`, `system_one_decision_model`, `state_chars`, `timeout`. `config.local_key` (KEV_API_KEY, optional) and `KEYLESS_PROVIDERS` so preflight asks for no key on that route.

**Run folder** (`runs/<ts>/`): `_run/` for preflight+queue, `<job folder>/` per job, each with `jev_inference_logs.json`, `llm_inference_logs.json`, `browser_actions.jsonl`; `answers.json` and `screenshot.jpg` per job when produced. `decisions.jsonl`, `calls.jsonl`, `answers/`, `shots/` are gone.

## Live gate evidence

- **Preflight** `runs/20260928-220412/`: `_run/jev_inference_logs.json` (Kev probe, `kev-latest`, exact §6.3 keys) and `_run/llm_inference_logs.json` (`provider=vercel/mistral`, full messages+completion, exact §6.2 keys). No key strings in either.
- **Job run** `runs/20260928-221437/` (`--no-record --job` Linda AI): went to Needs Attention `load_failure` at the entry decision. `<job>/jev_inference_logs.json` (3 entries, exact §6.3 keys, object State) + `browser_actions.jsonl`; no forbidden files; nothing submitted; tab left open. No `llm_inference_logs.json`/`answers.json`/`screenshot.jpg` because the job never reached a form (no LLM call).
- **Numbers:** 1 job; 0 parked; 1 Needs Attention (`load_failure`); ≤ 3 System One requests for the entry; 0 LLM calls in the job; cost ≈ $0 (local Kev free; one Mistral probe in `_run`).
- **Offline:** 256 unit + 50 browser pass; `contract_check` clean; `run --dry-run` unchanged; T6 (`test_baseline_p0`) green.

## Known issues / open questions

- **kev-0.8b cannot classify a real LinkedIn posting.** On Linda AI it answered `kind="other"` at confidence 0.1238 with a flat distribution, *although* the "Easy Apply to this job" control was in the State it received (State len 3152, well under the 12000 cut). So no job gets past the entry decision with kev-0.8b. Root cause is model capacity/range (Kev's README caps trained state at ~384 tokens; the page state is ~3000). A live **end-to-end form-fill** (an LLM call + `answers.json` + `screenshot.jpg` inside a job folder) is therefore **proven only by unit/browser tests, not live**. Needs a stronger or fine-tuned System One model — P3/P4.
- **Paid Jev is unavailable on this account:** OpenRouter `typesafe/jev-1.13` → HTTP 402 (no credits); Vercel `typesafe-ai/jev` → HTTP 403 (free tier needs a card). Kev-local is the working route today.
- **All of build spec §13 (Jev request volume, provider limits, end-to-end) is still open** — P0 did not touch it; it is P1–P4.

## Deviations from the phase file (P0 said "no behaviour change")

Each was user-directed, to get past the credit blocker or on explicit request; all are in `DISCOVERY.md` with dates:
1. System One model moved Vercel → OpenRouter → local Kev (overrides settled decisions **D14** "Jev typesafe/jev-1.13, no `:free`" and **D19** "Jev only via the gateway").
2. System One `state` sent as a JSON string on the OpenRouter route (respan/span-01-lite rejects an object), kept an object on local Kev.
3. `respan_questions`: flatten question `instructions`/`criteria` to strings for non-Jev OpenRouter decisions models.
4. **LLM inference preflight probe** — a live check that the chat model answers. This is a P1 item pulled forward.
5. `preflight()` converted to a generator (each ✓ prints before a later check fails).
6. Config **role rename** `jev`/`jev_route` → `system_one_decision_model`/`system_one_decision_provider` (Jev is one instance of a System One decision model).
7. New **`[models.local]` Kev provider** (server, probe, `run_kev_server.command`) — new infrastructure, arguably P1 scope.
8. T6 baseline folds the deliberate screenshot-filename move; `test_decide`/`test_cli` assertions rewritten for the new names/shapes.

## Commands the next session needs

```bash
# env (venv + patched wheel), from Tools/Application_Assistant/
.venv/bin/python -m assistant preflight                     # LLM inference + System One + Chrome
./run_kev_server.command                                    # start local Kev before a run (downloads weights once)
.venv/bin/python -m assistant run --no-record --job <URL>   # fill + park one job, no writes
.venv/bin/pytest -m "unit or browser" -q                    # offline suite (throwaway Chrome on 9223)
.venv/bin/python -m assistant.contract_check                # package hooks still applied
```

Next phase is **P1** (`recore/P1_infrastructure.md`): paid routes, one model gateway, circuit breaker, spend cap, one Chrome connection per run. Note P1 assumes paid OpenRouter Jev; reconcile with the Kev-local route this phase added (decide whether Kev-local becomes the default System One provider or a fallback, and whether the credit issue is resolved).
