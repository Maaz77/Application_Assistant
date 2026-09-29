"""Replay harness: offline re-run of recorded jobs (P3 T1).

ReplayBrowser returns recorded browser_actions.jsonl results in order.
replay_gateway_post builds a Gateway post callable matched on request content.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any


class ReplayMiss(RuntimeError):
    """A request during replay had no match in the fixture (strict mode)."""


# ------------------------------------------------------------------ browser

class ReplayBrowser:
    """Duck-types Browser; returns recorded browser_actions.jsonl results in order.

    Guard runs on every act() so replays exercise never-submit.
    """

    def __init__(self, entries: list[dict], *, mode: str = "strict"):
        self._entries = entries
        self._cursor = 0
        self._mode = mode
        self.actions_log: Path | None = None
        self.cfg: Any = None

    @property
    def consumed(self) -> int:
        return self._cursor

    @property
    def total(self) -> int:
        return len(self._entries)

    def _next(self, tool: str) -> str:
        while self._cursor < len(self._entries):
            entry = self._entries[self._cursor]
            if entry["tool"] == tool:
                self._cursor += 1
                return entry["result"]
            if self._mode == "strict":
                raise ReplayMiss(f"expected {tool!r} at #{self._cursor}, got {entry['tool']!r}")
            self._cursor += 1
        if self._mode == "strict":
            raise ReplayMiss(f"replay exhausted looking for {tool!r} (consumed {len(self._entries)} entries)")
        return ""

    # -- infrastructure (no-ops) --
    def connect(self, timeout=None):
        pass

    def detach(self):
        pass

    def forget(self, session: str) -> str:
        return f"forgot {session}"

    def close(self, session: str) -> str:
        if self._cursor < len(self._entries) and self._entries[self._cursor]["tool"] == "close":
            return self._next("close")
        return f"closed {session}"

    def doctor(self) -> dict:
        return {}

    def new_tabs(self, session: str) -> list:
        return []

    def list_tabs(self) -> list:
        return []

    def session_target(self, session: str) -> str:
        return "replay-target"

    def close_tab_id(self, session: str, target_id: str):
        pass

    # -- typed calls --
    def open(self, url: str, session: str, **kw) -> str:
        return self._next("open")

    def observe(self, session: str, **kw) -> str:
        return self._next("observe")

    def table(self, session: str):
        from assistant.browser import split_json
        return split_json(self.observe(session))

    def act(self, ops: list[dict], session: str, table, **kw) -> str:
        from assistant import guard
        for op in ops:
            guard.check(op, table)
        return self._next("act")

    def probe(self, session: str, *names: str) -> dict[str, dict]:
        from assistant import probes
        from assistant.browser import Table
        ops = [{"op": "eval", "js": probes.EVAL_PROBES[n]} for n in names]
        text = self.act(ops, session, Table(url=""), observe_after=False, stop_on_error=False)
        values = probes.parse_eval_results(text)
        if len(values) != len(names):
            raise probes.ProbeError(f"expected {len(names)} probe results, got {len(values)}")
        return dict(zip(names, values))

    def assert_(self, checks: list[dict], session: str) -> str:
        return self._next("assert")

    def captcha_present(self, session: str) -> bool:
        return self.assert_([{}], session).startswith("PASS")

    def tabs(self, session: str, action: str = "list", **kw) -> str:
        if self._cursor < len(self._entries) and self._entries[self._cursor]["tool"] == "tabs":
            return self._next("tabs")
        return ""


# ------------------------------------------------------------------ gateway

def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _jev_key(state: Any, question_ids: list[str]) -> str:
    s = json.dumps(state, sort_keys=True, ensure_ascii=False)
    return _hash(s + "|" + "|".join(sorted(question_ids)))


def _llm_key(messages: list, parameters: dict) -> str:
    m = json.dumps(messages, sort_keys=True, ensure_ascii=False)
    p = json.dumps(parameters, sort_keys=True, ensure_ascii=False)
    return _hash(m + "|" + p)


def _is_jev(url: str) -> bool:
    return "systemone" in url or "alpha/decisions" in url


def replay_gateway_post(jev_logs: list[dict], llm_logs: list[dict], *, mode: str = "strict"):
    """Build a Gateway post callable that returns recorded model responses.

    Jev match: State hash + sorted question IDs.
    LLM match: messages hash + parameters (minus model).
    """
    jev_cache: dict[str, Any] = {}
    for entry in jev_logs:
        qids = [q["id"] for q in entry.get("Score", []) + entry.get("Noul", []) + entry.get("Choice", [])]
        key = _jev_key(entry["State"], qids)
        jev_cache[key] = entry["Response"]

    llm_cache: dict[str, dict] = {}
    for entry in llm_logs:
        key = _llm_key(entry["messages"], entry["parameters"])
        llm_cache[key] = {
            "choices": [{"message": {"content": entry["completion"]}}],
            "model": entry.get("model", ""),
            "usage": {"prompt_tokens": 100, "completion_tokens": 100},
        }

    stats = SimpleNamespace(jev_hits=0, llm_hits=0, jev_misses=0, llm_misses=0)

    def post(url: str, body: dict, headers: dict, timeout: float) -> tuple[int, Any]:
        if _is_jev(url):
            qids = list((body.get("questions") or {}).keys())
            key = _jev_key(body.get("state"), qids)
            if key in jev_cache:
                stats.jev_hits += 1
                return 200, jev_cache[key]
            stats.jev_misses += 1
            if mode == "strict":
                raise ReplayMiss(f"Jev request not in fixture (key={key}, {len(qids)} questions)")
        else:
            params = {k: v for k, v in body.items() if k not in ("model", "messages")}
            key = _llm_key(body.get("messages", []), params)
            if key in llm_cache:
                stats.llm_hits += 1
                return 200, llm_cache[key]
            stats.llm_misses += 1
            if mode == "strict":
                raise ReplayMiss(f"LLM request not in fixture (key={key})")
        raise ReplayMiss(f"no match for {url}")

    post.stats = stats  # type: ignore[attr-defined]
    return post


# ------------------------------------------------------------------ fixture I/O

def load_fixture(path: Path) -> tuple[list[dict], list[dict], list[dict]]:
    """Load (browser_actions, jev_logs, llm_logs) from a job folder."""
    ba = path / "browser_actions.jsonl"
    if not ba.exists():
        raise FileNotFoundError(f"{ba} not found")
    entries = [json.loads(line) for line in ba.read_text().splitlines() if line.strip()]
    jev_path = path / "jev_inference_logs.json"
    jev_logs = json.loads(jev_path.read_text()) if jev_path.exists() else []
    llm_path = path / "llm_inference_logs.json"
    llm_logs = json.loads(llm_path.read_text()) if llm_path.exists() else []
    return entries, jev_logs, llm_logs


# ------------------------------------------------------------------ runner

def _find_job_dir(cfg, url: str):
    """Search Applications, Pending-Review, Needs-Attention for folder matching URL's job ID."""
    from assistant.tracker import job_id
    jid = job_id(url)
    for key in ("applications", "pending_review", "needs_attention"):
        parent = cfg.path(key)
        if not parent.exists():
            continue
        for d in parent.iterdir():
            if d.name.startswith(jid) and (d / "job.md").exists():
                return d
    return None


