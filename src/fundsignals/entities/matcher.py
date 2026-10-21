"""Find fund mentions in documents.

Strategy, in order of confidence:
1. Exact, case-sensitive, word-boundary match on the fund name (1.0) or an alias (0.97).
2. Fuzzy match (rapidfuzz ratio) of capitalized word runs against long, multi-word names,
   for spelling variants like "Renaissance Technology".

Names that are also ordinary words or places ("Citadel", "Jane Street", "Virtu", ...) are
"ambiguous": they only count when the document has finance/trading context and the mention
is not one of the known non-fund phrasings ("The Citadel", "Jane Street and Hudson Street").
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from rapidfuzz import fuzz

from fundsignals.entities.universe import Fund
from fundsignals.models import FundMatch, RawDocument

NAME_CONFIDENCE = 1.0
ALIAS_CONFIDENCE = 0.97
AMBIGUOUS_CONFIDENCE = 0.92
FUZZY_MAX_CONFIDENCE = 0.95
FUZZY_MIN_TERM_CHARS = 10

# Terms that are common words, places or other organizations when used on their own.
AMBIGUOUS_TERMS = frozenset(
    {
        "Citadel",
        "Jump",
        "Millennium",
        "Virtu",
        "Man",
        "SIG",
        "Radix",
        "Headlands",
        "Jane Street",
        "Bridgewater",
        "Susquehanna",
        "Five Rings",
    }
)

FINANCE_CONTEXT = re.compile(
    r"\b(trading|trader|traders|hedge funds?|quant|quantitative|market[- ]mak\w*|investors?"
    r"|asset manager|securities|portfolio|exchanges?|hft|low[- ]latency|fpgas?|alpha"
    r"|options|futures|equities|systematic)\b",
    re.IGNORECASE,
)

# Non-fund phrasings, keyed by term: (pattern before the mention, pattern after it).
TRAP_CONTEXT: dict[str, tuple[re.Pattern[str] | None, re.Pattern[str] | None]] = {
    "Citadel": (re.compile(r"\bthe\s+$", re.IGNORECASE), None),
    "Jane Street": (
        re.compile(r"\b(on|at|of|off|along|near)\s+$", re.IGNORECASE),
        re.compile(r"^\s+and\s+[A-Z]\w*\s+(Street|St\.?|Avenue|Ave\.?)\b"),
    ),
    "Two Sigma": (None, re.compile(r"^[\s-]*(events?|moves?|outliers?)\b", re.IGNORECASE)),
}

_WORD_RUN = re.compile(r"[A-Z][\w.&'-]*(?:\s+(?:&\s+)?[A-Z][\w.&'-]*)*")


@dataclass(frozen=True)
class _Term:
    text: str
    fund_id: str
    fund_name: str
    confidence: float
    pattern: re.Pattern[str]

    @property
    def ambiguous(self) -> bool:
        return self.text in AMBIGUOUS_TERMS


@dataclass(frozen=True)
class _Hit:
    start: int
    end: int
    fund_id: str
    fund_name: str
    matched_text: str
    confidence: float


def _term_pattern(text: str) -> re.Pattern[str]:
    escaped = r"\s+".join(re.escape(part) for part in text.split())
    return re.compile(rf"(?<![\w-]){escaped}(?![\w-]*\w)")


def _strip_possessive(text: str) -> str:
    return re.sub(r"'s?$", "", text).rstrip(".,;:")


class FundMatcher:
    """Match RawDocuments against a fund universe."""

    def __init__(self, universe: list[Fund], threshold: float = 0.9) -> None:
        self.universe = list(universe)
        self.threshold = threshold
        self._terms: list[_Term] = []
        owner: dict[str, str] = {}
        for fund in self.universe:
            for i, text in enumerate([fund.name, *fund.aliases]):
                text = " ".join(text.split())
                if not text:
                    continue
                if owner.setdefault(text, fund.fund_id) != fund.fund_id:
                    raise ValueError(f"term {text!r} maps to both {owner[text]} and {fund.fund_id}")
                conf = NAME_CONFIDENCE if i == 0 else ALIAS_CONFIDENCE
                self._terms.append(_Term(text, fund.fund_id, fund.name, conf, _term_pattern(text)))
        # Longer terms first so "Citadel Securities" claims its span before "Citadel".
        self._terms.sort(key=lambda t: len(t.text), reverse=True)
        self._fuzzy_terms = [
            t
            for t in self._terms
            if not t.ambiguous and " " in t.text and len(t.text) >= FUZZY_MIN_TERM_CHARS
        ]

    def match(self, doc: RawDocument) -> list[FundMatch]:
        text = f"{doc.title}\n{doc.text}"
        has_context = FINANCE_CONTEXT.search(text) is not None
        hits = self._exact_hits(text, has_context)
        hits += self._fuzzy_hits(text, taken=[(h.start, h.end) for h in hits])

        best: dict[str, _Hit] = {}
        for hit in hits:
            if hit.confidence < self.threshold:
                continue
            current = best.get(hit.fund_id)
            if current is None or hit.confidence > current.confidence:
                best[hit.fund_id] = hit
        return [
            FundMatch(
                doc_id=doc.id,
                fund_id=h.fund_id,
                fund_name=h.fund_name,
                matched_text=h.matched_text,
                confidence=round(h.confidence, 4),
            )
            for h in sorted(best.values(), key=lambda h: (-h.confidence, h.start))
        ]

    def _exact_hits(self, text: str, has_context: bool) -> list[_Hit]:
        hits: list[_Hit] = []
        taken: list[tuple[int, int]] = []
        for term in self._terms:
            for m in term.pattern.finditer(text):
                start, end = m.span()
                if _overlaps(start, end, taken):
                    continue
                # Claim the span even if rejected, so "The Citadel" can't fall back elsewhere.
                taken.append((start, end))
                if _is_trap(term.text, text, start, end):
                    continue
                conf = term.confidence
                if term.ambiguous:
                    if not has_context:
                        continue
                    conf = min(conf, AMBIGUOUS_CONFIDENCE)
                hits.append(_Hit(start, end, term.fund_id, term.fund_name, m.group(0), conf))
        return hits

    def _fuzzy_hits(self, text: str, taken: list[tuple[int, int]]) -> list[_Hit]:
        hits: list[_Hit] = []
        for run in _WORD_RUN.finditer(text):
            words = [
                (run.start() + w.start(), w.group(0)) for w in re.finditer(r"\S+", run.group())
            ]
            for term in self._fuzzy_terms:
                n = len(term.text.split())
                for i in range(len(words) - n + 1):
                    start = words[i][0]
                    end = words[i + n - 1][0] + len(words[i + n - 1][1])
                    if _overlaps(start, end, taken):
                        continue
                    candidate = _strip_possessive(text[start:end])
                    score = fuzz.ratio(candidate, term.text) / 100.0
                    if score >= self.threshold:
                        conf = min(score, FUZZY_MAX_CONFIDENCE)
                        hits.append(
                            _Hit(start, end, term.fund_id, term.fund_name, candidate, conf)
                        )
        return hits


def _overlaps(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(start < e and s < end for s, e in spans)


def _is_trap(term: str, text: str, start: int, end: int) -> bool:
    before, after = TRAP_CONTEXT.get(term, (None, None))
    if before is not None and before.search(text[max(0, start - 30) : start]):
        return True
    return after is not None and after.match(text[end : end + 40]) is not None


def match_documents(
    docs: Iterable[RawDocument], universe: list[Fund], threshold: float = 0.9
) -> list[FundMatch]:
    """Match every document; at most one FundMatch per (doc, fund)."""
    matcher = FundMatcher(universe, threshold=threshold)
    return [m for doc in docs for m in matcher.match(doc)]
