"""Command line: run · preflight · tripwire · capture · requeue (§2, §1, §8.5)."""
from __future__ import annotations

import argparse
import subprocess
import sys
import dataclasses
import time
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import urlparse

from assistant import config as config_mod
from assistant import decide
from assistant import inference_log
from assistant import pages, records, tabs
from assistant.llm_inference import (LLMInferenceError, Policy, Sources, answer_page, call_engine,
                                     system_prompt)
from assistant.rotation import Rotation
from assistant import gateway as gateway_mod
from assistant.blockers import GoExternal, NeedsAttention, Parked, RestartFromEntry, StopRun
from assistant.fill import JobCtx, run_pages
from assistant import external
from assistant.browser import Browser, DriverError, split_json
from assistant.report import EXIT_PREFLIGHT, EXIT_STOPPED, JobResult, Report
from assistant.tracker import NEEDS_ATTENTION, PENDING_REVIEW, RESUME_BUILT, Tracker, TrackerError, job_id

RUNS = config_mod.TOOL_DIR / "runs"
CAPTURED = config_mod.TOOL_DIR / "tests" / "captured"
LINKEDIN_FEED = "https://www.linkedin.com/feed/"
CONNECT_TIMEOUT = 20.0     # s: the first connection may wait for a person to allow remote debugging in Chrome


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
    rep = sub.add_parser("replay", help="offline replay of a recorded job")
    rep.add_argument("path", type=Path, help="path to a job folder within a run")
    rep.add_argument("--live", action="store_true", help="call real APIs on cache miss and save to fixture")
    req = sub.add_parser("requeue", help="put Needs-Attention jobs back in the queue (status Resume Built)")
    req.add_argument("--job", metavar="URL", help="only the job with this LinkedIn URL")
    req.add_argument("--class", dest="klass", metavar="CLASS",
                     help="only jobs a run recorded under this Needs-Attention class (e.g. external_ats)")
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


def _probe_llm_inference(cfg: config_mod.Config) -> tuple[list[str], list[str]]:
    """Live check of every configured LLM inference model, over the strict json_schema path the real run uses
    (P1 T1): one trivial empty page each, so a model that is dead or out of quota is known before the first job.
    Returns (answered, out), both in configured order.

    It raises only when **none** of them answers, which is the condition that actually stops a run. Before
    2026-10-07 a single failing model failed preflight, because each cloud route was paid and a 429 there meant
    something was wrong. The FreeLLMAPI router serves free tiers that go in and out of quota minute by minute (one
    model answered a sweep and then 429'd a minute later, live 2026-10-07), so demanding all of them would fail
    preflight almost always while the run itself only ever needs one model per page — which is exactly what
    `Rotation` provides. The models that are out are reported, not fatal."""
    user = {"page": {"url": "about:blank", "title": "Preflight", "text": "", "fields": []},
            "sources": {"profile": "", "job": "", "resume": ""}}
    answered, out = [], []
    # Each model is asked on a Gateway of its own, and the run's is put back afterwards. `call_engine` reports one
    # failed request per model that cannot answer (D21), so sharing one breaker across the probe makes one model's
    # quota evidence about the next: three configured models out at once — ordinary on free tiers, and two of the
    # five sit next to each other — trips a sticky ProviderOutage, which both exits 3 (the outcome this probe was
    # relaxed to avoid) and then skips the models after it, including ones that would have answered. A breaker per
    # model is also what the question means: "can this model answer?", independent of the others. One request
    # cannot trip a fresh breaker, since D21 needs three in a row, so no outage can arise here at all.
    # Still the one send path (T7 A2): same sender, same retry rule, same log — only the breaker and the counters
    # are fresh. Taking the default sender instead would make the probe ignore an injected one and reach the
    # network for real.
    run_gateway = gateway_mod.current()
    sender = {"post": run_gateway.post, "sleep": run_gateway.sleep} if run_gateway is not None else {}
    try:
        for model in cfg.models.llm_inference:
            rotation = Rotation([model])       # one model at a time: a rotation would hide the one that is out
            gateway_mod.use(gateway_mod.for_config(cfg, **sender))
            try:
                call_engine(key=config_mod.chat_key(), models=rotation,
                            system=system_prompt(cfg.policy.free_text_max_chars), user=user,
                            url=config_mod.chat_url(cfg), timeout=PROBE_TIMEOUT)
            except LLMInferenceError as exc:
                # Only this model is out. A CreditOrKey is not caught: it names the key and must still reach
                # preflight.
                out.append(f"{model} ({exc})")
                continue
            answered.append(model)
    finally:
        gateway_mod.use(run_gateway)
    if not answered:
        raise LLMInferenceError("; ".join(out))
    return answered, out


