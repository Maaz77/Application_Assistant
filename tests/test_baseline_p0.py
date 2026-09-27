"""P0 T0/T6 — prove the re-core changes no behaviour.

Runs a fixed set of offline flows (FakeMCP + RuleDecider) and snapshots the three streams that P0's edits could
drift: the ordered Jev decisions (topic, question ids, answers used), the browser calls (function and arguments),
and the LLM inference model request bodies. The first run writes tests/golden/p0_baseline/; later runs assert the
streams are unchanged, after normalising the P0 component rename (see _canon) so T1 does not trip T6.
"""
import json
import re
from pathlib import Path

import pytest

from assistant import llm_inference as A, decide
from assistant.llm_inference import Policy
from assistant.fill import run_pages
from tests import test_fill_loop as F
from tests.fake_mcp import FakeMCP
from tests.rule_decider import RuleDecider
from tests.test_answers import PAGE, SRC, TODAY

pytestmark = pytest.mark.unit
BASELINE = Path(__file__).parent / "golden" / "p0_baseline"


def _canon(s: str) -> str:
    """Fold what P0 deliberately changes so the pre-P0 baseline still matches: the component rename, and the
    screenshot artifact's filename (T4 moved it to <job>/screenshot.jpg — an output sink, not call behaviour)."""
    s = re.sub(r"(?i)answer[ _-]?engine", "LLMINFERENCE", s)
    return re.sub(r'/shots/[^"]*\.jpg', "/shots/<SHOT>", s)


def _compact(a) -> dict:
    return {"p": round(a.noul or 0, 3)} if a.type == "noul" else {"c": a.choice, "conf": round(a.confidence or 0, 3)}


class _Rec:
    """Wraps the run's Decider to record every ask() without changing its answers."""

    def __init__(self, inner, sink):
        self._inner, self._sink = inner, sink

    def ask(self, topic, state, questions):
        ans = self._inner.ask(topic, state, questions)
        self._sink.append({"topic": topic, "ids": sorted(questions),
                           "answers": {k: _compact(v) for k, v in sorted(ans.items())}})
        return ans

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _run_flows(tmp_path):
    decisions, bodies, calls = [], [], []
    decide.use(_Rec(RuleDecider(), decisions))

    # A) the LLM inference: capture the model request body. An empty answer set keeps the response deterministic;
    # the request body (what the rename must not change) does not depend on it.
    def capture(url, body, headers, timeout):
        bodies.append(body)
        return 200, {"choices": [{"message": {"content": json.dumps({"questions": []})}}]}

    A.answer_page(PAGE, SRC, key="k", models="baseline", policy=Policy(), today=TODAY, post=capture)

    # B) fill flows: browser calls (FakeMCP.log) plus the fill / read-back / coverage decisions.
    flows = [(F.single_page, F.SINGLE_ANSWERS, "p1", {}),
             (F.steps_site, F.STEPS_ANSWERS, "s1", {}),
             (F.widget_page, F.WIDGET_ANSWERS, "p1", {"goal_clicks_submit": True})]
    for site_fn, ans, start, kw in flows:
        fake = FakeMCP(site_fn(), start, **kw)
        try:
            parked = run_pages(F.ctx_for(fake, ans, tmp_path))
            outcome = f"parked pages={parked.pages}"
        except Exception as exc:  # noqa: BLE001 - the outcome (park vs which NeedsAttention) is part of the snapshot
            outcome = f"{type(exc).__name__}: {exc}"
        calls.append({"flow": site_fn.__name__, "outcome": outcome, "log": fake.log})
    return decisions, bodies, calls


def _serialize(decisions, bodies, calls, tmp_path) -> dict[str, str]:
    tmp = str(tmp_path)
    dump = lambda x: _canon(json.dumps(x, indent=2, ensure_ascii=False, default=str)).replace(tmp, "<TMP>")
    return {"decisions.json": dump(decisions), "model_bodies.json": dump(bodies), "browser_calls.json": dump(calls)}


def test_no_behaviour_change(tmp_path):
    out = _serialize(*_run_flows(tmp_path), tmp_path)
    if not BASELINE.exists():
        BASELINE.mkdir(parents=True)
        for name, text in out.items():
            (BASELINE / name).write_text(text)
        pytest.skip("p0_baseline written; rerun to compare against it")
    for name, text in out.items():
        assert text == _canon((BASELINE / name).read_text()), f"{name} drifted from tests/golden/p0_baseline"
