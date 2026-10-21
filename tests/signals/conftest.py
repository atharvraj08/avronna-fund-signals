import json
from pathlib import Path

import pytest

from fundsignals.models import FundMatch, RawDocument

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SETTINGS = {"signals": {"max_snippet_chars": 300, "llm": {"min_confidence": 0.6}}}


@pytest.fixture(scope="session")
def index():
    return json.loads((FIXTURES / "fixture_index.json").read_text())


@pytest.fixture(scope="session")
def docs():
    return [RawDocument(**d) for d in json.loads((FIXTURES / "documents.json").read_text())]


@pytest.fixture(scope="session")
def docs_by_id(docs):
    by_id = {}
    for d in docs:
        by_id.setdefault(d.id, []).append(d)
    return by_id


@pytest.fixture(scope="session")
def matches(index):
    """FundMatch per fixture item with an expected fund (independent of the entities package)."""
    seen = set()
    out = []
    for item in index["items"]:
        fund_id = item["expected_fund_id"]
        if fund_id and (item["doc_id"], fund_id) not in seen:
            seen.add((item["doc_id"], fund_id))
            out.append(
                FundMatch(
                    doc_id=item["doc_id"],
                    fund_id=fund_id,
                    fund_name=fund_id,
                    matched_text=fund_id,
                    confidence=1.0,
                )
            )
    return out


@pytest.fixture
def settings():
    return json.loads(json.dumps(SETTINGS))
