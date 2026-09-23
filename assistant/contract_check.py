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


def main() -> int:
    from assistant import config, jev
    cfg = config.load()
    jev.apply_env(cfg, config.api_key())
    diffs = differences(jev.server_signatures())
    for d in diffs:
        print("✗", d)
    if not diffs:
        print(f"✓ {len(EXPECTED)} browser_* signatures match spec §3")
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
