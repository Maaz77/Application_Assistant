"""T6 live_model: f02 and f08 filled end-to-end with the real answer engine and parked, zero POSTs."""
import os
from datetime import date

import pytest
from pypdf import PdfWriter

from assistant.answers import Policy, Sources, answer_page
from assistant.blockers import NeedsAttention
from assistant.fill import JobCtx, run_pages
from tests.support import CDP_URL

pytestmark = pytest.mark.live_model

PROFILE = """# Resume
**Deep Learning Engineer & Researcher** · Dec 2024 – Dec 2025
**Full Stack Software Engineer** · Feb 2026 – Present
I live in Milan, Italy. My phone number is +39 333 1234567.
I worked at Tetra Pak.
# Scratch Pad
- Do you require visa sponsorship? No
- Preferred work model? Hybrid
- Earliest start date? 2026-11-15
"""


@pytest.fixture
def resume(tmp_path):
    pdf = tmp_path / "Amin_Acme_Data_Engineer.pdf"
    w = PdfWriter()
    w.add_blank_page(200, 200)
    with open(pdf, "wb") as fh:
        w.write(fh)
    return pdf


@pytest.mark.parametrize("page", ["f02.html", "f08.html"])
def test_parked_live(new_browser, fixture_server, api_key, cfg, resume, tmp_path, chrome, page):
    model = os.environ.get("AA_ANSWER_MODEL") or cfg.models.answer_engine
    src = Sources(PROFILE, "Acme, Milan. Data Engineer.", "Amin Abbaszadeh\nMilan, Italy")

    def engine(p):
        return answer_page(p, src, key=api_key, model=model, policy=Policy(), today=date(2026, 9, 23))

    with new_browser(api_key) as browser:
        browser.open(fixture_server.url(page), "live")
        ctx = JobCtx(browser=browser, session="live", cdp_url=CDP_URL, resume_pdf=resume, answer_fn=engine,
                     baseline={t["id"] for t in chrome.tabs()}, answers_log=tmp_path / "answers.json",
                     shots_dir=tmp_path / "shots", folder=page)
        try:
            parked = run_pages(ctx)
        except NeedsAttention as na:
            parked = na
        browser.close("live")
    assert fixture_server.posts() == []
    if page == "f08.html" and isinstance(parked, NeedsAttention):
        # DISCOVERY.md: the read-only date picker cannot be set on jev 0.1.5; nothing else may fail
        assert parked.what.startswith("field would not accept its value: 'Earliest start date'"), parked.what
        return
    assert not isinstance(parked, NeedsAttention), parked
    assert parked.title and (tmp_path / "shots" / f"{page}.jpg").exists()