def preflight(browser: Browser) -> Iterator[str]:
    """LLM inference, decision model, server, capabilities, LinkedIn sign-in. Yields each check as it passes (so a
    later failure does not hide an earlier ✓), and raises PreflightError on the first that fails. Writes only the
    run's inference logs."""
    try:
        models, out = _probe_llm_inference(browser.cfg)
    except gateway_mod.CreditOrKey as exc:
        # P1 T1: in preflight this is a preflight failure naming the key, not a stopped run — nothing was written.
        raise PreflightError(f"{exc}. Check {config_mod.CHAT_KEY_NAME} in "
                             f"Tools/Application_Assistant/.env") from exc
    except LLMInferenceError as exc:
        raise PreflightError(f"no LLM inference model answers: {exc}") from exc
    yield (f"LLM inference answers: {', '.join(models)} (via the FreeLLMAPI router)"
           + (f"; out of quota, the rotation will skip: {', '.join(out)}" if out else ""))
    cfg = browser.cfg
    name = cfg.models.system_one_decision_model
    try:
        card = decide.server_card(cfg)
    except decide.DecisionError as exc:
        raise PreflightError(str(exc)) from exc
    yield (f"Kev server on {cfg.models.local.base_url}: {card.get('run')} on {card.get('device')} "
           f"via {card.get('backend')} ({card.get('dtype')})")
    try:
        a = decide.current().ask("preflight", "A job application form asks for the candidate's email address.",
                                 {"form": decide.noul("Is this about a job application?")})
    except gateway_mod.CreditOrKey as exc:
        raise PreflightError(f"{exc}. Check {config_mod.LOCAL_KEY_NAME} in "
                             f"Tools/Application_Assistant/.env (only needed when the Kev server was started "
                             f"with one)") from exc
    except decide.DecisionError as exc:
        hint = decide.start_hint(cfg)
        raise PreflightError(f"the decision model ({name}) does not answer: {exc}"
                             f"{'. ' + hint if hint else ''}") from exc
    if not a["form"].yes(0.5):
        raise PreflightError(f"the decision model ({name}) answered a trivial question wrongly")
    yield f"decision model {name} answers (via the Kev server on this machine)"
    doc = browser.doctor()
    for cap in ("uploads", "js_eval"):
        if doc.get(cap) is not True:
            raise PreflightError(f"driver doctor: {cap} is not enabled")
    yield "driver connected; uploads and JS eval enabled"
    try:
        browser.open(LINKEDIN_FEED, "preflight", timeout=CONNECT_TIMEOUT)
        _, table = split_json(browser.observe("preflight", include_text=False))
    except DriverError as exc:
        raise PreflightError(f"could not open LinkedIn in your Chrome ({exc}). Is Chrome running with "
                             f"remote debugging on {browser.cfg.browser.cdp_url}?") from exc
    finally:
        try:
            browser.close("preflight")            # a scratch tab, safe to close (never a job tab)
        except DriverError:
            pass
    if not pages.linkedin_feed_ok(table.url):
        raise PreflightError(f"LinkedIn is signed out in this Chrome (landed on {table.url})")
    yield "LinkedIn signed in"
    if (browser.doctor()).get("connected") is not True:
        raise PreflightError("driver doctor: not connected after the LinkedIn probe")
    yield "attached to Chrome"
    try:
        book = tabs.TabBook(browser)
        browser.open("about:blank", "preflight-tabs")
        book.current("preflight-tabs")            # the driver reports a full target id for the scratch tab
        browser.close("preflight-tabs")
        book.close()
    except (tabs.TabError, DriverError) as exc:
        raise PreflightError(f"tab handling does not work in this Chrome: {exc}") from exc
    yield "tab handling works"


