"""Weekly fund ranking: decay-weighted sum of deduplicated signals.

score(fund) = sum(weight[signal_type] * strength * 0.5 ** (age_days / half_life_days))

The reference "now" is the END of the report week: week_of (a Monday) at 00:00 UTC + 7 days.
age_days = max(0, (now - published_at).days); naive datetimes are treated as UTC.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from rapidfuzz import fuzz

from fundsignals.models import FundScore, Signal

DEFAULT_HALF_LIFE_DAYS = 21.0
DEFAULT_MAX_SIGNALS_PER_STORY = 1
NEAR_DUPLICATE_RATIO = 90.0
TOP_SIGNALS = 3
_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def reference_now(week_of: date) -> datetime:
    """End of the report week: Monday 00:00 UTC + 7 days."""
    return datetime.combine(week_of, time.min, tzinfo=UTC) + timedelta(days=7)


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _normalize(text: str) -> str:
    return _NON_ALNUM.sub(" ", text.lower()).strip()


def _scoring_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Accept either the full settings dict or just its `scoring` section."""
    section = settings.get("scoring") if settings else None
    return section if isinstance(section, dict) else (settings or {})


class _Scored:
    __slots__ = ("signal", "contribution", "published", "norm")

    def __init__(self, signal: Signal, contribution: float):
        self.signal = signal
        self.contribution = contribution
        self.published = _as_utc(signal.published_at)
        self.norm = _normalize(signal.evidence_snippet)

    def sort_key(self) -> tuple:
        s = self.signal
        return (
            -self.contribution,
            -self.published.timestamp(),
            s.fund_id,
            s.signal_type.value,
            s.doc_id,
            s.url,
            s.evidence_snippet,
            -s.strength,
        )


def _dedupe(items: list[_Scored], max_per_story: int) -> list[_Scored]:
    """Drop repeated (fund, doc, type) and cap near-identical snippets per (fund, type).

    Items are processed in contribution order, so the strongest copy is the one kept.
    """
    seen_keys: set[tuple[str, str, str]] = set()
    # (fund_id, signal_type) -> list of [representative normalized snippet, kept count]
    clusters: dict[tuple[str, str], list[list]] = defaultdict(list)
    kept: list[_Scored] = []
    for item in sorted(items, key=_Scored.sort_key):
        s = item.signal
        key = (s.fund_id, s.doc_id, s.signal_type.value)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        group = clusters[(s.fund_id, s.signal_type.value)]
        for cluster in group:
            if fuzz.ratio(item.norm, cluster[0]) >= NEAR_DUPLICATE_RATIO:
                if cluster[1] >= max_per_story:
                    break
                cluster[1] += 1
                kept.append(item)
                break
        else:
            group.append([item.norm, 1])
            kept.append(item)
    return kept


def score_funds(
    signals: list[Signal],
    fund_names: dict[str, str],
    week_of: date,
    settings: dict,
) -> list[FundScore]:
    """Rank funds by decayed, deduplicated signal strength for the week starting `week_of`.

    Funds with no positive score are excluded. Ties break on score desc, signal_count desc,
    most recent signal desc, fund_id asc. Ranks start at 1; scores are rounded to 4 decimals.
    """
    cfg = _scoring_settings(settings)
    weights: dict[str, float] = {k: float(v) for k, v in (cfg.get("weights") or {}).items()}
    half_life = float(cfg.get("half_life_days", DEFAULT_HALF_LIFE_DAYS))
    if half_life <= 0:
        raise ValueError("scoring.half_life_days must be positive")
    max_per_story = max(1, int(cfg.get("max_signals_per_story", DEFAULT_MAX_SIGNALS_PER_STORY)))
    now = reference_now(week_of)

    scored = []
    for sig in signals:
        age_days = max(0, (now - _as_utc(sig.published_at)).days)
        weight = weights.get(sig.signal_type.value, 0.0)
        scored.append(_Scored(sig, weight * sig.strength * 0.5 ** (age_days / half_life)))

    by_fund: dict[str, list[_Scored]] = defaultdict(list)
    for item in _dedupe(scored, max_per_story):
        by_fund[item.signal.fund_id].append(item)

    rows = []
    for fund_id, items in by_fund.items():
        score = round(sum(i.contribution for i in items), 4)
        if score <= 0:
            continue
        items.sort(key=_Scored.sort_key)
        latest = max(i.published for i in items)
        rows.append((score, len(items), latest, fund_id, items))

    rows.sort(key=lambda r: (-r[0], -r[1], -r[2].timestamp(), r[3]))
    return [
        FundScore(
            fund_id=fund_id,
            fund_name=fund_names.get(fund_id, fund_id),
            score=score,
            rank=rank,
            signal_count=count,
            top_signals=[i.signal for i in items[:TOP_SIGNALS]],
            week_of=week_of,
        )
        for rank, (score, count, _latest, fund_id, items) in enumerate(rows, start=1)
    ]
