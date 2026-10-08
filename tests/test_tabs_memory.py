"""Cross-run tab bookkeeping (live 2026-10-08). D13 keeps a parked job's tab open on purpose, but nothing
closed the tab the PREVIOUS run of the same job left, so each re-run added another live, filled form: the
user's Chrome held 7 jobs x 2 runs = 14 job tabs, and the two tabs for one Flex posting disagreed (the older
one carried an `Age` typed by hand, the newer one did not)."""
import json

import pytest

from assistant.tabs import TabMemory

pytestmark = pytest.mark.unit


def test_a_missing_or_corrupt_file_is_an_empty_memory(tmp_path):
    """Losing the memory costs one extra tab next run; it must never fail a job."""
    assert TabMemory(tmp_path / "nope.json").get("4470918779") == ""
    bad = tmp_path / "open-tabs.json"
    bad.write_text("{not json")
    assert TabMemory(bad).get("4470918779") == ""
    bad.write_text('["a list, not a map"]')
    assert TabMemory(bad).get("4470918779") == ""


def test_a_job_remembers_its_tab_across_runs(tmp_path):
    path = tmp_path / "runs" / "open-tabs.json"
    TabMemory(path).remember("4470918779", "27AE5834C87D461FDC8A9712B6FE5B19")
    TabMemory(path).remember("4470932445", "3E87AF66CDD5B45367EC0472BB22557E")
    # A fresh instance reads it, as the next run's process() does.
    assert TabMemory(path).get("4470918779") == "27AE5834C87D461FDC8A9712B6FE5B19"
    assert TabMemory(path).get("4470932445") == "3E87AF66CDD5B45367EC0472BB22557E"
    assert TabMemory(path).get("4470941188") == ""          # a job that has never run


def test_a_later_run_overwrites_only_its_own_job(tmp_path):
    path = tmp_path / "open-tabs.json"
    TabMemory(path).remember("4470918779", "OLD")
    TabMemory(path).remember("4470932445", "OTHER")
    TabMemory(path).remember("4470918779", "NEW")
    assert json.loads(path.read_text()) == {"4470918779": "NEW", "4470932445": "OTHER"}


class _Tabs:
    """Just enough Browser for TabBook.close_stale: a tab list and a recorded close."""

    def __init__(self, tabs):
        self.tabs, self.closed = tabs, []

    def list_tabs(self):
        return list(self.tabs)

    def close_tab_id(self, session, target_id):
        self.closed.append(target_id)
        self.tabs = [t for t in self.tabs if t["target_id"] != target_id]


def _book(tabs):
    from assistant.tabs import TabBook
    b = _Tabs(tabs)
    return TabBook(b), b


LI = "https://www.linkedin.com/jobs/view/"


def test_close_stale_closes_this_jobs_leftovers_and_nothing_else():
    """The two kinds of leftover look different: an adopted ATS tab is only recognisable by the id TabMemory
    recorded, a LinkedIn posting tab by its own job id (which needs no state, so it works on the first run
    after this fix)."""
    book, b = _book([
        {"target_id": "NOW", "url": f"{LI}4470918779/?trackingId=new"},          # this run — keep
        {"target_id": "OLD-LI", "url": f"{LI}4470918779/?trackingId=old"},       # earlier run, same job
        {"target_id": "OLD-ATS", "url": "https://jobs.ashbyhq.com/The-Flex/d9457005/application"},
        {"target_id": "OTHER-JOB", "url": f"{LI}4470932445/?trackingId=x"},      # a different job — keep
        {"target_id": "USER", "url": "https://docs.stagehand.dev/v4"},           # the user's own — keep
    ])
    closed = book.close_stale("job-4470918779", "NOW", job_key="4470918779", remembered="OLD-ATS")
    assert sorted(closed) == ["OLD-ATS", "OLD-LI"]
    assert sorted(t["target_id"] for t in b.tabs) == ["NOW", "OTHER-JOB", "USER"]


def test_close_stale_never_closes_the_tab_this_run_is_driving():
    """Even when the remembered id IS the current tab — a job whose previous run left the very tab this run
    adopted — the live tab must survive."""
    book, b = _book([{"target_id": "NOW", "url": f"{LI}4470918779/"}])
    assert book.close_stale("job-4470918779", "NOW", job_key="4470918779", remembered="NOW") == []
    assert b.closed == []


def test_close_stale_is_a_no_op_for_a_job_that_has_never_run():
    book, b = _book([{"target_id": "NOW", "url": f"{LI}4470941188/"},
                     {"target_id": "USER", "url": "https://claude.ai/new"}])
    assert book.close_stale("job-4470941188", "NOW", job_key="4470941188", remembered="") == []
    assert b.closed == []
