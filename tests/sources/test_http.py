import httpx
import pytest

from fundsignals.sources.http import FetchError, HttpClient, RobotsDisallowed


def _client(handler, fake_time, **kw):
    options = dict(max_retries=3, backoff_seconds=1.0, min_interval_seconds=0.0)
    options.update(kw)
    return HttpClient(
        transport=httpx.MockTransport(handler),
        sleep=fake_time.sleep,
        monotonic=fake_time.monotonic,
        **options,
    )


def test_robots_disallow_is_honored(fake_time):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
        return httpx.Response(200, text="ok")

    client = _client(handler, fake_time)
    with pytest.raises(RobotsDisallowed):
        client.get("https://site.example.com/private/feed.rss")
    assert client.get("https://site.example.com/public/feed.rss").text == "ok"
    assert calls == ["/robots.txt", "/public/feed.rss"]  # robots cached, private never hit


@pytest.mark.parametrize("status,allowed", [(404, True), (403, False)])
def test_robots_status_codes(fake_time, status, allowed):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(status)
        return httpx.Response(200, text="ok")

    assert _client(handler, fake_time).allowed("https://s.example.com/x") is allowed


def test_robots_unreachable_assumes_allowed(fake_time):
    def handler(request):
        if request.url.path == "/robots.txt":
            raise httpx.ConnectError("down")
        return httpx.Response(200, text="ok")

    assert _client(handler, fake_time).get("https://s.example.com/x").text == "ok"


def test_retry_with_exponential_backoff_then_success(fake_time):
    attempts = {"n": 0}

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx.ReadTimeout("slow")
        if attempts["n"] == 2:
            return httpx.Response(503)
        return httpx.Response(200, text="ok")

    client = _client(handler, fake_time, backoff_seconds=0.5)
    assert client.get("https://s.example.com/feed").text == "ok"
    assert fake_time.sleeps == [0.5, 1.0]


def test_timeout_exhausts_retries(fake_time):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        raise httpx.TimeoutException("timed out")

    client = _client(handler, fake_time, max_retries=2)
    with pytest.raises(FetchError, match="3 attempts"):
        client.get("https://s.example.com/feed")
    assert fake_time.sleeps == [1.0, 2.0]


def test_client_error_is_not_retried(fake_time):
    hits = []

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        hits.append(1)
        return httpx.Response(404)

    with pytest.raises(FetchError, match="404"):
        _client(handler, fake_time).get("https://s.example.com/missing")
    assert len(hits) == 1
    assert fake_time.sleeps == []


def test_per_host_rate_limit(fake_time):
    def handler(request):
        fake_time.now += 0.25  # each request takes a quarter second
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200)

    client = _client(handler, fake_time, min_interval_seconds=2.0)
    client.get("https://a.example.com/1")  # robots then page: waits after robots
    client.get("https://a.example.com/2")
    client.get("https://b.example.com/1")  # new host: its first request is not throttled
    assert fake_time.sleeps == [pytest.approx(1.75), pytest.approx(1.75), pytest.approx(1.75)]


def test_from_settings_reads_http_keys(fake_time):
    client = HttpClient.from_settings(
        {"timeout_seconds": 3, "max_retries": 5, "backoff_seconds": 2.0,
         "min_interval_seconds": 0.5, "user_agent": "UA/1", "ignored": 1},
        sleep=fake_time.sleep,
    )
    assert client.max_retries == 5
    assert (client.backoff_seconds, client.min_interval_seconds) == (2.0, 0.5)
    assert client.user_agent == "UA/1"
    client.close()
