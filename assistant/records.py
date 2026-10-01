"""Jobs, the queue (§1.3), recording a result (§8.2–8.4): tracker row + folder move + job.md note, journaled."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from assistant.blockers import NeedsAttention, StopRun
from assistant.tracker import NEEDS_ATTENTION, PENDING_REVIEW, RESUME_BUILT, Tracker, job_id

OVERVIEW_RE = r"^\s*(?:[-*]\s*)?(?:\*\*)?{label}:?(?:\*\*)?:?\s*(.+?)\s*$"   # "- Label: v" and "**Label:** v"
SECTION_HEADING = "# Application Assistant"


def overview_field(text: str, label: str) -> str | None:
    m = re.search(OVERVIEW_RE.format(label=re.escape(label)), text, re.M | re.I)
    return m.group(1).strip() if m else None


@dataclass
class Job:
    dir: Path
    linkedin_url: str
    company: str
    title: str
    key: str = field(init=False)

    def __post_init__(self):
        self.key = job_id(self.linkedin_url)

    @property
    def folder(self) -> str:
        return self.dir.name

    @property
    def job_md(self) -> Path:
        return self.dir / "job.md"

    @property
    def label(self) -> str:
        return f"{self.company} – {self.title}"

    def resume_pdf(self) -> Path:
        pdfs = sorted(self.dir.glob("Amin_*.pdf"))
        if len(pdfs) != 1:
            raise NeedsAttention("resume", "resume PDF missing or ambiguous"
                                 + (f" ({len(pdfs)} Amin_*.pdf files)" if pdfs else ""))
        return pdfs[0]

    @classmethod
    def from_dir(cls, d: Path) -> "Job | None":
        md = d / "job.md"
        if not md.exists():
            return None
        text = md.read_text()
        url = overview_field(text, "LinkedIn URL")
        if not url:
            return None
        parts = d.name.split("_", 2)          # {Jobid}_{Company}_{JobTitle} — display fallback only
        company = overview_field(text, "Company") or (parts[1] if len(parts) > 1 else d.name)
        title = overview_field(text, "Job Title") or (parts[2].replace("-", " ") if len(parts) > 2 else "")
        return cls(d, url, company, title)


@dataclass
class Queue:
    jobs: list[Job]
    new_rows: set[str]                        # job keys that need a tracker row added at record time
    anomalies: list[str]


def build_queue(applications: Path, tracker: Tracker) -> Queue:
    folders: dict[str, Job] = {}
    anomalies = []
    for d in sorted(p for p in applications.iterdir() if p.is_dir()) if applications.exists() else []:
        job = Job.from_dir(d)
        if job is None:
            anomalies.append(f"{d.name}: no job.md with a LinkedIn URL — skipped")
            continue
        if job.key in folders:
            anomalies.append(f"{d.name}: same job as {folders[job.key].folder} — skipped")
            continue
        folders[job.key] = job
    rows = {job_id(r.get("Job URL")): r for _, r in tracker.rows()}
    jobs, new_rows = [], set()
    for key, job in folders.items():
        row = rows.get(key)
        if row is None:
            jobs.append(job)
            new_rows.add(key)
        elif row.get("Status") == RESUME_BUILT:
            jobs.append(job)
        else:
            anomalies.append(f"{job.folder}: tracker status is {row.get('Status')!r}, not 'Resume Built' — skipped")
    for key, row in rows.items():
        if row.get("Status") == RESUME_BUILT and key not in folders:
            anomalies.append(f"tracker row {row.get('Job URL')} is 'Resume Built' but has no folder — skipped")
    return Queue(jobs, new_rows, anomalies)


# ------------------------------------------------------------------ journal (§8.4)

class Journal:
    def __init__(self, path: Path):
        self.path = path

    def event(self, job: str, event: str, status: str, src: str, dst: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": datetime.now().isoformat(timespec="seconds"), "job": job, "event": event,
                                 "status": status, "src": src, "dst": dst}) + "\n")


def unfinished(runs_dir: Path) -> list[dict]:
    """The last record_start of every job, across all runs, that has no record_done after it."""
    open_: dict[str, dict] = {}
    for jf in sorted(runs_dir.glob("*/journal.jsonl")):
        for line in jf.read_text().splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            if e["event"] == "record_start":
                open_[e["job"]] = e | {"journal": str(jf)}
            elif e["event"] == "record_done":
                open_.pop(e["job"], None)
    return list(open_.values())


def recover(runs_dir: Path, tracker: Tracker) -> list[str]:
    """Complete interrupted records idempotently (§8.4). Returns one line per recovered job."""
    done = []
    for e in unfinished(runs_dir):
        hit = tracker.find(e["job"])
        if hit and hit[1].get("Status") != e["status"]:
            tracker.set(e["job"], e["status"])
            tracker.save()
        src, dst = Path(e["src"]), Path(e["dst"])
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.rename(src, dst)
        elif src.exists() and dst.exists():
            raise StopRun(f"journal recovery: both {src} and {dst} exist")
        Journal(Path(e["journal"])).event(e["job"], "record_done", e["status"], e["src"], e["dst"])
        done.append(f"recovered {e['job']} → {e['status']}")
    return done


# ------------------------------------------------------------------ requeue

NA_NOTE_RE = re.compile(r"^Needs Attention: .*$\n?", re.M)


def requeue(tracker: Tracker, journal: Journal, needs_dir: Path, applications: Path,
            key: str | None = None) -> list[str]:
    """Put Needs-Attention jobs back in the queue (all, or only `key`): Status back to Resume Built, the
    "Needs Attention: …" line a run added to Notes removed, the folder moved back to Applications/. Journaled like
    a record, so recover() completes an interrupted requeue. job.md keeps its notes: they are the job's history."""
    done = []
    for d in sorted(p for p in needs_dir.iterdir() if p.is_dir()) if needs_dir.exists() else []:
        job = Job.from_dir(d)
        if job is None or (key and job.key != key):
            continue
        hit = tracker.find(job.key)
        if hit is None:
            done.append(f"skipped {d.name}: no tracker row")
            continue
        dst = applications / d.name
        if dst.exists():
            raise StopRun(f"requeue: {dst} already exists")
        journal.event(job.key, "record_start", RESUME_BUILT, str(d), str(dst))
        tracker.set(job.key, RESUME_BUILT)
        tracker.set_notes(job.key, NA_NOTE_RE.sub("", hit[1].get("Notes") or "").strip())
        tracker.save()
        journal.event(job.key, "tracker_saved", RESUME_BUILT, str(d), str(dst))
        os.rename(d, dst)
        journal.event(job.key, "folder_moved", RESUME_BUILT, str(d), str(dst))
        journal.event(job.key, "record_done", RESUME_BUILT, str(d), str(dst))
        done.append(f"requeued {d.name}")
    return done


