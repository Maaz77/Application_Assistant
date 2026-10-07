"""Test setup (P2). The owned driver attaches to the throwaway Chrome via its cdp_url — there is no package
env to apply. All browser tests share that Chrome; the model key (if any) comes from .env so --live tests work."""
from contextlib import nullcontext

import pytest

from assistant import config, guard
from tests.support import CDP_URL, FixtureServer, ThrowawayChrome


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


@pytest.fixture(autouse=True)
def decider(request):
    """Every decision the program asks of the System One model: the offline rule stand-in
    (tests/rule_decider.py), or — for a live_model test — the real thing, the Kev server on this machine, which
    the test then needs running."""
    from assistant import decide
    from assistant import gateway as gateway_mod
    from tests.rule_decider import RuleDecider
    guard.FORM.started = False                # the never-submit rule's stage: each test starts before any form
    guard.FORM.final = False
    cfg = config.load()
    live = "live_model" in request.keywords
    # A live test sends for real, so it needs the run's Gateway, as a run has one. An offline test gets none: a
    # sender with no Gateway refuses rather than reaching a provider, which is what keeps the suite off the network.
    gateway = gateway_mod.for_config(cfg) if live else None
    gateway_mod.use(gateway)
    d = decide.for_config(cfg, gateway) if live else RuleDecider()
    decide.use(d)
    yield d
    decide.use(None)
    gateway_mod.use(None)


@pytest.fixture(autouse=True)
def _reset_inference_log():
    """No active run by default: a test that never calls start_run gets no-op loggers, and no test writes into
    another's run dir (preflight/run now start a run)."""
    from assistant import inference_log
    inference_log.start_run(None)
    yield


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
    """Factory: `with new_browser() as b:` — a Browser over the owned driver on the shared throwaway Chrome."""
    from assistant.browser import Browser
    c = cfg.model_copy(update={"browser": cfg.browser.model_copy(update={"cdp_url": CDP_URL})})
    return lambda key="": nullcontext(Browser(c, key, actions_log=tmp_path / "browser_actions.jsonl"))


class Secret(str):
    """A str whose repr never shows the value (pytest prints fixture values in failure reports)."""
    def __repr__(self):
        return "'***'"


@pytest.fixture(scope="session")
def chat_key(cfg):
    """FREELLMAPI_KEY: the FreeLLMAPI router's key, which every chat request uses."""
    key = config.chat_key()
    if not key:
        pytest.skip(f"no {config.CHAT_KEY_NAME} in .env")
    return Secret(key)
