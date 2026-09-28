"""jev.py (spec v2 §3, §9): env before import, stray keys removed, sole importer, timeout → exit 3, contract."""
import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from assistant import jev
from assistant.contract_check import differences

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parent.parent


def child(code: str, env: dict | None = None, timeout: float = 60) -> subprocess.CompletedProcess:
    """Run `code` in a fresh interpreter: the package config is fixed per process."""
    base = {k: v for k, v in os.environ.items() if not k.startswith(jev.STRIPPED_PREFIXES)}
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)], cwd=ROOT, capture_output=True,
                          text=True, timeout=timeout, env={**base, **(env or {})})


def test_server_is_not_imported_before_apply_env():
    r = child("""
        import sys
        from assistant import config, jev
        assert "jev_ultrafast_mcp.server" not in sys.modules
        try:
            jev.load()
            raise SystemExit("load() before apply_env() must fail")
        except RuntimeError:
            pass
        jev.apply_env(config.load(), "k")
        assert "jev_ultrafast_mcp.server" not in sys.modules      # apply_env only reads .config
        jev.load()
        assert "jev_ultrafast_mcp.server" in sys.modules
        try:
            jev.apply_env(config.load(), "k")
            raise SystemExit("apply_env() after load() must fail")
        except RuntimeError:
            pass
        print("ok")
    """)
    assert r.returncode == 0 and r.stdout.strip() == "ok", r.stderr


def test_stray_typesafe_key_and_package_vars_are_removed():
    r = child("""
        import json, os
        from assistant import config, jev
        cfg = config.load()
        jev.apply_env(cfg, "k-123")
        # the stray value is gone; what takes its place is the configured route's key (the gateway key on Vercel,
        # nothing on OpenRouter, the Kev server's key or its placeholder on the local route)
        key = os.environ.get("TYPESAFE_API_KEY")
        assert key != "stray" and key == jev.agent_route(cfg, "k-123").get("TYPESAFE_API_KEY")
        assert "JEVMCP_ALLOW_DOMAINS" not in os.environ
        assert "TEXT_MODEL_EXTRA" not in os.environ
        assert os.environ["TEXT_MODEL_API_KEY"] == "k-123"      # the chat route's key, whichever route is set
        d = jev.Jev(config.load(), "k-123").doctor()
        print(json.dumps([d["allow_domains"], d["typesafe_turbo"], d["text_model"], d["js_eval"], d["uploads"],
                          d["mode"]]))
    """, env={"TYPESAFE_API_KEY": "stray", "JEVMCP_ALLOW_DOMAINS": "evil.example", "TEXT_MODEL_EXTRA": "x"})
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == '[[], true, true, true, true, "attach"]'


def test_a_hung_call_writes_the_report_and_exits_3(tmp_path):
    marker, hook = tmp_path / "report.md", tmp_path / "atexit.txt"
    r = child(f"""
        import atexit, time
        from assistant import config, jev
        atexit.register(lambda: open({str(hook)!r}, "w").write("ran"))
        class Stub:
            def browser_doctor(self):
                time.sleep(30)
        def write_report(msg):
            open({str(marker)!r}, "w").write(msg)
        j = jev.Jev(config.load(), "", server=Stub(), on_timeout=write_report, timeouts=(0.5, 0.5),
                    calls_log=__import__("pathlib").Path({str(tmp_path / "calls.jsonl")!r}))
        print("before the hang")                 # must survive os._exit when stdout is a pipe/file
        j.call("browser_doctor")
        print("not reached")
    """)
    assert r.returncode == 3 and "not reached" not in r.stdout
    assert "before the hang" in r.stdout
    assert marker.read_text().startswith("browser_doctor did not return within")
    assert not hook.exists()                                     # os._exit skipped the exit hooks (the tab stays)
    assert '"timeout: ' in (tmp_path / "calls.jsonl").read_text()


