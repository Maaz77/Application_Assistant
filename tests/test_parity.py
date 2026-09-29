"""P2 parity: the owned driver's element table matches the golden capture (tests/golden/tables/, T1)
on every fixture — the same controls (role, name, value, checked, options) in the same order. The
driver may report longer strings and extra fields; those are allowed, so strings are compared by
prefix (the vendored observer that produced the golden cut labels at 160 chars)."""
import json
from pathlib import Path

import pytest

from assistant.driver import BrowserManager, Settings
from tests.support import CDP_URL

pytestmark = pytest.mark.browser

GOLD = Path(__file__).parent / "golden" / "tables"
FIXTURES = Path(__file__).parent / "fixtures"
PAGES = sorted(p.relative_to(FIXTURES).as_posix() for p in FIXTURES.rglob("*.html"))


@pytest.fixture(scope="module")
def parity_session(cfg, chrome):
    mgr = BrowserManager(Settings(cdp_url=CDP_URL, max_actions=cfg.browser.max_actions))
    try:
        yield mgr.session("parity")
    finally:
        mgr.detach()


def _prefix_ok(new: str, gold: str) -> bool:
    return (new or "").startswith((gold or "").rstrip("…"))


@pytest.mark.parametrize("page", PAGES)
def test_owned_observer_matches_golden(page, parity_session, fixture_server):
    parity_session.navigate(fixture_server.url(page))
    obs = parity_session.observe()
    gold = json.loads((GOLD / f"{page.replace('/', '__')}.json").read_text())
    assert len(obs.elements) == len(gold["elements"]), f"{page}: element count"
    for i, (e, g) in enumerate(zip(obs.elements, gold["elements"])):
        assert e.role == g["role"], f"{page}[{i}] role {e.role!r} vs {g['role']!r}"
        assert _prefix_ok(e.name, g["name"]), f"{page}[{i}] name {e.name!r} vs {g['name']!r}"
        assert _prefix_ok(e.value, g.get("value") or ""), f"{page}[{i}] value {e.value!r} vs {g.get('value')!r}"
        assert e.checked == g.get("checked"), f"{page}[{i}] checked {e.checked!r} vs {g.get('checked')!r}"
        gold_opts = g.get("options") or []
        new_opts = [o.get("label") for o in e.options]
        assert len(new_opts) == len(gold_opts), f"{page}[{i}] options count {new_opts} vs {gold_opts}"
        for n, o in zip(new_opts, gold_opts):
            assert _prefix_ok(n or "", o or ""), f"{page}[{i}] option {n!r} vs {o!r}"
