"""RSS/Atom feeds from URLs or local files (demo mode)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import feedparser

from fundsignals.models import RawDocument, SourceType
from fundsignals.sources.base import (
    Clock,
    Source,
    filter_since,
    guess_source_type,
    make_document,
    strip_html,
)
from fundsignals.sources.http import FetchError, HttpClient

log = logging.getLogger(__name__)


def is_url(location: str) -> bool:
    return location.startswith(("http://", "https://"))


def feed_source_name(location: str) -> str:
    """"rss:<hostname>" for URLs, "rss:<file stem>" for local files."""
    if is_url(location):
        return f"rss:{urlsplit(location).hostname}"
    return f"rss:{Path(location).stem}"


class RSSSource(Source):
    """Reads a list of feeds; each entry may be an http(s) URL or a local file path."""

    name = "rss"

    def __init__(
        self,
        feeds: list[str],
        client: HttpClient | None = None,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(clock)
        self.feeds = [str(f) for f in feeds]
        self.client = client

    def fetch(self, since: datetime) -> list[RawDocument]:
        docs: list[RawDocument] = []
        for location in self.feeds:
            docs.extend(self.fetch_feed(location, since))
        return docs

    def fetch_feed(self, location: str, since: datetime) -> list[RawDocument]:
        """One feed's in-window documents; any failure is logged and yields []."""
        source = feed_source_name(location)
        try:
            raw = self._read(location)
            parsed = feedparser.parse(raw)
        except (FetchError, OSError) as exc:
            log.warning("%s: fetch failed for %s: %s", source, location, exc)
            return []
        except Exception:  # feedparser should not raise, but never crash the run
            log.exception("%s: unexpected error parsing %s", source, location)
            return []
        if parsed.bozo:
            log.warning("%s: malformed feed %s: %s", source, location, parsed.get("bozo_exception"))
        fetched_at = self.clock()
        docs = [d for e in parsed.entries if (d := self._entry_to_doc(e, source, fetched_at))]
        return filter_since(docs, since)

    def _read(self, location: str) -> bytes:
        if is_url(location):
            if self.client is None:
                raise FetchError("no HTTP client configured for URL feeds")
            return self.client.get(location).content
        return Path(location).read_bytes()

    @staticmethod
    def _entry_to_doc(entry: Any, source: str, fetched_at: datetime) -> RawDocument | None:
        url = (entry.get("link") or "").strip()
        title = strip_html(entry.get("title"))
        stamp = entry.get("published_parsed") or entry.get("updated_parsed")
        if not url or not title or not stamp:
            log.warning("%s: skipping entry without link/title/date: %r", source, title or url)
            return None
        text = strip_html(entry.get("summary") or entry.get("description"))
        try:
            return make_document(
                source=source,
                source_type=guess_source_type(url, SourceType.NEWS),
                url=url,
                title=title,
                text=text,
                published_at=datetime(*stamp[:6], tzinfo=UTC),
                fetched_at=fetched_at,
            )
        except ValueError as exc:
            log.warning("%s: skipping invalid entry %s: %s", source, url, exc)
            return None
