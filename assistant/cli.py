"""Command line: run · preflight · tripwire · capture (§2, §1, §8.5)."""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from assistant import config as config_mod
from assistant import jev as jevlib
from assistant import pages, records, tabs
from assistant.answers import Policy, Sources, answer_page, resume_text
from assistant.blockers import NeedsAttention, Parked, RestartFromEntry, StopRun
from assistant.entry import EntryRefused, entry_click
from assistant.fill import JobCtx, run_pages
from assistant.jev import Jev, JevError, split_json
from assistant.report import EXIT_PREFLIGHT, EXIT_STOPPED, JobResult, Report
from assistant.tracker import NEEDS_ATTENTION, PENDING_REVIEW, Tracker, TrackerError

RUNS = config_mod.TOOL_DIR / "runs"
CAPTURED = config_mod.TOOL_DIR / "tests" / "captured"
LINKEDIN_FEED = "https://www.linkedin.com/feed/"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m assistant", description="Application Assistant v2")
    p.add_argument("--config", type=Path, default=config_mod.DEFAULT_CONFIG)
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="fill every 'Resume Built' job and park it before submission")
    run.add_argument("--dry-run", action="store_true", help="no browser, no writes; print the queue")
    run.add_argument("--no-record", action="store_true", help="fill and park; no tracker, folder or job.md writes")
    run.add_argument("--job", metavar="URL", help="only the job with this LinkedIn URL")
    run.add_argument("--limit", type=int, metavar="N", help="at most N jobs")

    sub.add_parser("preflight", help="check config, key, server and LinkedIn sign-in")
    trip = sub.add_parser("tripwire", help="run the fixture tripwire suite")
    trip.add_argument("--live", action="store_true", help="also run the live_model goal tripwire (OpenRouter)")
    cap = sub.add_parser("capture", help="save a read-only snapshot of a page for tests")
    cap.add_argument("url")
    return p


def tripwire(live: bool) -> int:
    """Guard/probe unit tests + the browser tripwire on local fixtures, in a throwaway Chrome."""
    tests = config_mod.TOOL_DIR / "tests"
    cmd = [sys.executable, "-m", "pytest", "-q", str(tests / "test_guard.py"), str(tests / "test_tripwire.py")]
    if live:
        cmd.append("--live")
    return subprocess.call(cmd, cwd=config_mod.TOOL_DIR)


# ------------------------------------------------------------------ preflight (§1.1)

class PreflightError(RuntimeError):
    pass


def preflight(browser: Jev) -> list[str]:
    """Server, capabilities, LinkedIn sign-in. Raises PreflightError. Writes nothing."""
    done = []
    doc = browser.doctor()
    for cap in ("text_model", "uploads", "js_eval"):
        if doc.get(cap) is not True:
            raise PreflightError(f"browser_doctor: {cap} is not enabled")
    done.append("server started; text helper, uploads and JS eval enabled")
    try:
        browser.open(LINKEDIN_FEED, "preflight")
        _, table = split_json(browser.observe("preflight", include_text=False))
    except JevError as exc:
        raise PreflightError(f"could not open LinkedIn in your Chrome ({exc}). Is Chrome running with "
                             f"remote debugging on {browser.cfg.browser.cdp_url}?") from exc
    finally:
        try:
            browser.close("preflight")
        except JevError:
            pass
    if not pages.linkedin_feed_ok(table.url):
        raise PreflightError(f"LinkedIn is signed out in this Chrome (landed on {table.url})")
    done.append("LinkedIn signed in")
    if (browser.doctor()).get("connected") is not True:
        raise PreflightError("browser_doctor: not connected after the LinkedIn probe")
    done.append("attached to Chrome")
    return done


def _static_checks(cfg: config_mod.Config, key: str) -> list[str]:
    problems = cfg.problems()
    if not key:
        problems.append("OPENROUTER_API_KEY is missing (put it in Tools/Application_Assistant/.env)")
    return problems


# ------------------------------------------------------------------ one job (§5)