class _FakeBook:
    """Minimal TabBook stand-in for replay — no real tabs."""
    def handles(self): return set()
    def current_handle(self, s): return "replay"
    def close_junk(self, s, baseline, keep): pass
    def release(self, s): pass
    def close(self): pass


def replay_job(path, *, mode: str = "strict", cfg=None) -> int:
    """Replay one recorded job offline. Returns 0 on success, 1 on error."""
    from datetime import date
    from assistant import config as config_mod, decide, gateway as gw_mod, guard, inference_log, pages
    from assistant.fill import JobCtx, run_pages
    from assistant.llm_inference import Policy, Sources, answer_page, resume_text
    from assistant.rotation import Rotation
    from assistant.blockers import NeedsAttention, Parked
    from assistant.records import Job

    path = Path(path)
    entries, jev_logs, llm_logs = load_fixture(path)
    print(f"Fixture: {len(entries)} browser, {len(jev_logs)} Jev, {len(llm_logs)} LLM entries")

    cfg = cfg or config_mod.load()
    browser = ReplayBrowser(entries, mode=mode)
    browser.cfg = cfg

    post = replay_gateway_post(jev_logs, llm_logs, mode=mode)
    gateway = gw_mod.private(post=post, max_attempts=1)
    gw_mod.use(gateway)
    decider = decide.for_config(cfg, gateway)
    decide.use(decider)
    inference_log.start_run(None)

    first_open = next((e for e in entries if e["tool"] == "open"), None)
    url = first_open["args"]["url"] if first_open else ""
    session = first_open["args"].get("session", "replay") if first_open else "replay"
    job_dir = _find_job_dir(cfg, url)
    if not job_dir:
        print(f"✗ job folder for {url!r} not found in Applications/Pending-Review/Needs-Attention")
        return 1

    job = Job(dir=job_dir, linkedin_url=url, company="replay", title="replay")
    pdf = job.resume_pdf()
    profile = cfg.path("profile").read_text()
    src = Sources(profile=profile, job=job.job_md.read_text(), resume=resume_text(pdf))
    policy = Policy(cfg.policy.prefill, cfg.policy.free_text_max_chars)
    engines = Rotation(cfg.models.llm_inference)
    key = config_mod.chat_key(cfg)

    def engine(p):
        return answer_page(p, src, key=key, models=engines, policy=policy,
                           today=date.today(), url=config_mod.chat_url(cfg))

    ctx = JobCtx(browser=browser, session=session, book=_FakeBook(), resume_pdf=pdf,
                 answer_fn=engine, baseline=set(), max_pages=cfg.browser.max_pages_per_job,
                 sleep=lambda _: None)

    guard.FORM.started = False
    guard.FORM.final = False
    result = ""
    try:
        browser.open(url, session)
        p = ctx.read()
        if any(m in p.url for m in pages.SIGNED_OUT_MARKERS):
            result = "signed out"
        else:
            parked = run_pages(ctx)
            result = f"Parked: {parked.how}"
    except NeedsAttention as na:
        result = f"Needs Attention: {na.cls} — {na.what}"
    except ReplayMiss as rm:
        result = f"REPLAY MISS: {rm}"
    except Exception as exc:
        result = f"Error: {type(exc).__name__}: {exc}"
    finally:
        gw_mod.use(None)
        decide.use(None)

    s = post.stats
    print(f"\nResult: {result}")
    print(f"Browser: {browser.consumed}/{browser.total} consumed")
    print(f"Model: Jev {s.jev_hits} hits/{s.jev_misses} misses, LLM {s.llm_hits} hits/{s.llm_misses} misses")
    print(f"Pages: {ctx.pages} visited, {ctx.filled_count} filled")

    stages = ["opened"]
    if ctx.stage != "entry":
        stages.append("Easy Apply dialog")
    if ctx.filled_count > 0:
        stages.append(f"{ctx.filled_count} filled")
    if ctx.pages > 1:
        stages.append(f"advanced {ctx.pages - 1}x")
    stages.append(result)
    print(f"Funnel: {' → '.join(stages)}")
    return 0
