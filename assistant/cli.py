"""Command line: run · preflight · tripwire · capture · requeue (§2, §1, §8.5)."""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import urlparse

from assistant import config as config_mod
from assistant import decide
from assistant import inference_log
from assistant import jev as jevlib
from assistant import pages, records, tabs
from assistant.llm_inference import (LLMInferenceError, Policy, Sources, answer_page, call_engine, resume_text,
                                     system_prompt)
from assistant.rotation import Rotation
from assistant.blockers import NeedsAttention, Parked, RestartFromEntry, StopRun
from assistant.fill import JobCtx, run_pages
from assistant.jev import Jev, JevError, split_json
from assistant.report import EXIT_PREFLIGHT, EXIT_STOPPED, JobResult, Report
from assistant.tracker import NEEDS_ATTENTION, PENDING_REVIEW, Tracker, TrackerError

RUNS = config_mod.TOOL_DIR / "runs"
CAPTURED = config_mod.TOOL_DIR / "tests" / "captured"
LINKEDIN_FEED = "https://www.linkedin.com/feed/"
CONNECT_TIMEOUT = 180.0     # s: the first connection may wait for a person to allow remote debugging in Chrome


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
    req = sub.add_parser("requeue", help="put Needs-Attention jobs back in the queue (status Resume Built)")
    req.add_argument("--job", metavar="URL", help="only the job with this LinkedIn URL")
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


PROBE_TIMEOUT = 60.0   # a preflight LLM-inference probe: a trivial page should answer well within this


def _probe_llm_inference(cfg: config_mod.Config) -> str:
    """Live check that a configured LLM inference model answers with a valid PageAnswers, over the strict
    json_schema path the real run uses. One trivial empty page; LLMInferenceError when no model answers. Returns
    the model that answered."""
    models = Rotation(cfg.models.llm_inference)
    user = {"page": {"url": "about:blank", "title": "Preflight", "text": "", "fields": []},
            "sources": {"profile": "", "job": "", "resume": ""}}
    call_engine(key=config_mod.chat_key(cfg), models=models, system=system_prompt(cfg.policy.free_text_max_chars),
                user=user, url=config_mod.chat_url(cfg), timeout=PROBE_TIMEOUT)
    return models.last or cfg.models.llm_inference[0]


def preflight(browser: Jev) -> Iterator[str]:
    """LLM inference, decision model, server, capabilities, LinkedIn sign-in. Yields each check as it passes (so a
    later failure does not hide an earlier ✓), and raises PreflightError on the first that fails. Writes only the
    run's inference logs."""
    try:
        model = _probe_llm_inference(browser.cfg)
    except LLMInferenceError as exc:
        raise PreflightError(f"the LLM inference model does not answer: {exc}") from exc
    yield f"LLM inference {model} answers (via {browser.cfg.models.chat_route})"
    try:
        a = decide.current().ask("preflight", "A job application form asks for the candidate's email address.",
                                 {"form": decide.noul("Is this about a job application?")})
    except decide.DecisionError as exc:
        raise PreflightError(f"the decision model (Jev) does not answer: {exc}") from exc
    if not a["form"].yes(0.5):
        raise PreflightError("the decision model (Jev) answered a trivial question wrongly")
    yield f"decision model {browser.cfg.models.jev} answers (via {browser.cfg.models.jev_route})"
    doc = browser.doctor()
    for cap in ("text_model", "uploads", "js_eval"):
        if doc.get(cap) is not True:
            raise PreflightError(f"browser_doctor: {cap} is not enabled")
    yield "server started; text helper, uploads and JS eval enabled"
    print("… connecting to Chrome — if Chrome shows “Allow remote debugging?”, click Allow", flush=True)
    try:
        browser.open(LINKEDIN_FEED, "preflight", timeout=CONNECT_TIMEOUT)
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
    yield "LinkedIn signed in"
    if (browser.doctor()).get("connected") is not True:
        raise PreflightError("browser_doctor: not connected after the LinkedIn probe")
    yield "attached to Chrome"
    try:
        book = tabs.TabBook(browser)
        browser.open("about:blank", "preflight-tabs")
        book.current("preflight-tabs")            # a tab made after the book started must resolve to a full ID
        browser.close("preflight-tabs")
        book.close()
    except (tabs.TabError, JevError) as exc:
        raise PreflightError(f"tab bookkeeping does not work in this Chrome: {exc}") from exc
    yield "tab release works"


