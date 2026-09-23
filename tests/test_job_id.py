import pytest

from assistant.tracker import job_id

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("url, key", [
    ("https://www.linkedin.com/jobs/view/4012345678/", "4012345678"),
    ("https://www.linkedin.com/jobs/view/senior-data-engineer-at-acme-4012345678?trk=x", "4012345678"),
    ("https://www.linkedin.com/jobs/search/?currentJobId=4012345678&geoId=1", "4012345678"),
    ("https://boards.greenhouse.io/acme/jobs/123", "https://boards.greenhouse.io/acme/jobs/123"),
    ("  https://x.io/j/1  ", "https://x.io/j/1"),
    ("https://Jobs.Lever.co/Acme/abc?utm_source=li", "https://jobs.lever.co/acme/abc"),
    (None, ""),
])
def test_job_id(url, key):
    assert job_id(url) == key