# ------------------------------------------------------------------ job.md note (§8.3)

def append_note(job_md: Path, entry: str) -> None:
    text = job_md.read_text() if job_md.exists() else ""
    lines = text.split("\n")
    if not any(l.rstrip() == SECTION_HEADING for l in lines):
        text = text.rstrip("\n") + ("\n\n" if text.strip() else "") + SECTION_HEADING + "\n"
        job_md.write_text(text + "\n" + entry.rstrip("\n") + "\n")
        return
    start = next(i for i, l in enumerate(lines) if l.rstrip() == SECTION_HEADING)
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^# (?!#)", lines[i])), len(lines))
    body = lines[:end]
    while body and not body[-1].strip():
        body.pop()
    new = body + ["", *entry.rstrip("\n").split("\n")] + ([""] + lines[end:] if end < len(lines) else [])
    job_md.write_text("\n".join(new).rstrip("\n") + "\n")


def needs_attention_note(na: NeedsAttention, when: datetime) -> str:
    lines = [f"## {when:%Y-%m-%d %H:%M} — Needs Attention: {na.cls}",
             f"- What: {na.what}",
             f"- Where: {na.url or '-'} · \"{na.title or '-'}\" · page {na.page} · {na.stage or '-'}",
             f"- Filled before stopping: {na.filled} fields"]
    if na.questions or na.optional:
        lines.append("- Open questions:")
        for q in na.questions:
            lines.append(f"  - \"{q.question}\" — {q.kind}" + (f"; options: {' | '.join(q.options)}" if q.options else ""))
        for q in na.optional:
            lines.append(f"  - \"{q.question}\" — {q.kind}, optional, left empty")
    return "\n".join(lines)


def parked_note(parked, when: datetime) -> str:
    lines = [f"## {when:%Y-%m-%d %H:%M} — Pending Review",
             f"- Parked at: {parked.url} · \"{parked.title}\" · page {parked.pages}"]
    if parked.parked_at:
        lines.append("- Answer before you submit:")
        for q in parked.parked_at:
            lines.append(f"  - \"{q.question}\" — {q.kind}" + (f"; options: {' | '.join(q.options)}" if q.options else ""))
    for q, t in parked.generated:
        lines.append(f"- Generated for \"{q}\": {t[:200]}{'…' if len(t) > 200 else ''}")
    for q, v in parked.prefills:
        lines.append(f"- Kept LinkedIn pre-fill \"{q}\": {v}")
    for q in parked.optional_empty:
        lines.append(f"- Left empty (optional): \"{q.question}\"")
    return "\n".join(lines)


# ------------------------------------------------------------------ record (§8.2)

@dataclass
class Recorder:
    tracker: Tracker
    journal: Journal
    base: Path
    pending_dir: Path
    needs_dir: Path
    new_rows: set[str] = field(default_factory=set)

    def record(self, job: Job, status: str, note: str, tracker_note: str | None) -> Path:
        """job.md note, then tracker row and folder move together, journaled. Returns the new folder path."""
        dst_root = self.pending_dir if status == PENDING_REVIEW else self.needs_dir
        dst = dst_root / job.folder
        if dst.exists():
            raise StopRun(f"destination folder already exists: {dst}")
        append_note(job.job_md, note)
        self.journal.event(job.key, "record_start", status, str(job.dir), str(dst))
        if job.key in self.new_rows:
            self.tracker.add({"Job URL": job.linkedin_url, "Status": status, "Notes": tracker_note,
                              "Company": job.company, "Job Title": job.title})
            self.new_rows.discard(job.key)
        else:
            self.tracker.set(job.key, status, tracker_note)
        self.tracker.save()
        self.journal.event(job.key, "tracker_saved", status, str(job.dir), str(dst))
        dst_root.mkdir(parents=True, exist_ok=True)
        os.rename(job.dir, dst)
        self.journal.event(job.key, "folder_moved", status, str(job.dir), str(dst))
        self.journal.event(job.key, "record_done", status, str(job.dir), str(dst))
        return dst
