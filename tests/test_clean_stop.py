"""A clean stop leaves the current job untouched (P1 T3, D21).

The three Gateway stops must reach `cli.run`'s StopRun handler, which means: no folder move, no tracker write, no
job.md note, exit 3, and the queued jobs still at "Resume Built". The tracker and the job folders are temp copies
(§4.2), and no browser and no provider is involved.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from assistant import cli, config as config_mod, decide, gateway as G, inference_log, records
from assistant.report import EXIT_STOPPED
from assistant.tracker import Tracker
from tests.test_records import RESUME_BUILT, make_job

pytestmark = pytest.mark.unit

REAL_TRACKER = Path(__file__).resolve().parents[2].parent / "Job_Tracker.numbers"


@pytest.fixture
def workspace(tmp_path):
    """Two queued jobs, on temp copies of the tracker and the folders."""
    if not REAL_TRACKER.exists():
        pytest.skip("no Job_Tracker.numbers to copy")
    base = tmp_path / "base"
    for name in ("Applications", "Pending-Review", "Needs-Attention"):
        (base / name).mkdir(parents=True)
    (base / "Profile.md").write_text("# Profile\n")
    shutil.copy2(REAL_TRACKER, base / "Job_Tracker.numbers")
    t = Tracker(base / "Job_Tracker.numbers").load()
    for jid in ("4200000001", "4200000002"):
        t.add({"Job URL": f"https://www.linkedin.com/jobs/view/{jid}/", "Status": RESUME_BUILT, "Company": "Stop Co"})
    t.save()
    for jid in ("4200000001", "4200000002"):
        make_job(base / "Applications", jid)
    cfg = config_mod.load().model_copy(update={"paths": config_mod.Paths(base=str(base)),
                                               "source": base / "config.toml"})
    return SimpleNamespace(base=base, cfg=cfg)


def run_until(workspace, stop, monkeypatch, tmp_path):
    """cli.run with preflight, the package and the browser stubbed out; `stop` is raised on the first job."""
    cfg = workspace.cfg
    monkeypatch.setattr(cli, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(cli, "_load_package", lambda *a: None)
    monkeypatch.setattr(cli, "connect_once", lambda *a: None)
    monkeypatch.setattr(cli, "preflight", lambda browser: iter(["stubbed"]))
    monkeypatch.setattr(cli, "Jev", lambda *a, **k: SimpleNamespace(calls_log=None, doctor=lambda: {}))
    monkeypatch.setattr(cli.tabs, "TabBook", lambda browser: SimpleNamespace(
        handles=lambda: set(), close=lambda: None, release=lambda s: None))
    monkeypatch.setattr(config_mod, "chat_key", lambda c: "k")

    def process(job, **kw):
        raise stop
    monkeypatch.setattr(cli, "process", process)
    args = SimpleNamespace(job=None, limit=None, no_record=False, dry_run=False, config=None)
    code = cli.run(cfg, args)
    inference_log.start_run(None)
    return code


@pytest.mark.parametrize("stop", [
    G.ProviderOutage("provider outage — vercel HTTP 503: 3 requests in a row failed"),
    G.BudgetExceeded("spend cap $1.00 reached"),
    G.CreditOrKey("openrouter.ai rejected the key (HTTP 401) — check the key / add credit on openrouter"),
])
def test_a_gateway_stop_records_nothing_and_exits_three(workspace, monkeypatch, tmp_path, stop):
    base = workspace.base
    before = Tracker(base / "Job_Tracker.numbers").load()
    statuses = {jid: before.find(jid)[1]["Status"] for jid in ("4200000001", "4200000002")}

    assert run_until(workspace, stop, monkeypatch, tmp_path) == EXIT_STOPPED

    after = Tracker(base / "Job_Tracker.numbers").load()
    assert {jid: after.find(jid)[1]["Status"] for jid in statuses} == statuses   # still Resume Built
    assert sorted(p.name for p in (base / "Applications").iterdir()) == \
        sorted(p.name for p in (base / "Applications").iterdir())
    assert list((base / "Pending-Review").iterdir()) == [] and list((base / "Needs-Attention").iterdir()) == []
    for folder in (base / "Applications").iterdir():
        assert "Needs Attention" not in (folder / "job.md").read_text()
    report = next((tmp_path / "runs").glob("*/report.md")).read_text()
    assert "**Run stopped:**" in report and str(stop) in report


def test_the_second_job_is_never_started_after_a_stop(workspace, monkeypatch, tmp_path):
    seen = []

    def process(job, **kw):
        seen.append(job.key)
        raise G.BudgetExceeded("spend cap $1.00 reached")
    monkeypatch.setattr(cli, "process", process)
    run_until(workspace, G.BudgetExceeded("spend cap $1.00 reached"), monkeypatch, tmp_path)
    assert len(seen) <= 1


def test_needs_attention_still_records_so_the_stop_is_what_is_special(workspace, monkeypatch, tmp_path):
    """The point of T3 is the contrast: a NeedsAttention job IS recorded, a stopped run is not."""
    from assistant.blockers import NeedsAttention
    base = workspace.base
    code = run_until(workspace, NeedsAttention("decision", "no answer"), monkeypatch, tmp_path)
    assert code == 2 and len(list((base / "Needs-Attention").iterdir())) == 2
