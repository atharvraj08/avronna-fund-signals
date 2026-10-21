import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fundsignals.config import REPO_ROOT, load_settings
from fundsignals.entities import Fund, FundMatcher, load_universe, match_documents
from fundsignals.models import RawDocument, make_doc_id

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
LABELS = json.loads((Path(__file__).parent / "labels.json").read_text())
INDEX = json.loads((FIXTURES / "fixture_index.json").read_text())
UNIVERSE_CSV = REPO_ROOT / "data" / "fund_universe.sample.csv"


@pytest.fixture(scope="module")
def universe() -> list[Fund]:
    return load_universe(UNIVERSE_CSV)


@pytest.fixture(scope="module")
def matcher(universe) -> FundMatcher:
    return FundMatcher(universe, threshold=load_settings()["entities"]["fuzzy_threshold"])


@pytest.fixture(scope="module")
def docs() -> list[RawDocument]:
    return [RawDocument(**d) for d in json.loads((FIXTURES / "documents.json").read_text())]


def make_doc(text: str, title: str = "Headline") -> RawDocument:
    url = f"https://example.com/{abs(hash((title, text)))}"
    now = datetime(2026, 10, 19, tzinfo=UTC)
    return RawDocument(
        id=make_doc_id(url),
        source="test",
        source_type="news",
        url=url,
        title=title,
        text=text,
        published_at=now,
        fetched_at=now,
    )


def fund_ids(matcher: FundMatcher, text: str, title: str = "Headline") -> list[str]:
    return [m.fund_id for m in matcher.match(make_doc(text, title))]


# --- universe -------------------------------------------------------------------------


def test_load_universe(universe):
    assert len(universe) == 25
    hrt = next(f for f in universe if f.fund_id == "hrt")
    assert hrt.name == "Hudson River Trading"
    assert hrt.aliases == ["HRT", "Hudson River Trading LLC"]
    assert hrt.website == "hudsonrivertrading.com"


def test_load_universe_skips_blank_and_rejects_duplicates(tmp_path):
    csv = tmp_path / "u.csv"
    csv.write_text("fund_id,name,aliases,website,hq\na,Alpha Fund,,,\n,,,,\nb,Beta,B1| |B2,,\n")
    funds = load_universe(csv)
    assert [f.fund_id for f in funds] == ["a", "b"]
    assert funds[0].aliases == []
    assert funds[1].aliases == ["B1", "B2"]
    csv.write_text("fund_id,name,aliases,website,hq\na,Alpha,,,\na,Alpha2,,,\n")
    with pytest.raises(ValueError, match="duplicate"):
        load_universe(csv)


def test_alias_owned_by_two_funds_rejected():
    funds = [Fund(fund_id="a", name="Alpha", aliases=["AX"]), Fund(fund_id="b", name="AX")]
    with pytest.raises(ValueError, match="maps to both"):
        FundMatcher(funds)


# --- fixture metrics ------------------------------------------------------------------


def test_labels_cover_all_documents(docs):
    assert {d.id for d in docs} == set(LABELS)


def test_precision_and_recall(docs, universe):
    matches = match_documents(docs, universe)
    predicted = {(m.doc_id, m.fund_id) for m in matches}
    expected = {(doc_id, f) for doc_id, funds in LABELS.items() for f in funds}
    tp = len(predicted & expected)
    precision = tp / len(predicted)
    recall = tp / len(expected)
    assert precision >= 0.90, sorted(predicted - expected)
    assert recall >= 0.85, sorted(expected - predicted)


def test_one_match_per_doc_and_fund(docs, matcher):
    for doc in docs:
        ids = [m.fund_id for m in matcher.match(doc)]
        assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    "key", [i["key"] for i in INDEX["items"] if i["category"] == "trap"]
)
def test_fixture_traps_rejected(key, docs, matcher):
    doc_id = next(i["doc_id"] for i in INDEX["items"] if i["key"] == key)
    doc = next(d for d in docs if d.id == doc_id)
    assert matcher.match(doc) == []


