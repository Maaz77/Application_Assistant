import json
from datetime import date

import pytest

from assistant import llm_inference as A
from assistant.llm_inference import PageAnswers, Policy, Sources, check_answers, total_months
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


def test_quote_must_be_in_one_of_the_sources_and_the_source_is_corrected():
    (good, wrong_src, invented), _ = run(
        q(question="Notice period", ref="e2", answer="3 months", source="profile",
          quote="- What is your notice period?   3 months"),
        q(question="Notice period", ref="e2", answer="3 months", source="job", quote="What is your notice period? 3 months"),
        q(question="Phone", ref="e6", answer="+39 111", source="resume", quote="+39 111"))
    assert good.answer == "3 months"
    assert wrong_src.answer == "3 months" and wrong_src.source == "profile" and "not job" in wrong_src.note
    assert invented.answer == "+39 000" and invented.source == "linkedin-prefill"   # the invented value is dropped;
    assert "quote not found" in invented.note                                         # the page keeps its own


def test_choice_answer_must_be_an_option_on_the_page():
    fact = "I built a real-time computer vision pipeline reaching 200 FPS on an NPU."
    (ok, not_option, typo_in_echo), _ = run(
        q(question="Work model", kind="choice", ref="e4", options=["Remote", "Hybrid"], answer="hybrid",
          source="profile", quote=fact),
        q(question="Work model", kind="choice", ref="e4", options=["Remote", "Hybrid"], answer="Anywhere",
          source="profile", quote=fact),
        q(question="Work model", kind="choice", ref="e4", options=["Re mote", "Hybrid"], answer="Remote",
          source="profile", quote=fact))
    assert ok.answer == "Hybrid"                      # normalised to the field's own option
    assert not_option.answer is None
    assert typo_in_echo.answer == "Remote"            # judged by the field's options, not the echoed list


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
    assert kept.answer == "+39 000"
    assert differs.answer == "amin@example.com" and "pre-fill not kept" in differs.note   # never the claimed value
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
    pa = A.answer_page(PAGE, SRC, key="k", models="m", policy=Policy(), today=TODAY, post=post)
    assert pa.questions[0].answer == "3 months"
    assert calls[0]["response_format"]["type"] == "json_schema" and calls[1]["response_format"] == {"type": "json_object"}
    assert calls[0]["temperature"] == 0


def test_an_unavailable_model_hands_over_to_the_next():
    """429 (rate-limited upstream), 5xx, a 200 that carries an error body (an overloaded provider, live 2026-09-24)
    and invalid output each move on to the next model, with no waiting."""
    for failure in [(429, {"error": {"code": 429, "message": "Provider returned error",
                                     "metadata": {"raw": "a:free is temporarily rate-limited upstream"}}}),
                    (502, {}),
                    (200, {"error": {"code": 503, "message": "Upstream error: Service temporarily overloaded"},
                           "choices": [{"message": {"content": ""}}]}),
                    (200, "not json")]:
        post, calls = canned(failure, (200, GOOD))
        pa = A.answer_page(PAGE, SRC, key="k", models=["a:free", "b:free"], policy=Policy(), today=TODAY, post=post)
        assert [c["model"] for c in calls] == ["a:free", "b:free"] and pa.model == "b:free"


def test_when_no_model_answers_the_error_names_each_failure():
    post, calls = canned((429, {"error": {"code": 429, "metadata": {"raw": "rate-limited upstream"}}}),
                         (200, {"error": {"code": 503, "message": "Service temporarily overloaded"}}),
                         (200, '{"questions": [{"id": 1}]}'))
    with pytest.raises(A.LLMInferenceError) as ei:
        A.answer_page(PAGE, SRC, key="k", models=["a", "b", "c"], policy=Policy(), today=TODAY, post=post)
    msg = str(ei.value)
    assert "none of 3 models answered" in msg and len(calls) == 3
    assert "a: HTTP 429 rate-limited upstream" in msg and "b: HTTP 503 Service temporarily overloaded" in msg
    assert "c: output invalid" in msg


def test_a_timeout_moves_on_but_a_rejected_key_stops_at_once():
    import httpx

    def timing_out(url, payload, headers, timeout):
        if payload["model"] == "slow":
            raise httpx.ReadTimeout("read timed out")
        return 200, {"choices": [{"message": {"content": GOOD}}]}
    assert A.answer_page(PAGE, SRC, key="k", models=["slow", "fast"], policy=Policy(), today=TODAY,
                         post=timing_out).model == "fast"
    post, calls = canned((401, {"error": {"code": 401, "message": "No auth credentials found"}}))
    with pytest.raises(A.LLMInferenceError, match="rejected the key") as ei:
        A.answer_page(PAGE, SRC, key="k", models=["a", "b"], policy=Policy(), today=TODAY, post=post)
    assert len(calls) == 1 and not isinstance(ei.value, A.ModelUnavailable)


