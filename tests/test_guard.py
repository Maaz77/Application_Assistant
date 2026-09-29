import ast
from pathlib import Path

import pytest

from assistant import guard, probes
from assistant.guard import GuardError, check
from assistant.browser import Element, Option, Table

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
    {"op": "eval", "js": "document.forms[0].submit()"},
    {"op": "eval", "js": probes.REQUIRED_EMPTY + " "},
    {"op": "click", "ref": "e99"},
    {"op": "upload", "ref": "e99", "path": "/tmp/x.pdf"},
    {"op": "click", "ref": "e4", "confirm": True},            # nothing overrides the server's refusal
    {"op": "click", "ref": "e2", "confirm": False},
    {"op": "nav", "url": "https://example.com"},
    {"op": "tab", "action": "new"},
])
def test_guard_blocks(op):
    with pytest.raises(GuardError):
        check(op, TABLE)


@pytest.mark.parametrize("op", [
    {"op": "keys", "key": "Tab"},
    {"op": "type", "ref": "e1", "text": "Enter the dragon", "clear": True, "submit": False},
    {"op": "eval", "js": probes.MAXLENGTHS},
    {"op": "click", "ref": "e2"},
    {"op": "click", "ref": "e3"},                             # refused by the server rule, not here
    {"op": "click", "ref": "e6:2"},
    {"op": "select", "ref": "e6", "value": "Italy"},
    {"op": "upload", "ref": "e1", "path": "/tmp/x.pdf"},
])
def test_guard_allows(op):
    check(op, TABLE)


@pytest.fixture
def form_started():
    guard.FORM.started = True
    yield
    guard.FORM.started = False


def test_never_click_refuses_submit_and_send_everywhere():
    """User decision 2026-09-24: the one never-submit rule, with no model call."""
    guard.FORM.started = False
    for label in ("Submit application", "Submit", "SUBMIT YOUR APPLICATION", "Submit Application →",
                  "Send application", "Send", "Send my application"):
        assert guard.never_click(label, "button") and guard.never_click(label, "link") and guard.never_click(label)
    assert guard.never_click("Submit application", "textbox") is None       # not a click target
    for label in ("Easy Apply", "Apply now", "Apply for this job", "Next", "Review", "Continue applying",
                  "Sender name", "Sending tips"):
        assert guard.never_click(label, "button") is None                  # the posting: Apply starts the application


def test_never_click_refuses_apply_once_the_form_is_being_filled(form_started):
    for label in ("Apply", "Apply now!", "Easy Apply", "Apply for this job", "Submit application"):
        assert guard.never_click(label, "button"), label
    for label in ("Next", "Review", "Continue", "Application questions", "Applied filters"):
        assert guard.never_click(label, "button") is None, label


def test_the_rule_covers_only_submit_and_apply(form_started):
    """Deliberately narrow (user decision 2026-09-24): other final-step labels are not refused."""
    for label in ("Done", "Finish", "Confirm my choices", "Complete"):
        assert guard.never_click(label, "button") is None, label


def test_the_driver_refuses_a_submit_press_and_has_no_enter_op():
    """P2: the vendored guard_clicks hook is gone. The never-submit rule is enforced in the driver's press
    path (guard.never_click_element, injected by Browser) and the driver has no op that sends Enter."""
    from assistant.driver import session
    # a submit-labelled control is refused; a plain field is not (the press path guards clicks, not typing).
    assert guard.never_click_element({"name": "Submit application", "role": "button", "tag": "BUTTON"})
    assert guard.never_click_element({"name": "First name", "role": "textbox", "tag": "INPUT"}) is None
    # the driver executes only these ops — no `keys`, so no Enter/Escape can ever be sent.
    assert session.CLICKABLE_KINDS == {"click", "type", "select", "toggle", "upload"}
    assert not hasattr(session.Session, "_dispatch_keys")


def _constants(path: Path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


def test_nothing_sends_confirm():
    """Nothing in the program asks the server to make a click it refused: "confirm" appears only in guard.py,
    which rejects any op carrying it."""
    for f in PKG.glob("*.py"):
        if f.name != "guard.py":
            assert "confirm" not in set(_constants(f)), f"{f.name} mentions 'confirm'"


def test_only_browser_py_imports_the_driver():
    """P2 interface: only assistant/browser.py imports assistant.driver (the interface boundary)."""
    for f in PKG.glob("*.py"):
        if f.name == "browser.py":
            continue
        for node in ast.walk(ast.parse(f.read_text())):
            mod = node.module if isinstance(node, ast.ImportFrom) else None
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
            assert not (mod and mod.startswith("assistant.driver")), f"{f.name} imports {mod}"
            assert not any(n.startswith("assistant.driver") for n in names), f.name


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
