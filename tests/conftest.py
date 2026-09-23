"""Test setup. The package reads its config once per process, at `jev_ultrafast_mcp.server` import (spec v2 §9):
apply_env() runs here, pointed at the throwaway Chrome, before anything can load the server. All browser tests
share that Chrome; the model key (if any) comes from .env so --live tests work in the same process."""
from contextlib import nullcontext

import pytest

from assistant import config, jev
from tests.support import CDP_URL, FixtureServer, ThrowawayChrome

jev.apply_env(config.load(), config.api_key(), cdp_url=CDP_URL)


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", help="run live_model tests (OpenRouter, local fixtures)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="live_model needs --live")
    for item in items:
        if "live_model" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def cfg():
    return config.load()


@pytest.fixture(scope="session")
def _fixture_server():
    with FixtureServer() as srv:
        yield srv


@pytest.fixture
def fixture_server(_fixture_server):
    """The shared server with posts.log emptied for this test."""
    from tests.support import POSTS_LOG
    POSTS_LOG.write_text("")
    return _fixture_server


@pytest.fixture(scope="session")
def chrome(cfg):
    with ThrowawayChrome(cfg.browser.chrome) as c:
        yield c


@pytest.fixture
def new_browser(cfg, chrome, tmp_path):
    """Factory: `with new_browser() as b:` — a Jev client on the shared throwaway Chrome.
    The key only matters for calls.jsonl redaction; the model key was fixed by apply_env() above."""
    return lambda key="": nullcontext(jev.Jev(cfg, key, calls_log=tmp_path / "calls.jsonl"))


class Secret(str):
    """A str whose repr never shows the value (pytest prints fixture values in failure reports)."""
    def __repr__(self):
        return "'***'"


@pytest.fixture(scope="session")
def api_key():
    key = config.api_key()
    if not key:
        pytest.skip("no OPENROUTER_API_KEY in .env")
    return Secret(key)
