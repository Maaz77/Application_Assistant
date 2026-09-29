"""Replay harness tests (P3 T1)."""
import json
import pytest
from pathlib import Path
from tests.replay.harness import ReplayBrowser, ReplayMiss, replay_gateway_post, load_fixture


FIXTURE = Path(__file__).parent / "replay" / "fixtures" / "linda-ai-p2"


@pytest.mark.unit
class TestReplayBrowser:
    def test_sequential_consume(self):
        entries = [
            {"tool": "open", "args": {}, "result": "ok open"},
            {"tool": "observe", "args": {}, "result": "page text"},
            {"tool": "act", "args": {}, "result": "ok act"},
        ]
        rb = ReplayBrowser(entries)
        assert rb.open("http://x", "s") == "ok open"
        assert rb.observe("s") == "page text"
        assert rb.consumed == 2
        assert rb.total == 3

    def test_strict_mismatch_raises(self):
        entries = [{"tool": "observe", "args": {}, "result": "x"}]
        rb = ReplayBrowser(entries, mode="strict")
        with pytest.raises(ReplayMiss, match="expected"):
            rb.open("http://x", "s")

    def test_strict_exhausted_raises(self):
        rb = ReplayBrowser([], mode="strict")
        with pytest.raises(ReplayMiss, match="exhausted"):
            rb.observe("s")

    def test_lenient_skips(self):
        entries = [
            {"tool": "open", "args": {}, "result": "opened"},
            {"tool": "observe", "args": {}, "result": "page"},
        ]
        rb = ReplayBrowser(entries, mode="lenient")
        assert rb.observe("s") == "page"
        assert rb.consumed == 2


@pytest.mark.unit
class TestReplayGateway:
    def test_jev_hit(self):
        logs = [{"State": {"url": "http://x"}, "Score": [{"id": "q1"}], "Noul": [], "Choice": [],
                 "Response": {"q1": {"answer": "yes", "score": 0.9}}}]
        post = replay_gateway_post(logs, [])
        status, body = post("http://api/systemone", {"state": {"url": "http://x"}, "questions": {"q1": {}}}, {}, 10)
        assert status == 200
        assert body["q1"]["answer"] == "yes"
        assert post.stats.jev_hits == 1

    def test_llm_hit(self):
        logs = [{"messages": [{"role": "user", "content": "hi"}], "parameters": {"temperature": 0},
                 "model": "test", "completion": '{"answers": []}'}]
        post = replay_gateway_post([], logs)
        status, body = post("http://api/chat", {"messages": [{"role": "user", "content": "hi"}],
                                                 "temperature": 0, "model": "gpt"}, {}, 10)
        assert status == 200
        assert body["choices"][0]["message"]["content"] == '{"answers": []}'
        assert post.stats.llm_hits == 1

    def test_strict_miss(self):
        post = replay_gateway_post([], [])
        with pytest.raises(ReplayMiss):
            post("http://api/systemone", {"state": {}, "questions": {"q1": {}}}, {}, 10)


@pytest.mark.unit
def test_load_fixture():
    if not FIXTURE.exists():
        pytest.skip("P2 fixture not present")
    entries, jev_logs, llm_logs = load_fixture(FIXTURE)
    assert len(entries) == 51
    assert len(jev_logs) == 13
    assert len(llm_logs) == 3
