"""T9: --dry-run on a temp workspace prints the right queue and writes nothing; CLI plumbing."""
import re
import hashlib
import shutil
from pathlib import Path

import pytest

from assistant import cli
from assistant.tracker import RESUME_BUILT, Tracker
from tests.test_records import REAL_TRACKER, make_job

pytestmark = pytest.mark.unit


def snapshot(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "dir"
            for p in sorted(root.rglob("*"))}


@pytest.fixture
def workspace(tmp_path):
    if not REAL_TRACKER.exists():
        pytest.skip("no Job_Tracker.numbers to copy")
    base = tmp_path / "base"
    (base / "Applications").mkdir(parents=True)
    shutil.copy2(REAL_TRACKER, base / "Job_Tracker.numbers")
    (base / "Profile.md").write_text("# Resume\n")
    t = Tracker(base / "Job_Tracker.numbers").load()
    t.add({"Job URL": "https://www.linkedin.com/jobs/view/4100000001/", "Status": RESUME_BUILT})
    t.add({"Job URL": "https://www.linkedin.com/jobs/view/4100000005/", "Status": RESUME_BUILT})
    t.save()
    make_job(base / "Applications", "4100000001", "Acme", "Data Engineer")
    make_job(base / "Applications", "4100000009", "Gamma", "ML Engineer")
    cfg = tmp_path / "config.toml"
    cfg.write_text(Path(cli.config_mod.DEFAULT_CONFIG).read_text().replace('base = "../.."', f'base = "{base}"'))
    return base, cfg


