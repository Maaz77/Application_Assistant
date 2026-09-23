"""Job_Tracker.numbers adapter (§8.1). Tracker I/O is imported from Tools/Reconcile/reconcile.py."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
from pathlib import Path

from assistant.blockers import StopRun

VIEW_ID_RE = re.compile(r"/jobs/view/(?:[^/?#]*?-)?(\d{6,})")
CURRENT_JOB_ID_RE = re.compile(r"[?&]currentJobId=(\d+)")
RECONCILE_DIR = Path(__file__).resolve().parents[2] / "Reconcile"

RESUME_BUILT, PENDING_REVIEW, NEEDS_ATTENTION = "Resume Built", "Pending Review", "Needs Attention"


def job_id(url: str | None) -> str:
    """The key that joins a tracker row to a job folder.

    Order (§8.1): the numeric ID in /jobs/view/<slug->ID, else ?currentJobId=ID,
    else the URL normalised like reconcile.job_key (lowercased, no query, no trailing slash).
    Never derived from a folder name.
    """
    url = (url or "").strip()
    for rx in (VIEW_ID_RE, CURRENT_JOB_ID_RE):
        if m := rx.search(url):
            return m.group(1)
    return url.rstrip("/").split("?")[0].lower()


def _reconcile():
    if str(RECONCILE_DIR) not in sys.path:
        sys.path.insert(0, str(RECONCILE_DIR))
    import reconcile
    return reconcile


class TrackerError(RuntimeError):
    pass


def fingerprint(path: Path) -> tuple[float, str]:
    return os.stat(path).st_mtime, hashlib.sha256(path.read_bytes()).hexdigest()


class Tracker:
    """load() · find(job_id) · set(job_id, status, notes) · add(fields) · save(). Guards against outside edits."""

    def __init__(self, path: Path, sheet: str = "Jobs"):
        self.path, self.sheet = Path(path), sheet
        self.doc = self.table = None
        self.header: list[str] = []
        self._fp: tuple[float, str] | None = None

    def load(self) -> "Tracker":
        rec = _reconcile()
        if self.sheet == rec.SHEET_NAME:
            try:
                self.doc, self.table, _, _ = rec.load_tracker(self.path)   # reconcile's I/O and its checks
            except SystemExit as exc:                                      # reconcile.fatal() exits
                raise TrackerError(f"tracker could not be loaded by reconcile.py (exit {exc.code})") from exc
        else:
            from numbers_parser import Document
            self.doc = Document(str(self.path))
            self.table = self.doc.sheets[self.sheet].tables[0]
        self.header = [c.value for c in self.table.rows()[0]]
        for need in ("Status", "Job URL", "Notes"):
            if need not in self.header:
                raise TrackerError(f"tracker has no '{need}' column")
        self._fp = fingerprint(self.path)
        return self

    def col(self, name: str) -> int:
        return self.header.index(name)

    def rows(self) -> list[tuple[int, dict]]:
        return [(i, {h: c.value for h, c in zip(self.header, row)})
                for i, row in enumerate(self.table.rows()) if i > 0]

    def find(self, key: str) -> tuple[int, dict] | None:
        hits = [(i, r) for i, r in self.rows() if job_id(r.get("Job URL")) == key]
        return hits[0] if hits else None

    def set(self, key: str, status: str, notes: str | None = None) -> None:
        hit = self.find(key)
        if hit is None:
            raise TrackerError(f"no tracker row for job {key}")
        i, r = hit
        self.table.write(i, self.col("Status"), status)
        if notes:
            old = r.get("Notes") or ""
            self.table.write(i, self.col("Notes"), old + ("\n" if old else "") + notes)

    def add(self, fields: dict) -> None:
        i = self.table.num_rows
        for name, value in fields.items():
            if name in self.header and value is not None:
                self.table.write(i, self.col(name), value)

    def save(self) -> None:
        """Save only if nobody else changed the file since load (else StopRun, exit 3)."""
        if fingerprint(self.path) != self._fp:
            raise StopRun(f"tracker changed on disk since it was loaded: {self.path}")
        self.doc.save(str(self.path))
        self._fp = fingerprint(self.path)

    def backup(self, dst: Path) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.path, dst)