def process(job: records.Job, *, browser: Jev, cfg: config_mod.Config, key: str, profile: str,
                  run_dir: Path, today: date) -> Parked:
    """Open → entry → page loop → parked. Raises NeedsAttention / StopRun. Tabs are released in all cases."""
    pdf = job.resume_pdf()                                              # before any browser work
    S = f"job-{job.key}"
    cdp = cfg.browser.cdp_url
    baseline = tabs.tab_ids(cdp)
    src = Sources(profile=profile, job=job.job_md.read_text(), resume=resume_text(pdf))
    policy = Policy(cfg.policy.prefill, cfg.policy.free_text_max_chars)

    def engine(p):
        return answer_page(p, src, key=key, model=cfg.models.answer_engine, policy=policy, today=today)

    ctx = JobCtx(browser=browser, session=S, cdp_url=cdp, resume_pdf=pdf, answer_fn=engine, baseline=baseline,
                 google_email=cfg.google.account_email, max_pages=cfg.browser.max_pages_per_job,
                 answers_log=run_dir / "answers" / f"{job.folder}.json", shots_dir=run_dir / "shots",
                 folder=job.folder)
    opened = False
    try:
        for attempt in (1, 2):
            browser.open(job.linkedin_url, S)
            opened = True
            p = ctx.read()
            state = pages.classify_entry(p)
            if state == "signed_out":
                raise StopRun("LinkedIn is signed out (C19); the job was left untouched")
            if state in ("closed", "applied"):
                raise NeedsAttention(state, "LinkedIn says this job is " + (
                    "no longer accepting applications" if state == "closed" else "already applied to"))
            if state != "entry":
                raise NeedsAttention("load_failure", "no Easy Apply / Apply button on the LinkedIn job page")
            known = tabs.tab_ids(cdp)
            try:
                entry_click(browser, S, p.table)
            except EntryRefused as exc:
                raise NeedsAttention("load_failure", f"entry click refused: {exc}") from exc
            tabs.hand_off(browser, S, cdp, baseline, known)
            try:
                return run_pages(ctx)
            except RestartFromEntry as exc:
                if attempt == 2:
                    raise NeedsAttention("load_failure", f"{exc} (after 2 attempts)") from exc
    except NeedsAttention as na:
        if ctx.last and not na.url:
            na.url, na.title = ctx.last.url, ctx.last.title
        raise
    finally:
        if opened:
            try:
                app = tabs.current_tab(browser, S, cdp)
                tabs.close_junk(browser, S, cdp, baseline, {app})
            except (JevError, RuntimeError):
                pass
            tabs.release(browser, S, cdp)
    raise AssertionError("unreachable")


# ------------------------------------------------------------------ run

def _queue(cfg: config_mod.Config, tracker: Tracker, job_url: str | None, limit: int | None) -> records.Queue:
    q = records.build_queue(cfg.path("applications"), tracker)
    if job_url:
        from assistant.tracker import job_id
        q.jobs = [j for j in q.jobs if j.key == job_id(job_url)]
    if limit is not None:
        q.jobs = q.jobs[:limit]
    return q


def dry_run(cfg: config_mod.Config, args) -> int:
    try:
        tracker = Tracker(cfg.path("tracker"), cfg.paths.tracker_sheet).load()
    except TrackerError as exc:
        print(f"✗ {exc}")
        return EXIT_PREFLIGHT
    q = _queue(cfg, tracker, args.job, args.limit)
    print(f"Queue ({len(q.jobs)} job{'s' if len(q.jobs) != 1 else ''}):")
    for j in q.jobs:
        print(f"  • {j.label}  {j.linkedin_url}  [{j.folder}]" + ("  (new tracker row)" if j.key in q.new_rows else ""))
    if q.anomalies:
        print("Anomalies:")
        for a in q.anomalies:
            print(f"  - {a}")
    return 0


def _load_package(cfg: config_mod.Config, key: str) -> None:
    """Preflight step: the package imports after apply_env() (§1.1, §3)."""
    try:
        jevlib.apply_env(cfg, key)
        jevlib.load()
    except Exception as exc:  # noqa: BLE001 - any import failure is a preflight failure
        raise PreflightError(f"jev-ultrafast-mcp did not load: {exc}") from exc