def test_a_raising_function_becomes_an_error_result(tmp_path):
    class Stub:
        def browser_doctor(self):
            raise ValueError("boom")
    from assistant import config
    j = jev.Jev(config.load(), "", server=Stub())
    assert j.call("browser_doctor") == "error(ValueError): boom"
    with pytest.raises(jev.JevError):
        j.checked("browser_doctor")


def test_only_jev_py_imports_the_package():
    offenders = []
    for f in list((ROOT / "assistant").glob("*.py")) + list((ROOT / "tests").glob("*.py")):
        if f.name == "jev.py":
            continue
        for node in ast.walk(ast.parse(f.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                    [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            if any(n.split(".")[0] in ("jev_ultrafast_mcp", "mcp") for n in names):
                offenders.append(f.name)
    assert offenders == []


def test_signatures_match_spec_section_3():
    assert differences(jev.server_signatures()) == []


def test_contract_check_reports_a_changed_signature():
    import inspect

    def browser_goal(goal, session="default", max_steps=20, verify=None, verbose=False, url=""):
        pass
    sigs = dict(jev.server_signatures(), browser_goal=inspect.signature(browser_goal))
    assert differences(sigs) and "browser_goal" in differences(sigs)[0]


def test_the_patched_package_is_installed():
    assert jev.package_version() == jev.EXPECTED_PACKAGE_VERSION == "0.1.5+aa6"
    wheel = ROOT / "vendor" / "jev_ultrafast_mcp-0.1.5+aa6-py3-none-any.whl"
    assert wheel.exists() and (ROOT / "vendor" / "jev_ultrafast_mcp-0.1.5+aa6.patch").exists()


def test_load_refuses_an_unpatched_package():
    r = child("""
        from assistant import config, jev
        jev.package_version = lambda: "0.1.5"
        jev.apply_env(config.load(), "k")
        try:
            jev.load()
        except RuntimeError as e:
            print("refused:", e)
    """)
    assert r.returncode == 0 and "refused: jev-ultrafast-mcp 0.1.5 is installed" in r.stdout, r.stderr


def test_a_lone_surrogate_from_the_page_never_reaches_a_log():
    """Genesys, live 2026-09-24: page text cut in JavaScript left "\\ud835" alone; writing calls.jsonl raised
    UnicodeEncodeError and ended the run."""
    import json
    assert jev.clean_text("Styled \ud835 cut") == "Styled ? cut" and jev.clean_text("plain 𝐀") == "plain 𝐀"
    json.dumps(jev.clean_text("x\udc00y"), ensure_ascii=False).encode("utf-8")


def test_the_package_s_requests_are_cleaned_before_httpx_encodes_them():
    """Genesys and Mastercard, live 2026-09-24: browser_goal sent page text with a lone surrogate and httpx
    raised UnicodeEncodeError on every step."""
    import json
    import types
    sent = []

    def _post(url, key, body):
        sent.append(json.dumps(body, ensure_ascii=False).encode("utf-8"))
        return {"ok": True}
    policy = types.SimpleNamespace(_post=_post)
    jev.clean_requests(policy)
    jev.clean_requests(policy)                                   # wrapping again never nests
    assert policy._post("u", "k", {"state": {"text": "Job \ud835 title", "items": ["a\udc00"]}}) == {"ok": True}
    assert json.loads(sent[0]) == {"state": {"text": "Job ? title", "items": ["a?"]}}
    assert policy._post.__wrapped__ is _post


def test_a_goal_the_decision_model_could_not_serve_is_asked_again_then_is_a_decision_error():
    """Mastercard, live 2026-09-24: Vercel's Jev answered 503 through the package's ~1.5 s of retries on both
    navigate goals, and the job was reported as "no way forward"."""
    from assistant.decide import RETRY_WAITS, DecisionError
    out503 = "goal: x\nstatus: turbo_unavailable: Decision model returned HTTP 503; no action executed.\nsteps: 0\n"
    done = "goal: x\nstatus: done\nsteps: 1\n"
    replies, slept = [out503, out503, done], []

    class Srv:
        def browser_goal(self, **kw):
            return replies.pop(0)
    b = jev.Jev(None, "", server=Srv(), sleep=slept.append)
    assert b.goal("x", "s", max_steps=1) == done and slept == list(RETRY_WAITS[:2])
    replies[:] = [out503] * (len(RETRY_WAITS) + 1)
    with pytest.raises(DecisionError, match="HTTP 503"):
        b.goal("x", "s", max_steps=1)
    acted = "goal: x\nstatus: turbo_unavailable: Decision model returned HTTP 503; no action executed.\nsteps: 2\n"
    replies[:] = [acted]
    assert b.goal("x", "s") == acted                     # it already acted: its caller reads the page again
    replies[:] = ["goal: x\nstatus: turbo_unavailable: no decision-model key\nsteps: 0\n"]
    assert "no decision-model key" in b.goal("x", "s")   # a setup problem is not retried


# ------------------------------------------------------------------ the decision route the package is given

def route_config(provider: str, **local):
    from assistant import config
    return config.Config(paths=config.Paths(base="."),
                         models=config.Models(system_one_decision_provider=provider,
                                              local=config.LocalKev(**local),
                                              vercel=config.ChatModels(system_one_decision_model="typesafe-ai/jev"),
                                              openrouter=config.ChatModels(llm_inference="m", text_helper="t")))


def test_the_package_agent_is_pointed_at_the_local_kev_server(monkeypatch):
    """A non-OpenRouter TYPESAFE_BASE_URL is used by the package as the full System One endpoint
    (config._turbo_backend), so the Kev server needs no other change."""
    from assistant import config
    monkeypatch.setattr(config, "local_key", lambda *a: "")
    env = jev.env_values(route_config("local", base_url="http://127.0.0.1:8010"), "chat-key")
    assert env["TYPESAFE_BASE_URL"] == "http://127.0.0.1:8010/v1/systemone"
    assert env["TYPESAFE_MODEL"] == "kev-latest"
    # the package refuses turbo mode on an empty key; an open Kev server ignores this placeholder
    assert env["TYPESAFE_API_KEY"] == jev.LOCAL_PLACEHOLDER_KEY
    assert env["TEXT_MODEL_API_KEY"] == "chat-key"          # the chat models stay on models.chat_route


def test_a_kev_server_with_a_key_gets_that_key(monkeypatch):
    from assistant import config
    monkeypatch.setattr(config, "local_key", lambda *a: "kev-secret")
    assert jev.env_values(route_config("local"), "chat-key")["TYPESAFE_API_KEY"] == "kev-secret"


def test_the_vercel_route_is_unchanged(monkeypatch):
    from assistant import config
    monkeypatch.setattr(config, "gateway_key", lambda *a: "gw")
    env = jev.env_values(route_config("vercel"), "chat-key")
    assert env["TYPESAFE_BASE_URL"] == "https://ai-gateway.vercel.sh/typesafe/v1/systemone"
    assert env["TYPESAFE_API_KEY"] == "gw" and env["TYPESAFE_MODEL"] == "typesafe-ai/jev"


def test_the_local_route_sends_the_state_as_an_object():
    """Kev renders an object as labeled text (kev/api.py render()); a JSON string would spend a small model's
    tokens on escaped quotes. The cloud routes keep the string OpenRouter's decisions models require."""
    import json
    import types
    sent = []

    def _post(url, key, body):
        sent.append(body)
        return {"ok": True}
    body = {"model": "kev-latest", "state": {"page": {"text": "Apply"}}, "questions": {"q": {"type": "noul"}}}
    local = types.SimpleNamespace(_post=_post)
    jev.clean_requests(local, string_state=False)
    local._post("http://127.0.0.1:8009/v1/systemone", "local", body)
    assert sent[-1]["state"] == {"page": {"text": "Apply"}}

    cloud = types.SimpleNamespace(_post=_post)
    jev.clean_requests(cloud)
    cloud._post("https://openrouter.ai/api/alpha/decisions", "k", body)
    assert json.loads(sent[-1]["state"]) == {"page": {"text": "Apply"}}
