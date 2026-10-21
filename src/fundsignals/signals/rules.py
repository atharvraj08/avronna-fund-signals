"""Layer 1: keyword/regex rules that turn a document into typed signal hits."""

from __future__ import annotations

import re
from dataclasses import dataclass

from fundsignals.models import RawDocument, SignalType, SourceType

DEFAULT_MAX_SNIPPET_CHARS = 300

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Hit:
    """A document-level signal: type, strength and a verbatim evidence quote."""

    signal_type: SignalType
    strength: float
    evidence: str


@dataclass(frozen=True)
class Rule:
    """Fires when every pattern matches within one sentence of a doc of an allowed type."""

    name: str
    signal_type: SignalType
    strength: float
    patterns: tuple[re.Pattern[str], ...]
    source_types: frozenset[SourceType]


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


JOB = frozenset({SourceType.JOB})
NEWS = frozenset({SourceType.NEWS, SourceType.FILING})
ANY = frozenset(SourceType)

_HIRING = _rx(
    r"\b(?:hiring|hires|hired|recruit\w*|builds? out|building out"
    r"|expand\w* (?:its|our) (?:\w+ ){0,3}team)\b"
)
_FPGA_TERMS = _rx(r"\b(?:FPGAs?|ASICs?|hardware acceleration)\b")
_LOW_LATENCY = _rx(r"\b(?:ultra-)?low[- ]latency\b")
_APPOINT = _rx(r"\b(?:hires|hired|appoint\w*|names|named|taps|tapped|poach\w*)\b")
_ROLE = _rx(
    r"\b(?:chief technology officer|CTO|chief hardware officer|vice president|VP"
    r"|head of (?:\w+ ){0,2}(?:hardware|engineering|technology|infrastructure))\b"
)
_HW_CONTEXT = _rx(
    r"\b(?:hardware|silicon|semiconductor|chips?|Nvidia|Intel|AMD|GPUs?|FPGAs?|ASICs?)\b"
)
_BUILD = _rx(
    r"\b(?:expand\w*|deploy\w*|build\w*|doubl\w*|add\w*|install\w*|bring\w*|brought|launch\w*"
    r"|online)\b"
)
_GPU_FLEET = _rx(
    r"\b(?:GPU (?:cluster|farm|fleet|supercomputer)s?|[\d,]+[- ]GPU\b|[\d,]+\+? (?:\w+ ){0,3}GPUs"
    r"|(?:thousands|hundreds) of (?:\w+ ){0,2}GPUs|H100s?|H200s?|B200s?)"
)
_LAUNCH = _rx(r"\b(?:launch\w*|unveil\w*|debut\w*|rolls? out|rolled out|starts?|started)\b")
_QUANT = _rx(
    r"\b(?:quant|quantitative|systematic|machine learning|ML|algorithmic"
    r"|high-frequency|AI-driven)\b"
)
_STRATEGY = _rx(r"\b(?:strateg(?:y|ies)|fund|pod|desk)\b")

RULES: tuple[Rule, ...] = (
    Rule(
        "fpga_job",
        SignalType.FPGA_HIRING,
        0.9,
        (
            _rx(
                r"\b(?:FPGAs?|ASICs?|RTL|Verilog|VHDL|SystemVerilog|HLS|Hardcaml"
                r"|hardware acceleration|custom silicon)\b"
            ),
        ),
        JOB,
    ),
    Rule("fpga_news", SignalType.FPGA_HIRING, 0.8, (_HIRING, _FPGA_TERMS), NEWS),
    Rule(
        "low_latency_job",
        SignalType.LOW_LATENCY_HIRING,
        0.7,
        (_rx(r"\b(?:(?:ultra-)?low[- ]latency|nanoseconds?|kernel bypass|lock-free)\b"),),
        JOB,
    ),
    Rule(
        "low_latency_news",
        SignalType.LOW_LATENCY_HIRING,
        0.6,
        (_HIRING, _LOW_LATENCY, _rx(r"\b(?:engineers?|developers?)\b")),
        NEWS,
    ),
    Rule(
        "hardware_leader",
        SignalType.HARDWARE_LEADERSHIP_HIRE,
        0.85,
        (_APPOINT, _ROLE, _HW_CONTEXT),
        NEWS,
    ),
    Rule("gpu_buildout", SignalType.GPU_BUILDOUT, 0.8, (_BUILD, _GPU_FLEET), ANY),
    Rule(
        "colocation_network",
        SignalType.COLOCATION_OR_NETWORK,
        0.7,
        (
            _rx(
                r"\b(?:co-?location|colo|microwave|millimeter[- ]wave|shortwave|laser link"
                r"|cross-connects?|dark fiber)\b"
            ),
        ),
        ANY,
    ),
    Rule(
        "latency_constraint",
        SignalType.LATENCY_STATEMENT,
        0.85,
        (
            _rx(
                r"\blatency\b.{0,60}\b(?:constraint|bottleneck|limit\w*)"
                r"|\bcompute (?:constraints?|bottlenecks?|limits?)\b"
                r"|\b(?:CPUs?|GPUs?|hardware)\b.{0,60}\b(?:can no longer|cannot|can't) keep up\b"
            ),
        ),
        NEWS,
    ),
    Rule(
        "new_quant_strategy",
        SignalType.NEW_QUANT_STRATEGY,
        0.5,
        (_LAUNCH, _QUANT, _STRATEGY),
        NEWS,
    ),
)

# When a doc is already an FPGA hire, its "low-latency" wording is not a separate signal.
_SUPPRESSED_BY = {SignalType.LOW_LATENCY_HIRING: SignalType.FPGA_HIRING}


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def make_snippet(sentence: str, anchor: int, max_chars: int) -> str:
    """Verbatim slice of `sentence` of at most `max_chars`, kept around `anchor`."""
    if len(sentence) <= max_chars:
        return sentence
    start = max(0, min(anchor - 40, len(sentence) - max_chars))
    return sentence[start : start + max_chars].strip()


class RuleExtractor:
    """Applies RULES to a document; one Hit per signal type (strongest rule wins)."""

    def __init__(
        self,
        rules: tuple[Rule, ...] = RULES,
        max_snippet_chars: int = DEFAULT_MAX_SNIPPET_CHARS,
    ) -> None:
        self.rules = rules
        self.max_snippet_chars = max_snippet_chars

    def extract(self, doc: RawDocument) -> list[Hit]:
        # Body sentences first (richer evidence), then the headline.
        sentences = split_sentences(doc.text) + split_sentences(doc.title)
        hits: dict[SignalType, Hit] = {}
        for rule in self.rules:
            if doc.source_type not in rule.source_types:
                continue
            hit = self._apply(rule, sentences)
            if hit and (
                hit.signal_type not in hits or hit.strength > hits[hit.signal_type].strength
            ):
                hits[hit.signal_type] = hit
        for weaker, stronger in _SUPPRESSED_BY.items():
            if stronger in hits:
                hits.pop(weaker, None)
        return list(hits.values())

    def _apply(self, rule: Rule, sentences: list[str]) -> Hit | None:
        for sentence in sentences:
            found = [p.search(sentence) for p in rule.patterns]
            if all(found):
                anchor = min(m.start() for m in found if m)
                snippet = make_snippet(sentence, anchor, self.max_snippet_chars)
                return Hit(rule.signal_type, rule.strength, snippet)
        return None
