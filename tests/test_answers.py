import json
from datetime import date

import pytest

from assistant import llm_inference as A
from assistant.llm_inference import PageAnswers, Policy, Sources, check_answers, total_months
from assistant.browser import Element, Option, Table
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


def test_radio_group_with_a_compound_uuid_key_falls_back_to_select_one():
    """P5 (live 2026-10-07): Ashby groups radios under a compound UUID id (two UUIDs joined). _group_label must
    treat it as having no question text and fall back to 'Select one', not emit space-separated hex as the
    question — which also keeps demographic options from being sent to the model under a meaningless label."""
    key = "5e8c23fc-a45b-4898-b611-85a74dcb9cf6_07d924f7-8d86-4961-9d0e-1a2b3c4d5e6f"
    demog = [Element(ref="e20", role="radio", name="Male", tag="INPUT", context=""),
             Element(ref="e21", role="radio", name="Female", tag="INPUT", context="")]
    assert A._group_label(key, demog) == "Select one"
    real = [Element(ref="e1", role="radio", name="Yes", tag="INPUT"),
            Element(ref="e2", role="radio", name="No", tag="INPUT")]
    assert A._group_label("work_authorization", real) == "Work authorization"   # real keys still read as words


def test_contact_fallback_fills_phone_and_email_the_model_left_blank():
    """User decision 2026-10-08: a required phone/email with no model answer is filled from Profile.md by regex
    (the free router missed DMI's phone, 2026-10-07). A country-code field and an already-answered or file
    field are left alone; name is not extracted."""
    prof = "Amin Abbaszadeh\nEmail: abbaszadehmohammadamin@yahoo.com\nMobile: +39 351 935 8813\nMilan, Italy\n"
    pa = PageAnswers.model_validate({"questions": [
        q(id="a", question="Mobile phone number"), q(id="b", question="Email"),
        q(id="c", question="Phone country code"), q(id="d", question="Mobile phone number", answer="already"),
        q(id="e", question="Cover letter", kind="file")]})
    A.fill_contact_from_profile(pa, Sources(profile=prof, job="", resume=""))
    byid = {x.id: x for x in pa.questions}
    assert byid["a"].answer == "+39 351 935 8813" and byid["a"].source == "profile"
    assert byid["b"].answer == "abbaszadehmohammadamin@yahoo.com"
    assert byid["c"].answer is None and byid["d"].answer == "already" and byid["e"].answer is None


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


GOOD = json.dumps({"answers": [{"id": "t_e2", "answer": "3 months", "source": "profile",
                                 "quote": "What is your notice period? 3 months", "relies_on": None}]})


