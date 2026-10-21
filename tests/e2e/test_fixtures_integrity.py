"""Sanity checks on the shared fixtures that the e2e assertions rely on."""

import csv
import json
from pathlib import Path

from fundsignals.models import RawDocument, make_doc_id

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
INDEX = json.loads((FIXTURES / "fixture_index.json").read_text())
DOCUMENTS = json.loads((FIXTURES / "documents.json").read_text())


def test_fixture_index_present():
    assert len(INDEX["items"]) >= 40
    assert INDEX["expected_top5_contains"]
    assert INDEX["expected_absent_from_ranking"]


def test_documents_are_valid_and_ids_match_urls():
    for raw in DOCUMENTS:
        doc = RawDocument.model_validate(raw)
        assert doc.id == make_doc_id(doc.url)


def test_index_doc_ids_exist_in_documents():
    doc_ids = {d["id"] for d in DOCUMENTS}
    for item in INDEX["items"]:
        assert item["doc_id"] in doc_ids, item["key"]
        assert item["doc_id"] == make_doc_id(item["url"])


def test_expected_funds_are_in_universe():
    with open(ROOT / "data" / "fund_universe.sample.csv") as fh:
        universe = {row["fund_id"] for row in csv.DictReader(fh)}
    expected = set(INDEX["expected_top5_contains"]) | set(INDEX["expected_absent_from_ranking"])
    expected |= {i["expected_fund_id"] for i in INDEX["items"] if i["expected_fund_id"]}
    assert expected <= universe, sorted(expected - universe)
