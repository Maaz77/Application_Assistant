"""live_model: the configured LLM inference on f08 with small, fixed sources."""
import os
from datetime import date

import pytest

from assistant import config
from assistant.llm_inference import Policy, Sources, answer_page, uncovered_required
from assistant.pages import read_page

pytestmark = pytest.mark.live_model

PROFILE = """# Resume
**Deep Learning Engineer & Researcher** · Dec 2024 – Dec 2025 — STMicroelectronics, Milan, Italy
I live in Milan, Italy.
# Scratch Pad
- Are you legally authorized to work in the United Kingdom? No, I need visa sponsorship.
- Preferred work model? Hybrid
"""


def test_engine_answers_f08(new_browser, fixture_server, chat_key, cfg):
    with new_browser() as browser:
        browser.open(fixture_server.url("f08.html"), "a")
        p = read_page(browser, "a")
        browser.close("a")
    pa = answer_page(p, Sources(PROFILE, "Acme, Milan. Data Engineer.", "Amin Abbaszadeh\nMilan, Italy"),
                     key=chat_key, models=os.environ.get("AA_ANSWER_MODEL") or cfg.models.llm_inference, policy=Policy(),
                     today=date(2026, 9, 23), url=config.chat_url(cfg))
    got = {q.question: q for q in pa.questions}
    print({k: (v.answer, v.source, v.note) for k, v in got.items()})
    city = next(v for k, v in got.items() if "city" in k.lower())
    assert city.answer and "Milan" in city.answer
    assert all(q.answer is None or q.source for q in pa.questions)
    assert not any("salary" in q.question.lower() and q.answer for q in pa.questions)
    assert uncovered_required(pa) == [] or all(q.answer is None for q in uncovered_required(pa))
