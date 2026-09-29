"""split_json parses a golden observe (include_json) block into a Table (moved to assistant.browser, P2)."""
from pathlib import Path

import pytest

from assistant.browser import split_json

pytestmark = pytest.mark.unit
GOLDEN = Path(__file__).parent / "golden"


def test_golden_observe_parses():
    view, table = split_json((GOLDEN / "observe_full_json.txt").read_text())
    roles = {e.role for e in table.elements}
    assert {"textbox", "combobox", "radio", "checkbox", "file", "button"} <= roles
    country = next(e for e in table.elements if e.name == "Country")
    assert [o.label for o in country.options] == ["Choose", "Italy", "Iran"]
    assert table.title == "Discovery form" and view.startswith("[obs#")
