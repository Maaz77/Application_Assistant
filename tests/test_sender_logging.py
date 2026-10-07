"""P0 T3 — every sender writes one inference-log entry per HTTP attempt, retries and fallbacks included."""
import json

import httpx
import pytest

from assistant import decide, inference_log as L, llm_inference as A

pytestmark = pytest.mark.unit
OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"


def _read(tmp, name):
    return json.loads((tmp / "_run" / name).read_text())


def test_decider_logs_every_jev_attempt_including_a_retry(tmp_path):
    L.start_run(tmp_path)
    n = []

    def post(url, body, headers, timeout):
        n.append(1)
        if len(n) == 1:
            return 503, {"error": "Service temporarily unavailable"}     # first attempt fails -> one retry
        return 200, {"answers": {"q1": {"type": "noul", "noul": 0.9}}}

    d = decide.Decider("k", "kev-latest", url="http://127.0.0.1:8009/v1/systemone", post=post,
                       sleep=lambda s: None)
    d.ask("topic", {"page": "x"}, {"q1": decide.noul({"instructions": "?"})})
    entries = _read(tmp_path, L._JEV_FILE)
    assert len(entries) == 2
    assert entries[0]["Response"] == {"error": "Service temporarily unavailable"}
    # keep_object_state is the default now (Kev renders an object as labeled text), so the state is logged
    # as the object that was sent, not as the JSON string a cloud route needed.
    assert entries[1]["Noul"][0]["id"] == "q1" and entries[1]["State"] == {"page": "x"}


def test_ask_model_logs_every_llm_attempt_including_the_json_object_fallback(tmp_path):
    L.start_run(tmp_path)

    def post(url, body, headers, timeout):
        if body["response_format"]["type"] == "json_schema":
            return 400, {"error": {"message": "structured outputs unsupported"}}   # forces the json_object retry
        return 200, {"model": "mm", "choices": [{"message": {"content": '{"questions": []}'}}]}

    A.call_engine(key="k", models="m", system="sys", user={"a": 1}, post=post, url=OPENROUTER)
    entries = _read(tmp_path, L._LLM_FILE)
    assert len(entries) == 2
    assert entries[0]["parameters"]["response_format"]["type"] == "json_schema"
    assert entries[1]["parameters"]["response_format"] == {"type": "json_object"}
    assert entries[1]["model"] == "mm"                       # response.model, not the requested "m"
    assert entries[0]["messages"][0]["content"] == "sys"     # full prompt is logged


def test_ask_model_logs_an_unreachable_attempt_with_no_body(tmp_path):
    L.start_run(tmp_path)

    def post(url, body, headers, timeout):
        raise httpx.ConnectError("connection refused")

    with pytest.raises(A.LLMInferenceError):
        A.call_engine(key="k", models="m", system="s", user={}, post=post, url=OPENROUTER)
    assert _read(tmp_path, L._LLM_FILE)[-1]["completion"].startswith("<no response body:")
