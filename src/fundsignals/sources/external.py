"""Adapter for an in-house scraper module named by EXTERNAL_SCRAPER_MODULE."""

from __future__ import annotations

import importlib
import logging
from datetime import datetime

from fundsignals.models import RawDocument, SourceType
from fundsignals.sources.base import Clock, Source, document_from_dict, filter_since

log = logging.getLogger(__name__)


class ExternalScraperSource(Source):
    """Calls `fetch` on an importable module (e.g. EXTERNAL_SCRAPER_MODULE=mycorp.scraper).

    The module must expose::

        def fetch(since: datetime) -> list[dict]

    where each dict has keys:
        url (str), title (str), text (str),
        published_at (tz-aware datetime or ISO-8601 str; naive is treated as UTC),
        source_type (optional: "news" | "job" | "filing", default "news"),
        source (optional str, default "external:<module>").

    Import errors, exceptions raised by the scraper and malformed items are logged and
    skipped; they never crash the run.
    """

    name = "external"

    def __init__(self, module_name: str, clock: Clock | None = None) -> None:
        super().__init__(clock)
        self.module_name = module_name
        self.name = f"external:{module_name}"

    def fetch(self, since: datetime) -> list[RawDocument]:
        try:
            module = importlib.import_module(self.module_name)
            items = module.fetch(since)
        except Exception:
            log.exception("%s: scraper failed", self.name)
            return []
        if not isinstance(items, list):
            log.warning("%s: expected list of dicts, got %s", self.name, type(items).__name__)
            return []
        fetched_at = self.clock()
        docs = [
            document_from_dict(
                item, source=self.name, source_type=SourceType.NEWS, fetched_at=fetched_at
            )
            for item in items
            if isinstance(item, dict)
        ]
        return filter_since([d for d in docs if d], since)
