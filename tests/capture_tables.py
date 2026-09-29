"""P2 T1: capture the CURRENT observer's element table for every fixture page, into
tests/golden/tables/<fixture>.json. This is the frozen parity baseline the owned driver
(assistant/driver/, P2 T2) is graded against in T6 — run it on the vendored wheel, before
any driver change. Re-running after the port is how T6 proves nothing regressed.

    .venv/bin/python -m tests.capture_tables
"""
import json
from pathlib import Path

from assistant import config, jev
from tests.support import CDP_URL, FixtureServer, ThrowawayChrome

FIXTURES = Path(__file__).parent / "fixtures"
OUT = Path(__file__).parent / "golden" / "tables"


def row(e) -> dict:
    """The parity-relevant fields, kept in table order. T6 compares exactly these (role, name,
    value, checked, options); the observer's other fields and any longer strings may differ."""
    return {
        "ref": e.ref,
        "role": e.role,
        "name": e.name,
        "value": e.value,
        "checked": e.checked,
        "options": [o.label for o in e.options],
    }


def capture(m: jev.Jev, srv: FixtureServer, page: str) -> dict:
    m.open(srv.url(page), "cap")
    _, table = m.table("cap")
    return {"url": page, "title": table.title, "elements": [row(e) for e in table.elements]}


def main() -> None:
    cfg = config.load()
    OUT.mkdir(parents=True, exist_ok=True)
    pages = sorted(p.relative_to(FIXTURES).as_posix() for p in FIXTURES.rglob("*.html"))
    with FixtureServer() as srv, ThrowawayChrome(cfg.browser.chrome):
        m = jev.Jev(cfg, "")
        for page in pages:
            data = capture(m, srv, page)
            (OUT / f"{page.replace('/', '__')}.json").write_text(
                json.dumps(data, indent=2, ensure_ascii=False))
            print(f"{page}: {len(data['elements'])} elements")


if __name__ == "__main__":
    jev.apply_env(config.load(), "", cdp_url=CDP_URL)
    main()
