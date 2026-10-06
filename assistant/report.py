"""runs/<ts>/report.md, terminal lines and exit codes (§8.5)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from assistant.blockers import NeedsAttention, Parked
from assistant.gateway import Counters

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
    models: Counters | None = None      # this job's model requests, from the Gateway (P1 T6)

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
    decisions: tuple[int, float] | None = None      # the decision model: calls and cost in USD (each one is in
    decision_model: str = ""                        # jev_inference_logs.json); a model on this machine costs nothing
    gateway: Counters | None = None                 # the run's model requests (P1 T6); a Gateway is also accepted
    by_fallback: int = 0                            # decisions the chat fallback answered (D19, T4)
    nothing_matched: str | None = None              # --job named a job that is not in the queue: exit 1, not 0

    @property
    def totals(self) -> Counters | None:
        """The run's counters, whether a Gateway or a bare Counters was handed over."""
        g = self.gateway
        return getattr(g, "run", g) if g is not None else None

    def exit_code(self) -> int:
        if self.stopped:
            return EXIT_STOPPED
        if self.nothing_matched:
            # A run asked for one job by name and found none is a usage error, not "all parked" (live
            # 2026-10-06: `run --job <a job sitting in Needs-Attention/>` printed seven green preflight
            # ticks, worked on nothing, wrote an empty report and exited 0).
            return EXIT_PREFLIGHT
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
        if self.nothing_matched:
            L.append(f"- **No job matched `--job`:** {self.nothing_matched}")
        if self.recovered:
            L += ["- Journal recovery: " + "; ".join(self.recovered)]
        if self.decisions:
            cost = f", ${self.decisions[1]:.4f}" if self.decisions[1] else ""
            L.append(f"- Decisions by {self.decision_model or 'the System One model'}: "
                     f"{self.decisions[0]} calls{cost}")
        if self.by_fallback:
            L.append(f"- Decisions by fallback: {self.by_fallback}")
        if (t := self.totals) is not None:
            spend = f"${t.cost:.4f}{' (estimated)' if t.estimated else ' (reported)'}" if t.cost else "$0"
            L += [f"- Model requests: {t.jev_requests} System One, {t.chat_requests} LLM inference "
                  f"({t.attempts} attempts, {t.failures} failed)",
                  f"- Model spend: {spend}",
                  f"- Highest number of requests in flight: {t.in_flight}"]
        L += ["", "## Parked", ""]
        for r in parked:
            L.append(f"### {r.label}")
            L += [f"- URL: {r.url}", f"- Folder: {r.folder}", f"- Parked on: {r.parked.url} (page {r.parked.pages})"]
            L += [f"- Generated — \"{q}\": {t}" for q, t in r.parked.generated]
            for q, v in r.parked.prefills:
                L.append(f"- Kept pre-fill — \"{q}\": {v}")
            if r.parked.parked_at:
                L.append("")
                L.append("  Questions for your Scratch Pad:")
                for q in r.parked.parked_at:
                    L.append(f"    - \"{q.question}\" — {q.kind}" + (f"; options: {' | '.join(q.options)}" if q.options else ""))
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
        L += ["## Timings", ""] + [f"- {r.label}: {r.seconds:.0f} s" + (f" · {r.models.row()}" if r.models else "")
                                   for r in self.results]
        return "\n".join(L) + "\n"

    def write(self, run_dir: Path) -> Path:
        path = run_dir / "report.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.markdown())
        return path
