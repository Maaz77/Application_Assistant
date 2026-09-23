import ast
from pathlib import Path

import pytest

from assistant import guard, probes
from assistant.guard import ENTRY, GuardError, check
from assistant.jev import Element, Option, Table

pytestmark = pytest.mark.unit
PKG = Path(__file__).resolve().parent.parent / "assistant"

TABLE = Table(url="https://www.linkedin.com/jobs/view/4012345678/", elements=[
    Element(ref="e1", role="textbox", name="First name"),
    Element(ref="e2", role="button", name="Next"),
    Element(ref="e3", role="button", name="Submit application"),
    Element(ref="e4", role="button", name="Easy Apply"),
    Element(ref="e5", role="button", name="Done"),
    Element(ref="e6", role="combobox", name="Country", options=[Option(label="Send later"), Option(label="Italy")]),
])


@pytest.mark.parametrize("op", [
    {"op": "keys", "key": "Enter"},
    {"op": "keys", "key": "Return"},
    {"op": "keys", "key": "enter"},
    {"op": "keys", "key": "Shift+Enter"},
    {"op": "keys", "key": "Meta+Return"},
    {"op": "keys", "key": "NumpadEnter"},
    {"op": "keys", "keys": ["Tab", "Enter"]},
    {"op": "keys", "keys": "Enter"},
    {"op": "type", "ref": "e1", "text": "x", "submit": True},
    {"op": "eval", "js": "document.forms[0].submit()"},
    {"op": "eval", "js": probes.REQUIRED_EMPTY + " "},
    {"op": "click", "ref": "e3"},
    {"op": "click", "ref": "e5"},
    {"op": "click", "ref": "e6:1"},
    {"op": "click", "ref": "e99"},
    {"op": "click", "ref": "e2", "confirm": True},
    {"op": "click", "ref": "e4"},
    {"op": "nav", "url": "https://example.com"},
    {"op": "tab", "action": "new"},
])
def test_guard_blocks(op):
    with pytest.raises(GuardError):
        check(op, TABLE)


@pytest.mark.parametrize("op", [
    {"op": "keys", "key": "Tab"},
    {"op": "keys", "keys": ["ArrowDown", "Tab"]},
    {"op": "keys", "key": "Escape"},
    {"op": "type", "ref": "e1", "text": "Enter the dragon", "clear": True, "submit": False},
    {"op": "eval", "js": probes.MAXLENGTHS},
    {"op": "click", "ref": "e2"},
    {"op": "click", "ref": "e6:2"},
    {"op": "select", "ref": "e6", "value": "Italy"},
    {"op": "upload", "ref": "e1", "path": "/tmp/x.pdf"},
])
def test_guard_allows(op):
    check(op, TABLE)


def test_entry_token_unlocks_only_entry_labels():
    check({"op": "click", "ref": "e4", "confirm": True}, TABLE, ENTRY)
    for ref in ("e3", "e2", "e5"):
        with pytest.raises(GuardError):
            check({"op": "click", "ref": ref, "confirm": True}, TABLE, ENTRY)
    with pytest.raises(GuardError):
        check({"op": "click", "ref": "e4", "confirm": True}, TABLE, object())


def _imports_and_names(path: Path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "assistant.guard":
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.Attribute):
            yield node.attr
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield "str:" + node.value


def test_only_entry_py_uses_confirm_and_entry_token():
    for f in PKG.glob("*.py"):
        names = set(_imports_and_names(f))
        if f.name in {"entry.py", "guard.py"}:
            continue
        assert "ENTRY" not in names, f"{f.name} touches guard.ENTRY"
        assert "str:confirm" not in names, f"{f.name} mentions 'confirm'"
    assert "ENTRY" in set(_imports_and_names(PKG / "entry.py"))


def test_only_jev_py_calls_browser_act():
    """No call passes "browser_act" (as a name argument) or calls .browser_act() outside jev.py."""
    for f in PKG.glob("*.py"):
        if f.name == "jev.py":
            continue
        for node in ast.walk(ast.parse(f.read_text())):
            if not isinstance(node, ast.Call):
                continue
            assert not (isinstance(node.func, ast.Attribute) and node.func.attr == "browser_act"), f.name
            assert not any(isinstance(a, ast.Constant) and a.value == "browser_act" for a in node.args), f.name


FORBIDDEN = [r"\.submit\b", r"\.click\s*\(", r"dispatchEvent", r"\bfetch\b",
             r"XMLHttpRequest", r"\blocation\b"]


@pytest.mark.parametrize("name", sorted(probes.ALL))
def test_probes_are_read_only(name):
    import re
    js = probes.ALL[name]
    for pat in FORBIDDEN:
        assert not re.search(pat, js), (name, pat)
    # no DOM assignment: `<something>.<prop> =` (local const/let bindings and comparisons are fine)
    assert not re.search(r"\.[A-Za-z_$][\w$]*\s*=(?!=|>)", js), name
    assert not re.search(r"\]\s*=(?!=|>)", js), name
