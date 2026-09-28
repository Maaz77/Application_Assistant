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


def run_until(workspace, stop, monkeypatch, tmp_path, process=None):
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

    def raise_stop(job, **kw):
        raise stop
    monkeypatch.setattr(cli, "process", process or raise_stop)
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
    folders = sorted(p.name for p in (base / "Applications").iterdir())

    assert run_until(workspace, stop, monkeypatch, tmp_path) == EXIT_STOPPED

    after = Tracker(base / "Job_Tracker.numbers").load()
    assert {jid: after.find(jid)[1]["Status"] for jid in statuses} == statuses   # still Resume Built
    assert sorted(p.name for p in (base / "Applications").iterdir()) == folders
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
    run_until(workspace, None, monkeypatch, tmp_path, process=process)
    assert seen and len(seen) == 1          # two jobs were queued; the second was never started


def test_needs_attention_still_records_so_the_stop_is_what_is_special(workspace, monkeypatch, tmp_path):
    """The point of T3 is the contrast: a NeedsAttention job IS recorded, a stopped run is not."""
    from assistant.blockers import NeedsAttention
    base = workspace.base
    code = run_until(workspace, NeedsAttention("decision", "no answer"), monkeypatch, tmp_path)
    assert code == 2 and len(list((base / "Needs-Attention").iterdir())) == 2


