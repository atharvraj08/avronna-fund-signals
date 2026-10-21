from datetime import UTC, datetime, timedelta, timezone

from fundsignals.models import RawDocument, SourceType, make_doc_id
from fundsignals.sources import dedupe
from fundsignals.sources.base import (
    document_from_dict,
    guess_source_type,
    normalize_title,
    strip_html,
    to_utc,
)

T0 = datetime(2026, 10, 1, tzinfo=UTC)


def _doc(url, title, hours=0, source="rss:a", source_type=SourceType.NEWS):
    return RawDocument(
        id=make_doc_id(url),
        source=source,
        source_type=source_type,
        url=url,
        title=title,
        text="",
        published_at=T0 + timedelta(hours=hours),
        fetched_at=T0,
    )


def test_strip_html_handles_escaped_markup_and_entities():
    assert strip_html("&lt;p&gt;Hello &amp;amp; bye&lt;/p&gt;&lt;p&gt;Next&lt;/p&gt;") == (
        "Hello & bye Next"
    )
    assert strip_html("<b>FPGA</b>  engineer\n<br>now") == "FPGA engineer now"
    assert strip_html(None) == ""


def test_to_utc_variants():
    assert to_utc("2026-10-14T13:00:00Z") == datetime(2026, 10, 14, 13, tzinfo=UTC)
    assert to_utc(datetime(2026, 1, 1)).tzinfo is UTC
    est = datetime(2026, 1, 1, 7, tzinfo=timezone(timedelta(hours=-5)))
    assert to_utc(est) == datetime(2026, 1, 1, 12, tzinfo=UTC)


def test_normalize_title():
    assert normalize_title("Two Sigma Deploys 10,000-GPU Cluster!") == normalize_title(
        "two sigma deploys 10000 GPU cluster"
    )


def test_guess_source_type():
    assert guess_source_type("https://boards.greenhouse.io/x/jobs/1") is SourceType.JOB
    assert guess_source_type("https://jobs.lever.co/x/1") is SourceType.JOB
    assert guess_source_type("https://news.example.com/a") is SourceType.NEWS


def test_dedupe_by_url_keeps_earliest():
    late = _doc("https://a.example.com/1", "Story", hours=5, source="rss:b")
    early = _doc("https://a.example.com/1", "Story", hours=1, source="rss:c")
    assert dedupe([late, early]) == [early]


def test_dedupe_by_normalized_title_keeps_earliest():
    a = _doc("https://a.example.com/1", "Two Sigma deploys 10,000-GPU cluster", hours=2)
    b = _doc("https://b.example.com/2", "TWO SIGMA DEPLOYS 10000 GPU CLUSTER.", hours=1)
    c = _doc("https://c.example.com/3", "Something else", hours=0)
    assert dedupe([a, b, c]) == [c, b]


def test_dedupe_tie_broken_by_source_name():
    syn = _doc("https://a.example.com/1", "Story", source="rss:syndication")
    orig = _doc("https://a.example.com/1", "Story", source="rss:markets_wire")
    assert dedupe([syn, orig])[0].source == "rss:markets_wire"


def test_dedupe_does_not_merge_job_posts_with_same_title():
    a = _doc("https://boards.greenhouse.io/a/jobs/1", "FPGA Engineer", source_type=SourceType.JOB)
    b = _doc("https://boards.greenhouse.io/b/jobs/2", "FPGA Engineer", source_type=SourceType.JOB)
    assert len(dedupe([a, b])) == 2


def test_document_from_dict_valid_and_invalid(caplog):
    doc = document_from_dict(
        {"url": " https://x.example.com/a ", "title": "<b>T</b>", "text": "body",
         "published_at": "2026-10-01T00:00:00+00:00", "source_type": "filing"},
        source="ext",
        fetched_at=T0,
    )
    assert doc.url == "https://x.example.com/a"
    assert doc.id == make_doc_id("https://x.example.com/a")
    assert doc.source_type is SourceType.FILING
    assert doc.title == "T"
    assert document_from_dict({"url": "u"}, source="ext", fetched_at=T0) is None
    assert document_from_dict(
        {"url": "", "title": "t", "published_at": T0}, source="ext", fetched_at=T0
    ) is None
    assert document_from_dict(
        {"url": "u", "title": "t", "published_at": "not a date"}, source="ext", fetched_at=T0
    ) is None
    assert "malformed" in caplog.text
