"""Wires the five stages together: sources -> entities -> signals -> scoring -> report."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from fundsignals.config import load_env, load_settings, resolve_path
from fundsignals.entities import load_universe, match_documents
from fundsignals.models import FundMatch, FundScore, RawDocument, Signal
from fundsignals.report import deliver, write_report
from fundsignals.scoring import score_funds
from fundsignals.signals import extract_signals
from fundsignals.sources import build_sources, collect_documents

log = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    week_of: date
    documents: list[RawDocument]
    matches: list[FundMatch]
    signals: list[Signal]
    scores: list[FundScore]
    outputs: dict[str, Path]
    delivery: dict[str, str] = field(default_factory=dict)


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def run_pipeline(
    week_of: date | None = None,
    demo: bool = False,
    out_dir: Path | None = None,
    settings: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
) -> PipelineResult:
    """Run the full weekly pipeline. Demo mode uses only bundled fixtures and no keys."""
    settings = settings if settings is not None else load_settings()
    env = {} if demo else (env if env is not None else load_env())

    if week_of is None:
        week_of = (
            date.fromisoformat(settings["demo"]["week_of"]) if demo else datetime.now(UTC).date()
        )
    week_of = monday_of(week_of)
    lookback = int(settings.get("run", {}).get("lookback_days", 90))
    since = datetime.combine(week_of, time.min, tzinfo=UTC) - timedelta(days=lookback)
    default_out = settings.get("run", {}).get("output_dir", "output")
    out_dir = Path(out_dir) if out_dir else resolve_path(default_out)

    universe_path = env.get("FUND_UNIVERSE_PATH") or settings["fund_universe_path"]
    universe = load_universe(resolve_path(universe_path))
    fund_names = {f.fund_id: f.name for f in universe}

    documents = collect_documents(build_sources(settings, env, demo=demo), since)
    log.info("collected %d documents since %s", len(documents), since.date())

    threshold = float(settings.get("entities", {}).get("fuzzy_threshold", 0.9))
    matches = match_documents(documents, universe, threshold=threshold)
    signals = extract_signals(documents, matches, settings, env)
    scores = score_funds(signals, fund_names, week_of, settings)
    top_n = int(settings.get("run", {}).get("top_n", 25))
    scores = scores[:top_n]
    listed = {s.fund_id for s in scores}
    report_signals = [s for s in signals if s.fund_id in listed]

    outputs = write_report(scores, report_signals, week_of, out_dir)
    delivery = {} if demo else deliver(outputs, scores, env)
    return PipelineResult(week_of, documents, matches, signals, scores, outputs, delivery)