def test_dry_run_prints_queue_and_writes_nothing(workspace, capsys):
    base, cfg = workspace
    before = snapshot(base.parent)
    runs_before = snapshot(cli.RUNS) if cli.RUNS.exists() else {}
    assert cli.main(["--config", str(cfg), "run", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Queue (2 jobs)" in out and "Acme – Data Engineer" in out and "Gamma – ML Engineer" in out
    assert "(new tracker row)" in out and "4100000005" in out          # anomaly: row without folder
    assert snapshot(base.parent) == before
    assert (snapshot(cli.RUNS) if cli.RUNS.exists() else {}) == runs_before


def test_dry_run_limit_and_job(workspace, capsys):
    base, cfg = workspace
    cli.main(["--config", str(cfg), "run", "--dry-run", "--limit", "1"])
    assert "Queue (1 job)" in capsys.readouterr().out
    cli.main(["--config", str(cfg), "run", "--dry-run", "--job",
              "https://www.linkedin.com/jobs/view/4100000009/?trk=x"])
    out = capsys.readouterr().out
    assert "Queue (1 job)" in out and "Gamma" in out


def test_a_job_that_is_not_queued_says_why_and_is_exit_1(workspace, capsys):
    """Live 2026-10-06: `run --job <a job in Needs-Attention/>` printed seven green preflight ticks, opened no
    tab, wrote an empty report and exited 0 — nothing said the queue was empty. `--job` filters the queue, it
    does not bypass it, so a job outside Applications/ matches nothing."""
    base, cfg = workspace
    make_job(base / "Needs-Attention", "4100000077", "Delta", "Backend Engineer")
    assert cli.main(["--config", str(cfg), "run", "--dry-run", "--job", "4100000077"]) == 1
    out = capsys.readouterr().out
    assert "Queue (0 jobs)" in out
    assert "no queued job has that id" in out
    assert "Needs-Attention/" in out and "requeue --job 4100000077" in out      # names the way out


def test_an_unknown_job_id_says_to_check_the_url(workspace, capsys):
    base, cfg = workspace
    assert cli.main(["--config", str(cfg), "run", "--dry-run", "--job", "4109999999"]) == 1
    assert "check the URL" in capsys.readouterr().out


def test_a_job_parked_for_review_is_named_as_already_parked(workspace, capsys):
    base, cfg = workspace
    make_job(base / "Pending-Review", "4100000088", "Epsilon", "SRE")
    assert cli.main(["--config", str(cfg), "run", "--dry-run", "--job", "4100000088"]) == 1
    assert "Pending-Review/" in capsys.readouterr().out


def test_a_job_whose_row_is_not_resume_built_names_the_status(workspace, capsys):
    base, cfg = workspace
    t = Tracker(base / "Job_Tracker.numbers").load()
    t.set("4100000001", "Interviewing")
    t.save()
    assert cli.main(["--config", str(cfg), "run", "--dry-run", "--job", "4100000001"]) == 1
    assert "'Interviewing'" in capsys.readouterr().out


def test_a_queued_job_still_matches_and_is_exit_0(workspace, capsys):
    """The guard must not fire on the happy path."""
    base, cfg = workspace
    assert cli.main(["--config", str(cfg), "run", "--dry-run", "--job", "4100000009"]) == 0
    out = capsys.readouterr().out
    assert "Queue (1 job)" in out and "no queued job has that id" not in out


def test_a_queue_that_skipped_every_job_is_not_silent(workspace, capsys):
    """Anomalies used to reach the terminal on --dry-run only, under an "Anomalies:" heading a real run never
    printed. Now both runs print one line each, so a run that skipped everything says so."""
    base, cfg = workspace
    assert cli.main(["--config", str(cfg), "run", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "⚠ queue anomaly:" in out and "4100000005" in out


def test_nothing_matched_makes_the_report_exit_1(workspace):
    """The code path a real run takes: the queue check sets Report.nothing_matched, and the report's own
    exit_code turns it into 1 instead of "all parked"."""
    from assistant.report import Report
    from datetime import datetime
    r = Report(datetime.now())
    assert r.exit_code() == 0
    r.nothing_matched = "its folder is in Needs-Attention/"
    assert r.exit_code() == 1
    assert "No job matched `--job`" in r.markdown()


def test_run_without_key_fails_preflight_with_exit_1(workspace, monkeypatch, capsys):
    """The FreeLLMAPI router answers HTTP 401 without a key, so a missing FREELLMAPI_KEY must stop the run before
    anything is written rather than failing the first page. It is the one key a run requires: the Kev decision
    server is open unless it was started with KEV_API_KEY."""
    base, cfg = workspace
    monkeypatch.setattr(cli.config_mod, "chat_key", lambda *a: "")
    assert cli.main(["--config", str(cfg), "run"]) == 1
    out = capsys.readouterr().out
    assert out.count("FREELLMAPI_KEY is missing") == 1 and "KEV_API_KEY" not in out


def test_bad_config_is_exit_1(tmp_path, capsys):
    bad = tmp_path / "c.toml"
    bad.write_text('[paths]\nbase = "x"\nnope = 1\n')
    assert cli.main(["--config", str(bad), "preflight"]) == 1


def test_the_llm_inference_probe_asks_every_configured_model(monkeypatch):
    """P1 T1: each configured model, not just the first that answers, so the report names the ones that are out.
    One trivial empty page each."""
    cfg = cli.config_mod.load()
    seen = []
    monkeypatch.setattr(cli, "call_engine",
                        lambda **kw: seen.append((kw["url"], list(kw["models"].models))) or None)
    assert cli._probe_llm_inference(cfg) == (list(cfg.models.llm_inference), [])
    assert [m for _, ms in seen for m in ms] == list(cfg.models.llm_inference)
    assert {url for url, _ in seen} == {cli.config_mod.chat_url(cfg)}


def pinned_cfg(*models):
    """A config with several concrete model IDs pinned, instead of the shipped `auto`. The probe's per-model
    behaviour is still worth testing: pinning a list is still supported, it is just not what we ship."""
    cfg = cli.config_mod.load()
    return cfg.model_copy(update={"models": cfg.models.model_copy(update={
        "freellmapi": cfg.models.freellmapi.model_copy(update={"llm_inference": models})})})


def test_one_model_out_of_quota_is_reported_but_does_not_fail_preflight(monkeypatch):
    """The FreeLLMAPI router's free tiers go in and out of quota minute by minute (live 2026-10-07), and a run
    only ever needs one model per page — that is what the rotation is for. So a single 429 is reported, not fatal.
    Before 2026-10-07 every cloud model was paid and one failure did fail preflight."""
    cfg = pinned_cfg("alpha", "beta", "gamma")
    dead = cfg.models.llm_inference[-1]

    def fake(**kw):
        if dead in kw["models"].models:
            raise cli.LLMInferenceError("HTTP 429 All models exhausted")
    monkeypatch.setattr(cli, "call_engine", fake)
    answered, out = cli._probe_llm_inference(cfg)
    assert dead not in answered and answered == [m for m in cfg.models.llm_inference if m != dead]
    assert len(out) == 1 and out[0].startswith(dead) and "429" in out[0]


def test_models_out_of_quota_do_not_trip_the_runs_breaker(monkeypatch):
    """Regression (2026-10-07): `call_engine` reports one failed request per model that cannot answer, so probing
    five models fed five verdicts into the run's breaker. Three failures in a row is a D21 ProviderOutage — and
    three configured models being out of quota at once is ordinary on free tiers, two of the five being
    neighbours — so preflight exited 3 instead of reporting them, defeating the relaxation above, and the models
    after the third were skipped even when they would have answered. Each model is now probed on a Gateway of its
    own, and the run's is restored."""
    from assistant import gateway as gateway_mod
    cfg = pinned_cfg("m1", "m2", "m3", "m4", "m5")
    models = list(cfg.models.llm_inference)
    monkeypatch.setattr(cli.config_mod, "chat_key", lambda *a: "k")

    def post(url, body, headers, timeout):
        if body["model"] in models[1:4]:                 # three neighbours out of quota
            return 429, {"error": {"message": "All models exhausted", "type": "rate_limit_error"}}
        return 200, {"choices": [{"message": {"content": '{"answers": []}'}}]}

    run_gateway = gateway_mod.for_config(cfg, post=post, sleep=lambda s: None)
    gateway_mod.use(run_gateway)
    try:
        answered, out = cli._probe_llm_inference(cfg)
    finally:
        gateway_mod.use(None)
    assert answered == [models[0], models[4]] and len(out) == 3
    assert run_gateway.tripped is None                   # the run's breaker never saw the probe's failures
    assert gateway_mod.current() is None                 # and the run's Gateway was put back


def test_a_rejected_key_during_the_probe_still_reaches_preflight(monkeypatch):
    """The probe's own Gateway must not swallow a CreditOrKey: P1 T1 wants preflight to name the key."""
    from assistant import gateway as gateway_mod
    cfg = cli.config_mod.load()
    monkeypatch.setattr(cli.config_mod, "chat_key", lambda *a: "bad")
    gateway_mod.use(gateway_mod.for_config(
        cfg, post=lambda *a: (401, {"error": {"message": "Invalid API key"}}), sleep=lambda s: None))
    try:
        with pytest.raises(gateway_mod.CreditOrKey):
            cli._probe_llm_inference(cfg)
    finally:
        gateway_mod.use(None)


def test_a_skipped_models_reason_is_one_readable_line():
    """The probe asks one model at a time, so call_engine's "none of 1 models answered (<model>: …)" wrapper
    repeats the name the caller already prints. Preflight listed five of those nested in one line, which wrapped
    into an unreadable paragraph (live 2026-10-07). `_why` keeps the status and the start of what the provider
    said; the full text stays in llm_inference_logs.json."""
    nested = ("LLM inference: none of 1 models answered (qwen3.8-27b: HTTP 413 The request is too large for "
              "every available candidate's context/token window. Reduce the prompt/history size)")
    why = cli._why(cli.LLMInferenceError(nested))
    assert why.startswith("HTTP 413 The request is too large")
    assert "none of" not in why and "qwen3.8-27b" not in why     # the caller prints the model itself
    assert len(why) <= cli.PROBE_REASON_CHARS
    # a short reason is left alone, and an unwrapped message passes through
    assert cli._why(cli.LLMInferenceError(
        "LLM inference: none of 1 models answered (muse-glimmer-30b: output invalid)")) == "output invalid"
    assert cli._why(cli.LLMInferenceError("HTTP 429 rate limited")) == "HTTP 429 rate limited"


def test_llm_inference_probe_raises_when_no_model_answers(monkeypatch):
    """None answering is the condition that really stops a run, and the error names every model that failed."""
    cfg = pinned_cfg("alpha", "beta")

    def fake(**kw):
        raise cli.LLMInferenceError("all models unavailable")
    monkeypatch.setattr(cli, "call_engine", fake)
    with pytest.raises(cli.LLMInferenceError) as exc:
        cli._probe_llm_inference(cfg)
    for model in cfg.models.llm_inference:
        assert model in str(exc.value)


def test_the_decision_server_asks_for_no_key(tmp_path, monkeypatch):
    """A Kev server on this machine is open unless it was started with KEV_API_KEY: preflight demands only the
    chat key, and never KEV_API_KEY."""
    f = tmp_path / "c.toml"
    f.write_text(f'[paths]\nbase = "{tmp_path}"\n[models.freellmapi]\nllm_inference = ["m"]\n')
    monkeypatch.setattr(cli.config_mod, "local_key", lambda *a: "")
    cfg = cli.config_mod.load(f)
    assert not [p for p in cli._static_checks(cfg, "chat-key") if "API_KEY" in p]
    missing = [p for p in cli._static_checks(cfg, "") if "API_KEY" in p]
    assert len(missing) == 1 and missing[0].startswith("FREELLMAPI_KEY is missing") and "KEV_" not in missing[0]


def test_a_rejected_key_fails_preflight_and_names_the_variable(monkeypatch):
    """P1 T1: in preflight a 401/402/403 is a preflight failure (exit 1, nothing written) that says which key to
    check — not a stopped run. During the job loop the same condition is a clean stop (exit 3, T3)."""
    from assistant import gateway as G
    cfg = cli.config_mod.load()

    def fake(**kw):
        raise G.CreditOrKey("the provider rejected the key (HTTP 401) — check the key / add credit")
    monkeypatch.setattr(cli, "call_engine", fake)
    browser = type("B", (), {"cfg": cfg})()
    with pytest.raises(cli.PreflightError, match=cli.config_mod.CHAT_KEY_NAME):
        list(cli.preflight(browser))


def test_process_entry_uses_navigate_not_a_page_kind_gate():
    """Regression (live gate iter 1, 2026-09-29): cli.process must NOT call pages.classify_entry / pages.judge
    before run_pages — that gate asks kev "what kind of page?", which fails on every real posting. Entry is
    navigate.enter's job (deterministic, no page-kind model call)."""
    import ast
    src = (Path(__file__).resolve().parent.parent / "assistant" / "cli.py").read_text()
    process = next(n for n in ast.walk(ast.parse(src))
                   if isinstance(n, ast.FunctionDef) and n.name == "process")
    called = {n.func.attr for n in ast.walk(process)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "classify_entry" not in called and "judge" not in called, called
    assert "run_pages" in {n.func.id for n in ast.walk(process)
                           if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
