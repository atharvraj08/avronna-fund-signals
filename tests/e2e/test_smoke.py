import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_fixture_index_present():
    index = json.loads((FIXTURES / "fixture_index.json").read_text())
    assert len(index["items"]) >= 40
