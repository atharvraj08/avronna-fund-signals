"""News API stub behind NEWS_API_KEY."""

from __future__ import annotations

import logging
from datetime import datetime

from fundsignals.models import RawDocument, SourceType
from fundsignals.sources.base import Clock, Source, document_from_dict, filter_since
from fundsignals.sources.http import FetchError, HttpClient

log = logging.getLogger(__name__)

# PLACEHOLDER: replace with your news provider's endpoint.
DEFAULT_NEWS_API_ENDPOINT = "https://newsapi.example.com/v2/everything"
DEFAULT_NEWS_QUERY = "hedge fund AND (FPGA OR GPU OR latency OR colocation)"


class NewsAPISource(Source):
    """Simple news-search API client.

    GET <endpoint>?q=<query>&from=<iso> with header X-Api-Key, expecting
    {"articles": [{url, title, description|content, publishedAt}]}. Disabled when the key
    is missing.
    """

    name = "news_api"

    def __init__(
        self,
        api_key: str | None,
        client: HttpClient | None = None,
        endpoint: str = DEFAULT_NEWS_API_ENDPOINT,
        query: str = DEFAULT_NEWS_QUERY,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(clock)
        self.api_key = api_key or ""
        self.client = client
        self.endpoint = endpoint
        self.query = query

    def fetch(self, since: datetime) -> list[RawDocument]:
        if not self.api_key or self.client is None:
            log.warning("NEWS_API_KEY not set; news API source disabled")
            return []
        try:
            payload = self.client.get(
                self.endpoint,
                params={"q": self.query, "from": since.isoformat()},
                headers={"X-Api-Key": self.api_key},
            ).json()
            articles = payload["articles"]
        except (FetchError, ValueError, KeyError, TypeError) as exc:
            log.warning("news_api: fetch failed: %s", exc)
            return []
        fetched_at = self.clock()
        docs = []
        for article in articles:
            if not isinstance(article, dict):
                continue
            item = {
                "url": article.get("url"),
                "title": article.get("title"),
                "text": article.get("description") or article.get("content") or "",
                "published_at": article.get("publishedAt"),
            }
            doc = document_from_dict(
                item, source="news_api", source_type=SourceType.NEWS, fetched_at=fetched_at
            )
            if doc:
                docs.append(doc)
        return filter_since(docs, since)
