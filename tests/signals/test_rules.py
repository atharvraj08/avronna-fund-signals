from datetime import UTC, datetime

import pytest

from fundsignals.models import RawDocument, SignalType, SourceType, make_doc_id
from fundsignals.signals import RuleExtractor
from fundsignals.signals.rules import make_snippet, split_sentences

NOW = datetime(2026, 10, 19, tzinfo=UTC)


def _doc(title, text, source_type=SourceType.NEWS, url="https://x.example.com/a"):
    return RawDocument(
        id=make_doc_id(url),
        source="test",
        source_type=source_type,
        url=url,
        title=title,
        text=text,
        published_at=NOW,
        fetched_at=NOW,
    )


def _types(hits):
    return {h.signal_type for h in hits}


def test_each_positive_fixture_fires_exactly_expected(index, docs_by_id):
    extractor = RuleExtractor()
    for item in index["items"]:
        if item["category"] != "positive":
            continue
        doc = docs_by_id[item["doc_id"]][0]
        got = {t.value for t in _types(extractor.extract(doc))}
        assert got == set(item["expected_signals"]), item["key"]


@pytest.mark.parametrize("category", ["negative", "trap"])
def test_negative_and_trap_fixtures_fire_nothing_for_rules(index, docs_by_id, category):
    extractor = RuleExtractor()
    for item in index["items"]:
        if item["category"] != category:
            continue
        doc = docs_by_id[item["doc_id"]][0]
        # Vendor/unrelated-company news may mention FPGAs or GPUs; it is gated by FundMatch.
        if item["expected_fund_id"] is None:
            continue
        assert extractor.extract(doc) == [], item["key"]


def test_every_signal_type_is_covered_by_fixtures(index):
    covered = {
        s for i in index["items"] if i["category"] == "positive" for s in i["expected_signals"]
    }
    assert covered == {t.value for t in SignalType}


def test_snippets_are_verbatim_and_bounded(docs):
    extractor = RuleExtractor()
    for doc in docs:
        for hit in extractor.extract(doc):
            assert 0 < len(hit.evidence) <= 300
            assert hit.evidence in doc.text or hit.evidence in doc.title


def test_low_latency_suppressed_on_fpga_job():
    doc = _doc("FPGA Engineer", "Build low-latency trading logic in Verilog.", SourceType.JOB)
    assert _types(RuleExtractor().extract(doc)) == {SignalType.FPGA_HIRING}


def test_job_only_rules_do_not_fire_on_news():
    doc = _doc("Vendor ships FPGA board", "The new FPGA board supports low-latency networking.")
    assert RuleExtractor().extract(doc) == []


def test_long_sentence_is_truncated_around_hit():
    filler = "word " * 120
    text = f"{filler}the firm is building a new microwave network to New Jersey {filler}."
    doc = _doc("Update", text)
    hits = RuleExtractor(max_snippet_chars=120).extract(doc)
    assert len(hits) == 1
    assert len(hits[0].evidence) <= 120
    assert "microwave" in hits[0].evidence
    assert hits[0].evidence in doc.text


def test_make_snippet_short_sentence_unchanged():
    assert make_snippet("short one.", 3, 300) == "short one."


def test_split_sentences():
    assert split_sentences("One. Two!  Three?") == ["One.", "Two!", "Three?"]
    assert split_sentences("") == []
