"""P0 T5 — assistant/inference_log.py: the two entry shapes (00_common §6.2, §6.3), full-length logging, failed
attempts, always-valid JSON, scope switching (incl. from another thread), and no secret ever reaching a log."""
import json
import threading

import pytest
from dotenv import dotenv_values

from assistant import config, inference_log as L

pytestmark = pytest.mark.unit


def _read(tmp, scope, name):
    return json.loads((tmp / scope / name).read_text())


def test_llm_entry_has_exactly_the_six2_keys(tmp_path):
    L.start_run(tmp_path)
    L.log_llm("openrouter", {"model": "m", "messages": [{"role": "user", "content": "hi"}], "temperature": 0,
                             "max_tokens": 9}, {"model": "m-v2", "choices": [{"message": {"content": "ok"}}]})
    e = _read(tmp_path, "_run", L._LLM_FILE)[0]
    assert set(e) == {"model", "provider", "parameters", "messages", "completion"}
    assert e["model"] == "m-v2"                                  # response.model wins over the requested model
    assert e["parameters"] == {"temperature": 0, "max_tokens": 9}   # model and messages are excluded
    assert e["completion"] == "ok"


def test_jev_entry_grouped_by_type_with_ids_and_empty_types(tmp_path):
    L.start_run(tmp_path)
    L.log_jev({"model": "jev", "state": {"page": "x"}, "questions": {
        "q1": {"type": "noul", "instructions": "a"},
        "q2": {"type": "choice", "instructions": "b", "criteria": {"x": "y"}},
    }}, {"answers": {}})
    e = _read(tmp_path, "_run", L._JEV_FILE)[0]
    assert set(e) == {"State", "Score", "Noul", "Choice", "Response"}
    assert e["State"] == {"page": "x"}
    assert e["Score"] == []                                       # a type with no questions is []
    assert e["Noul"] == [{"id": "q1", "type": "noul", "instructions": "a"}]
    assert [q["id"] for q in e["Choice"]] == ["q2"] and e["Choice"][0]["criteria"] == {"x": "y"}


def test_long_message_and_completion_are_logged_in_full(tmp_path):
    L.start_run(tmp_path)
    big = "x" * 50_000
    L.log_llm("openrouter", {"model": "m", "messages": [{"role": "user", "content": big}]},
              {"choices": [{"message": {"content": big}}]})
    e = _read(tmp_path, "_run", L._LLM_FILE)[0]
    assert e["messages"][0]["content"] == big
    assert len(e["completion"]) == 50_000


def test_failed_attempts_record_the_error_body_or_a_placeholder(tmp_path):
    L.start_run(tmp_path)
    body = {"model": "m", "messages": []}
    L.log_llm("openrouter", body, {"error": {"code": 429, "message": "rate-limited"}})   # 429 body
    L.log_llm("openrouter", body, {"error": {"code": 503, "message": "overloaded"}})     # 503 body
    L.log_llm("openrouter", body, None, "timeout after 45 s")                            # no body
    L.log_llm("openrouter", body, {"error": {"message": "bad"}, "choices": []})          # HTTP 200 with error body
    es = _read(tmp_path, "_run", L._LLM_FILE)
    assert es[0]["completion"] == json.dumps({"error": {"code": 429, "message": "rate-limited"}}, ensure_ascii=False)
    assert "overloaded" in es[1]["completion"]
    assert es[2]["completion"] == "<no response body: timeout after 45 s>"
    assert "bad" in es[3]["completion"]                          # the empty choice falls back to the raw body
    # Jev with no body uses the same placeholder shape
    L.log_jev({"state": {}, "questions": {}}, None, "connection refused")
    assert _read(tmp_path, "_run", L._JEV_FILE)[0]["Response"] == "<no response body: connection refused>"


def test_provider_names_the_upstream_when_reported(tmp_path):
    """The FreeLLMAPI router reports the free tier it routed to at `_routed_via.platform` (live 2026-10-07). The
    configured model ID is only a slot, so without that the log cannot say who actually answered."""
    L.start_run(tmp_path)
    L.log_llm("freellmapi", {"model": "kimi-k3", "messages": []},
              {"choices": [{"message": {"content": "x"}}],
               "_routed_via": {"platform": "huggingface", "model": "moonshot/kimi-k3"}})
    L.log_llm("freellmapi", {"model": "kimi-k3", "messages": []}, {"choices": [{"message": {"content": "x"}}]})
    L.log_llm("freellmapi", {"model": "kimi-k3", "messages": []}, None, "timeout")
    es = _read(tmp_path, "_run", L._LLM_FILE)
    assert [e["provider"] for e in es] == ["freellmapi/huggingface", "freellmapi", "freellmapi"]


def test_gateway_of_tells_the_two_local_servers_apart():
    """Both servers run on 127.0.0.1, so a label taken from the host alone would file a chat failure and a Kev
    failure under the same name in the outage and rejected-key messages."""
    assert L.gateway_of("http://127.0.0.1:31415/v1/chat/completions") == "freellmapi"
    assert L.gateway_of("http://127.0.0.1:8009/v1/systemone") == "local"
    assert L.gateway_of("http://example.com/v1/chat/completions") == "example.com"


def test_file_is_valid_json_after_every_append(tmp_path):
    L.start_run(tmp_path)
    for i in range(5):
        L.log_jev({"state": {"i": str(i)}, "questions": {}}, {"answers": {}})
        data = json.loads((tmp_path / "_run" / L._JEV_FILE).read_text())   # parses at every step
        assert len(data) == i + 1


def test_scope_switching_and_a_cross_thread_call_land_in_the_active_scope(tmp_path):
    L.start_run(tmp_path)
    L.log_jev({"state": {}, "questions": {}}, {"answers": {}})              # _run
    with L.scope("1_Acme"):
        L.log_jev({"state": {}, "questions": {}}, {"answers": {}})          # job folder
        t = threading.Thread(target=lambda: L.log_jev({"state": {"t": "1"}, "questions": {}}, {"answers": {}}))
        t.start()
        t.join()                                                           # active scope is still 1_Acme
    L.log_jev({"state": {}, "questions": {}}, {"answers": {}})              # back to _run
    assert len(_read(tmp_path, "_run", L._JEV_FILE)) == 2
    assert len(_read(tmp_path, "1_Acme", L._JEV_FILE)) == 2


def test_no_key_or_env_value_appears_in_any_log(tmp_path):
    L.start_run(tmp_path)
    L.log_llm("openrouter", {"model": "m", "messages": [{"role": "system", "content": "fill the form"}]},
              {"choices": [{"message": {"content": "ok"}}]})
    text = "".join(p.read_text() for p in tmp_path.rglob("*.json"))
    assert "sk-" not in text and "Bearer " not in text
    env = dotenv_values(config.DEFAULT_ENV) if config.DEFAULT_ENV.exists() else {}
    for value in env.values():
        if value and len(value) > 8:               # ignore blank or trivially short settings
            assert value not in text
