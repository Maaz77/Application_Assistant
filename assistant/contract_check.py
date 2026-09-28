"""Compare the package's public browser_* signatures with spec v2 §3.

    python -m assistant.contract_check        # exit 0 = matches, 1 = differs (stop and report, rule 7)
"""
from __future__ import annotations

import inspect
import sys

_E = inspect.Parameter.empty
# name → [(parameter, default)] in order; `session` is listed where the spec says "all except browser_doctor"
EXPECTED: dict[str, list[tuple[str, object]]] = {
    "browser_doctor": [],
    "browser_open": [("url", _E), ("session", "default"), ("hint", "")],
    "browser_observe": [("session", "default"), ("mode", "auto"), ("include_text", True), ("include_json", False)],
    "browser_act": [("ops", _E), ("session", "default"), ("dry_run", False), ("stop_on_error", True),
                    ("observe_after", True)],
    "browser_assert": [("checks", _E), ("session", "default")],
    "browser_goal": [("goal", _E), ("session", "default"), ("max_steps", 20), ("verify", None), ("verbose", False)],
    "browser_tabs": [("session", "default"), ("action", "list"), ("index", -1), ("target_id", ""),
                     ("url", "about:blank")],
    "browser_close": [("session", "default"), ("shutdown_browser", False)],
}


def differences(signatures: dict[str, inspect.Signature]) -> list[str]:
    """Parameter names and defaults per function, order-insensitive (every call uses keywords only)."""
    out = []
    for name, want in EXPECTED.items():
        sig = signatures.get(name)
        if sig is None:
            out.append(f"{name}: missing")
            continue
        got = {p.name: p.default for p in sig.parameters.values()}
        if got != dict(want):
            out.append(f"{name}: expected {dict(want)}, found {got}")
    return out


def text_helper_differences(server) -> list[str]:
    """jev.rotate_text_helper wraps policy.text_for(cfg, …); the wrap applies only while server.py looks it up on
    the module at call time and passes the Config dataclass first."""
    out = []
    if "policy.text_for(CONFIG," not in inspect.getsource(server):
        out.append("server.py no longer calls policy.text_for(CONFIG, …): the text helper would not rotate models")
    original = getattr(server.policy.text_for, "__wrapped__", None)
    if original is None:
        out.append("policy.text_for is not wrapped by jev.rotate_text_helper")
    elif next(iter(inspect.signature(original).parameters), None) != "cfg":
        out.append(f"policy.text_for{inspect.signature(original)} no longer takes cfg first")
    return out


def request_differences(server) -> list[str]:
    """jev.clean_requests wraps policy._post(url, key, body); it covers every request only while that is the
    package's one place that sends, and policy.py calls it by its global name."""
    import pathlib
    out = []
    original = getattr(server.policy._post, "__wrapped__", None)
    if original is None:
        out.append("policy._post is not wrapped by jev.clean_requests")
    elif list(inspect.signature(original).parameters) != ["url", "key", "body"]:
        out.append(f"policy._post{inspect.signature(original)} no longer takes (url, key, body)")
    pkg = pathlib.Path(inspect.getfile(server)).parent
    senders = [f.name for f in pkg.glob("*.py") if ".post(" in f.read_text() or "httpx.post" in f.read_text()]
    if senders != ["policy.py"] or inspect.getsource(server.policy).count(".post(") != 1:
        out.append(f"the package sends requests outside policy._post ({senders}): bodies may not be cleaned")
    return out


def click_rule_differences(server) -> list[str]:
    """jev.guard_clicks replaces the package's confirm_reason(cfg, name, role), the check made before every click;
    it applies only while browser.py looks it up on its module at call time."""
    import importlib
    browser = importlib.import_module(server.__name__.rpartition(".")[0] + ".browser")
    out = []
    original = getattr(browser.confirm_reason, "__wrapped__", None)
    if original is None:
        out.append("browser.confirm_reason is not replaced by jev.guard_clicks: the never-submit rule is off")
    elif list(inspect.signature(original).parameters) != ["cfg", "name", "role"]:
        out.append(f"confirm_reason{inspect.signature(original)} no longer takes (cfg, name, role)")
    if "blocked = confirm_reason(self.cfg, target_label" not in inspect.getsource(browser):
        out.append("browser.py no longer checks confirm_reason before a click: the never-submit rule is off")
    return out


def connection_differences(server) -> list[str]:
    """`jev.connect_chrome` opens the run's single CDP connection itself (P1 T5), which means reaching into the
    package's browser manager. Check that what it reaches for is still there."""
    import importlib
    browser = importlib.import_module(server.__name__.rpartition(".")[0] + ".browser")   # only jev.py imports it by name
    out = []
    manager = server.MANAGER
    if not hasattr(manager, "_cdp"):
        out.append("browser.BrowserManager has no _cdp: jev.connect_chrome cannot pre-open the run's connection")
    if not hasattr(manager.cfg, "attach_data_dirs"):
        out.append("browser config has no attach_data_dirs(): jev.connect_chrome cannot pass it to attach_chrome")
    params = inspect.signature(browser.attach_chrome).parameters
    missing = [p for p in ("url", "timeout", "data_dirs", "open_timeout") if p not in params]
    if missing:
        out.append(f"attach_chrome{inspect.signature(browser.attach_chrome)} no longer takes {missing}")
    if "open_timeout=max(" not in inspect.getsource(type(manager).cdp.fget):
        out.append("browser.BrowserManager.cdp no longer sets its own open_timeout: re-check the handshake budget")
    return out


def main() -> int:
    from assistant import config, jev
    cfg = config.load()
    jev.apply_env(cfg, config.chat_key(cfg))
    diffs = (differences(jev.server_signatures()) + text_helper_differences(jev.load())
             + request_differences(jev.load()) + click_rule_differences(jev.load())
             + connection_differences(jev.load()))
    for d in diffs:
        print("✗", d)
    if not diffs:
        print(f"✓ {len(EXPECTED)} browser_* signatures match spec §3; text helper rotation, request cleaning, "
              f"the click rule and the pre-opened CDP connection are in place")
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