def _static_checks(cfg: config_mod.Config, key: str) -> list[str]:
    """`key` is the chat route's key (config.chat_key). A key both routes use is reported once."""
    problems = cfg.problems()
    missing: dict[str, list[str]] = {}
    if not key:
        missing.setdefault(config_mod.KEY_NAMES[cfg.models.chat_route], []).append("the LLM inference and text helper")
    if not config_mod.jev_key(cfg):
        missing.setdefault(config_mod.KEY_NAMES[cfg.models.jev_route], []).append("Jev")
    for name, users in missing.items():
        problems.append(f"{name} is missing ({' and '.join(users)} need it; put it in Tools/Application_Assistant/.env)")
    return problems


# ------------------------------------------------------------------ one job (§5)

def process(job: records.Job, *, browser: Jev, book: tabs.TabBook, cfg: config_mod.Config, key: str, profile: str,
                  run_dir: Path, today: date, warn: Callable[[str], None] | None = None,
                  engines: Rotation | None = None) -> Parked:
    """Open the posting → the page loop (the browser agent from there) → parked. Raises NeedsAttention / StopRun.
    Tabs are released in all cases; a release that fails is reported through `warn` instead of crashing the run."""
    pdf = job.resume_pdf()                                              # before any browser work
    S = f"job-{job.key}"
    baseline = book.handles()
    src = Sources(profile=profile, job=job.job_md.read_text(), resume=resume_text(pdf))
    policy = Policy(cfg.policy.prefill, cfg.policy.free_text_max_chars)
    engines = engines or Rotation(cfg.models.llm_inference)

    def engine(p):
        return answer_page(p, src, key=key, models=engines, policy=policy, today=today,
                           url=config_mod.chat_url(cfg))

    ctx = JobCtx(browser=browser, session=S, book=book, resume_pdf=pdf, answer_fn=engine, baseline=baseline,
                 google_email=cfg.google.account_email, max_pages=cfg.browser.max_pages_per_job,
                 answers_log=run_dir / job.folder / "answers.json", shots_dir=run_dir / job.folder,
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
            if state != "open":
                raise NeedsAttention("load_failure", "no Easy Apply / Apply button on the LinkedIn job page")
            try:
                return run_pages(ctx)                          # the browser agent takes it from the posting
            except RestartFromEntry as exc:
                if attempt == 2:
                    raise NeedsAttention("load_failure", f"{exc} (after 2 attempts)") from exc
    except decide.DecisionError as exc:
        raise NeedsAttention("decision", str(exc)) from exc
    except NeedsAttention as na:
        if ctx.last and not na.url:
            na.url, na.title = ctx.last.url, ctx.last.title
        raise
    finally:
        if opened:
            try:
                app = book.current_handle(S)
                book.close_junk(S, baseline, {app})
            except (JevError, RuntimeError):
                pass
            try:
                book.release(S)
            except (JevError, RuntimeError) as exc:           # TabError is a RuntimeError
                (warn or print)(f"{job.label}: its tab could not be released ({exc}); "
                                "the tab may close when the run ends")
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
    key = config_mod.chat_key(cfg)
    if problems := _static_checks(cfg, key):
        for p in problems:
            print(f"✗ preflight: {p}")
        return EXIT_PREFLIGHT
    started = datetime.now()
    run_dir = RUNS / started.strftime("%Y%m%d-%H%M%S")
    inference_log.start_run(run_dir)                   # scope _run until a job starts (§6.1); preflight logs here
    report = Report(started)

    def on_timeout(msg: str) -> None:                  # §3: write the report, then the caller os._exit(3)s
        report.stopped = f"a browser call hung: {msg}"
        print(f"■ run stopped: {report.stopped}")
        print(f"Report: {report.write(run_dir)}")

    browser = Jev(cfg, key, on_timeout=on_timeout)     # calls are not logged until preflight passes
    decide.use(decide.for_config(cfg))
    try:
        _load_package(cfg, key)
        tracker = Tracker(cfg.path("tracker"), cfg.paths.tracker_sheet).load()
        for line in preflight(browser):
            print(f"✓ {line}")
    except (PreflightError, TrackerError, JevError) as exc:
        print(f"✗ preflight: {exc}")
        return EXIT_PREFLIGHT
    try:
        browser.calls_log = run_dir / "_run" / "browser_actions.jsonl"   # preflight/queue calls; repointed per job
        book = tabs.TabBook(browser)                   # before any job tab exists (full IDs, §4.5)
        tracker.backup(run_dir / "tracker-backup.numbers")
        if not args.no_record:
            report.recovered = records.recover(RUNS, tracker)
        q = _queue(cfg, tracker, args.job, args.limit)
        report.anomalies = q.anomalies
        recorder = records.Recorder(tracker, records.Journal(run_dir / "journal.jsonl"), cfg.base_dir(),
                                    cfg.path("pending_review"), cfg.path("needs_attention"), set(q.new_rows))
        profile = cfg.path("profile").read_text()
        engines = Rotation(cfg.models.llm_inference)   # one for the run: each page starts at the last model that answered
        for job in q.jobs:
            with inference_log.scope(job.folder):      # §6.1: this job's logs land in run_dir/<job folder>/
                browser.calls_log = run_dir / job.folder / "browser_actions.jsonl"
                t0 = time.monotonic()
                result = JobResult(job.company, job.title, job.linkedin_url, job.folder, 0,
                                   date=started.strftime("%Y-%m-%d"))
                try:
                    result.parked = process(job, browser=browser, book=book, cfg=cfg, key=key, profile=profile,
                                            run_dir=run_dir, today=started.date(), warn=report.warnings.append,
                                            engines=engines)
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
    finally:
        browser.calls_log = run_dir / "_run" / "browser_actions.jsonl"   # tab cleanup is run-level, not a job's
        try:
            book.close()
        except (NameError, JevError):
            pass
        d = decide.current()
        report.decisions = (d.calls, d.cost)
    path = report.write(run_dir)
    print(f"Report: {path}")
    return report.exit_code()


def run_preflight(cfg: config_mod.Config) -> int:
    key = config_mod.chat_key(cfg)
    if problems := _static_checks(cfg, key):
        for p in problems:
            print(f"✗ {p}")
        return EXIT_PREFLIGHT
    inference_log.start_run(RUNS / datetime.now().strftime("%Y%m%d-%H%M%S"))   # preflight's Jev call -> _run/ (§6.1)
    print(f"✓ config valid, keys present (chat models via {cfg.models.chat_route}, Jev via {cfg.models.jev_route})")
    try:
        _load_package(cfg, key)
        print("✓ jev-ultrafast-mcp loaded after apply_env()")
        Tracker(cfg.path("tracker"), cfg.paths.tracker_sheet).load()
        print("✓ tracker readable")
        decide.use(decide.for_config(cfg))
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
    key = config_mod.chat_key(cfg)
    jevlib.apply_env(cfg, key)
    decide.use(decide.for_config(cfg))
    browser = Jev(cfg, key)
    book = tabs.TabBook(browser)                       # before the captured tab exists
    S = "capture"
    browser.open(url, S)
    pages.settle(lambda: pages.read_page(browser, S))  # wait for client-rendered content before the snapshot
    full = browser.observe(S, mode="full", include_json=True)
    _, table = split_json(full)
    (out / "observe.txt").write_text(full)
    (out / "text.txt").write_text(pages.view_text(full.rpartition("\n\njson: ")[0]))
    browser.act([{"op": "screenshot", "path": str(out / "screenshot.jpg"), "full": True}], S, table,
                observe_after=False)
    book.release(S)                                    # the captured page stays open in your Chrome
    book.close()
    print(f"Saved {out}")
    print("Optionally add expected_kind.txt (form, final, blocker, …) to pin the classifier result.")
    return 0


def requeue(cfg: config_mod.Config, job_url: str | None) -> int:
    """Needs-Attention/ → Applications/ with Status back to Resume Built; the tracker is backed up first."""
    from assistant.tracker import job_id
    run_dir = RUNS / f"{datetime.now():%Y%m%d-%H%M%S}-requeue"
    try:
        tracker = Tracker(cfg.path("tracker"), cfg.paths.tracker_sheet).load()
        tracker.backup(run_dir / "tracker-backup.numbers")
        lines = records.requeue(tracker, records.Journal(run_dir / "journal.jsonl"), cfg.path("needs_attention"),
                                cfg.path("applications"), job_id(job_url) if job_url else None)
    except (TrackerError, StopRun) as exc:
        print(f"✗ {exc}")
        return EXIT_STOPPED
    for line in lines or ["nothing to requeue"]:
        print(f"• {line}")
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
    if args.cmd == "requeue":
        return requeue(cfg, args.job)
    return EXIT_STOPPED
