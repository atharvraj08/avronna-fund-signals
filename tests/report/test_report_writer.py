import csv
import json
import os
from datetime import date
from pathlib import Path

import pytest

from fundsignals.models import FundScore, Signal, SignalType
from fundsignals.report import why_line, write_report
from fundsignals.report.writer import safe_url
from tests.report.helpers import WEEK_OF, build, sig

GOLDEN = Path(__file__).parent / "golden"
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"


@pytest.fixture
def paths(tmp_path):
    scores, signals = build()
    return write_report(scores, signals, WEEK_OF, tmp_path)


def _golden(name, actual):
    path = GOLDEN / name
    if UPDATE:
        path.write_text(actual, encoding="utf-8")
    assert actual == path.read_text(encoding="utf-8")


def test_paths_and_layout(paths, tmp_path):
    assert set(paths) == {"md", "html", "csv", "json"}
    for p in paths.values():
        assert p.parent == tmp_path / "2026-10-19"
        assert p.exists()
    assert [p.name for p in paths.values()] == [
        "report.md", "report.html", "ranking.csv", "signals.json"
    ]


def test_markdown_golden(paths):
    _golden("report.md", paths["md"].read_text(encoding="utf-8"))


def test_csv_golden(paths):
    _golden("ranking.csv", paths["csv"].read_text(encoding="utf-8"))


def test_csv_columns_and_order(paths):
    with paths["csv"].open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0]) == [
        "rank", "fund_id", "fund_name", "score", "signal_count", "why", "top_source_url"
    ]
    assert [r["fund_id"] for r in rows] == ["hrt", "xtx", "jump"]
    assert rows[0]["why"] == (
        "Hiring FPGA engineers; Hiring low-latency engineers; GPU cluster buildout"
    )
    assert rows[0]["top_source_url"] == "https://boards.greenhouse.io/hrt/jobs/1"


def test_every_fund_has_source_link(paths):
    scores, _ = build()
    md = paths["md"].read_text()
    html = paths["html"].read_text()
    for fs in scores:
        assert fs.fund_name in md and fs.fund_name in html
        url = fs.top_signals[0].url
        assert f'<a href="{url}">source</a>' in html
    assert md.count("[source](") == 5
    assert "[source](https://news.example.com/xtx-%28gpu%29)" in md


def test_markdown_escapes_brackets_and_shows_top3(paths):
    md = paths["md"].read_text()
    assert r"adds \[thousands\] of GPUs" in md
    hrt_section = md.split("## 1.")[1].split("## 2.")[0]
    assert hrt_section.count("[source](") == 3
    assert "(2026-10-14)" in hrt_section and "**fpga_hiring**" in hrt_section


def test_html_escapes_snippets(tmp_path):
    s = sig("evil", SignalType.FPGA_HIRING, "javascript:alert(1)", 10,
            snippet="<script>alert('x')</script>")
    fs = FundScore(fund_id="evil", fund_name="Evil <Fund>", score=1.0, rank=1, signal_count=1,
                   top_signals=[s], week_of=WEEK_OF)
    out = write_report([fs], [s], WEEK_OF, tmp_path)
    html = out["html"].read_text()
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "Evil &lt;Fund&gt;" in html
    assert 'href="#"' in html and "javascript:" not in html


def test_signals_json_sorted_and_roundtrips(paths):
    data = json.loads(paths["json"].read_text())
    assert len(data) == 6
    keys = [(d["fund_id"], d["published_at"]) for d in data]
    assert keys == sorted(keys)
    assert [Signal.model_validate(d).fund_id for d in data][0] == "hrt"


def test_deterministic_regardless_of_input_order(tmp_path):
    scores, signals = build()
    a = write_report(scores, signals, WEEK_OF, tmp_path / "a")
    b = write_report(list(reversed(scores)), list(reversed(signals)), WEEK_OF, tmp_path / "b")
    for key in a:
        assert a[key].read_bytes() == b[key].read_bytes()


def test_empty_ranking(tmp_path):
    out = write_report([], [], date(2026, 10, 26), tmp_path)
    assert "No funds showed" in out["md"].read_text()
    assert "No funds showed" in out["html"].read_text()
    assert out["csv"].read_text().strip().count("\n") == 0
    assert json.loads(out["json"].read_text()) == []


def test_fund_without_top_signals_falls_back_to_its_signals(tmp_path):
    s1 = sig("two_sigma", SignalType.NEW_QUANT_STRATEGY, "https://n.example.com/a", 9, 0.4)
    s2 = sig("two_sigma", SignalType.COLOCATION_OR_NETWORK, "https://n.example.com/b", 9, 0.9)
    fs = FundScore(fund_id="two_sigma", fund_name="Two Sigma", score=1.0, rank=1,
                   signal_count=2, top_signals=[], week_of=WEEK_OF)
    out = write_report([fs], [s1, s2], WEEK_OF, tmp_path)
    rows = list(csv.DictReader(out["csv"].open(newline="")))
    assert rows[0]["top_source_url"] == "https://n.example.com/b"


def test_why_line():
    assert why_line([]) == "No signals"
    sigs = [
        sig("f", SignalType.NEW_QUANT_STRATEGY, "https://a.example.com", 1),
        sig("f", SignalType.GPU_BUILDOUT, "https://b.example.com", 1),
        sig("f", SignalType.GPU_BUILDOUT, "https://c.example.com", 1),
        sig("f", SignalType.FPGA_HIRING, "https://d.example.com", 1),
        sig("f", SignalType.LATENCY_STATEMENT, "https://e.example.com", 1),
    ]
    assert why_line(sigs) == (
        "GPU cluster buildout; Hiring FPGA engineers; Public statements on latency limits"
    )


@pytest.mark.parametrize(
    "url,expected",
    [("https://x.example.com", "https://x.example.com"), ("HTTP://x", "HTTP://x"),
     ("ftp://x", "#"), ("javascript:alert(1)", "#")],
)
def test_safe_url(url, expected):
    assert safe_url(url) == expected