def _static_checks(cfg: config_mod.Config, key: str) -> list[str]:
    """`key` is the FreeLLMAPI router's key (config.chat_key). The Kev decision server needs no key unless it was
    started with one, so only the chat key is required here."""
    problems = cfg.problems()
    if not key:
        problems.append(f"{config_mod.CHAT_KEY_NAME} is missing (the LLM inference and the chat fallback need it; "
                        f"put it in Tools/Application_Assistant/.env)")
    return problems


# ------------------------------------------------------------------ one job (§5)

def process(job: records.Job, *, browser: Browser, book: tabs.TabBook, cfg: config_mod.Config, key: str, profile: str,
                  run_dir: Path, today: date, warn: Callable[[str], None] | None = None,
                  engines: Rotation | None = None) -> Parked:
    """Open the posting → the page loop (the browser agent from there) → parked. Raises NeedsAttention / StopRun.
    Tabs are released in all cases; a release that fails is reported through `warn` instead of crashing the run."""
    pdf = job.resume_pdf()                                              # before any browser work
    S = f"job-{job.key}"
    baseline = book.handles()
    # The resume is uploaded to the form but its extracted text is NOT a source (user decision 2026-10-06):
    # Profile.md is the one document the user maintains for this, and a PDF's extracted text was giving the
    # model a second, differently-worded copy of the same facts to splice quotes across. `pdf` is still read
    # above, because the file itself is what gets uploaded.
    src = Sources(profile=profile, job=job.job_md.read_text(), resume="")
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
            # P2: only the signed-out check stays here — it is a deterministic URL test. The posting's entry
            # (Easy Apply / closed / applied / external ATS) is decided by navigate.enter inside run_pages, with
            # NO page-kind model call. The old pages.classify_entry gate asked kev "what kind of page is this?",
            # which it cannot answer on a real posting (kind="other" at ~0.12), so every job died here as
            # load_failure before navigation could run.
            if any(m in p.url for m in pages.SIGNED_OUT_MARKERS):
                raise StopRun("LinkedIn is signed out (C19); the job was left untouched")
            try:
                return run_pages(ctx)                          # navigate.py decides entry, then fills
            except GoExternal:
                return external.run_external(ctx)              # P5: the apply control leads off LinkedIn
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
            except (DriverError, RuntimeError):
                pass
            try:
                book.release(S)
            except (DriverError, RuntimeError) as exc:        # TabError is a RuntimeError
                (warn or print)(f"{job.label}: its tab could not be released ({exc}); "
                                "the tab may close when the run ends")
    raise AssertionError("unreachable")


# ------------------------------------------------------------------ run

def _queue(cfg: config_mod.Config, tracker: Tracker, job_url: str | None, limit: int | None) -> records.Queue:
    q = records.build_queue(cfg.path("applications"), tracker)
    if job_url:
        q.jobs = [j for j in q.jobs if j.key == job_id(job_url)]
    if limit is not None:
        q.jobs = q.jobs[:limit]
    return q


def _folder_in(cfg: config_mod.Config, which: str, key: str) -> bool:
    d = cfg.path(which)
    if not d.exists():
        return False
    return any((j := records.Job.from_dir(p)) is not None and j.key == key
               for p in d.iterdir() if p.is_dir())


def _why_not_queued(cfg: config_mod.Config, tracker: Tracker, key: str) -> str:
    """Why `--job` matched no queued job. One line, naming the way out where there is one.

    `--job` filters the queue, it does not bypass it: a job is only queueable while its folder is in
    Applications/ and its tracker row is 'Resume Built' (or absent). Finding none used to be silent."""
    if _folder_in(cfg, "needs_attention", key):
        return ("its folder is in Needs-Attention/ — put it back in the queue first: "
                f"python -m assistant requeue --job {key}")
    if _folder_in(cfg, "pending_review", key):
        return "its folder is in Pending-Review/ — it is already parked, waiting for you to submit it"
    hit = tracker.find(key)
    if hit is None:
        return "no folder in Applications/ and no tracker row has that job id — check the URL"
    status = hit[1].get("Status")
    if status != RESUME_BUILT:
        return f"its tracker row says Status {status!r}, and only {RESUME_BUILT!r} is queued"
    return f"its tracker row is {RESUME_BUILT!r} but it has no folder in Applications/"