def run(cfg: config_mod.Config, args) -> int:
    key = config_mod.api_key()
    if problems := _static_checks(cfg, key):
        for p in problems:
            print(f"✗ preflight: {p}")
        return EXIT_PREFLIGHT
    started = datetime.now()
    run_dir = RUNS / started.strftime("%Y%m%d-%H%M%S")
    report = Report(started)

    def on_timeout(msg: str) -> None:                  # §3: write the report, then the caller os._exit(3)s
        report.stopped = f"a browser call hung: {msg}"
        print(f"■ run stopped: {report.stopped}")
        print(f"Report: {report.write(run_dir)}")

    browser = Jev(cfg, key, on_timeout=on_timeout)     # calls are not logged until preflight passes
    try:
        _load_package(cfg, key)
        tracker = Tracker(cfg.path("tracker"), cfg.paths.tracker_sheet).load()
        for line in preflight(browser):
            print(f"✓ {line}")
    except (PreflightError, TrackerError, JevError) as exc:
        print(f"✗ preflight: {exc}")
        return EXIT_PREFLIGHT
    try:
        browser.calls_log = run_dir / "calls.jsonl"
        tracker.backup(run_dir / "tracker-backup.numbers")
        if not args.no_record:
            report.recovered = records.recover(RUNS, tracker)
        q = _queue(cfg, tracker, args.job, args.limit)
        report.anomalies = q.anomalies
        recorder = records.Recorder(tracker, records.Journal(run_dir / "journal.jsonl"), cfg.base_dir(),
                                    cfg.path("pending_review"), cfg.path("needs_attention"), set(q.new_rows))
        profile = cfg.path("profile").read_text()
        for job in q.jobs:
            t0 = time.monotonic()
            result = JobResult(job.company, job.title, job.linkedin_url, job.folder, 0,
                               date=started.strftime("%Y-%m-%d"))
            try:
                result.parked = process(job, browser=browser, cfg=cfg, key=key, profile=profile, run_dir=run_dir,
                                        today=started.date())
            except NeedsAttention as na:
                result.attention = na
            result.seconds = time.monotonic() - t0
            if not args.no_record:
                if result.parked:
                    dst = recorder.record(job, PENDING_REVIEW, records.parked_note(result.parked, datetime.now()),
                                          None)
                else:
                    na = result.attention
                    dst = recorder.record(job, NEEDS_ATTENTION, records.needs_attention_note(na, datetime.now()),
                                          f"Needs Attention: {na.cls} — {na.what}"[:240])
                result.folder = str(dst.relative_to(cfg.base_dir()))
            report.results.append(result)
            print(result.terminal_line())
    except StopRun as exc:
        report.stopped = str(exc)
        print(f"■ run stopped: {exc}")
    path = report.write(run_dir)
    print(f"Report: {path}")
    return report.exit_code()


def run_preflight(cfg: config_mod.Config) -> int:
    key = config_mod.api_key()
    if problems := _static_checks(cfg, key):
        for p in problems:
            print(f"✗ {p}")
        return EXIT_PREFLIGHT
    print("✓ config valid, OpenRouter key present")
    try:
        _load_package(cfg, key)
        print("✓ jev-ultrafast-mcp loaded after apply_env()")
        Tracker(cfg.path("tracker"), cfg.paths.tracker_sheet).load()
        print("✓ tracker readable")
        for line in preflight(Jev(cfg, key)):
            print(f"✓ {line}")
    except (PreflightError, TrackerError, JevError) as exc:
        print(f"✗ {exc}")
        return EXIT_PREFLIGHT
    return 0


def capture(cfg: config_mod.Config, url: str) -> int:
    """Read-only snapshot of a real page for tests/captured/: no clicks, no typing."""
    host = (urlparse(url).hostname or "page").replace(".", "-")
    out = CAPTURED / f"{host}-{datetime.now():%Y%m%d-%H%M%S}"
    out.mkdir(parents=True)
    key = config_mod.api_key()
    jevlib.apply_env(cfg, key)
    browser = Jev(cfg, key)
    S = "capture"
    browser.open(url, S)
    full = browser.observe(S, mode="full", include_json=True)
    _, table = split_json(full)
    (out / "observe.txt").write_text(full)
    (out / "text.txt").write_text(pages.view_text(full.rpartition("\n\njson: ")[0]))
    browser.act([{"op": "screenshot", "path": str(out / "screenshot.jpg"), "full": True}], S, table,
                observe_after=False)
    tabs.release(browser, S, cfg.browser.cdp_url)
    print(f"Saved {out}")
    print("Optionally add expected_kind.txt (form, final, blocker, …) to pin the classifier result.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "tripwire":
        return tripwire(args.live)
    try:
        cfg = config_mod.load(args.config)
    except Exception as exc:  # noqa: BLE001 - any config error is a preflight failure
        print(f"✗ config: {exc}")
        return EXIT_PREFLIGHT
    if args.cmd == "run":
        if args.dry_run:
            return dry_run(cfg, args)
        return run(cfg, args)
    if args.cmd == "preflight":
        return run_preflight(cfg)
    if args.cmd == "capture":
        return capture(cfg, args.url)
    return EXIT_STOPPED