def test_a_stop_during_preflight_exits_three_with_a_report(workspace, monkeypatch, tmp_path):
    """A provider outage found by preflight's own live probes is a stop: the report says why and the exit code is 3.
    A *key* failure there is the other case — a PreflightError naming the key, exit 1, nothing written (T1); that is
    `test_cli.py::test_a_rejected_key_fails_preflight_and_names_the_variable`."""
    cfg = workspace.cfg
    monkeypatch.setattr(cli, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(cli, "_load_package", lambda *a: None)
    monkeypatch.setattr(cli, "connect_once", lambda *a: None)
    monkeypatch.setattr(cli, "Jev", lambda *a, **k: SimpleNamespace(calls_log=None))
    monkeypatch.setattr(config_mod, "chat_key", lambda c: "k")

    def preflight(browser):
        raise G.ProviderOutage("provider outage — vercel HTTP 503: 3 requests in a row failed")
        yield
    monkeypatch.setattr(cli, "preflight", preflight)
    args = SimpleNamespace(job=None, limit=None, no_record=False, dry_run=False, config=None)
    assert cli.run(cfg, args) == EXIT_STOPPED
    inference_log.start_run(None)
    report = next((tmp_path / "runs").glob("*/report.md")).read_text()
    assert "provider outage — vercel HTTP 503" in report
    assert list((workspace.base / "Needs-Attention").iterdir()) == []


def test_the_report_shows_the_model_rows_p1_asks_for(tmp_path):
    """T6: the Summary totals and the per-job breakdown in Timings."""
    from assistant.report import JobResult, Report
    from datetime import datetime
    from assistant.blockers import Parked
    run = G.Counters(requests=9, attempts=11, failures=1, cost=0.0123, in_flight=1,
                     jev_requests=7, chat_requests=2)
    job = G.Counters(requests=4, attempts=5, jev_requests=3, chat_requests=1, cost=0.004)
    r = Report(datetime(2026, 9, 28, 12, 0), gateway=SimpleNamespace(run=run), by_fallback=2,
               decisions=(7, 0.0), decision_model="kev-latest",
               results=[JobResult("Acme", "DE", "u", "f", 42, parked=Parked("u", "t", 2), models=job)])
    md = r.write(tmp_path).read_text()
    assert "- Model requests: 7 System One, 2 LLM inference (11 attempts, 1 failed)" in md
    assert "- Model spend: $0.0123 (reported)" in md
    assert "- Highest number of requests in flight: 1" in md
    assert "- Decisions by fallback: 2" in md
    assert "3 System One, 1 LLM, 5 attempts" in md          # the Timings row


def test_an_estimated_spend_says_so(tmp_path):
    from assistant.report import Report
    from datetime import datetime
    run = G.Counters(cost=0.5, estimated=True)
    md = Report(datetime(2026, 9, 28, 12, 0), gateway=SimpleNamespace(run=run)).markdown()
    assert "$0.5000 (estimated)" in md


# ------------------------------------------------------------------ the cleanup path


class _StubServer:
    """Enough of the package's browser_* surface for a real Jev and a real TabBook to work on."""

    def __init__(self):
        self.calls = []

    def _note(self, name, **kw):
        self.calls.append(name)

    def browser_open(self, url, session="default", hint=""):
        self._note("browser_open")
        return f"opened {url}"

    def browser_observe(self, session="default", **kw):
        self._note("browser_observe")
        return "elements:\n"

    def browser_tabs(self, session="default", action="list", **kw):
        self._note("browser_tabs")
        if action != "list":
            return f"{action} ok"
        # tabs.LIST_RE: "  [n] * #HHHHHHHH  <url>". The helper's own tab plus the job's.
        return "  [0] * #AAAAAAAA  about:blank\n  [1]   #BBBBBBBB  https://www.linkedin.com/jobs/view/1\n"

    def browser_close(self, session="default", shutdown_browser=False):
        self._note("browser_close")
        return "closed 1"


def _gateway_that_trips():
    return G.Gateway(limits=SimpleNamespace(max_in_flight=1, min_interval_s=0.0, max_attempts=1),
                     budget=SimpleNamespace(max_usd_per_run=0.0),
                     post=lambda *a: (402, {"error": {"message": "no credit"}}), sleep=lambda _: None)


def test_the_call_that_trips_raises_but_the_cleanup_calls_afterwards_do_not():
    """`Jev.call` asks the Gateway for a stop after every browser call, so that one raised on the package's own
    thread reaches the run. It must ask only about THIS call: the tab cleanup that runs after a stop goes through
    here too, and re-raising the old stop there escapes the `finally` blocks in cli.process and cli.run, which is
    how the report stops being written at all."""
    from assistant import jev as jevlib
    gateway = _gateway_that_trips()
    G.use(gateway)
    try:
        browser = jevlib.Jev(config_mod.load(), "", server=_StubServer())
        browser.open("about:blank", "s")                       # no model request: must not raise
        with pytest.raises(G.CreditOrKey):                     # the request that trips
            gateway.send(G.JEV, "http://127.0.0.1:8009/v1/systemone",
                         {"model": "m", "state": "s", "questions": {}}, {})
        assert gateway.tripped is not None
        browser.close("s")                                     # cleanup after the stop: must not raise
        browser.open("about:blank", "s2")
    finally:
        G.use(None)


def test_a_stop_raised_inside_a_browser_call_still_reaches_the_caller():
    """The other half: a stop that this call caused is raised on the caller's thread, not swallowed into an
    "error(...)" string by `Jev.call`'s broad `except Exception`."""
    from assistant import jev as jevlib
    gateway = _gateway_that_trips()
    G.use(gateway)

    class Tripping(_StubServer):
        def browser_goal(self, **kw):
            gateway.send(G.JEV, "http://127.0.0.1:8009/v1/systemone",
                         {"model": "m", "state": "s", "questions": {}}, {})
            return "goal: x\nstatus: done\nsteps: 1\n"
    try:
        browser = jevlib.Jev(config_mod.load(), "", server=Tripping())
        with pytest.raises(G.CreditOrKey):
            browser.call("browser_goal", goal="x", session="s")
    finally:
        G.use(None)


def test_a_stop_in_a_job_still_writes_the_report_with_a_real_tabbook(workspace, monkeypatch, tmp_path):
    """The whole path, with the real TabBook and the real Jev that the cleanup runs through: exit 3, a report that
    names the reason, and nothing recorded."""
    cfg = workspace.cfg
    gateway = _gateway_that_trips()
    server = _StubServer()
    monkeypatch.setattr(cli, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(cli, "_load_package", lambda *a: None)
    monkeypatch.setattr(cli, "connect_once", lambda *a: None)
    monkeypatch.setattr(cli, "preflight", lambda browser: iter(["stubbed"]))
    monkeypatch.setattr(cli, "Jev", lambda c, k, **kw: cli.jevlib.Jev(c, k, server=server, **kw))
    monkeypatch.setattr(cli.gateway_mod, "for_config", lambda c, **kw: gateway)
    monkeypatch.setattr(config_mod, "chat_key", lambda c: "k")

    def process(job, *, browser, book, **kw):
        browser.open(job.linkedin_url, f"job-{job.key}")
        gateway.send(G.JEV, "http://127.0.0.1:8009/v1/systemone",
                     {"model": "m", "state": "s", "questions": {}}, {})
        raise AssertionError("unreachable: the send above trips")
    monkeypatch.setattr(cli, "process", process)
    args = SimpleNamespace(job=None, limit=None, no_record=False, dry_run=False, config=None)
    code = cli.run(cfg, args)
    inference_log.start_run(None)

    assert code == EXIT_STOPPED
    report = next((tmp_path / "runs").glob("*/report.md")).read_text()
    assert "**Run stopped:**" in report and "no credit" in report
    assert list((workspace.base / "Needs-Attention").iterdir()) == []
    assert "browser_close" in server.calls          # the cleanup really did run
