import logging
from datetime import UTC, datetime

import httpx

from fundsignals.config import load_settings
from fundsignals.models import RawDocument, make_doc_id
from fundsignals.sources import (
    ExternalScraperSource,
    JobBoardSource,
    JobsAPISource,
    NewsAPISource,
    RSSSource,
    Source,
    build_sources,
    collect_documents,
    dedupe,
)

from .conftest import FETCHED_AT, SINCE


def _expected_unique_in_window(fixture_index, fixture_docs):
    drop = {(i["doc_id"], i["source"]) for i in fixture_index["items"]
            if i["category"] in ("duplicate", "out_of_window")}
    return [d for d in fixture_docs if (d["id"], d["source"]) not in drop]


def test_demo_mode_yields_exactly_the_36_unique_in_window_docs(fixture_index, fixture_docs, clock):
    since = datetime.fromisoformat(fixture_index["since"]).replace(tzinfo=UTC)
    docs = collect_documents(build_sources(load_settings(), {}, demo=True, clock=clock), since)
    expected = _expected_unique_in_window(fixture_index, fixture_docs)
    assert len(docs) == len(expected) == 36
    assert [d.id for d in docs] == [d.id for d in sorted(
        docs, key=lambda d: (d.published_at, d.id))]
    by_id = {d["id"]: RawDocument(**d) for d in expected}
    assert len(by_id) == 36
    assert {d.id: d for d in docs} == by_id


def test_demo_mode_ignores_keys_and_uses_only_local_sources():
    env = {"NEWS_API_KEY": "k", "JOBS_API_KEY": "k", "EXTERNAL_SCRAPER_MODULE": "x"}
    sources = build_sources(load_settings(), env, demo=True)
    assert [type(s) for s in sources] == [RSSSource, JobBoardSource]
    assert sources[0].client is None and sources[1].client is None
    assert len(sources[0].feeds) == 5


def test_dedupe_removes_the_three_known_fixture_duplicates(fixture_index, fixture_docs):
    docs = [RawDocument(**d) for d in fixture_docs]
    out = dedupe(docs)
    assert len(docs) - len(out) == 3
    dup_items = [i for i in fixture_index["items"] if i["category"] == "duplicate"]
    assert len(dup_items) == 3
    kept = {(d.id, d.source) for d in out}
    for item in dup_items:
        assert (item["doc_id"], item["source"]) not in kept


def test_missing_keys_disable_sources_with_warnings(caplog):
    with caplog.at_level(logging.INFO):
        sources = build_sources(load_settings(), {})
    assert [type(s) for s in sources] == [RSSSource, JobBoardSource]
    assert "NEWS_API_KEY not set" in caplog.text
    assert "JOBS_API_KEY not set" in caplog.text
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_keys_enable_optional_sources():
    settings = {"sources": {"news_api": {"endpoint": "https://n.example.com/x", "query": "q"},
                            "jobs_api": {"endpoint": "https://j.example.com/y"}}}
    env = {"NEWS_API_KEY": "a", "JOBS_API_KEY": "b", "EXTERNAL_SCRAPER_MODULE": "mod.x"}
    sources = build_sources(settings, env)
    assert [type(s) for s in sources] == [NewsAPISource, JobsAPISource, ExternalScraperSource]
    assert sources[0].endpoint == "https://n.example.com/x" and sources[0].query == "q"
    assert sources[1].endpoint == "https://j.example.com/y"


def test_live_mode_network_failures_never_crash(caplog, fake_time):
    def handler(request):
        raise httpx.TimeoutException("timed out")

    settings = load_settings()
    settings["sources"]["http"].update(max_retries=0, min_interval_seconds=0)
    settings["sources"]["rss_feeds"] = settings["sources"]["rss_feeds"][:2]
    sources = build_sources(settings, {}, transport=httpx.MockTransport(handler))
    assert collect_documents(sources, SINCE) == []
    assert "fetch failed" in caplog.text


class _Exploding(Source):
    name = "exploding"

    def fetch(self, since):
        raise RuntimeError("boom")


class _Static(Source):
    name = "static"

    def __init__(self, docs):
        super().__init__()
        self.docs = docs

    def fetch(self, since):
        return self.docs  # deliberately ignores `since`


def test_collect_documents_survives_source_errors_and_filters_since(caplog):
    def doc(url, day):
        return RawDocument(id=make_doc_id(url),
                           source="s", source_type="news", url=url, title=url, text="",
                           published_at=datetime(2026, 10, day, tzinfo=UTC), fetched_at=FETCHED_AT)

    old = RawDocument(**{**doc("https://x.example.com/old", 1).model_dump(),
                         "published_at": datetime(2026, 1, 1, tzinfo=UTC)})
    docs = [doc("https://x.example.com/b", 3), doc("https://x.example.com/a", 2), old]
    out = collect_documents([_Exploding(), _Static(docs)], datetime(2026, 7, 21))  # naive -> UTC
    assert [d.url for d in out] == ["https://x.example.com/a", "https://x.example.com/b"]
    assert "source exploding failed" in caplog.text
