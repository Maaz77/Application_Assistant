"""T9: --dry-run on a temp workspace prints the right queue and writes nothing; CLI plumbing."""
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
    monkeypatch.setattr(cli.config_mod, "api_key", lambda *a: "")
    assert cli.main(["--config", str(cfg), "run"]) == 1
    assert "OPENROUTER_API_KEY is missing" in capsys.readouterr().out


def test_bad_config_is_exit_1(tmp_path, capsys):
    bad = tmp_path / "c.toml"
    bad.write_text('[paths]\nbase = "x"\nnope = 1\n')
    assert cli.main(["--config", str(bad), "preflight"]) == 1