def test_schema_400_falls_back_to_json_object():
    post, calls = canned((400, {"error": "response_format"}), (200, GOOD))
    pa = A.answer_page(PAGE, SRC, key="k", models="m", policy=Policy(), today=TODAY, post=post)
    q = next(x for x in pa.questions if x.question == "Notice period")
    assert q.answer == "3 months"
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
    # P1 T3: a rejected key stops the whole run (CreditOrKey), because no other model on that key would do better.
    from assistant import gateway as G
    post, calls = canned((401, {"error": {"code": 401, "message": "No auth credentials found"}}))
    with pytest.raises(G.CreditOrKey, match="rejected the key") as ei:
        A.answer_page(PAGE, SRC, key="k", models=["a", "b"], policy=Policy(), today=TODAY, post=post)
    assert len(calls) == 1 and not isinstance(ei.value, A.ModelUnavailable)   # the second model is never tried


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
    from assistant import gateway as G
    with pytest.raises(G.CreditOrKey, match="ai-gateway.vercel.sh rejected the key"):
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
    first = json.dumps({"answers": [{"id": "t_e3", "answer": "made up", "source": "generated",
                                     "quote": None, "relies_on": ["Not in sources."]}]})
    second = json.dumps({"answers": [{"id": "t_e3", "answer": "Real text.", "source": "generated",
                                      "quote": None, "relies_on": [fact]}]})
    post, calls = canned((200, "```json\n" + first + "\n```"), (200, second))
    pa = A.answer_page(PAGE, SRC, key="k", models="m", policy=Policy(), today=TODAY, post=post)
    q = next(x for x in pa.questions if x.question == "Why Acme?")
    assert q.answer == "Real text." and "regenerate" in json.loads(calls[1]["messages"][1]["content"])


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
    one ("LLM inference output invalid"). With reasoning off the same 43-field page answered in 21 s.

    The field is `reasoning_effort`, not OpenRouter's `reasoning: {"enabled": false}`: the FreeLLMAPI router
    passed the latter through and ignored it (645 of 696 completion tokens were reasoning tokens), while
    `reasoning_effort: "none"` did switch reasoning off (kimi-k3: 0 reasoning tokens, live 2026-10-07)."""
    post, calls = canned((200, GOOD))
    A.answer_page(PAGE, SRC, key="k", models="m", policy=Policy(), today=TODAY, post=post)
    assert calls[0]["reasoning_effort"] == "none" and "reasoning" not in calls[0]


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


def test_an_option_answer_survives_a_citation_that_is_not_verbatim():
    """Linda AI, live 2026-10-06. "Have you completed the following level of education: Bachelor's Degree?" was
    answered "Yes" twice and dropped twice as "quote not found in the sources", so the required radio stayed empty
    and the job ended as broken_form. Neither failure was about formatting (`quoted_in` already ignores case,
    spacing and punctuation): attempt 1 cited the resume and spliced "Jun" into its date range, attempt 2 cited
    Profile.md and elided "(Final Grade: 100/110)" from the middle of the line. An insertion or an omission inside
    a quote is not a contiguous substring at all.

    A `choice` answer no longer needs the quote, because it has to be one of the page's own options instead — but
    the record keeps the citation and says it was not confirmed.
    """
    profile = "**M.Sc. in Computer Science and Engineering (majoring Artificial Intelligence)** " \
              "(Final Grade: 100/110) · Sep 2022 – Dec 2025"
    src = Sources(profile, "", "")
    elided = "M.Sc. in Computer Science and Engineering (majoring Artificial Intelligence) " \
             "· Sep 2022 – Dec 2025"
    assert not A.quoted_in(elided, profile)        # the normalisation cannot save an elision; that is the bug

    page = Page(url="https://linda.ai/apply", title="Apply",
                text="Have you completed the following level of education: Bachelor's Degree? Yes No",
                table=Table(url="https://linda.ai/apply", elements=[
                    Element(ref="e147", role="radio", name="Have you completed the following level of "
                            "education: Bachelor's Degree?", label="Yes"),
                    Element(ref="e148", role="radio", name="Have you completed the following level of "
                            "education: Bachelor's Degree?", label="No"),
                ]))
    pa = PageAnswers.model_validate({"questions": [
        q(id="r_e147", question="Have you completed the following level of education: Bachelor's Degree?",
          kind="choice", options=["Yes", "No"], answer="Yes", source="profile", quote=elided)]})
    check_answers(pa, page, src, Policy(), TODAY)
    kept = pa.questions[0]
    assert kept.answer == "Yes"                                    # the fix: no longer dropped
    assert kept.quote == elided and "not verbatim" in kept.note    # but the record still says so


def test_free_text_still_needs_a_verbatim_quote():
    """The relaxation is scoped to `choice`. Free text has no second kind of evidence, so an unverifiable
    citation still drops it — otherwise the model could type any fact into the form."""
    (invented,), _ = run(q(question="Notice period", ref="e2", answer="6 weeks", source="profile",
                           quote="My notice period is six weeks."))
    assert invented.answer is None and "quote not found" in invented.note


def test_an_option_answer_off_the_page_is_still_dropped():
    """Skipping the quote must not make a `choice` answer evidence-free: the option check is what replaces it."""
    (bad,), _ = run(q(question="Work model", kind="choice", ref="e4", options=["Remote", "Hybrid"],
                      answer="Anywhere", source="profile", quote="not in any source"))
    assert bad.answer is None and "not one of the field's options" in bad.note


# ------------------------------------------------------------------ extraction tests (T6)

def test_extract_questions_ids_are_stable():
    """Each field gets a stable ID: t_{ref} for text, s_{ref} for selects, r_{group} for radios."""
    from assistant.llm_inference import extract_questions
    p = Page(url="https://x.io/apply", title="Apply", text="City Work model Visa",
             table=Table(url="https://x.io/apply", elements=[
                 Element(ref="e1", role="textbox", name="City", required=True),
                 Element(ref="e2", role="combobox", name="Work model", required=True),
                 Element(ref="e3", role="radio", name="Yes", group="Visa needed?", required=True),
                 Element(ref="e4", role="radio", name="No", group="Visa needed?"),
             ]))
    ext = extract_questions(p)
    ids = [q.id for q in ext.questions]
    assert "t_e1" in ids       # textbox → t_
    assert "s_e2" in ids      # combobox → s_
    assert any(ids)            # at least one question
    # radio group should use r_ prefix
    radio_q = next(q for q in ext.questions if q.kind == "choice")
    assert radio_q.id.startswith("r_")


def test_extract_questions_radio_grouping():
    """Two radios with the same group get one choice question with both options."""
    from assistant.llm_inference import extract_questions
    p = Page(url="https://x.io/apply", title="Apply", text="Remote or Hybrid?",
             table=Table(url="https://x.io/apply", elements=[
                 Element(ref="e1", role="radio", name="Remote", group="Work mode", checked=True),
                 Element(ref="e2", role="radio", name="Hybrid", group="Work mode"),
             ]))
    ext = extract_questions(p)
    assert len(ext.questions) == 1
    q = ext.questions[0]
    assert q.kind == "choice"
    assert "Remote" in q.options
    assert "Hybrid" in q.options


def test_extract_questions_option_map_builds_correctly():
    """option_maps maps qid to {option_label: element_ref} for post-check assignment."""
    from assistant.llm_inference import extract_questions
    p = Page(url="https://x.io/apply", title="Apply", text="Visa?",
             table=Table(url="https://x.io/apply", elements=[
                 Element(ref="e1", role="radio", name="Yes", group="Visa"),
                 Element(ref="e2", role="radio", name="No", group="Visa"),
             ]))
    ext = extract_questions(p)
    qid = ext.questions[0].id
    omap = ext.option_maps[qid]
    assert omap.get("Yes") == "e1"
    assert omap.get("No") == "e2"


def test_extract_questions_current_values_tracked():
    """Pre-checked radios and pre-filled textboxes go into current_values."""
    from assistant.llm_inference import extract_questions
    p = Page(url="https://x.io/apply", title="Apply", text="City Salary",
             table=Table(url="https://x.io/apply", elements=[
                 Element(ref="e1", role="textbox", name="City", value="Milan", required=True),
                 Element(ref="e2", role="radio", name="Yes", group="Visa", checked=True),
                 Element(ref="e3", role="radio", name="No", group="Visa"),
             ]))
    ext = extract_questions(p)
    city_q = next(q for q in ext.questions if "City" in q.question)
    visa_q = next(q for q in ext.questions if q.kind == "choice")
    assert ext.current_values.get(city_q.id) == "Milan"
    assert ext.current_values.get(visa_q.id) == "Yes"


# --- a required LinkedIn radio group the control itself does not mark (live 2026-10-08, Linda AI) ---------

LINDA_P3_TEXT = ("Apply to Linda AI 3/4 pages Additional Questions "
                 "Have you completed the following level of education: Bachelor's Degree?* Yes No "
                 "Are you comfortable working in an onsite setting?* Yes No "
                 "Are you legally authorized to work in Ireland?* Yes No Back Review")
ONSITE = "Are you comfortable working in an onsite setting?"


def _linda_page_3() -> Page:
    """Linda AI's Easy Apply page 3 as the driver saw it: DIV role=radio, required False, aria-label without
    the asterisk, and REQUIRED_EMPTY empty (it scans input/select/textarea only)."""
    els = []
    for ref, question, label in (("e162", "Have you completed the following level of education: "
                                  "Bachelor's Degree?", "Yes"),
                                 ("e148", "Have you completed the following level of education: "
                                  "Bachelor's Degree?", "No"),
                                 ("e149", ONSITE, "Yes"), ("e150", ONSITE, "No")):
        els.append(Element(ref=ref, role="radio", name=question, label=label, tag="DIV",
                           required=False, dialog="<dialog>", scope="<dialog>", group=question))
    return Page(url="https://www.linkedin.com/jobs/view/4470454940/", title="Founding Software Engineer",
                text=LINDA_P3_TEXT, table=Table(url="https://www.linkedin.com/jobs/view/4470454940/",
                                                elements=els),
                dialogs=["Apply to Linda AI"])


def test_a_starred_radio_group_is_required_even_when_the_dom_says_otherwise():
    """Live 2026-10-08: the free router returned a generated (so rejected) answer for the onsite question, and
    nothing marked the group required — LinkedIn's DIV radios carry no `required`, their aria-label drops the
    asterisk, and REQUIRED_EMPTY never sees them. The group was treated as optional, left empty, and "Review"
    then refused to advance, which the loop reported as broken_form instead of parking at the question."""
    p = _linda_page_3()
    assert p.required_empty["items"] == []                        # the probe cannot see DIV radios
    assert all(not e.required for e in p.elements)                # nor can the observer
    by_q = {q.question: q for q in A.extract_questions(p).questions}
    assert by_q[ONSITE].required is True


def test_the_asterisk_rule_does_not_leak_into_text_fields():
    """Radio groups only: a text field's label is routinely a substring of another one's, so a page-text rule
    there would mark optional fields required and park jobs that fill fine today."""
    p = Page(url="https://acme.io/apply", title="Apply", text="Last Name* Name Email*",
             table=Table(url="https://acme.io/apply", elements=[
                 Element(ref="e1", role="textbox", name="Name", required=False),
                 Element(ref="e2", role="textbox", name="Last Name", required=False)]))
    by_q = {q.question: q for q in A.extract_questions(p).questions}
    assert by_q["Name"].required is False and by_q["Last Name"].required is False


def test_an_unanswered_starred_radio_group_is_an_uncovered_required_question():
    """The point of the required flag: with no answer the fill path parks at the question (ParkedAtQuestion)
    rather than advancing into LinkedIn's "This field is required"."""
    p = _linda_page_3()
    ext = A.extract_questions(p)
    pa = PageAnswers.model_validate({"questions": [
        {**que.model_dump(), "answer": "Yes" if que.question != ONSITE else None,
         "source": "profile" if que.question != ONSITE else None,
         "quote": None, "relies_on": None}
        for que in ext.questions]})
    assert [q.question for q in A.uncovered_required(pa)] == [ONSITE]
