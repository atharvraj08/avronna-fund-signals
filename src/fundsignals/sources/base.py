"""Shared pieces for all sources: the Source ABC, clock, HTML stripping, and dedupe."""

from __future__ import annotations

import html
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any

from fundsignals.models import RawDocument, SourceType, make_doc_id

log = logging.getLogger(__name__)

Clock = Callable[[], datetime]

JOB_BOARD_HOSTS = ("boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co")


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_utc(value: datetime | str) -> datetime:
    """Parse an ISO string or datetime into a tz-aware UTC datetime (naive is assumed UTC)."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise TypeError(f"expected datetime or ISO string, got {type(value).__name__}")
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class Source(ABC):
    """A pluggable document source. `fetch` must never raise for routine fetch failures."""

    name: str

    def __init__(self, clock: Clock | None = None) -> None:
        self.clock = clock or utcnow

    @abstractmethod
    def fetch(self, since: datetime) -> list[RawDocument]:
        """Return documents published at or after `since`."""


_BLOCK_TAGS = {"p", "div", "br", "li", "ul", "ol", "tr", "td", "h1", "h2", "h3", "h4", "h5", "h6"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append(" ")


def strip_html(value: str | None) -> str:
    """Plain text from (possibly entity-escaped) HTML, with whitespace collapsed."""
    if not value:
        return ""
    unescaped = html.unescape(value)  # Greenhouse double-escapes: &lt;p&gt; -> <p>
    parser = _TextExtractor()
    parser.feed(unescaped)
    parser.close()
    return " ".join("".join(parser.parts).split())


def guess_source_type(url: str, default: SourceType = SourceType.NEWS) -> SourceType:
    """Links into a job board are job posts even when they arrive via a news feed."""
    host = re.sub(r"^https?://", "", url.strip().lower()).split("/", 1)[0]
    return SourceType.JOB if host in JOB_BOARD_HOSTS else default


def make_document(
    *,
    source: str,
    source_type: SourceType | str,
    url: str,
    title: str,
    text: str,
    published_at: datetime | str,
    fetched_at: datetime,
) -> RawDocument:
    url = url.strip()
    return RawDocument(
        id=make_doc_id(url),
        source=source,
        source_type=SourceType(source_type),
        url=url,
        title=" ".join(title.split()),
        text=text,
        published_at=to_utc(published_at),
        fetched_at=to_utc(fetched_at),
    )


def document_from_dict(
    item: dict[str, Any],
    *,
    source: str,
    source_type: SourceType = SourceType.NEWS,
    fetched_at: datetime,
) -> RawDocument | None:
    """Build a RawDocument from a generic {url, title, text, published_at, ...} dict.

    Returns None (and logs) when required fields are missing or invalid.
    """
    try:
        url = str(item["url"]).strip()
        title = strip_html(str(item["title"]))
        if not url or not title:
            raise ValueError("empty url or title")
        return make_document(
            source=str(item.get("source") or source),
            source_type=item.get("source_type") or source_type,
            url=url,
            title=title,
            text=strip_html(str(item.get("text") or "")),
            published_at=item["published_at"],
            fetched_at=fetched_at,
        )
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("%s: skipping malformed item (%s): %r", source, exc, item.get("url"))
        return None


def filter_since(docs: Iterable[RawDocument], since: datetime) -> list[RawDocument]:
    since = to_utc(since)
    return [d for d in docs if d.published_at >= since]


def normalize_title(title: str) -> str:
    """Lowercase with all punctuation and whitespace removed."""
    return re.sub(r"[\W_]+", "", title.lower())


def dedupe(docs: Iterable[RawDocument]) -> list[RawDocument]:
    """Drop duplicates by URL and by normalized title, keeping the earliest published copy.

    Ties on published_at are broken by source name, then id, so results are deterministic.
    Job posts are deduped by URL only: identical job titles at different firms are distinct.
    Output is sorted by (published_at, id).
    """
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    kept: list[RawDocument] = []
    for doc in sorted(docs, key=lambda d: (d.published_at, d.source, d.id)):
        url_key = doc.url.strip()
        title_key = normalize_title(doc.title) if doc.source_type != SourceType.JOB else ""
        if url_key in seen_urls or (title_key and title_key in seen_titles):
            log.debug("dedupe: dropping %s (%s) from %s", doc.id, doc.url, doc.source)
            continue
        seen_urls.add(url_key)
        if title_key:
            seen_titles.add(title_key)
        kept.append(doc)
    kept.sort(key=lambda d: (d.published_at, d.id))
    return kept
