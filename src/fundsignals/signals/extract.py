"""Turn matched documents into typed, evidenced Signal objects."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from fundsignals.models import FundMatch, RawDocument, Signal, SignalType
from fundsignals.signals.llm import (
    DEFAULT_ENDPOINT,
    DEFAULT_MODEL,
    HttpxLLMClient,
    LLMClassifier,
    LLMClient,
)
from fundsignals.signals.rules import DEFAULT_MAX_SNIPPET_CHARS, Hit, RuleExtractor


def _build_classifier(
    cfg: dict[str, Any], env: dict[str, str], llm_client: LLMClient | None, max_chars: int
) -> LLMClassifier | None:
    llm_cfg = cfg.get("llm") or {}
    api_key = env.get("LLM_API_KEY")
    if not api_key or not llm_cfg.get("enabled_if_key", True):
        return None
    client = llm_client or HttpxLLMClient(
        api_key,
        model=env.get("LLM_MODEL") or DEFAULT_MODEL,
        endpoint=llm_cfg.get("endpoint", DEFAULT_ENDPOINT),
        timeout=float(llm_cfg.get("timeout_seconds", 20.0)),
    )
    return LLMClassifier(
        client,
        max_snippet_chars=max_chars,
        min_confidence=float(llm_cfg.get("min_confidence", 0.0)),
        max_doc_chars=int(llm_cfg.get("max_doc_chars", 4000)),
    )


def extract_signals(
    docs: list[RawDocument],
    matches: list[FundMatch],
    settings: dict,
    env: dict,
    llm_client: LLMClient | None = None,
) -> list[Signal]:
    """Rule signals (plus LLM signals when LLM_API_KEY is set) for every matched fund.

    One Signal per (fund_id, doc_id, signal_type), keeping the strongest evidence.
    Sorted by fund_id, published_at, signal_type.
    """
    cfg = settings.get("signals") or {}
    max_chars = int(cfg.get("max_snippet_chars", DEFAULT_MAX_SNIPPET_CHARS))
    rules = RuleExtractor(max_snippet_chars=max_chars)
    classifier = _build_classifier(cfg, env, llm_client, max_chars)

    funds_by_doc: dict[str, set[str]] = defaultdict(set)
    for m in matches:
        funds_by_doc[m.doc_id].add(m.fund_id)

    best: dict[tuple[str, str, SignalType], Signal] = {}
    for doc in docs:
        fund_ids = funds_by_doc.get(doc.id)
        if not fund_ids or not doc.url:
            continue
        hits: list[Hit] = rules.extract(doc)
        if classifier:
            hits += classifier.classify(doc)
        for hit in hits:
            for fund_id in fund_ids:
                key = (fund_id, doc.id, hit.signal_type)
                current = best.get(key)
                if current is None or hit.strength > current.strength:
                    best[key] = Signal(
                        fund_id=fund_id,
                        doc_id=doc.id,
                        signal_type=hit.signal_type,
                        strength=hit.strength,
                        evidence_snippet=hit.evidence,
                        url=doc.url,
                        published_at=doc.published_at,
                    )
    return sorted(best.values(), key=lambda s: (s.fund_id, s.published_at, s.signal_type.value))
