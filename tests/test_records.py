"""Records on temp copies (§9): tracker adapter, journal kill-recovery, job.md heading rules, folder-move invariant."""
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from assistant import records
from assistant.blockers import NeedsAttention, OpenQuestion, Parked, StopRun
from assistant.records import Job, Journal, Recorder, append_note, build_queue, recover
from assistant.report import JobResult, Report
from assistant.tracker import NEEDS_ATTENTION, PENDING_REVIEW, RESUME_BUILT, Tracker

pytestmark = pytest.mark.unit
REAL_TRACKER = Path(__file__).resolve().parents[3] / "Job_Tracker.numbers"
WHEN = datetime(2026, 9, 23, 14, 3)


def make_job(apps: Path, jid: str, company="Acme", title="Data Engineer", bold=False) -> Path:
    d = apps / f"{jid}_{company}_{title.replace(' ', '-')}"
    d.mkdir(parents=True)
    url = f"https://www.linkedin.com/jobs/view/{jid}"
    overview = (f"**LinkedIn URL:** {url}\n**Company:** {company}\n**Job Title:** {title}\n" if bold else
                f"- Job Title: {title}\n- Company: {company}\n- LinkedIn URL: {url}\n")
    (d / "job.md").write_text(f"# Job Discovery & Scoring\n\n## 1. Overview\n{overview}\n## 2. Job Description\nText.\n")
    (d / f"Amin_{company}_{title.replace(' ', '-')}.pdf").write_bytes(b"%PDF")
    return d


@pytest.fixture
def ws(tmp_path):
    """A temp workspace with a copy of the real tracker plus three rows of our own."""
    if not REAL_TRACKER.exists():
        pytest.skip("no Job_Tracker.numbers to copy")
    base = tmp_path / "base"
    (base / "Applications").mkdir(parents=True)
    shutil.copy2(REAL_TRACKER, base / "Job_Tracker.numbers")
    t = Tracker(base / "Job_Tracker.numbers").load()
    for jid, status in (("4100000001", RESUME_BUILT), ("4100000002", "Submitted"), ("4100000003", RESUME_BUILT)):
        t.add({"Job URL": f"https://www.linkedin.com/jobs/view/{jid}/", "Status": status, "Company": "Row Co"})
    t.save()
    make_job(base / "Applications", "4100000001")                    # row Resume Built → queued
    make_job(base / "Applications", "4100000002", "Beta")            # row Submitted → anomaly
    make_job(base / "Applications", "4100000009", "Gamma", bold=True)  # no row → queued, row added later
    return base                                                      # 4100000003: row without folder → anomaly


def test_real_tracker_copy_loads_through_reconcile(ws):
    t = Tracker(ws / "Job_Tracker.numbers").load()
    assert {"Status", "Job URL", "Notes"} <= set(t.header)
    assert t.find("4100000001")[1]["Status"] == RESUME_BUILT


def test_queue_joins_by_job_id_and_reports_anomalies(ws):
    q = build_queue(ws / "Applications", Tracker(ws / "Job_Tracker.numbers").load())
    assert [j.key for j in q.jobs] == ["4100000001", "4100000009"] and q.new_rows == {"4100000009"}
    text = "\n".join(q.anomalies)
    assert "tracker status is 'Submitted'" in text and "4100000003" in text


def test_job_md_both_overview_formats():
    for bold in (False, True):
        import tempfile
        d = make_job(Path(tempfile.mkdtemp()), "4100000042", "Acme", "ML Engineer", bold=bold)
        j = Job.from_dir(d)
        assert (j.key, j.company, j.title) == ("4100000042", "Acme", "ML Engineer")


def test_record_moves_folder_and_updates_row_together(ws):
    t = Tracker(ws / "Job_Tracker.numbers").load()
    q = build_queue(ws / "Applications", t)
    rec = Recorder(t, Journal(ws / "runs/1/journal.jsonl"), ws, ws / "Pending-Review", ws / "Needs-Attention",
                   new_rows=set(q.new_rows))
    job, new = q.jobs
    dst = rec.record(job, PENDING_REVIEW, "## note", None)
    assert dst == ws / "Pending-Review" / job.folder and dst.exists() and not job.dir.exists()
    rec.record(new, NEEDS_ATTENTION, "## note", "Needs Attention: captcha")
    t2 = Tracker(ws / "Job_Tracker.numbers").load()
    assert t2.find("4100000001")[1]["Status"] == PENDING_REVIEW
    row = t2.find("4100000009")[1]
    assert row["Status"] == NEEDS_ATTENTION and row["Notes"] == "Needs Attention: captcha" and row["Company"] == "Gamma"
    events = [json.loads(l)["event"] for l in (ws / "runs/1/journal.jsonl").read_text().splitlines()]
    assert events == ["record_start", "tracker_saved", "folder_moved", "record_done"] * 2


