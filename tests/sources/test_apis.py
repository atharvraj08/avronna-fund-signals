import sys
import types
from datetime import UTC, datetime

import httpx
import pytest

from fundsignals.models import SourceType
from fundsignals.sources import ExternalScraperSource, NewsAPISource
from fundsignals.sources.http import HttpClient

from .conftest import FETCHED_AT, SINCE


def _client(handler, fake_time):
    return HttpClient(transport=httpx.MockTransport(handler), sleep=fake_time.sleep,
                      monotonic=fake_time.monotonic, min_interval_seconds=0, max_retries=1)


def test_news_api_disabled_without_key(caplog):
    assert NewsAPISource("").fetch(SINCE) == []
    assert "NEWS_API_KEY not set" in caplog.text


def test_news_api_with_key(fake_time):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        assert request.headers["X-Api-Key"] == "test-key"
        assert request.url.params["from"] == SINCE.isoformat()
        return httpx.Response(200, json={"articles": [
            {"url": "https://n.example.com/1", "title": "HRT buys GPUs",
             "description": "Lots of GPUs", "publishedAt": "2026-10-02T10:00:00Z"},
            {"url": "https://n.example.com/2", "title": "Content only",
             "content": "Body", "publishedAt": "2026-10-03T10:00:00Z"},
            {"url": "https://n.example.com/3", "title": "No date"},
            "junk",
        ]})

    docs = NewsAPISource("test-key", client=_client(handler, fake_time),
                         clock=lambda: FETCHED_AT).fetch(SINCE)
    assert [(d.title, d.text, d.source_type) for d in docs] == [
        ("HRT buys GPUs", "Lots of GPUs", SourceType.NEWS),
        ("Content only", "Body", SourceType.NEWS),
    ]
    assert docs[0].published_at == datetime(2026, 10, 2, 10, tzinfo=UTC)


def test_news_api_timeout(fake_time, caplog):
    def handler(request):
        raise httpx.TimeoutException("slow")

    assert NewsAPISource("k", client=_client(handler, fake_time)).fetch(SINCE) == []
    assert "fetch failed" in caplog.text


@pytest.fixture
def scraper_module(monkeypatch):
    module = types.ModuleType("fake_inhouse_scraper")
    monkeypatch.setitem(sys.modules, "fake_inhouse_scraper", module)
    return module


def test_external_scraper_adapter(scraper_module):
    def fetch(since):
        return [
            {"url": "https://int.example.com/a", "title": "Filing A", "text": "t",
             "published_at": datetime(2026, 10, 1, 12), "source_type": "filing"},
            {"url": "https://int.example.com/b", "title": "B", "text": "t",
             "published_at": "2026-10-02T00:00:00+00:00", "source": "custom:feed"},
            {"url": "https://int.example.com/old", "title": "Old", "text": "t",
             "published_at": "2020-01-01T00:00:00+00:00"},
            {"title": "missing url"},
            "junk",
        ]

    scraper_module.fetch = fetch
    source = ExternalScraperSource("fake_inhouse_scraper", clock=lambda: FETCHED_AT)
    docs = source.fetch(SINCE)
    assert source.name == "external:fake_inhouse_scraper"
    assert [(d.title, d.source, d.source_type) for d in docs] == [
        ("Filing A", "external:fake_inhouse_scraper", SourceType.FILING),
        ("B", "custom:feed", SourceType.NEWS),
    ]
    assert docs[0].published_at.tzinfo is not None


def test_external_scraper_failures_do_not_crash(scraper_module, caplog):
    assert ExternalScraperSource("no_such_module_xyz").fetch(SINCE) == []

    def boom(since):
        raise RuntimeError("scraper exploded")

    scraper_module.fetch = boom
    assert ExternalScraperSource("fake_inhouse_scraper").fetch(SINCE) == []
    scraper_module.fetch = lambda since: {"not": "a list"}
    assert ExternalScraperSource("fake_inhouse_scraper").fetch(SINCE) == []
    assert "scraper failed" in caplog.text
    assert "expected list" in caplog.text
