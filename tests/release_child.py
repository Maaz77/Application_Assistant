"""Child process for the B3 release test: open a fixture tab, type a value, optionally release, exit normally.

    python -m tests.release_child <url> <value> release|keep
Prints the tab's target ID. The package's exit hook then closes each session's *current* tab.
"""
import sys

from assistant import config, jev, tabs
from tests.support import CDP_URL


def main(url: str, value: str, mode: str) -> None:
    cfg = config.load()
    jev.apply_env(cfg, "", cdp_url=CDP_URL)
    b = jev.Jev(cfg, "")
    book = tabs.TabBook(b)
    b.open(url, "child")
    _, table = b.table("child")
    note = next(e.ref for e in table.elements if e.name == "Cover note")
    b.act([{"op": "type", "ref": note, "text": value, "clear": True, "submit": False}], "child", table)
    print(book.current("child"), flush=True)
    if mode == "release":
        book.release("child")
    book.close()


if __name__ == "__main__":
    main(*sys.argv[1:4])
