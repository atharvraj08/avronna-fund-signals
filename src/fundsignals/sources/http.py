"""Polite HTTP helper: robots.txt, per-host rate limit, retry with exponential backoff."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "AvronnaFundSignals/0.1"
RETRY_STATUSES = {429, 500, 502, 503, 504}


class FetchError(Exception):
    """A URL could not be fetched (after retries)."""


class RobotsDisallowed(FetchError):
    """robots.txt forbids fetching this URL."""


class HttpClient:
    """Synchronous httpx wrapper shared by all network sources.

    `transport`, `sleep` and `monotonic` are injectable so tests never touch the network
    or really sleep.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
        min_interval_seconds: float = 1.0,
        user_agent: str = DEFAULT_USER_AGENT,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_retries = max(0, int(max_retries))
        self.backoff_seconds = float(backoff_seconds)
        self.min_interval_seconds = float(min_interval_seconds)
        self.user_agent = user_agent
        self._sleep = sleep
        self._monotonic = monotonic
        self._client = httpx.Client(
            timeout=timeout_seconds,
            headers={"User-Agent": user_agent},
            transport=transport,
            follow_redirects=True,
        )
        self._robots: dict[str, RobotFileParser] = {}
        self._last_request: dict[str, float] = {}

    @classmethod
    def from_settings(cls, http: dict[str, Any] | None, **kwargs: Any) -> HttpClient:
        http = http or {}
        keys = (
            "timeout_seconds",
            "max_retries",
            "backoff_seconds",
            "min_interval_seconds",
            "user_agent",
        )
        options = {k: http[k] for k in keys if k in http}
        options.update(kwargs)
        return cls(**options)

    def close(self) -> None:
        self._client.close()

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """GET with robots check, throttling and retries. Raises FetchError on failure."""
        if not self.allowed(url):
            raise RobotsDisallowed(f"robots.txt disallows {url}")
        error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._request(url, params=params, headers=headers)
            except httpx.TransportError as exc:  # includes timeouts
                error = exc
            else:
                if response.status_code in RETRY_STATUSES:
                    error = FetchError(f"HTTP {response.status_code}")
                elif response.is_error:
                    raise FetchError(f"HTTP {response.status_code} for {url}")
                else:
                    return response
            if attempt < self.max_retries:
                delay = self.backoff_seconds * (2**attempt)
                log.info("retrying %s in %.1fs after %r", url, delay, error)
                self._sleep(delay)
        raise FetchError(f"giving up on {url} after {self.max_retries + 1} attempts: {error!r}")

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        parser = self._robots.get(origin)
        if parser is None:
            parser = self._robots[origin] = self._load_robots(origin)
        return parser.can_fetch(self.user_agent, url)

    def _load_robots(self, origin: str) -> RobotFileParser:
        parser = RobotFileParser(f"{origin}/robots.txt")
        try:
            response = self._request(f"{origin}/robots.txt")
        except httpx.HTTPError as exc:
            log.info("robots.txt unavailable for %s (%r); assuming allowed", origin, exc)
            parser.parse([])
            return parser
        if response.status_code in (401, 403):
            parser.disallow_all = True
        elif response.status_code >= 400:
            parser.allow_all = True
        else:
            parser.parse(response.text.splitlines())
        return parser

    def _request(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        self._throttle(urlsplit(url).netloc)
        return self._client.get(url, params=params, headers=headers)

    def _throttle(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            wait = self.min_interval_seconds - (self._monotonic() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_request[host] = self._monotonic()
