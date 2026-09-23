import json
from datetime import date

import pytest

from assistant import answers as A
from assistant.answers import PageAnswers, Policy, Sources, check_answers, total_months
from assistant.jev import Element, Option, Table
from assistant.pages import Page

pytestmark = pytest.mark.unit
TODAY = date(2026, 9, 23)

PROFILE = """# Resume
**Full Stack Software Engineer** · Feb 2026 – Present
**Deep Learning Engineer & Researcher** · Dec 2024 – Dec 2025
**Computer Vision Research Assistant** · Sep 2024 – Dec 2024
I built a real-time computer vision pipeline reaching 200 FPS on an NPU.
# Scratch Pad
- What is your notice period? 3 months
"""
JOB = "Acme builds perception software for robots. We value ownership."
RESUME = "Amin Abbaszadeh\nMilan, Italy\namin@example.com"
SRC = Sources(PROFILE, JOB, RESUME)

PAGE = Page(url="https://acme.io/apply", title="Apply", text=(
    "Email Notice period Why Acme? Work model Remote Hybrid On-site Total years of experience Phone"),
    table=Table(url="https://acme.io/apply", elements=[
        Element(ref="e1", role="textbox", name="Email", value="amin@example.com"),
        Element(ref="e2", role="textbox", name="Notice period"),
        Element(ref="e3", role="textbox", name="Why Acme?"),
        Element(ref="e4", role="combobox", name="Work model", options=[Option(label="Remote"), Option(label="Hybrid")]),
        Element(ref="e5", role="textbox", name="Total years of experience"),
        Element(ref="e6", role="textbox", name="Phone", value="+39 000"),
    ]), maxlengths={"n": 1, "more": False, "items": [["Why Acme?", 120]], "min": 120})


def q(**kw):
    base = dict(id="q1", question="Q", kind="text", ref=None, option_ref=None, options=None, required=True,
                answer=None, source=None, quote=None, relies_on=None)
    return {**base, **kw}


def run(*qs, policy=Policy()):
    pa = PageAnswers.model_validate({"questions": list(qs)})
    bad = check_answers(pa, PAGE, SRC, policy, TODAY)
    return pa.questions, bad


def test_quote_must_be_in_the_named_source():
    (good, wrong_src, invented), _ = run(
        q(question="Notice period", ref="e2", answer="3 months", source="profile",
          quote="- What is your notice period?   3 months"),
        q(question="Notice period", ref="e2", answer="3 months", source="job", quote="What is your notice period? 3 months"),
        q(question="Phone", ref="e6", answer="+39 111", source="resume", quote="+39 111"))
    assert good.answer == "3 months"
    assert wrong_src.answer is None and invented.answer is None


def test_choice_answer_must_be_an_option_on_the_page():
    (ok, not_option, off_page), _ = run(
        q(question="Work model", kind="choice", ref="e4", options=["Remote", "Hybrid"], answer="hybrid",
          source="profile", quote="I built a real-time computer vision pipeline reaching 200 FPS on an NPU."),
        q(question="Work model", kind="choice", ref="e4", options=["Remote", "Hybrid"], answer="Anywhere",
          source="profile", quote="I built a real-time computer vision pipeline reaching 200 FPS on an NPU."),
        q(question="Work model", kind="choice", ref="e4", options=["Remote", "Mars"], answer="Remote",
          source="profile", quote="I built a real-time computer vision pipeline reaching 200 FPS on an NPU."))
    assert ok.answer == "Hybrid"                      # normalised to the option's spelling
    assert not_option.answer is None and off_page.answer is None


def test_generated_rules():
    fact = "I built a real-time computer vision pipeline reaching 200 FPS on an NPU."
    (ok, forbidden, bad_relies, too_long, wrong_kind), bad = run(
        q(question="Why Acme?", kind="longtext", ref="e3", answer="I love robots.", source="generated", relies_on=[fact]),
        q(question="Expected salary", kind="text", answer="Lots", source="generated", relies_on=[fact]),
        q(id="q3", question="Why Acme?", kind="longtext", answer="x", source="generated", relies_on=["I invented this."]),
        q(id="q4", question="Why Acme?", kind="longtext", answer="y" * 200, source="generated", relies_on=[fact]),
        q(question="Work model", kind="choice", answer="Remote", source="generated", relies_on=[fact]))
    assert ok.answer == "I love robots."
    assert forbidden.answer is None and wrong_kind.answer is None
    assert [b.id for b in bad] == ["q3", "q4"]        # regenerated once by answer_page, not dropped yet


@pytest.mark.parametrize("lines, months", [
    (["**A** · Feb 2026 – Present"], 8),                                   # Feb..Sep 2026 inclusive
    (["**A** · Dec 2024 – Dec 2025", "**B** · Sep 2024 – Dec 2024"], 16),   # overlap in Dec 2024 counted once
    (["**A** · Jan 2020 – Jun 2020", "**B** · Jan 2021 – Jun 2021"], 12),   # gap not counted
    (["03/2019 - 02/2020", "2020-03 to 2020-12"], 22),                      # adjacent spans merge
    (["September 2018 – current"], 97),
    (["no dates here"], 0),
])
def test_total_months(lines, months):
    assert total_months(lines, TODAY) == months


