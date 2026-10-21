"""Shared data contracts between pipeline stages.

Owned by the Orchestrator. Agents build against these models and must not change them.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SourceType(StrEnum):
    NEWS = "news"
    JOB = "job"
    FILING = "filing"


class SignalType(StrEnum):
    FPGA_HIRING = "fpga_hiring"
    LOW_LATENCY_HIRING = "low_latency_hiring"
    HARDWARE_LEADERSHIP_HIRE = "hardware_leadership_hire"
    GPU_BUILDOUT = "gpu_buildout"
    COLOCATION_OR_NETWORK = "colocation_or_network"
    LATENCY_STATEMENT = "latency_statement"
    NEW_QUANT_STRATEGY = "new_quant_strategy"


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RawDocument(_Model):
    """One fetched document (news article, job post, or filing)."""

    id: str
    source: str
    source_type: SourceType
    url: str
    title: str
    text: str
    published_at: datetime
    fetched_at: datetime


class FundMatch(_Model):
    """A document mentions a fund in the universe."""

    doc_id: str
    fund_id: str
    fund_name: str
    matched_text: str
    confidence: float = Field(ge=0.0, le=1.0)


class Signal(_Model):
    """A typed compute-bottleneck signal for a fund, backed by evidence."""

    fund_id: str
    doc_id: str
    signal_type: SignalType
    strength: float = Field(ge=0.0, le=1.0)
    evidence_snippet: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=1)
    published_at: datetime


class FundScore(_Model):
    """One row of the weekly ranking."""

    fund_id: str
    fund_name: str
    score: float = Field(ge=0.0)
    rank: int = Field(ge=1)
    signal_count: int = Field(ge=0)
    top_signals: list[Signal]
    week_of: date


def make_doc_id(url: str) -> str:
    """Deterministic document id: first 16 hex chars of sha1(url). All sources must use this."""
    import hashlib

    return hashlib.sha1(url.strip().encode("utf-8")).hexdigest()[:16]
