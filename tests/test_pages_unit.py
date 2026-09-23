from pathlib import Path

import pytest

from assistant import pages
from assistant.fill import page_goal
from assistant.jev import Element, Table
from assistant.pages import Page, classify, gate

pytestmark = pytest.mark.unit
CAPTURED = Path(__file__).parent / "captured"


def page(elements, text="", url="https://jobs.example.com/apply", **kw):
    return Page(url=url, title=kw.pop("title", "Apply"), text=text, table=Table(url=url, elements=elements), **kw)


FINAL = [Element(ref="e1", role="textbox", name="Why us?", value="Because " * 30),
         Element(ref="e2", role="file", name="Resume", value="Amin_Acme_Data_Engineer_2026.pdf"),
         Element(ref="e3", role="button", name="Submit application")]


def test_gate_passes_on_a_clean_final_page():
    assert gate(page(FINAL), "Amin_Acme_Data_Engineer_2026.pdf", {"e1": "Because " * 30}) is None


@pytest.mark.parametrize("change, reason", [
    (dict(required_empty={"n": 2, "more": False, "items": [["Phone", "tel"]]}), "required fields still empty"),
    (dict(text="Please enter a valid phone"), "validation message"),
    (dict(text="Thank you for applying!"), "confirmation text"),
])
def test_gate_fails(change, reason):
    assert gate(page(FINAL, **change), "Amin_Acme_Data_Engineer_2026.pdf", {}).startswith(reason)


def test_gate_checks_resume_submit_and_typed_text():
    assert "resume file name" in gate(page(FINAL), "Amin_Other.pdf", {})
    assert "no submit button" in gate(page(FINAL[:2]), "Amin_Acme_Data_Engineer_2026.pdf", {})
    assert "typed text" in gate(page(FINAL), "Amin_Acme_Data_Engineer_2026.pdf", {"e1": "Something else"})


def test_transmit_and_advance_flags():
    p = page([Element(ref="e1", role="textbox", name="City"), Element(ref="e2", role="button", name="Continue"),
              Element(ref="e3", role="button", name="Save and continue")])
    assert pages.has_advance(p) and not pages.is_final(p) and len(pages.advance_buttons(p)) == 2
    q = page([Element(ref="e1", role="button", name="Continue to submit")])   # advance-looking but TRANSMIT
    assert not pages.has_advance(q) and pages.is_final(q)


def test_google_rules():
    g = page([Element(ref="e1", role="textbox", name="Enter your password")], url="https://accounts.google.com/x")
    assert classify(g).kind == "google" and "password" in pages.google_blocker(g)
    c = page([Element(ref="e1", role="button", name="Allow")], text="Acme wants to access your Google Account",
             url="https://accounts.google.com/consent")
    assert "consent" in pages.google_blocker(c)
    back = page([], text="Complete your registration to continue")
    assert pages.after_google_blocker(back)


def test_linkedin_entry_and_feed():
    assert pages.classify_entry(page([], url="https://www.linkedin.com/authwall?x")) == "signed_out"
    assert pages.linkedin_feed_ok("https://www.linkedin.com/feed/")
    assert not pages.linkedin_feed_ok("https://www.linkedin.com/checkpoint/lg/login?feed")


def test_page_goal_prompt():
    goal, steps = page_goal([("City", "Milan"), ("Do you require visa sponsorship?", "No")])
    assert '- "City" → "Milan"' in goal and "Do not click Submit" in goal and steps == 7


@pytest.mark.parametrize("capture", sorted(CAPTURED.glob("*/observe.txt")) or [None])
def test_classifier_on_captured_pages(capture):
    if capture is None:
        pytest.skip("no tests/captured/ pages yet (the user adds them with `capture URL`)")
    p = pages.page_from_capture(capture.read_text())
    v = classify(p)
    expected = capture.parent / "expected_kind.txt"
    if expected.exists():
        assert v.kind == expected.read_text().strip(), (capture.parent.name, v)
    else:
        assert v.kind != "alarm", (capture.parent.name, v)
