import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from fundsignals.models import (
    FundMatch,
    FundScore,
    RawDocument,
    Signal,
    SignalType,
    SourceType,
    make_doc_id,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
NOW = datetime(2026, 10, 19, tzinfo=UTC)


def _doc():
    return RawDocument(
        id=make_doc_id("https://x.example.com/a"),
        source="rss:test",
        source_type=SourceType.NEWS,
        url="https://x.example.com/a",
        title="t",
        text="body",
        published_at=NOW,
        fetched_at=NOW,
    )


def _signal(**kw):
    base = dict(
        fund_id="hrt",
        doc_id="abc",
        signal_type=SignalType.FPGA_HIRING,
        strength=0.8,
        evidence_snippet="hiring an FPGA Engineer",
        url="https://x.example.com/a",
        published_at=NOW,
    )
    base.update(kw)
    return Signal(**base)


@pytest.mark.parametrize(
    "obj",
    [
        _doc(),
        FundMatch(doc_id="abc", fund_id="hrt", fund_name="Hudson River Trading",
                  matched_text="HRT", confidence=0.95),
        _signal(),
        FundScore(fund_id="hrt", fund_name="Hudson River Trading", score=4.2, rank=1,
                  signal_count=1, top_signals=[_signal()], week_of=date(2026, 10, 19)),
    ],
)
def test_round_trip(obj):
    cls = type(obj)
    assert cls.model_validate_json(obj.model_dump_json()) == obj
    assert cls.model_validate(json.loads(obj.model_dump_json())) == obj


def test_confidence_and_strength_bounds():
    with pytest.raises(ValidationError):
        FundMatch(doc_id="a", fund_id="b", fund_name="c", matched_text="d", confidence=1.5)
    with pytest.raises(ValidationError):
        _signal(strength=-0.1)


def test_signal_requires_url_and_short_snippet():
    with pytest.raises(ValidationError):
        _signal(url="")
    with pytest.raises(ValidationError):
        _signal(evidence_snippet="")
    with pytest.raises(ValidationError):
        _signal(evidence_snippet="x" * 301)


def test_unknown_enum_and_extra_fields_rejected():
    with pytest.raises(ValidationError):
        _signal(signal_type="not_a_type")
    with pytest.raises(ValidationError):
        RawDocument(**_doc().model_dump(), extra_field=1)


def test_doc_id_is_deterministic():
    assert make_doc_id("https://a") == make_doc_id(" https://a ")
    assert len(make_doc_id("https://a")) == 16


def test_fixture_documents_validate():
    docs = json.loads((FIXTURES / "documents.json").read_text())
    assert len(docs) >= 40
    for d in docs:
        doc = RawDocument.model_validate(d)
        assert doc.id == make_doc_id(doc.url)