def test_the_llm_inference_posts_to_the_routes_url():
    """Vercel AI Gateway takes the same chat/completions request; its 401 names the host, not OpenRouter."""
    seen = []

    def post(url, payload, headers, timeout):
        seen.append((url, headers["Authorization"]))
        return 200, {"choices": [{"message": {"content": GOOD}}]}
    vercel = "https://ai-gateway.vercel.sh/v1/chat/completions"
    A.answer_page(PAGE, SRC, key="gw", models="mistral/mistral-nemo", policy=Policy(), today=TODAY, url=vercel,
                  post=post)
    assert seen == [(vercel, "Bearer gw")]
    post401, _ = canned((401, {"error": {"message": "Authentication failed.", "type": "authentication_error"}}))
    with pytest.raises(A.LLMInferenceError, match="ai-gateway.vercel.sh rejected the key"):
        A.answer_page(PAGE, SRC, key="bad", models="m", policy=Policy(), today=TODAY, url=vercel, post=post401)


def test_a_short_retry_after_is_waited_out_once_then_the_model_is_out():
    """Vercel, live 2026-09-24: 429 "this team's limit of 5 requests per minute … Retry after 22s" with Retry-After."""
    slept = []
    limit = (429, {"error": {"message": "Rate limit exceeded: 5 requests per minute", "type": "rate_limit_exceeded"},
                   "_retry_after": "22"})
    post, calls = canned(limit, (200, GOOD))
    pa = A.call_engine(key="k", models="m", system="s", user={}, post=post, sleep=slept.append)
    assert slept == [22.0] and len(calls) == 2 and pa.model == "m"
    post, calls = canned(limit, limit, (200, GOOD))
    assert A.call_engine(key="k", models=["m", "n"], system="s", user={}, post=post, sleep=slept.append).model == "n"
    long = (429, {"error": {"message": "daily quota"}, "_retry_after": "3600"})
    post, calls = canned(long, (200, GOOD))
    slept.clear()
    assert A.call_engine(key="k", models=["m", "n"], system="s", user={}, post=post, sleep=slept.append).model == "n"
    assert slept == []


def test_the_run_keeps_asking_the_model_that_answered_last():
    """One Rotation for the run: the next page starts at the model that answered, not at a rate-limited first."""
    from assistant.rotation import Rotation
    engines = Rotation(["a", "b"])
    post, calls = canned((429, {"error": "rate"}), (200, GOOD), (200, GOOD))
    A.answer_page(PAGE, SRC, key="k", models=engines, policy=Policy(), today=TODAY, post=post)
    A.answer_page(PAGE, SRC, key="k", models=engines, policy=Policy(), today=TODAY, post=post)
    assert [c["model"] for c in calls] == ["a", "b", "b"]


def test_fenced_json_is_accepted_and_bad_generated_regenerated_once():
    fact = "I built a real-time computer vision pipeline reaching 200 FPS on an NPU."
    first = json.dumps({"questions": [q(question="Why Acme?", kind="longtext", ref="e3", answer="made up",
                                        source="generated", relies_on=["Not in sources."])]})
    second = json.dumps({"questions": [q(question="Why Acme?", kind="longtext", ref="e3", answer="Real text.",
                                         source="generated", relies_on=[fact])]})
    post, calls = canned((200, "```json\n" + first + "\n```"), (200, second))
    pa = A.answer_page(PAGE, SRC, key="k", models="m", policy=Policy(), today=TODAY, post=post)
    assert pa.questions[0].answer == "Real text." and "regenerate" in json.loads(calls[1]["messages"][1]["content"])


def test_schema_is_strict_mode_shaped():
    item = A.SCHEMA["properties"]["questions"]["items"]
    assert set(item["required"]) == set(item["properties"]) and item["additionalProperties"] is False



# ------------------------------------------------------------------ live lessons (qwen3.7-flash, LinkedIn, 2026-09-23)

LI = Page(url="https://www.linkedin.com/jobs/view/1/", title="Apply", text="Contact info Email address* Phone country code*",
          table=Table(url="u", elements=[
              Element(ref="e78", role="combobox", name="Email address*", value="amin@example.com",
                      current="amin@example.com", options=[Option(label="amin@example.com"), Option(label="x@y.z")]),
              Element(ref="e79", role="combobox", name="Phone country code*", value="it", current="Italy (+39)",
                      options=[Option(label="Andorra (+376)"), Option(label="Austria (+43)")]),   # first 40 only
              Element(ref="e80", role="textbox", name="Mobile phone number*"),
              Element(ref="e81", role="radio", name="Bachelor's Degree?", label="Yes"),
              Element(ref="e82", role="radio", name="Bachelor's Degree?", label="No")]))


