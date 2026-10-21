import httpx

from fundsignals.models import RawDocument, SourceType
from fundsignals.sources import JobBoardSource, JobsAPISource
from fundsignals.sources.http import HttpClient

from .conftest import FETCHED_AT, FIXTURES, SINCE

JOBS_DIR = FIXTURES / "jobs"
GREENHOUSE = ["hudsonrivertrading", "jumptrading", "twosigma", "citadelsecurities",
              "janestreet", "bridgewater"]


def _client(handler, fake_time, **kw):
    return HttpClient(transport=httpx.MockTransport(handler), sleep=fake_time.sleep,
                      monotonic=fake_time.monotonic, min_interval_seconds=0, **kw)


def test_every_fixture_job_file_parses_exactly(fixture_docs, clock):
    source = JobBoardSource(GREENHOUSE, {"xtx": "xtxmarkets"}, jobs_dir=JOBS_DIR, clock=clock)
    docs = source.fetch(SINCE)
    expected = [RawDocument(**d) for d in fixture_docs
                if d["source"].startswith(("greenhouse:", "lever:"))]
    assert sorted(docs, key=lambda d: d.id) == sorted(expected, key=lambda d: d.id)
    assert len(docs) == 9


def test_greenhouse_html_is_stripped(clock):
    docs = JobBoardSource(["hudsonrivertrading"], jobs_dir=JOBS_DIR, clock=clock).fetch(SINCE)
    assert all("<" not in d.text and "&lt;" not in d.text for d in docs)
    assert {d.source for d in docs} == {"greenhouse:hudsonrivertrading"}
    assert {d.source_type for d in docs} == {SourceType.JOB}


def test_missing_and_malformed_board_files(tmp_path, caplog, clock):
    (tmp_path / "greenhouse_bad.json").write_text("{not json")
    (tmp_path / "greenhouse_shape.json").write_text('{"jobs": "nope"}')
    (tmp_path / "lever_shape.json").write_text('{"oops": 1}')
    (tmp_path / "greenhouse_items.json").write_text(
        '{"jobs": [{"id": 1, "title": "No url"}, {"id": 2, "title": "Ok",'
        ' "absolute_url": "https://boards.greenhouse.io/i/jobs/2",'
        ' "updated_at": "2026-10-01T00:00:00Z", "content": "&lt;p&gt;x&lt;/p&gt;"}]}'
    )
    (tmp_path / "lever_items.json").write_text(
        '[{"id": "a", "text": "No date", "hostedUrl": "https://jobs.lever.co/i/a"},'
        ' "garbage",'
        ' {"id": "b", "text": "Ok", "hostedUrl": "https://jobs.lever.co/i/b",'
        '  "createdAt": 1791622800000, "description": "<div>Hi</div>"}]'
    )
    source = JobBoardSource(["bad", "shape", "missing", "items"], ["shape", "items"],
                            jobs_dir=tmp_path, clock=clock)
    docs = source.fetch(SINCE)
    assert [(d.title, d.text) for d in docs] == [("Ok", "x"), ("Ok", "Hi")]
    assert "could not load job board" in caplog.text
    assert "unexpected payload shape" in caplog.text
    assert "skipping malformed" in caplog.text


def test_live_boards_via_http(fake_time, clock):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host == "boards-api.greenhouse.io":
            return httpx.Response(200, content=(JOBS_DIR / "greenhouse_twosigma.json").read_bytes())
        return httpx.Response(200, content=(JOBS_DIR / "lever_xtxmarkets.json").read_bytes())

    source = JobBoardSource({"two_sigma": "twosigma"}, {"xtx": "xtxmarkets"},
                            client=_client(handler, fake_time), clock=clock)
    docs = source.fetch(SINCE)
    assert len(docs) == 3
    assert "https://boards-api.greenhouse.io/v1/boards/twosigma/jobs?content=true" in seen
    assert "https://api.lever.co/v0/postings/xtxmarkets?mode=json" in seen


def test_live_board_timeout_returns_empty(fake_time, caplog):
    def handler(request):
        raise httpx.TimeoutException("timed out")

    source = JobBoardSource(["twosigma"], client=_client(handler, fake_time, max_retries=1))
    assert source.fetch(SINCE) == []
    assert "could not load job board" in caplog.text


def test_no_client_and_no_dir(caplog):
    assert JobBoardSource(["twosigma"]).fetch(SINCE) == []
    assert "no HTTP client" in caplog.text


def test_jobs_api_disabled_without_key(caplog):
    assert JobsAPISource(None).fetch(SINCE) == []
    assert "JOBS_API_KEY not set" in caplog.text


def test_jobs_api_with_key(fake_time):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200, json={"postings": [
            {"url": "https://jobs.example.com/1", "title": "FPGA Engineer",
             "text": "<p>Verilog</p>", "published_at": "2026-10-01T00:00:00Z"},
            {"url": "https://jobs.example.com/old", "title": "Old",
             "published_at": "2020-01-01T00:00:00Z"},
            {"title": "missing url"},
        ]})

    docs = JobsAPISource("test-key", client=_client(handler, fake_time),
                         clock=lambda: FETCHED_AT).fetch(SINCE)
    assert [(d.title, d.text, d.source, d.source_type) for d in docs] == [
        ("FPGA Engineer", "Verilog", "jobs_api", SourceType.JOB)
    ]


def test_jobs_api_bad_payload(fake_time, caplog):
    def handler(request):
        return httpx.Response(200, json={"unexpected": []})

    assert JobsAPISource("k", client=_client(handler, fake_time)).fetch(SINCE) == []
    assert "fetch failed" in caplog.text