def test_computed_years_recomputed():
    lines = ["**Full Stack Software Engineer** · Feb 2026 – Present",
             "**Deep Learning Engineer & Researcher** · Dec 2024 – Dec 2025",
             "**Computer Vision Research Assistant** · Sep 2024 – Dec 2024"]
    (right, wrong, invented), _ = run(
        q(question="Total years of experience", ref="e5", answer="2", source="computed", relies_on=lines),
        q(question="Total years of experience", ref="e5", answer="5", source="computed", relies_on=lines),
        q(question="Total years of experience", ref="e5", answer="0", source="computed",
          relies_on=["**Chief Robot** · Jan 2026 – Present"]))
    assert right.answer == "2" and wrong.answer is None and invented.answer is None


def test_linkedin_prefill():
    (kept, differs), _ = run(
        q(question="Phone", ref="e6", answer="+39 000", source="linkedin-prefill"),
        q(question="Email", ref="e1", answer="other@example.com", source="linkedin-prefill"))
    assert kept.answer == "+39 000" and differs.answer is None
    (strict,), _ = run(q(question="Phone", ref="e6", answer="+39 000", source="linkedin-prefill"),
                       policy=Policy(prefill="strict"))
    assert strict.answer is None


def test_file_questions_never_carry_answers_and_uncovered_lists():
    pa = PageAnswers.model_validate({"questions": [
        q(id="a", question="Resume", kind="file", answer="x.pdf", source="resume", quote="x"),
        q(id="b", question="Salary", required=True),
        q(id="c", question="Hobby", required=False)]})
    check_answers(pa, PAGE, SRC, Policy(), TODAY)
    assert pa.questions[0].answer is None
    assert [x.id for x in A.uncovered_required(pa)] == ["b"] and [x.id for x in A.uncovered_optional(pa)] == ["c"]


# ------------------------------------------------------------------ the HTTP contract

def canned(*responses):
    calls = []

    def post(url, payload, headers, timeout):
        calls.append(payload)
        status, content = responses[len(calls) - 1]
        body = {"choices": [{"message": {"content": content}}]} if isinstance(content, str) else content
        return status, body
    return post, calls


GOOD = json.dumps({"questions": [q(question="Notice period", ref="e2", answer="3 months", source="profile",
                                    quote="What is your notice period? 3 months")]})


def test_schema_400_falls_back_to_json_object():
    post, calls = canned((400, {"error": "response_format"}), (200, GOOD))
    pa = A.answer_page(PAGE, SRC, key="k", model="m", policy=Policy(), today=TODAY, post=post)
    assert pa.questions[0].answer == "3 months"
    assert calls[0]["response_format"]["type"] == "json_schema" and calls[1]["response_format"] == {"type": "json_object"}
    assert calls[0]["temperature"] == 0


def test_one_retry_on_5xx_then_error():
    post, _ = canned((502, {}), (200, GOOD))
    A.answer_page(PAGE, SRC, key="k", model="m", policy=Policy(), today=TODAY, post=post)
    post, _ = canned((502, {}), (503, {}))
    with pytest.raises(A.AnswerEngineError, match="unreachable"):
        A.answer_page(PAGE, SRC, key="k", model="m", policy=Policy(), today=TODAY, post=post)


def test_429_waits_and_retries_once(monkeypatch):
    waits = []
    monkeypatch.setattr(A.time, "sleep", waits.append)
    post, _ = canned((429, {"error": "rate"}), (200, GOOD))
    A.answer_page(PAGE, SRC, key="k", model="m", policy=Policy(), today=TODAY, post=post)
    assert waits == [A.RATE_LIMIT_WAIT]


def test_invalid_output_after_one_retry_is_a_blocker():
    post, _ = canned((200, "not json"), (200, '{"questions": [{"id": 1}]}'))
    with pytest.raises(A.AnswerEngineError, match="output invalid"):
        A.answer_page(PAGE, SRC, key="k", model="m", policy=Policy(), today=TODAY, post=post)


def test_fenced_json_is_accepted_and_bad_generated_regenerated_once():
    fact = "I built a real-time computer vision pipeline reaching 200 FPS on an NPU."
    first = json.dumps({"questions": [q(question="Why Acme?", kind="longtext", ref="e3", answer="made up",
                                        source="generated", relies_on=["Not in sources."])]})
    second = json.dumps({"questions": [q(question="Why Acme?", kind="longtext", ref="e3", answer="Real text.",
                                         source="generated", relies_on=[fact])]})
    post, calls = canned((200, "```json\n" + first + "\n```"), (200, second))
    pa = A.answer_page(PAGE, SRC, key="k", model="m", policy=Policy(), today=TODAY, post=post)
    assert pa.questions[0].answer == "Real text." and "regenerate" in json.loads(calls[1]["messages"][1]["content"])


def test_schema_is_strict_mode_shaped():
    item = A.SCHEMA["properties"]["questions"]["items"]
    assert set(item["required"]) == set(item["properties"]) and item["additionalProperties"] is False