def _report_queue(cfg: config_mod.Config, tracker: Tracker, q: records.Queue, job_url: str | None) -> str | None:
    """Print what the queue came to, and return the reason when `--job` matched nothing.

    Anomalies used to reach the terminal on `--dry-run` only, so a real run that skipped every job looked
    like a run that had nothing to do (live 2026-10-06)."""
    for a in q.anomalies:
        print(f"⚠ queue anomaly: {a}")
    if q.jobs:
        return None
    if job_url:
        why = _why_not_queued(cfg, tracker, job_id(job_url))
        print(f"✗ --job {job_url}: no queued job has that id — {why}")
        return why
    print("• nothing to do: no folder in Applications/ is at 'Resume Built'")
    return None


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
    # Same reporting as a real run, so `--dry-run --job …` is the free way to find this out (it costs no
    # preflight: a real run only discovers an empty queue after it has probed every model).
    return EXIT_PREFLIGHT if _report_queue(cfg, tracker, q, args.job) else 0


def connect_once(browser: Browser) -> None:
    """The run's single CDP connection (D13), opened before preflight so the whole run costs one "Allow" click.
    One Browser is built per run and owns one BrowserManager, so this is the only handshake there is."""
    print(f'Chrome will ask "Allow remote debugging?" — click Allow (waiting up to {CONNECT_TIMEOUT:.0f} s).',
          flush=True)
    try:
        browser.connect(CONNECT_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - any handshake failure is a preflight failure
        raise PreflightError(f"could not connect to Chrome on {browser.cfg.browser.cdp_url} ({exc}). Is Chrome "
                             f"running with remote debugging switched on in chrome://inspect?") from exc


def run(cfg: config_mod.Config, args) -> int:
    key = config_mod.chat_key()
    if problems := _static_checks(cfg, key):
        for p in problems:
            print(f"✗ preflight: {p}")
        return EXIT_PREFLIGHT
    started = datetime.now()
    run_dir = RUNS / started.strftime("%Y%m%d-%H%M%S")
    inference_log.start_run(run_dir)                   # scope _run until a job starts (§6.1); preflight logs here
    report = Report(started)

    browser = Browser(cfg, key)                        # one Browser per run: one BrowserManager, one connection
    gateway = gateway_mod.for_config(cfg)              # every model request in this run goes through it (T2)
    gateway_mod.use(gateway)
    report.gateway = gateway
    decide.use(decide.for_config(cfg, gateway))
    try:
        tracker = Tracker(cfg.path("tracker"), cfg.paths.tracker_sheet).load()
        connect_once(browser)                          # one "Allow remote debugging?" click for the whole run
        for line in preflight(browser):
            print(f"✓ {line}")
    except gateway_mod.GatewayStop as exc:     # T3: a stop during preflight is a stop, not a preflight failure
        report.stopped = str(exc)
        print(f"■ run stopped: {exc}")
        print(f"Report: {report.write(run_dir)}")
        return EXIT_STOPPED
    except (PreflightError, TrackerError, DriverError) as exc:
        print(f"✗ preflight: {exc}")
        return EXIT_PREFLIGHT
    try:
        browser.actions_log = run_dir / "_run" / "browser_actions.jsonl"   # preflight/queue calls; repointed per job
        book = tabs.TabBook(browser)                   # before any job tab exists (full IDs, §4.5)
        tracker.backup(run_dir / "tracker-backup.numbers")
        if not args.no_record:
            report.recovered = records.recover(RUNS, tracker)
        q = _queue(cfg, tracker, args.job, args.limit)
        report.anomalies = q.anomalies
        report.nothing_matched = _report_queue(cfg, tracker, q, args.job)
        recorder = records.Recorder(tracker, records.Journal(run_dir / "journal.jsonl"), cfg.base_dir(),
                                    cfg.path("pending_review"), cfg.path("needs_attention"), set(q.new_rows))
        profile = cfg.path("profile").read_text()
        engines = Rotation(cfg.models.llm_inference)   # one for the run: each page starts at the last model that answered
        for job in q.jobs:
            with inference_log.scope(job.folder):      # §6.1: this job's logs land in run_dir/<job folder>/
                browser.actions_log = run_dir / job.folder / "browser_actions.jsonl"
                counters = gateway.start_job()
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
                result.models = dataclasses.replace(counters)
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
        browser.actions_log = run_dir / "_run" / "browser_actions.jsonl"   # tab cleanup is run-level, not a job's
        try:
            book.close()
        except (NameError, DriverError):
            pass
        d = decide.current()
        report.decisions, report.decision_model = d.calls, d.model
        report.by_fallback = getattr(d, "by_fallback", 0)
    path = report.write(run_dir)
    print(f"Report: {path}")
    return report.exit_code()


def run_preflight(cfg: config_mod.Config) -> int:
    key = config_mod.chat_key()
    if problems := _static_checks(cfg, key):
        for p in problems:
            print(f"✗ {p}")
        return EXIT_PREFLIGHT
    inference_log.start_run(RUNS / datetime.now().strftime("%Y%m%d-%H%M%S"))   # preflight's Jev call -> _run/ (§6.1)
    print(f"✓ config valid, keys present (chat models via the FreeLLMAPI router on "
          f"{cfg.models.freellmapi.base_url}, System One decision model via the Kev server on "
          f"{cfg.models.local.base_url} — no key needed)")
    try:
        Tracker(cfg.path("tracker"), cfg.paths.tracker_sheet).load()
        print("✓ tracker readable")
        gateway = gateway_mod.for_config(cfg)
        gateway_mod.use(gateway)
        decide.use(decide.for_config(cfg, gateway))
        browser = Browser(cfg, key)
        connect_once(browser)
        for line in preflight(browser):
            print(f"✓ {line}")
    except gateway_mod.GatewayStop as exc:
        print(f"■ {exc}")
        return EXIT_STOPPED
    except (PreflightError, TrackerError, DriverError) as exc:
        print(f"✗ {exc}")
        return EXIT_PREFLIGHT
    return 0


def capture(cfg: config_mod.Config, url: str) -> int:
    """Read-only snapshot of a real page for tests/captured/: no clicks, no typing."""
    host = (urlparse(url).hostname or "page").replace(".", "-")
    out = CAPTURED / f"{host}-{datetime.now():%Y%m%d-%H%M%S}"
    out.mkdir(parents=True)
    key = config_mod.chat_key()
    gateway = gateway_mod.for_config(cfg)
    gateway_mod.use(gateway)
    decide.use(decide.for_config(cfg, gateway))
    browser = Browser(cfg, key)
    browser.connect(CONNECT_TIMEOUT)
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


def requeue(cfg: config_mod.Config, job_url: str | None, klass: str | None = None) -> int:
    """Needs-Attention/ → Applications/ with Status back to Resume Built; the tracker is backed up first.
    `klass` limits it to the jobs a run recorded under that class (T6: `--class external_ats`)."""
    from assistant.tracker import job_id
    run_dir = RUNS / f"{datetime.now():%Y%m%d-%H%M%S}-requeue"
    try:
        tracker = Tracker(cfg.path("tracker"), cfg.paths.tracker_sheet).load()
        tracker.backup(run_dir / "tracker-backup.numbers")
        lines = records.requeue(tracker, records.Journal(run_dir / "journal.jsonl"), cfg.path("needs_attention"),
                                cfg.path("applications"), job_id(job_url) if job_url else None, klass)
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
        return requeue(cfg, args.job, args.klass)
    if args.cmd == "replay":
        from tests.replay.harness import replay_job
        return replay_job(args.path, mode="live" if args.live else "strict", cfg=cfg)
    return EXIT_STOPPED