# --- exact / alias --------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("HRT is hiring.", "hrt"),
        ("Hudson River Trading LLC filed today.", "hrt"),
        ("Two Sigma Investments raised a fund.", "two_sigma"),
        ("Two Sigma Securities is a broker.", "two_sigma"),
        ("XTX made markets.", "xtx"),
        ("AQR published a paper.", "aqr"),
        ("QRT expanded in London.", "qube"),
        ("Cubist Systematic Strategies hired a PM.", "point72"),
        ("D. E. Shaw & Co. opened an office.", "deshaw"),
        ("Renaissance Technologies' fund rose.", "renaissance"),
        ("Citadel Securities LLC posted revenue.", "citadel_securities"),
    ],
)
def test_aliases_resolve_to_one_fund(matcher, text, expected):
    assert fund_ids(matcher, text) == [expected]


def test_citadel_securities_is_not_citadel(matcher):
    matches = matcher.match(make_doc("Citadel Securities said trading volumes rose."))
    assert [m.fund_id for m in matches] == ["citadel_securities"]
    assert matches[0].matched_text == "Citadel Securities"
    assert matches[0].confidence == 1.0


def test_both_citadels_in_one_doc(matcher):
    text = "Citadel LLC and Citadel Securities are separate firms."
    assert sorted(fund_ids(matcher, text)) == ["citadel", "citadel_securities"]


def test_keeps_highest_confidence_per_fund(matcher):
    matches = matcher.match(make_doc("HRT, formally Hudson River Trading, is hiring."))
    assert len(matches) == 1
    assert matches[0].confidence == 1.0
    assert matches[0].matched_text == "Hudson River Trading"


def test_short_aliases_are_case_sensitive(matcher):
    assert fund_ids(matcher, "the hrt and xtx and aqr and qrt tokens") == []
    assert fund_ids(matcher, "SHRT and XTXY are tickers") == []


def test_word_boundaries(matcher):
    assert fund_ids(matcher, "Point720 is not a fund. MAN Groupies.") == []
    assert fund_ids(matcher, "DRW's traders.") == ["drw"]


# --- ambiguous names ------------------------------------------------------------------


def test_ambiguous_name_needs_finance_context(matcher):
    assert fund_ids(matcher, "Citadel built a new office.") == []
    assert fund_ids(matcher, "Citadel, the hedge fund, hired traders.") == ["citadel"]
    assert fund_ids(matcher, "the citadel hedge fund hired traders.") == []


def test_ambiguous_confidence_below_exact(matcher):
    (m,) = matcher.match(make_doc("Virtu reported market-making revenue."))
    assert m.fund_id == "virtu" and m.confidence < 1.0
    assert FundMatcher(matcher.universe, threshold=0.95).match(
        make_doc("Virtu reported market-making revenue.")
    ) == []


def test_the_citadel_rejected_even_with_context(matcher):
    assert fund_ids(matcher, "The Citadel cadets studied trading and securities.") == []


def test_jane_street_road_rejected_even_with_context(matcher):
    assert fund_ids(matcher, "A trading firm opened on Jane Street.") == []
    assert fund_ids(matcher, "Jane Street and Bank Street, near traders' bars.") == []
    assert fund_ids(matcher, "Jane Street Capital opened a cafe.") == ["jane_street"]


def test_two_sigma_event_rejected_even_capitalized(matcher):
    assert fund_ids(matcher, "x", title="Why A Two Sigma Event Is Common In Trading") == []
    assert fund_ids(matcher, "A Two-Sigma move in futures.") == []


def test_other_traps(matcher):
    assert fund_ids(matcher, "Stocks jump as trading surges.") == []
    assert fund_ids(matcher, "Millennium Falcon toy sales jump for traders.") == []
    assert fund_ids(matcher, "Virtus Investment Partners trading results.") == []
    assert fund_ids(matcher, "Man walks into a trading floor.") == []


# --- fuzzy ----------------------------------------------------------------------------


def test_fuzzy_matches_spelling_variant(matcher):
    (m,) = matcher.match(make_doc("Renaissance Technology posted gains."))
    assert m.fund_id == "renaissance"
    assert 0.9 <= m.confidence < 1.0
    assert m.matched_text == "Renaissance Technology"


def test_fuzzy_handles_possessive(matcher):
    assert fund_ids(matcher, "Hudson River Tradings' new GPU cluster.") == ["hrt"]


def test_fuzzy_respects_threshold(universe):
    doc = make_doc("Renaissance Technology posted gains.")
    assert FundMatcher(universe, threshold=0.99).match(doc) == []


def test_no_fuzzy_on_unrelated_capitalized_text(matcher):
    assert fund_ids(matcher, "Hudson Street Partners and River Trading Co met.") == []


def test_match_documents_empty(universe):
    assert match_documents([], universe) == []
