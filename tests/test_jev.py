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
        jev.apply_env(config.load(), "k-123")
        # the stray value is gone; on the Vercel route the gateway key from .env takes its place
        assert os.environ.get("TYPESAFE_API_KEY") in (None, config.gateway_key()) != "stray"
        assert "JEVMCP_ALLOW_DOMAINS" not in os.environ
        assert "TEXT_MODEL_EXTRA" not in os.environ
        assert os.environ["OPENROUTER_API_KEY"] == os.environ["TEXT_MODEL_API_KEY"] == "k-123"
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
