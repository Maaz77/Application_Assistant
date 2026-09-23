from pathlib import Path

import pytest

from assistant import pages
from assistant.fill import page_goal
from assistant.jev import Element, Option, Table
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


def test_unsettled_pages_are_recognised():
    bare = page([Element(ref="e1", role="link", name="Home"), Element(ref="e2", role="combobox", name="Search")],
                text="0 notifications", url="https://www.linkedin.com/jobs/view/4460337345/")
    assert pages.unsettled(bare)                             # nav bar + site Search box: not drawn yet
    bare.table.elements.append(Element(ref="e3", role="textbox", name="Phone number"))
    assert not pages.unsettled(bare)                         # a real form field: drawn
    job = page([Element(ref="e1", role="button", name="Easy Apply")], text="Data Engineer")
    assert not pages.unsettled(job)
    assert not pages.unsettled(page([], text="x" * 250))                         # a real amount of text
    assert not pages.unsettled(page([], text="Thank you for applying!"))         # alarm text is never waited on
    assert not pages.unsettled(page([], text="", captcha=True))


def test_settle_rereads_until_rendered_and_gives_up_after_the_budget():
    bare = page([Element(ref="e1", role="link", name="Home")], text="0 notifications")
    job = page([Element(ref="e1", role="button", name="Easy Apply")], text="Data Engineer")
    seq, slept = iter([bare, bare, job]), []
    assert pages.settle(lambda: next(seq), slept.append) is job and slept == [1.0, 1.0]
    slept.clear()
    assert pages.settle(lambda: bare, slept.append, seconds=3) is bare and len(slept) == 3


def test_the_real_linkedin_capture_was_taken_too_early():
    """tests/captured/www-linkedin-com-20260923-123939: nav bar only, the job card came later (screenshot)."""
    early = sorted(CAPTURED.glob("www-linkedin-com-*/observe.txt"))
    if not early:
        pytest.skip("no LinkedIn capture")
    p = pages.page_from_capture(early[0].read_text())
    assert pages.classify_entry(p) == "none" and pages.unsettled(p)       # would have been waited for


# ------------------------------------------------------------------ live findings, 2026-09-23 (DISCOVERY.md)

def test_link_wrapping_a_same_label_button_is_one_entry():
    link, btn = Element(ref="e1", role="link", name="Apply"), Element(ref="e2", role="button", name="Apply")
    assert pages.single_entry([link, btn]) is btn
    assert pages.single_entry([Element(ref="e3", role="link", name="Apply for this Job"),
                               Element(ref="e4", role="button", name="Apply for this Job")]).ref == "e4"
    assert pages.single_entry([link, Element(ref="e5", role="button", name="Apply on company website")]) is None
    assert pages.single_entry([btn, Element(ref="e6", role="button", name="Apply")]) is None      # two buttons


def test_only_form_looking_iframes_are_followed():
    def p(*srcs):
        return page([], iframe_srcs={"n": len(srcs), "more": False, "items": list(srcs), "long": 0})
    assert pages.iframe_form_src(p("https://www.google.com/maps/embed/v1/place?key=x")) is None
    assert pages.iframe_form_src(p("https://www.youtube.com/embed/abc")) is None
    gh = "https://boards.greenhouse.io/embed/job_app?for=acme&token=1"
    assert pages.iframe_form_src(p("https://www.google.com/maps/embed/v1/place?key=x", gh)) == gh
    assert pages.iframe_form_src(p("http://localhost:1234/f12_form.html")) == "http://localhost:1234/f12_form.html"
    assert pages.iframe_form_src(p("https://acme.com/platform/overview")) is None


def test_a_loading_message_means_not_drawn_yet():
    ashby = page([Element(ref="e1", role="tab", name="Application")],
                 text="Data Scientist Location London Employment Type Full time " * 5 + "Fetching application form")
    assert pages.unsettled(ashby)
    assert not pages.unsettled(page([Element(ref="e1", role="textbox", name="Name")], text="Loading... Name"))


def test_linkedin_alert_switch_is_site_chrome():
    li = page([Element(ref="e1", role="switch", name="Set alert for similar jobs as Junior C++ Software Engineer"),
               Element(ref="e2", role="button", name="Easy Apply to this job")],
              url="https://www.linkedin.com/jobs/view/4460337345/")
    assert not pages.has_fields(li) and pages.classify(li).kind == "ats_entry"


def test_enrich_adds_file_group_labels_and_shown_combo_values():
    t = Table(url="u", elements=[
        Element(ref="e1", role="file", name="Attach"), Element(ref="e2", role="file", name="Attach"),
        Element(ref="e3", role="combobox", name="Visa?*"),
        Element(ref="e4", role="combobox", name="Country", value="it", current="Italy",
                options=[Option(label="Italy"), Option(label="Ireland")])])
    pages.enrich(t, {"FILE_LABELS": {"n": 2, "more": False, "items": [["Resume/CV*", "resume"], ["Cover Letter", ""]]},
                     "COMBO_VALUES_0": {"n": 2, "more": False, "items": ["No", "Italy"], "o": 0, "total": 2}})
    assert [e.label for e in t.elements[:2]] == ["Resume/CV* (resume)", "Cover Letter"]
    assert t.elements[2].current == "No" and t.elements[3].current == "Italy"      # native select untouched
    t2 = Table(url="u", elements=[Element(ref="e1", role="file", name="Attach")])
    pages.enrich(t2, {"FILE_LABELS": {"n": 2, "more": False, "items": [["a", ""], ["b", ""]]}})
    assert t2.elements[0].label == ""                                            # counts disagree: untouched
