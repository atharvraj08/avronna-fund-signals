from collections import Counter

from fundsignals.models import FundMatch, SignalType
from fundsignals.signals import extract_signals


def test_positive_and_duplicate_fixtures_produce_expected_signals(index, docs, matches, settings):
    signals = extract_signals(docs, matches, settings, env={})
    got = {(s.fund_id, s.doc_id, s.signal_type.value) for s in signals}
    for item in index["items"]:
        if item["category"] in ("positive", "duplicate", "out_of_window"):
            for sig in item["expected_signals"]:
                assert (item["expected_fund_id"], item["doc_id"], sig) in got, item["key"]


def test_nothing_fires_on_negative_docs(index, docs, matches, settings):
    signals = extract_signals(docs, matches, settings, env={})
    negative_ids = {i["doc_id"] for i in index["items"] if i["category"] in ("negative", "trap")}
    assert not [s for s in signals if s.doc_id in negative_ids]


def test_no_signals_without_fund_match(docs, settings):
    assert extract_signals(docs, [], settings, env={}) == []


def test_signals_only_for_matched_funds(docs, matches, settings):
    signals = extract_signals(docs, matches, settings, env={})
    matched = {(m.doc_id, m.fund_id) for m in matches}
    assert all((s.doc_id, s.fund_id) in matched for s in signals)


def test_one_signal_per_fund_doc_type_despite_duplicate_docs(docs, matches, settings):
    signals = extract_signals(docs, matches + matches, settings, env={})
    keys = Counter((s.fund_id, s.doc_id, s.signal_type) for s in signals)
    assert max(keys.values()) == 1


def test_every_signal_has_url_and_verbatim_bounded_snippet(docs, matches, settings):
    by_id = {}
    for d in docs:
        by_id.setdefault(d.id, []).append(d)
    for s in extract_signals(docs, matches, settings, env={}):
        assert s.url and s.evidence_snippet
        assert len(s.evidence_snippet) <= 300
        assert any(
            s.evidence_snippet in d.text or s.evidence_snippet in d.title for d in by_id[s.doc_id]
        )


def test_sorted_by_fund_published_type(docs, matches, settings):
    signals = extract_signals(docs, matches, settings, env={})
    keys = [(s.fund_id, s.published_at, s.signal_type.value) for s in signals]
    assert keys == sorted(keys)


def test_doc_matched_to_two_funds_signals_both(docs, settings):
    doc = next(d for d in docs if d.title.startswith("Jump Trading builds new microwave"))
    ms = [
        FundMatch(doc_id=doc.id, fund_id=f, fund_name=f, matched_text=f, confidence=0.95)
        for f in ("jump", "drw")
    ]
    signals = extract_signals([doc], ms, settings, env={})
    assert {s.fund_id for s in signals} == {"jump", "drw"}
    assert {s.signal_type for s in signals} == {SignalType.COLOCATION_OR_NETWORK}


def test_snippet_limit_from_settings(docs, matches, settings):
    settings["signals"]["max_snippet_chars"] = 40
    for s in extract_signals(docs, matches, settings, env={}):
        assert len(s.evidence_snippet) <= 40


def test_empty_settings_use_defaults(docs, matches):
    assert extract_signals(docs, matches, {}, env={})
