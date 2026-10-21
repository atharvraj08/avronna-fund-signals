from datetime import UTC, datetime

import httpx

from fundsignals.models import RawDocument, SourceType
from fundsignals.sources import RSSSource
from fundsignals.sources.http import HttpClient
from fundsignals.sources.rss import feed_source_name

from .conftest import FETCHED_AT, FIXTURES, SINCE

RSS_DIR = FIXTURES / "rss"
EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


def _expected(fixture_docs, source):
    return [RawDocument(**d) for d in fixture_docs if d["source"] == source]


def test_feed_source_name():
    assert feed_source_name("https://feeds.example.com/a/feed.rss") == "rss:feeds.example.com"
    assert feed_source_name("/x/y/quant_news.xml") == "rss:quant_news"


def test_every_fixture_feed_parses_exactly(fixture_docs, clock):
    for stem in ("markets_wire", "quant_news", "tech_wire", "syndication"):
        docs = RSSSource([str(RSS_DIR / f"{stem}.xml")], clock=clock).fetch(EPOCH)
        expected = _expected(fixture_docs, f"rss:{stem}")
        assert sorted(docs, key=lambda d: d.id) == sorted(expected, key=lambda d: d.id), stem


def test_job_link_in_news_feed_is_typed_job(clock):
    docs = RSSSource([str(RSS_DIR / "syndication.xml")], clock=clock).fetch(EPOCH)
    types = {d.url: d.source_type for d in docs}
    assert types["https://boards.greenhouse.io/hudsonrivertrading/jobs/7001001"] is SourceType.JOB


def test_since_filter_drops_old_entries(clock):
    docs = RSSSource([str(RSS_DIR / "quant_news.xml")], clock=clock).fetch(SINCE)
    assert "https://quantnews.example.com/2025/03/jump-fpga" not in {d.url for d in docs}
    assert all(d.published_at >= SINCE for d in docs)
    assert all(d.published_at.tzinfo is not None for d in docs)


def test_broken_and_missing_feeds_do_not_crash(caplog, clock):
    feeds = [str(RSS_DIR / "broken_feed.xml"), str(RSS_DIR / "nope.xml"),
             str(RSS_DIR / "tech_wire.xml")]
    docs = RSSSource(feeds, clock=clock).fetch(SINCE)
    assert {d.source for d in docs} == {"rss:tech_wire"}
    assert "malformed feed" in caplog.text
    assert "fetch failed" in caplog.text


def test_url_feed_over_http(clock, fake_time):
    body = (RSS_DIR / "quant_news.xml").read_bytes()

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=body)

    client = HttpClient(transport=httpx.MockTransport(handler), sleep=fake_time.sleep,
                        monotonic=fake_time.monotonic)
    docs = RSSSource(["https://feeds.example.com/q.rss"], client=client, clock=clock).fetch(SINCE)
    assert len(docs) == 7
    assert {d.source for d in docs} == {"rss:feeds.example.com"}
    assert {d.fetched_at for d in docs} == {FETCHED_AT}


def test_url_feed_timeout_returns_empty(caplog, clock, fake_time):
    def handler(request):
        raise httpx.TimeoutException("timed out")

    client = HttpClient(transport=httpx.MockTransport(handler), sleep=fake_time.sleep,
                        monotonic=fake_time.monotonic, max_retries=1)
    assert RSSSource(["https://feeds.example.com/q.rss"], client=client).fetch(SINCE) == []
    assert "fetch failed" in caplog.text


def test_url_feed_without_client_is_skipped(caplog):
    assert RSSSource(["https://feeds.example.com/q.rss"]).fetch(SINCE) == []
    assert "no HTTP client" in caplog.text


def test_entries_missing_fields_are_skipped(tmp_path, caplog, clock):
    feed = tmp_path / "partial.xml"
    feed.write_text(
        "<?xml version='1.0'?><rss version='2.0'><channel><title>P</title>"
        "<item><title>No link</title><pubDate>Tue, 13 Oct 2026 08:00:00 +0000</pubDate></item>"
        "<item><title>No date</title><link>https://p.example.com/1</link></item>"
        "<item><title>Good &amp; fine</title><link>https://p.example.com/2</link>"
        "<pubDate>Tue, 13 Oct 2026 08:00:00 +0000</pubDate>"
        "<description>&lt;p&gt;Body &lt;b&gt;bold&lt;/b&gt;&lt;/p&gt;</description></item>"
        "</channel></rss>"
    )
    docs = RSSSource([str(feed)], clock=clock).fetch(SINCE)
    assert [(d.title, d.text, d.source) for d in docs] == [
        ("Good & fine", "Body bold", "rss:partial")
    ]
    assert "skipping entry" in caplog.text