def test_existing_destination_stops_before_any_write(ws):
    t = Tracker(ws / "Job_Tracker.numbers").load()
    job = build_queue(ws / "Applications", t).jobs[0]
    (ws / "Pending-Review" / job.folder).mkdir(parents=True)
    before = (ws / "Job_Tracker.numbers").read_bytes()
    rec = Recorder(t, Journal(ws / "runs/1/journal.jsonl"), ws, ws / "Pending-Review", ws / "Needs-Attention")
    with pytest.raises(StopRun):
        rec.record(job, PENDING_REVIEW, "## n", None)
    assert (ws / "Job_Tracker.numbers").read_bytes() == before and job.dir.exists()
    assert "Application Assistant" not in job.job_md.read_text()


def test_tracker_changed_on_disk_stops_the_run(ws):
    t = Tracker(ws / "Job_Tracker.numbers").load()
    other = Tracker(ws / "Job_Tracker.numbers").load()
    other.set("4100000001", "Submitted")
    other.save()                                                       # somebody else saved meanwhile
    t.set("4100000001", PENDING_REVIEW)
    with pytest.raises(StopRun, match="changed on disk"):
        t.save()


@pytest.mark.parametrize("killed_after", ["record_start", "tracker_saved", "folder_moved"])
def test_journal_recovery_after_a_kill(ws, killed_after):
    t = Tracker(ws / "Job_Tracker.numbers").load()
    job = build_queue(ws / "Applications", t).jobs[0]
    dst = ws / "Pending-Review" / job.folder
    j = Journal(ws / "runs/20260923-140000/journal.jsonl")
    j.event(job.key, "record_start", PENDING_REVIEW, str(job.dir), str(dst))
    if killed_after in ("tracker_saved", "folder_moved"):
        t.set(job.key, PENDING_REVIEW)
        t.save()
        j.event(job.key, "tracker_saved", PENDING_REVIEW, str(job.dir), str(dst))
    if killed_after == "folder_moved":
        dst.parent.mkdir(parents=True)
        os.rename(job.dir, dst)
        j.event(job.key, "folder_moved", PENDING_REVIEW, str(job.dir), str(dst))
    t = Tracker(ws / "Job_Tracker.numbers").load()                   # the next run
    assert recover(ws / "runs", t) == [f"recovered {job.key} → {PENDING_REVIEW}"]
    assert dst.exists() and not job.dir.exists()
    assert Tracker(ws / "Job_Tracker.numbers").load().find(job.key)[1]["Status"] == PENDING_REVIEW
    assert recover(ws / "runs", Tracker(ws / "Job_Tracker.numbers").load()) == []       # idempotent


def test_job_md_note_heading_rules(tmp_path):
    md = tmp_path / "job.md"
    md.write_text("# Job Discovery & Scoring\n\n## 1. Overview\n- x\n")
    append_note(md, "## A\n- one")
    append_note(md, "## B\n- two")
    text = md.read_text()
    assert text.count("# Application Assistant\n") == 1
    assert text.index("## A") < text.index("## B") and text.endswith("- two\n")
    md.write_text("# Top\n\n# Application Assistant\n\n## old\n- x\n\n# Later Section\nkeep\n")
    append_note(md, "## new\n- y")
    text = md.read_text()
    assert text.index("## old") < text.index("## new") < text.index("# Later Section")      # end of that section
    assert "## Application Assistant" not in text