def run_li(*qs, policy=Policy()):
    pa = PageAnswers.model_validate({"questions": list(qs)})
    check_answers(pa, LI, SRC, policy, TODAY)
    return pa.questions


def test_a_wrong_option_over_a_prefill_is_dropped_and_the_prefill_kept():
    (code,) = run_li(q(question="Phone country code*", kind="choice", ref="e79", option_ref="e79:12",
                       options=["Andorra (+376)", "Austria (+43)"], answer="Austria (+43)", source="computed"))
    assert code.answer == "Italy (+39)" and code.source == "linkedin-prefill" and "kept" in code.note


def test_the_current_value_is_a_valid_option_even_beyond_the_first_40():
    (code,) = run_li(q(question="Phone country code*", kind="choice", ref="e79", answer="italy (+39)",
                       source="linkedin-prefill"))
    assert code.answer == "Italy (+39)"


def test_an_echo_typo_in_the_options_does_not_drop_a_correct_prefill():
    (email,) = run_li(q(question="Email address*", kind="choice", ref="e78", options=["a min@example.com", "x@y.z"],
                        answer="amin@example.com", source="linkedin-prefill"))
    assert email.answer == "amin@example.com"


def test_a_radio_answer_must_match_the_radio_it_targets():
    fact = "I built a real-time computer vision pipeline reaching 200 FPS on an NPU."
    ok, crossed = run_li(
        q(question="Bachelor's Degree?", kind="choice", option_ref="e81", options=["Yes", "No"], answer="Yes",
          source="profile", quote=fact),
        q(question="Bachelor's Degree?", kind="choice", option_ref="e82", options=["Yes", "No"], answer="Yes",
          source="profile", quote=fact))
    assert ok.answer == "Yes" and crossed.answer is None                  # "Yes" aimed at the "No" radio


def test_empty_or_placeholder_fields_are_not_kept_and_strict_keeps_nothing():
    (phone,) = run_li(q(question="Mobile phone number*", ref="e80", answer=None, source=None))
    assert phone.answer is None                                           # nothing held → still unanswered
    (code,) = run_li(q(question="Phone country code*", kind="choice", ref="e79", answer=None, source=None),
                     policy=Policy(prefill="strict"))
    assert code.answer is None



def test_computed_is_only_for_total_years_never_for_a_tool():
    lines = ["**Full Stack Software Engineer** · Feb 2026 – Present",
             "**Deep Learning Engineer & Researcher** · Dec 2024 – Dec 2025",
             "**Computer Vision Research Assistant** · Sep 2024 – Dec 2024"]
    total, tool, other = [x for x in run(
        q(question="Total years of professional experience", ref="e5", answer="2", source="computed", relies_on=lines),
        q(question="How many years of work experience do you have with C++?", ref="e5", answer="2",
          source="computed", relies_on=lines),
        q(question="Notice period", ref="e2", answer="2", source="computed", relies_on=lines))[0]]
    assert total.answer == "2"
    assert tool.answer is None and "only for total years" in tool.note              # live LinkedIn case
    assert other.answer is None


def test_the_llm_inference_is_asked_for_no_hidden_reasoning():
    """The Flex, live 2026-09-23: qwen3.7-flash spent all 8,192 tokens reasoning — an empty answer, then a cut-off
    one ("LLM inference output invalid"). With reasoning off the same 43-field page answered in 21 s."""
    post, calls = canned((200, GOOD))
    A.answer_page(PAGE, SRC, key="k", models="m", policy=Policy(), today=TODAY, post=post)
    assert calls[0]["reasoning"] == {"enabled": False}


def test_a_quote_that_differs_only_in_formatting_still_counts():
    """Mastercard, live 2026-09-23: the model quoted the resume's "+393519358813" as "+39 351 935 8813" and the
    email answer was dropped as "quote not found"."""
    resume = ("abbaszadehmohammadamin@yahoo.com • github.com/Maaz77/ • linkedin.com/in/amin8abbaszadeh/ • "
              "Phone: +393519358813")
    quote = ("abbaszadehmohammadamin@yahoo.com • github.com/Maaz77/ • linkedin.com/in/amin8abbaszadeh/ • "
             "Phone: +39 351 935 8813")
    assert A.quoted_in(quote, resume)
    assert not A.quoted_in("amin@example.org", resume)                 # a different fact is still not there
    assert not A.quoted_in("Y-e-s", "yes, relocation")                 # too short to match without its punctuation
    (email,), _ = run(q(question="Email", ref="e1", answer="amin@example.com", source="resume",
                        quote="AMIN @ example . com"))
    assert email.answer == "amin@example.com" and email.note is None
