"""Build the configured sources and collect deduped documents from them."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import httpx

from fundsignals.config import resolve_path
from fundsignals.models import RawDocument
from fundsignals.sources.base import Clock, Source, dedupe, filter_since, to_utc
from fundsignals.sources.external import ExternalScraperSource
from fundsignals.sources.http import HttpClient
from fundsignals.sources.jobs import DEFAULT_JOBS_API_ENDPOINT, JobBoardSource, JobsAPISource
from fundsignals.sources.news import DEFAULT_NEWS_API_ENDPOINT, DEFAULT_NEWS_QUERY, NewsAPISource
from fundsignals.sources.rss import RSSSource

log = logging.getLogger(__name__)


def build_sources(
    settings: dict[str, Any],
    env: Mapping[str, str],
    demo: bool = False,
    *,
    clock: Clock | None = None,
    transport: httpx.BaseTransport | None = None,
) -> list[Source]:
    """Instantiate sources from settings/env.

    demo=True uses only local fixture RSS files (all *.xml in demo.rss_dir) and local job
    files (demo.jobs_dir); API keys are ignored and nothing touches the network.
    `clock` and `transport` are test hooks.
    """
    cfg = settings.get("sources") or {}
    if demo:
        demo_cfg = settings.get("demo") or {}
        rss_dir = resolve_path(demo_cfg.get("rss_dir", "tests/fixtures/rss"))
        jobs_dir = resolve_path(demo_cfg.get("jobs_dir", "tests/fixtures/jobs"))
        feeds = [str(p) for p in sorted(rss_dir.glob("*.xml"))]
        return [
            RSSSource(feeds, clock=clock),
            JobBoardSource(
                cfg.get("greenhouse_boards"),
                cfg.get("lever_boards"),
                jobs_dir=jobs_dir,
                clock=clock,
            ),
        ]

    client = HttpClient.from_settings(cfg.get("http"), transport=transport)
    sources: list[Source] = []
    if cfg.get("rss_feeds"):
        sources.append(RSSSource(cfg["rss_feeds"], client=client, clock=clock))
    if cfg.get("greenhouse_boards") or cfg.get("lever_boards"):
        sources.append(
            JobBoardSource(
                cfg.get("greenhouse_boards"), cfg.get("lever_boards"), client=client, clock=clock
            )
        )

    news_cfg = cfg.get("news_api") or {}
    if env.get("NEWS_API_KEY"):
        sources.append(
            NewsAPISource(
                env["NEWS_API_KEY"],
                client=client,
                endpoint=news_cfg.get("endpoint", DEFAULT_NEWS_API_ENDPOINT),
                query=news_cfg.get("query", DEFAULT_NEWS_QUERY),
                clock=clock,
            )
        )
    else:
        log.warning("NEWS_API_KEY not set; news API source disabled")

    jobs_cfg = cfg.get("jobs_api") or {}
    if env.get("JOBS_API_KEY"):
        sources.append(
            JobsAPISource(
                env["JOBS_API_KEY"],
                client=client,
                endpoint=jobs_cfg.get("endpoint", DEFAULT_JOBS_API_ENDPOINT),
                clock=clock,
            )
        )
    else:
        log.warning("JOBS_API_KEY not set; paid jobs API source disabled")

    if env.get("EXTERNAL_SCRAPER_MODULE"):
        sources.append(ExternalScraperSource(env["EXTERNAL_SCRAPER_MODULE"], clock=clock))
    else:
        log.info("EXTERNAL_SCRAPER_MODULE not set; external scraper disabled")
    return sources


def collect_documents(sources: list[Source], since: datetime) -> list[RawDocument]:
    """Run every source (errors are logged, never raised), drop docs before `since`,
    dedupe, and sort by (published_at, id)."""
    since = to_utc(since)
    docs: list[RawDocument] = []
    for source in sources:
        try:
            fetched = source.fetch(since)
        except Exception:
            log.exception("source %s failed; continuing", getattr(source, "name", source))
            continue
        log.info("source %s: %d documents", getattr(source, "name", source), len(fetched))
        docs.extend(fetched)
    return dedupe(filter_since(docs, since))
