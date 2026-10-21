"""Job boards: Greenhouse and Lever public APIs (or local JSON in demo mode), plus a
paid job-data API stub."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fundsignals.models import RawDocument, SourceType
from fundsignals.sources.base import (
    Clock,
    Source,
    document_from_dict,
    filter_since,
    make_document,
    strip_html,
)
from fundsignals.sources.http import FetchError, HttpClient

log = logging.getLogger(__name__)

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
LEVER_URL = "https://api.lever.co/v0/postings/{slug}?mode=json"
# PLACEHOLDER: replace with your licensed job-data provider's endpoint.
DEFAULT_JOBS_API_ENDPOINT = "https://jobs-api.example.com/v1/postings"


def _board_ids(boards: dict[str, str] | Iterable[str] | None) -> list[str]:
    """Settings map fund_id -> board token; plain lists are accepted too."""
    if not boards:
        return []
    values = boards.values() if isinstance(boards, dict) else boards
    return [str(v) for v in values]


class JobBoardSource(Source):
    """Greenhouse boards and Lever companies. With `jobs_dir` set, reads
    `<jobs_dir>/greenhouse_<token>.json` / `lever_<slug>.json` instead of the network."""

    name = "job_boards"

    def __init__(
        self,
        greenhouse_boards: dict[str, str] | Iterable[str] | None = None,
        lever_boards: dict[str, str] | Iterable[str] | None = None,
        client: HttpClient | None = None,
        jobs_dir: str | Path | None = None,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(clock)
        self.greenhouse_boards = _board_ids(greenhouse_boards)
        self.lever_boards = _board_ids(lever_boards)
        self.client = client
        self.jobs_dir = Path(jobs_dir) if jobs_dir else None

    def fetch(self, since: datetime) -> list[RawDocument]:
        docs: list[RawDocument] = []
        for token in self.greenhouse_boards:
            payload = self._load("greenhouse", token, GREENHOUSE_URL.format(token=token))
            docs.extend(self._parse_greenhouse(token, payload))
        for slug in self.lever_boards:
            payload = self._load("lever", slug, LEVER_URL.format(slug=slug))
            docs.extend(self._parse_lever(slug, payload))
        return filter_since(docs, since)

    def _load(self, kind: str, board: str, url: str) -> Any:
        try:
            if self.jobs_dir is not None:
                return json.loads((self.jobs_dir / f"{kind}_{board}.json").read_text())
            if self.client is None:
                raise FetchError("no HTTP client configured")
            return self.client.get(url).json()
        except (FetchError, OSError, ValueError) as exc:
            log.warning("%s:%s: could not load job board: %s", kind, board, exc)
            return None

    def _parse_greenhouse(self, token: str, payload: Any) -> list[RawDocument]:
        source = f"greenhouse:{token}"
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(jobs, list):
            if payload is not None:
                log.warning("%s: unexpected payload shape", source)
            return []
        fetched_at = self.clock()
        docs = []
        for job in jobs:
            try:
                docs.append(
                    make_document(
                        source=source,
                        source_type=SourceType.JOB,
                        url=job["absolute_url"],
                        title=job["title"],
                        text=strip_html(job.get("content")),
                        published_at=job["updated_at"],
                        fetched_at=fetched_at,
                    )
                )
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                log.warning("%s: skipping malformed job %r: %s", source, _get(job, "id"), exc)
        return docs

    def _parse_lever(self, slug: str, payload: Any) -> list[RawDocument]:
        source = f"lever:{slug}"
        if not isinstance(payload, list):
            if payload is not None:
                log.warning("%s: unexpected payload shape", source)
            return []
        fetched_at = self.clock()
        docs = []
        for posting in payload:
            try:
                text = posting.get("descriptionPlain") or strip_html(posting.get("description"))
                docs.append(
                    make_document(
                        source=source,
                        source_type=SourceType.JOB,
                        url=posting["hostedUrl"],
                        title=posting["text"],
                        text=" ".join(text.split()),
                        published_at=datetime.fromtimestamp(posting["createdAt"] / 1000, UTC),
                        fetched_at=fetched_at,
                    )
                )
            except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as exc:
                log.warning(
                    "%s: skipping malformed posting %r: %s", source, _get(posting, "id"), exc
                )
        return docs


def _get(obj: Any, key: str) -> Any:
    return obj.get(key) if isinstance(obj, dict) else None


class JobsAPISource(Source):
    """Stub for a paid job-data API behind JOBS_API_KEY.

    Expects GET <endpoint>?posted_since=<iso> with a bearer key, returning
    {"postings": [{url, title, text, published_at, company?}]}. Disabled when the key is
    missing.
    """

    name = "jobs_api"

    def __init__(
        self,
        api_key: str | None,
        client: HttpClient | None = None,
        endpoint: str = DEFAULT_JOBS_API_ENDPOINT,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(clock)
        self.api_key = api_key or ""
        self.client = client
        self.endpoint = endpoint

    def fetch(self, since: datetime) -> list[RawDocument]:
        if not self.api_key or self.client is None:
            log.warning("JOBS_API_KEY not set; paid jobs API source disabled")
            return []
        try:
            payload = self.client.get(
                self.endpoint,
                params={"posted_since": since.isoformat()},
                headers={"Authorization": f"Bearer {self.api_key}"},
            ).json()
            items = payload["postings"]
        except (FetchError, ValueError, KeyError, TypeError) as exc:
            log.warning("jobs_api: fetch failed: %s", exc)
            return []
        fetched_at = self.clock()
        docs = [
            document_from_dict(
                item, source="jobs_api", source_type=SourceType.JOB, fetched_at=fetched_at
            )
            for item in items
            if isinstance(item, dict)
        ]
        return filter_since([d for d in docs if d], since)
