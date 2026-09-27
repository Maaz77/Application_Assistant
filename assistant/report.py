"""runs/<ts>/report.md, terminal lines and exit codes (§8.5)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from assistant.blockers import NeedsAttention, Parked

EXIT_OK, EXIT_PREFLIGHT, EXIT_ATTENTION, EXIT_STOPPED = 0, 1, 2, 3


@dataclass
class JobResult:
    company: str
    title: str
    url: str
    folder: str
    seconds: float
    parked: Parked | None = None
    attention: NeedsAttention | None = None
    date: str = ""

    @property
    def label(self) -> str:
        return f"{self.company} – {self.title}"

    def terminal_line(self) -> str:
        if self.parked:
            return f"✓ parked  {self.label}"
        return f"⚠ needs attention  {self.label}: {self.attention.what}"


@dataclass
class Report:
    started: datetime
    results: list[JobResult] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    recovered: list[str] = field(default_factory=list)
    stopped: str | None = None
    decisions: tuple[int, float] | None = None      # Jev: calls and cost in USD (jev_inference_logs.json has each one)

    def exit_code(self) -> int:
        if self.stopped:
            return EXIT_STOPPED
        return EXIT_ATTENTION if any(r.attention for r in self.results) else EXIT_OK

    def scratch_questions(self) -> list[str]:
        """Deduplicated Scratch Pad suggestions, in the Profile.md Scratch Pad style."""
        seen, out = set(), []
        for r in self.results:
            qs = (r.attention.questions if r.attention else []) + \
                 (r.attention.optional if r.attention else r.parked.optional_empty)
            for q in qs:
                key = " ".join(q.question.lower().split())
                if key in seen:
                    continue
                seen.add(key)
                out.append(f"- **Q:** {q.question} — **A:** ___ _(asked by {r.label}, {r.date})_")
        return out

    def markdown(self) -> str:
        parked = [r for r in self.results if r.parked]
        attention = [r for r in self.results if r.attention]
        L = [f"# Application Assistant run {self.started:%Y-%m-%d %H:%M}", "", "## Summary", "",
             f"- Parked (Pending Review): {len(parked)}", f"- Needs Attention: {len(attention)}",
             f"- Queue anomalies: {len(self.anomalies)}"]
        if self.stopped:
            L.append(f"- **Run stopped:** {self.stopped}")
        if self.recovered:
            L += ["- Journal recovery: " + "; ".join(self.recovered)]
        if self.decisions:
            L.append(f"- Decisions by Jev: {self.decisions[0]} calls, ${self.decisions[1]:.4f}")
        L += ["", "## Parked", ""]
        for r in parked:
            L.append(f"### {r.label}")
            L += [f"- URL: {r.url}", f"- Folder: {r.folder}", f"- Parked on: {r.parked.url} (page {r.parked.pages})"]
            for q, t in r.parked.generated:
                L.append(f"- Generated — \"{q}\": {t}")
            for q, v in r.parked.prefills:
                L.append(f"- Kept pre-fill — \"{q}\": {v}")
            L.append("")
        L += ["## Needs Attention", ""]
        for r in attention:
            a = r.attention
            L += [f"### {r.label}", f"- Reason: {a.cls} — {a.what}",
                  f"- Where: {a.url or r.url} · \"{a.title}\" · page {a.page} · {a.stage}", f"- Folder: {r.folder}", ""]
        L += ["## Queue anomalies", ""] + [f"- {x}" for x in self.anomalies] + [""]
        if self.warnings:
            L += ["## Warnings", ""] + [f"- {x}" for x in self.warnings] + [""]
        L += ["## Questions for your Scratch Pad", ""] + self.scratch_questions() + [""]
        L += ["## Timings", ""] + [f"- {r.label}: {r.seconds:.0f} s" for r in self.results]
        return "\n".join(L) + "\n"

    def write(self, run_dir: Path) -> Path:
        path = run_dir / "report.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.markdown())
        return path