def test_parked_note_includes_unanswered_questions(tmp_path):
    """T5: a parked job with unanswered required questions shows them in the note."""
    from assistant.blockers import OpenQuestion, Parked
    parked = Parked("https://acme.io/a", "Apply", 2, parked_at=[
        OpenQuestion("Salary?", "text"),
        OpenQuestion("Visa?", "choice", ["Yes", "No"])])
    note = records.parked_note(parked, WHEN)
    lines = note.splitlines()
    assert lines[0] == "## 2026-09-23 14:03 — Pending Review"
    assert "- Answer before you submit:" in lines
    assert '  - "Salary?" — text' in lines
    assert '  - "Visa?" — choice; options: Yes | No' in lines
    na = NeedsAttention("unanswered", "2 required question(s) have no answer in the files",
                        questions=[OpenQuestion("Salary?", "text"), OpenQuestion("Visa?", "choice", ["Yes", "No"])])
    na.url, na.title, na.page, na.stage, na.filled = "https://acme.io/a", "Apply", 2, "fill", 5
    note = records.needs_attention_note(na, WHEN)
    assert note.splitlines() == [
        "## 2026-09-23 14:03 — Needs Attention: unanswered",
        "- What: 2 required question(s) have no answer in the files",
        '- Where: https://acme.io/a · "Apply" · page 2 · fill',
        "- Filled before stopping: 5 fields",
        "- Open questions:",
        '  - "Salary?" — text',
        '  - "Visa?" — choice; options: Yes | No']


def test_report_and_exit_codes(tmp_path):
    na = NeedsAttention("unanswered", "1 required", questions=[OpenQuestion("Notice period?", "text")])
    r = Report(WHEN, results=[
        JobResult("Acme", "DE", "u1", "f1", 42, parked=Parked("u", "t", 3, generated=[("Why?", "Because.")]),
                  date="2026-09-23"),
        JobResult("Beta", "ML", "u2", "f2", 7, attention=na, date="2026-09-23"),
        JobResult("Gamma", "ML", "u3", "f3", 7, attention=NeedsAttention("x", "y", questions=[
            OpenQuestion("notice  period?", "text")]), date="2026-09-23")])
    assert r.exit_code() == 2 and r.results[0].terminal_line() == "✓ parked  Acme – DE"
    assert r.results[1].terminal_line() == "⚠ needs attention  Beta – ML: 1 required"
    pq = JobResult("Zeta", "SRE", "u4", "f4", 1, date="2026-09-23",       # park-at-question: labelled distinctly
                   parked=Parked("u", "t", 1, parked_at=[OpenQuestion("Mobile phone number", "text")]))
    assert pq.terminal_line() == "⏸ parked — 1 answer needed  Zeta – SRE"
    md = r.write(tmp_path).read_text()
    assert md.count("**Q:** Notice period?") == 1 and "(asked by Beta – ML, 2026-09-23)" in md
    for h in ("## Summary", "## Parked", "## Needs Attention", "## Queue anomalies",
              "## Questions for your Scratch Pad", "## Timings"):
        assert h in md
    assert Report(WHEN, stopped="alarm").exit_code() == 3 and Report(WHEN).exit_code() == 0



def test_report_lists_warnings_when_there_are_any(tmp_path):
    r = Report(WHEN, warnings=["Acme – DE: its tab could not be released (gone); the tab may close when the run ends"])
    md = r.write(tmp_path).read_text()
    assert "## Warnings" in md and "could not be released" in md
    assert "## Warnings" not in Report(WHEN).markdown()


def test_requeue_moves_needs_attention_jobs_back_and_resets_their_row(ws):
    """User decision 2026-09-23: the jobs of a run that all ended in Needs Attention go back to the queue."""
    t = Tracker(ws / "Job_Tracker.numbers").load()
    q = build_queue(ws / "Applications", t)
    rec = Recorder(t, Journal(ws / "runs/1/journal.jsonl"), ws, ws / "Pending-Review", ws / "Needs-Attention",
                   new_rows=set(q.new_rows))
    job, new = q.jobs
    rec.record(job, NEEDS_ATTENTION, "## note", "Needs Attention: navigation — no form")
    rec.record(new, PENDING_REVIEW, "## note", None)                 # parked jobs stay where they are
    t = Tracker(ws / "Job_Tracker.numbers").load()
    t.set_notes("4100000001", "Referral from Ana\nNeeds Attention: navigation — no form")
    t.save()
    lines = records.requeue(Tracker(ws / "Job_Tracker.numbers").load(), Journal(ws / "runs/2/journal.jsonl"),
                            ws / "Needs-Attention", ws / "Applications")
    assert lines == [f"requeued {job.folder}"]
    assert (ws / "Applications" / job.folder).exists() and not (ws / "Needs-Attention" / job.folder).exists()
    row = Tracker(ws / "Job_Tracker.numbers").load().find("4100000001")[1]
    assert row["Status"] == RESUME_BUILT and row["Notes"] == "Referral from Ana"     # only the run's line is gone
    assert "## note" in (ws / "Applications" / job.folder / "job.md").read_text()      # job.md keeps its history
    assert [j.key for j in build_queue(ws / "Applications", Tracker(ws / "Job_Tracker.numbers").load()).jobs] \
        == ["4100000001"]
    events = [json.loads(l)["event"] for l in (ws / "runs/2/journal.jsonl").read_text().splitlines()]
    assert events == ["record_start", "tracker_saved", "folder_moved", "record_done"]


