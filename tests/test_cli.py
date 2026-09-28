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


def test_run_without_key_fails_preflight_with_exit_1(workspace, monkeypatch, capsys):
    base, cfg = workspace
    cfg.write_text(re.sub(r'(?m)^chat_route = "\w+"', 'chat_route = "openrouter"', cfg.read_text()))
    monkeypatch.setattr(cli.config_mod, "api_key", lambda *a: "")
    assert cli.main(["--config", str(cfg), "run"]) == 1
    assert "OPENROUTER_API_KEY is missing" in capsys.readouterr().out


def test_a_key_both_routes_use_is_reported_once(workspace, monkeypatch, capsys):
    base, cfg = workspace
    text = re.sub(r'(?m)^chat_route = "\w+"', 'chat_route = "vercel"', cfg.read_text())
    cfg.write_text(re.sub(r'(?m)^system_one_decision_provider = "\w+"', 'system_one_decision_provider = "vercel"', text))   # both routes share the key
    monkeypatch.setattr(cli.config_mod, "gateway_key", lambda *a: "")
    assert cli.main(["--config", str(cfg), "run"]) == 1
    out = capsys.readouterr().out
    assert out.count("AI_GATEWAY_API_KEY is missing") == 1 and \
        "the LLM inference and text helper and the System One decision model" in out


def test_bad_config_is_exit_1(tmp_path, capsys):
    bad = tmp_path / "c.toml"
    bad.write_text('[paths]\nbase = "x"\nnope = 1\n')
    assert cli.main(["--config", str(bad), "preflight"]) == 1


def test_the_llm_inference_probe_asks_every_configured_model(monkeypatch):
    """P1 T1: each configured model, not just the first that answers — a dead second model must be found now and
    not mid-job. One trivial empty page each."""
    cfg = cli.config_mod.load()
    seen = []
    monkeypatch.setattr(cli, "call_engine",
                        lambda **kw: seen.append((kw["url"], list(kw["models"].models))) or None)
    assert cli._probe_llm_inference(cfg) == list(cfg.models.llm_inference)
    assert [m for _, ms in seen for m in ms] == list(cfg.models.llm_inference)
    assert {url for url, _ in seen} == {cli.config_mod.chat_url(cfg)}


def test_the_probe_names_the_model_that_did_not_answer(monkeypatch):
    cfg = cli.config_mod.load()
    dead = cfg.models.llm_inference[-1]

    def fake(**kw):
        if dead in kw["models"].models:
            raise cli.LLMInferenceError("no capacity")
    monkeypatch.setattr(cli, "call_engine", fake)
    with pytest.raises(cli.LLMInferenceError, match=dead):
        cli._probe_llm_inference(cfg)


def test_llm_inference_probe_raises_when_no_model_answers(monkeypatch):
    cfg = cli.config_mod.load()
    def fake(**kw):
        raise cli.LLMInferenceError("all models unavailable")
    monkeypatch.setattr(cli, "call_engine", fake)
    with pytest.raises(cli.LLMInferenceError):
        cli._probe_llm_inference(cfg)


def test_the_local_decision_route_asks_for_no_key(tmp_path, monkeypatch):
    """A Kev server on this machine is keyless (config.KEYLESS_PROVIDERS): preflight demands only the chat
    route's key, and never KEV_API_KEY."""
    f = tmp_path / "c.toml"
    f.write_text(f'[paths]\nbase = "{tmp_path}"\n[models]\nchat_route = "openrouter"\n'
                 f'system_one_decision_provider = "local"\n'
                 f'[models.openrouter]\nllm_inference = ["m"]\ntext_helper = ["t"]\n')
    monkeypatch.setattr(cli.config_mod, "local_key", lambda *a: "")
    cfg = cli.config_mod.load(f)
    assert not [p for p in cli._static_checks(cfg, "chat-key") if "API_KEY" in p]
    missing = [p for p in cli._static_checks(cfg, "") if "API_KEY" in p]
    assert len(missing) == 1 and missing[0].startswith("OPENROUTER_API_KEY is missing") and "KEV_" not in missing[0]


def test_a_rejected_key_fails_preflight_and_names_the_variable(monkeypatch):
    """P1 T1: in preflight a 401/402/403 is a preflight failure (exit 1, nothing written) that says which key to
    check — not a stopped run. During the job loop the same condition is a clean stop (exit 3, T3)."""
    from assistant import gateway as G
    cfg = cli.config_mod.load()

    def fake(**kw):
        raise G.CreditOrKey("ai-gateway.vercel.sh rejected the key (HTTP 401) — check the key / add credit on vercel")
    monkeypatch.setattr(cli, "call_engine", fake)
    browser = type("B", (), {"cfg": cfg})()
    with pytest.raises(cli.PreflightError, match="AI_GATEWAY_API_KEY"):
        list(cli.preflight(browser))
