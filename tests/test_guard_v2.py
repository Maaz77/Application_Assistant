"""P2 guard v2 (00_common §4.1): the element-level never-submit rule and the deterministic final-page
judgment. Uses plain descriptor dicts (the shape observer.js emits for the driver's press-path check)."""
from types import SimpleNamespace

import pytest

from assistant import guard

pytestmark = pytest.mark.unit

FRESH = SimpleNamespace(started=False, final=False)
STARTED = SimpleNamespace(started=True, final=False)
FINAL = SimpleNamespace(started=True, final=True)


def el(**kw):
    base = {"name": "", "value": "", "role": "button", "tag": "BUTTON", "type": "",
            "form": "", "dialog": "", "consent": ""}
    base.update(kw)
    return base


@pytest.mark.parametrize("label", ["Submit", "Submit application", "SUBMIT YOUR APPLICATION",
                                   "Send", "Send application", "Confirm", "Done", "Finish", "Complete"])
def test_submit_labels_refused_everywhere(label):
    # P2 §4.1 makes the rule absolute: done/finish/complete/confirm are refused too (v3 allowed them).
    assert guard.never_click_element(el(name=label), FRESH)


def test_apply_refused_only_once_filling_started():
    assert guard.never_click_element(el(name="Apply now!"), FRESH) is None
    assert guard.never_click_element(el(name="Easy Apply"), FRESH) is None
    assert guard.never_click_element(el(name="Apply now!"), STARTED)
    assert guard.never_click_element(el(name="Apply for this job"), STARTED)


def test_structural_submit_refused_unless_advance_and_not_final():
    # A submit-typed control is structural; only an allowlisted advance label passes, and only off-final.
    assert guard.never_click_element(el(name="Go", type="submit", form="f"), FRESH)
    assert guard.never_click_element(el(name="", tag="INPUT", type="image", form="f"), FRESH)
    assert guard.never_click_element(el(name="Continue", tag="BUTTON", type="", form="f"), FRESH) is None
    for adv in ["Next", "Continue", "Continue to next step", "Review", "Review your application",
                "Save and continue"]:
        assert guard.never_click_element(el(name=adv, type="submit", form="f"), FRESH) is None, adv
        assert guard.never_click_element(el(name=adv, type="submit", form="f"), FINAL), f"{adv} on final"


def test_button_without_type_is_structural_only_inside_a_form():
    assert guard.never_click_element(el(name="Go", tag="BUTTON", type="", form="app"), FRESH)
    assert guard.never_click_element(el(name="Go", tag="BUTTON", type="", form=""), FRESH) is None


def test_everything_refused_on_a_final_page():
    assert guard.never_click_element(el(name="Anything at all"), FINAL)
    assert guard.never_click_element(el(name="Anything at all"), FRESH) is None


def test_cookie_consent_exemption():
    # A consent control outside any form/dialog is allowed even when labelled "Confirm my choices".
    assert guard.never_click_element(
        el(name="Confirm my choices", consent="#onetrust-banner-sdk"), FRESH) is None
    assert guard.never_click_element(el(name="Accept all", consent="#didomi-host"), FRESH) is None
    assert guard.never_click_element(el(name="Reject all", consent=".qc-cmp2-container"), STARTED) is None
    # but submit/send/apply are never exempt, and a consent control inside a form is not exempt.
    assert guard.never_click_element(el(name="Submit", consent="#onetrust-banner-sdk"), FRESH)
    assert guard.never_click_element(el(name="Confirm", consent="#x", form="app"), FRESH)


def test_div_named_consent_is_not_a_real_container():
    # A <div class="consent"> is not a known cookie container: observer emits consent="", so a "Submit"
    # inside it is refused (T5 fixture).
    assert guard.never_click_element(el(name="Submit", type="submit", form="app", consent=""), FRESH)


def test_looks_final():
    submit = el(name="Submit application", type="submit", form="f")
    nxt = el(name="Next", type="submit", form="f")
    field = el(name="First name", role="textbox", tag="INPUT", type="text")
    assert guard.looks_final([field, submit]) is True
    assert guard.looks_final([field, nxt, submit]) is False   # an advance control means not final
    assert guard.looks_final([field]) is False                # nothing to submit
    assert guard.looks_final([el(name="Go", type="submit", form="f")]) is True