def test_requeue_class_moves_only_that_class(ws):
    """T6: `requeue --class external_ats` moves back only the jobs a run recorded under that class."""
    t = Tracker(ws / "Job_Tracker.numbers").load()
    q = build_queue(ws / "Applications", t)
    rec = Recorder(t, Journal(ws / "runs/1/journal.jsonl"), ws, ws / "Pending-Review", ws / "Needs-Attention",
                   new_rows=set(q.new_rows))
    ext, other = q.jobs                                              # 4100000001, 4100000009
    rec.record(ext, NEEDS_ATTENTION, "## n", "Needs Attention: external_ats — external ATS, not yet supported")
    rec.record(other, NEEDS_ATTENTION, "## n", "Needs Attention: navigation — no form")
    lines = records.requeue(Tracker(ws / "Job_Tracker.numbers").load(), Journal(ws / "runs/2/journal.jsonl"),
                            ws / "Needs-Attention", ws / "Applications", klass="external_ats")
    assert lines == [f"requeued {ext.folder}"]
    assert (ws / "Applications" / ext.folder).exists()
    assert (ws / "Needs-Attention" / other.folder).exists()         # the navigation job stays put
    reloaded = Tracker(ws / "Job_Tracker.numbers").load()
    assert reloaded.find(ext.key)[1]["Status"] == RESUME_BUILT
    assert reloaded.find(other.key)[1]["Status"] == NEEDS_ATTENTION


def test_recorded_class_falls_back_to_job_md_heading(ws):
    """When the tracker Notes line was edited away, the class is read from the job.md heading."""
    job = make_job(ws / "Needs-Attention", "4100000055", "Zeta", "SRE")
    records.append_note(job / "job.md", "## 2026-10-07 09:00 — Needs Attention: external_ats\n- What: x")
    j = Job.from_dir(job)
    assert records.recorded_class(None, j) == "external_ats"
    assert records.recorded_class("Needs Attention: captcha — robots", j) == "captcha"   # Notes wins


def test_requeue_takes_a_parked_job_from_pending_review(ws):
    """`requeue --from pending-review` re-runs a job that is already parked and waiting to be submitted
    (user decision 2026-10-08, to exercise code paths whose only jobs were all parked). records.requeue
    already took the source directory; what is new is the CLI naming Pending-Review/ as that source. The
    "Needs Attention: …" Notes line it strips is simply not there for a parked job, so nothing else changes."""
    from assistant import cli
    t = Tracker(ws / "Job_Tracker.numbers").load()
    q = build_queue(ws / "Applications", t)
    rec = Recorder(t, Journal(ws / "runs/1/journal.jsonl"), ws, ws / "Pending-Review", ws / "Needs-Attention",
                   new_rows=set(q.new_rows))
    job, _new = q.jobs
    rec.record(job, PENDING_REVIEW, "## parked here", None)
    assert (ws / "Pending-Review" / job.folder).exists()
    assert not records.requeue(Tracker(ws / "Job_Tracker.numbers").load(),
                               Journal(ws / "runs/2/journal.jsonl"),
                               ws / "Needs-Attention", ws / "Applications")      # not there, so untouched

    lines = records.requeue(Tracker(ws / "Job_Tracker.numbers").load(), Journal(ws / "runs/3/journal.jsonl"),
                            ws / "Pending-Review", ws / "Applications")
    assert lines == [f"requeued {job.folder}"]
    assert (ws / "Applications" / job.folder).exists() and not (ws / "Pending-Review" / job.folder).exists()
    row = Tracker(ws / "Job_Tracker.numbers").load().find("4100000001")[1]
    assert row["Status"] == RESUME_BUILT
    assert "## parked here" in (ws / "Applications" / job.folder / "job.md").read_text()
    assert "4100000001" in [j.key for j in
                            build_queue(ws / "Applications", Tracker(ws / "Job_Tracker.numbers").load()).jobs]
    assert cli.REQUEUE_FROM["both"] == ("needs_attention", "pending_review")
