"""Render the weekly ranking to report.md, report.html, ranking.csv and signals.json."""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from collections.abc import Iterable
from datetime import date
from pathlib import Path

from jinja2 import Environment, PackageLoader, StrictUndefined

from fundsignals.models import FundScore, Signal, SignalType

TOP_SIGNALS_PER_FUND = 3
MAX_WHY_PHRASES = 3
CSV_COLUMNS = ("rank", "fund_id", "fund_name", "score", "signal_count", "why", "top_source_url")

WHY_PHRASES: dict[SignalType, str] = {
    SignalType.FPGA_HIRING: "Hiring FPGA engineers",
    SignalType.LOW_LATENCY_HIRING: "Hiring low-latency engineers",
    SignalType.HARDWARE_LEADERSHIP_HIRE: "Senior hardware leadership hire",
    SignalType.GPU_BUILDOUT: "GPU cluster buildout",
    SignalType.COLOCATION_OR_NETWORK: "Colocation/network upgrades",
    SignalType.LATENCY_STATEMENT: "Public statements on latency limits",
    SignalType.NEW_QUANT_STRATEGY: "Launching new quant strategies",
}
_TYPE_ORDER = {t: i for i, t in enumerate(SignalType)}

_env = Environment(
    loader=PackageLoader("fundsignals.report", "templates"),
    autoescape=lambda name: bool(name) and name.endswith(".html.j2"),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


def why_line(signals: Iterable[Signal]) -> str:
    """One-line reason from the most frequent signal types, e.g. 'Hiring FPGA engineers; ...'."""
    counts = Counter(s.signal_type for s in signals)
    ordered = sorted(counts, key=lambda t: (-counts[t], _TYPE_ORDER[t]))
    return "; ".join(WHY_PHRASES[t] for t in ordered[:MAX_WHY_PHRASES]) or "No signals"


def safe_url(url: str) -> str:
    """Only http(s) links are rendered as clickable; anything else becomes '#'."""
    return url if re.match(r"^https?://", url.strip(), re.IGNORECASE) else "#"


def _md_url(url: str) -> str:
    return safe_url(url).replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _signal_sort_key(s: Signal) -> tuple:
    return (s.fund_id, s.published_at.isoformat(), s.signal_type.value, s.doc_id, s.url, s.strength)


def _fund_rows(scores: list[FundScore], signals: list[Signal]) -> list[dict]:
    by_fund: dict[str, list[Signal]] = {}
    for s in signals:
        by_fund.setdefault(s.fund_id, []).append(s)
    rows = []
    for fs in sorted(scores, key=lambda f: (f.rank, f.fund_id)):
        fund_signals = by_fund.get(fs.fund_id) or list(fs.top_signals)
        top = list(fs.top_signals) or sorted(
            fund_signals, key=lambda s: (-s.strength, -s.published_at.timestamp(), s.url)
        )
        top = [
            {
                "type": s.signal_type.value,
                "date": s.published_at.date().isoformat(),
                "snippet": _one_line(s.evidence_snippet),
                "md_snippet": re.sub(r"([\\\[\]])", r"\\\1", _one_line(s.evidence_snippet)),
                "url": safe_url(s.url),
                "md_url": _md_url(s.url),
            }
            for s in top[:TOP_SIGNALS_PER_FUND]
        ]
        rows.append(
            {
                "rank": fs.rank,
                "fund_id": fs.fund_id,
                "fund_name": fs.fund_name,
                "score": fs.score,
                "signal_count": fs.signal_count,
                "why": why_line(fund_signals),
                "top": top,
                "top_source_url": top[0]["url"] if top else "",
            }
        )
    return rows


def write_report(
    scores: list[FundScore], signals: list[Signal], week_of: date, out_dir: Path
) -> dict[str, Path]:
    """Write the weekly report files to <out_dir>/<week_of>/ and return their paths."""
    target = Path(out_dir) / week_of.isoformat()
    target.mkdir(parents=True, exist_ok=True)
    rows = _fund_rows(scores, signals)
    context = {"week_of": week_of.isoformat(), "funds": rows, "signal_total": len(signals)}

    paths = {
        "md": target / "report.md",
        "html": target / "report.html",
        "csv": target / "ranking.csv",
        "json": target / "signals.json",
    }
    paths["md"].write_text(_env.get_template("report.md.j2").render(context), encoding="utf-8")
    paths["html"].write_text(_env.get_template("report.html.j2").render(context), encoding="utf-8")

    with paths["csv"].open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(CSV_COLUMNS)
        for r in rows:
            writer.writerow(
                [r["rank"], r["fund_id"], r["fund_name"], f"{r['score']:.4f}",
                 r["signal_count"], r["why"], r["top_source_url"]]
            )

    dumped = [s.model_dump(mode="json") for s in sorted(signals, key=_signal_sort_key)]
    paths["json"].write_text(
        json.dumps(dumped, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return paths
