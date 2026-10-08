"""Cross-run tab bookkeeping (live 2026-10-08). D13 keeps a parked job's tab open on purpose, but nothing
closed the tab the PREVIOUS run of the same job left, so each re-run added another live, filled form: the
user's Chrome held 7 jobs x 2 runs = 14 job tabs, and the two tabs for one Flex posting disagreed (the older
one carried an `Age` typed by hand, the newer one did not)."""
import json

import pytest

from assistant.tabs import TabBook, TabMemory

pytestmark = pytest.mark.unit
LI = "https://www.linkedin.com/jobs/view/"
ASHBY = "https://jobs.ashbyhq.com/The-Flex/d9457005/application"


def test_a_missing_or_corrupt_file_is_an_empty_memory(tmp_path):
    """Losing the memory costs one extra tab next run; it must never fail a job."""
    assert TabMemory(tmp_path / "nope.json").get("4470918779") == ("", "")
    bad = tmp_path / "open-tabs.json"
    bad.write_text("{not json")
    assert TabMemory(bad).get("4470918779") == ("", "")
    bad.write_text('["a list, not a map"]')
    assert TabMemory(bad).get("4470918779") == ("", "")


def test_a_job_remembers_its_tab_and_host_across_runs(tmp_path):
    path = tmp_path / "runs" / "open-tabs.json"
    TabMemory(path).remember("4470918779", "27AE5834C87D", "jobs.ashbyhq.com")
    TabMemory(path).remember("4470932445", "3E87AF66CDD5", "jobs.ashbyhq.com")
    assert TabMemory(path).get("4470918779") == ("27AE5834C87D", "jobs.ashbyhq.com")   # a fresh instance
    assert TabMemory(path).get("4470941188") == ("", "")                               # never run


def test_a_file_written_before_the_host_was_recorded_still_reads(tmp_path):
    """runs/open-tabs.json was seeded as {key: id} before the host was added; it must not become unreadable."""
    path = tmp_path / "open-tabs.json"
    path.write_text(json.dumps({"4470918779": "27AE5834C87D"}))
    assert TabMemory(path).get("4470918779") == ("27AE5834C87D", "")


def test_a_later_run_overwrites_only_its_own_job(tmp_path):
    path = tmp_path / "open-tabs.json"
    TabMemory(path).remember("4470918779", "OLD", "a.example")
    TabMemory(path).remember("4470932445", "OTHER", "b.example")
    TabMemory(path).remember("4470918779", "NEW", "a.example")
    assert json.loads(path.read_text()) == {"4470918779": {"id": "NEW", "host": "a.example"},
                                            "4470932445": {"id": "OTHER", "host": "b.example"}}


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
    b = _Tabs(tabs)
    return TabBook(b), b


def test_close_stale_closes_only_the_remembered_tab():
    book, b = _book([{"target_id": "NOW", "url": ASHBY},
                     {"target_id": "OLD", "url": ASHBY},
                     {"target_id": "OTHER-JOB", "url": f"{LI}4470932445/"},
                     {"target_id": "USER", "url": "https://docs.stagehand.dev/v4"}])
    assert book.close_stale("job-4470918779", "NOW", remembered="OLD", host="jobs.ashbyhq.com") == ["OLD"]
    assert sorted(t["target_id"] for t in b.tabs) == ["NOW", "OTHER-JOB", "USER"]


def test_close_stale_never_touches_a_tab_the_user_opened():
    """An earlier version also closed any tab whose URL held /jobs/view/<job_key>, with no ownership test at
    all — and list_tabs() is every tab in the user's Chrome. Reading the posting and then running the tool is
    exactly how the user queues a job, so that rule would have closed their own tab (reviewer finding)."""
    user_tab = {"target_id": "USER-READING-THE-POSTING", "url": f"{LI}4470918779/?trackingId=theirs"}
    book, b = _book([{"target_id": "NOW", "url": f"{LI}4470918779/?trackingId=ours"}, user_tab])
    assert book.close_stale("job-4470918779", "NOW", remembered="", host="") == []
    assert b.closed == [] and user_tab in b.tabs


def test_close_stale_leaves_a_tab_that_has_since_moved_on():
    """A target id outlives the page. If the remembered tab is no longer on the host we parked it on, the
    user has navigated it somewhere else and it is not ours to close."""
    book, b = _book([{"target_id": "NOW", "url": ASHBY},
                     {"target_id": "OLD", "url": "https://news.example.com/article"}])
    assert book.close_stale("job-4470918779", "NOW", remembered="OLD", host="jobs.ashbyhq.com") == []
    assert b.closed == []


def test_close_stale_never_closes_the_tab_this_run_is_driving():
    """A job whose previous run left the very tab this run adopted: the live tab must survive."""
    book, b = _book([{"target_id": "NOW", "url": ASHBY}])
    assert book.close_stale("job-4470918779", "NOW", remembered="NOW", host="jobs.ashbyhq.com") == []
    assert b.closed == []


def test_close_stale_is_a_no_op_when_the_tab_is_already_gone():
    book, b = _book([{"target_id": "NOW", "url": ASHBY}])
    assert book.close_stale("job-4470918779", "NOW", remembered="CLOSED-BY-THE-USER", host="") == []
    assert b.closed == []
