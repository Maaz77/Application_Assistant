"""§9: text helper, live_model — ANSWERS typed exactly on f08 through one page goal."""
import pytest

from assistant.fill import page_goal
from assistant.pages import read_page

pytestmark = pytest.mark.live_model


def test_answers_set_exactly_on_f08(new_browser, fixture_server, api_key):
    answers = [("City", "Milan"), ("Do you require visa sponsorship?", "No"), ("Employer 1", "Tetra Pak")]
    goal, steps = page_goal(answers)
    with new_browser(api_key) as browser:
        browser.open(fixture_server.url("f08.html"), "f08")
        p = read_page(browser, "f08")
        ref = {e.name: e.ref for e in p.elements}
        verify = [{"type": "value_equals", "ref": ref["City"], "value": "Milan"},
                  {"type": "checked", "ref": ref["No"], "state": True},
                  {"type": "value_equals", "ref": ref["Employer 1"], "value": "Tetra Pak"}]
        out = browser.goal(goal, "f08", max_steps=steps, verify=verify)
        browser.close("f08")
    assert "verified: PASS" in out, out
    assert fixture_server.posts() == []
