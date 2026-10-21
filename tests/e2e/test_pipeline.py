"""End-to-end checks of the integrated pipeline in demo mode (fixtures only, no network)."""

import json
import re
import socket
import sys
from datetime import date
from pathlib import Path

import httpx
import pytest

pipeline = pytest.importorskip("fundsignals.pipeline")

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
INDEX = json.loads((FIXTURES / "fixture_index.json").read_text())
DOCUMENTS = json.loads((FIXTURES / "documents.json").read_text())
WEEK_OF = date.fromisoformat(INDEX["week_of"])
URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")
# Every URL that appears anywhere in the fixture files (feeds, job boards, documents).
FIXTURE_URLS = {
    m.rstrip(".,;")
    for f in FIXTURES.rglob("*")
    if f.is_file()
    for m in URL_RE.findall(f.read_text(errors="ignore"))
}
DOC_URLS = {d["url"] for d in DOCUMENTS}
NO_SIGNAL_DOC_IDS = {
    i["doc_id"]
    for i in INDEX["items"]
    if i["category"] in ("negative", "trap", "out_of_window")
}


def _blocked(*args, **kwargs):
    raise AssertionError("network access attempted during an e2e test")


def _block_network(mp):
    mp.setattr(httpx.Client, "send", _blocked)
    mp.setattr(httpx.AsyncClient, "send", _blocked)
    mp.setattr(socket.socket, "connect", _blocked)
    mp.setattr(socket, "create_connection", _blocked)
    mp.setattr(socket, "getaddrinfo", _blocked)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    _block_network(monkeypatch)


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:
        _block_network(mp)
        return pipeline.run_pipeline(demo=True, out_dir=tmp_path_factory.mktemp("run1"))


def test_week_of_defaults_to_demo_week(result):
    assert result.week_of == WEEK_OF
    assert result.scores, "demo run produced no ranking"
    assert {s.week_of for s in result.scores} == {WEEK_OF}


def test_expected_funds_in_top5(result):
    top5 = [s.fund_id for s in sorted(result.scores, key=lambda s: s.rank)[:5]]
    for fund_id in INDEX["expected_top5_contains"]:
        assert fund_id in top5, f"{fund_id} not in top 5: {top5}"


def test_negative_funds_absent_from_ranking(result):
    ranked = {s.fund_id for s in result.scores}
    leaked = ranked & set(INDEX["expected_absent_from_ranking"])
    assert not leaked, f"funds with no real signal were ranked: {sorted(leaked)}"


def test_ranks_are_contiguous_and_ordered(result):
    ordered = sorted(result.scores, key=lambda s: s.rank)
    assert [s.rank for s in ordered] == list(range(1, len(ordered) + 1))
    assert [s.score for s in ordered] == sorted((s.score for s in ordered), reverse=True)
    for s in ordered:
        assert s.signal_count >= len(s.top_signals) >= 1


def test_documents_are_deduplicated(result):
    ids = [d.id for d in result.documents]
    assert len(ids) == len(set(ids))


def test_duplicate_stories_not_double_counted(result):
    by_key = {i["key"]: i for i in INDEX["items"]}
    top = {s.fund_id: {sig.doc_id for sig in s.top_signals} for s in result.scores}
    for item in INDEX["items"]:
        if item["category"] != "duplicate":
            continue
        original = by_key[item["duplicate_of"]]
        if original["doc_id"] == item["doc_id"]:
            continue  # same URL: collapsed at the document level
        counted = top.get(item["expected_fund_id"], set())
        assert not {original["doc_id"], item["doc_id"]} <= counted, item["key"]


def test_no_signals_from_negative_trap_or_stale_docs(result):
    bad = [s for s in result.signals if s.doc_id in NO_SIGNAL_DOC_IDS]
    assert not bad, [(s.fund_id, s.doc_id, s.signal_type) for s in bad]
    for score in result.scores:
        assert all(s.doc_id not in NO_SIGNAL_DOC_IDS for s in score.top_signals)


def test_every_signal_traces_to_a_fixture_document(result):
    by_id = {d["id"]: d for d in DOCUMENTS}
    signals = list(result.signals) + [s for sc in result.scores for s in sc.top_signals]
    assert signals
    for s in signals:
        assert s.url in DOC_URLS, s.url
        assert s.doc_id in by_id, s.doc_id
        assert by_id[s.doc_id]["url"] == s.url
        assert s.evidence_snippet.strip()


def test_output_files_exist_and_links_trace_to_fixtures(result):
    assert result.outputs, "pipeline reported no output files"
    for name, path in result.outputs.items():
        path = Path(path)
        assert path.is_file() and path.stat().st_size > 0, f"{name}: {path}"
        links = {m.rstrip(".,;") for m in URL_RE.findall(path.read_text(errors="ignore"))}
        unknown = links - FIXTURE_URLS
        assert not unknown, f"{name} links to URLs not in fixtures: {sorted(unknown)}"
    report_links = set()
    for path in result.outputs.values():
        report_links |= set(URL_RE.findall(Path(path).read_text(errors="ignore")))
    top_urls = {s.url for s in sorted(result.scores, key=lambda s: s.rank)[0].top_signals}
    assert top_urls & report_links, "top fund's evidence URLs not linked from any output"


def _snapshot(res, out_dir):
    files = {
        name: Path(p).read_text(errors="ignore").replace(str(out_dir), "<OUT>")
        for name, p in res.outputs.items()
    }
    return (
        [s.model_dump() for s in res.scores],
        [s.model_dump() for s in res.signals],
        files,
    )


def test_deterministic_across_runs(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    first = pipeline.run_pipeline(demo=True, out_dir=a)
    second = pipeline.run_pipeline(demo=True, out_dir=b)
    assert _snapshot(first, a) == _snapshot(second, b)


def test_demo_ignores_env_and_delivers_nothing_over_network(tmp_path):
    env = {
        "SLACK_WEBHOOK_URL": "https://hooks.example.com/placeholder",
        "SMTP_HOST": "smtp.example.com",
        "REPORT_RECIPIENTS": "someone@example.com",
        "LLM_API_KEY": "placeholder",
        "NEWS_API_KEY": "placeholder",
    }
    res = pipeline.run_pipeline(demo=True, out_dir=tmp_path, env=env)
    assert res.scores
    assert isinstance(res.delivery, dict)


def test_cli_demo_smoke(tmp_path, monkeypatch, capsys):
    cli = pytest.importorskip("fundsignals.cli")
    monkeypatch.setattr(sys, "argv", ["fundsignals", "run", "--demo", "--out", str(tmp_path)])
    try:
        code = cli.main()
    except SystemExit as exc:
        code = exc.code
    assert code in (0, None)
    produced = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert produced, "CLI demo run wrote no files"
