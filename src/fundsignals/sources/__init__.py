"""Ingestion: pull raw documents from RSS, job boards, news APIs and external scrapers."""

from fundsignals.sources.base import Source, dedupe
from fundsignals.sources.external import ExternalScraperSource
from fundsignals.sources.http import FetchError, HttpClient, RobotsDisallowed
from fundsignals.sources.jobs import JobBoardSource, JobsAPISource
from fundsignals.sources.news import NewsAPISource
from fundsignals.sources.pipeline import build_sources, collect_documents
from fundsignals.sources.rss import RSSSource

__all__ = [
    "ExternalScraperSource",
    "FetchError",
    "HttpClient",
    "JobBoardSource",
    "JobsAPISource",
    "NewsAPISource",
    "RSSSource",
    "RobotsDisallowed",
    "Source",
    "build_sources",
    "collect_documents",
    "dedupe",
]
