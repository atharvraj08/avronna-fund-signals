"""Layer 2: optional LLM classifier. Enabled only when LLM_API_KEY is set."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol

import httpx

from fundsignals.models import RawDocument, SignalType
from fundsignals.signals.rules import DEFAULT_MAX_SNIPPET_CHARS, Hit, make_snippet

log = logging.getLogger(__name__)

# PLACEHOLDER endpoint: point signals.llm.endpoint at the completion API your team uses.
DEFAULT_ENDPOINT = "https://llm.example.com/v1/complete"
DEFAULT_MODEL = "placeholder-model"

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")

PROMPT_TEMPLATE = """You classify documents about trading firms for signs that CPU/GPU speed limits
their models. Allowed signal types: {types}.
Reply with JSON only, exactly this schema:
{{"signals": [{{"signal_type": "<one allowed type>", "strength": <0..1>,
"evidence": "<verbatim quote copied from the document>"}}]}}
Return {{"signals": []}} if nothing applies. Ignore HR, marketing, art, fees, returns, podcasts.

TITLE: {title}
TEXT: {text}
"""


class LLMClient(Protocol):
    def complete(self, prompt: str) -> str: ...


class HttpxLLMClient:
    """Minimal JSON-over-HTTP completion client against a placeholder endpoint."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        self.endpoint = endpoint
        self._client = httpx.Client(
            timeout=timeout,
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    def complete(self, prompt: str) -> str:
        resp = self._client.post(self.endpoint, json={"model": self.model, "prompt": prompt})
        resp.raise_for_status()
        body = resp.json()
        return str(body.get("completion") or body.get("text") or "")


class LLMClassifier:
    """Asks the LLM for signals and keeps only well-formed, verbatim-evidenced ones."""

    def __init__(
        self,
        client: LLMClient,
        max_snippet_chars: int = DEFAULT_MAX_SNIPPET_CHARS,
        min_confidence: float = 0.0,
        max_doc_chars: int = 4000,
    ) -> None:
        self.client = client
        self.max_snippet_chars = max_snippet_chars
        self.min_confidence = min_confidence
        self.max_doc_chars = max_doc_chars

    def build_prompt(self, doc: RawDocument) -> str:
        return PROMPT_TEMPLATE.format(
            types=", ".join(t.value for t in SignalType),
            title=doc.title,
            text=doc.text[: self.max_doc_chars],
        )

    def classify(self, doc: RawDocument) -> list[Hit]:
        try:
            raw = self.client.complete(self.build_prompt(doc))
        except Exception as exc:  # network/provider failure must never stop the run
            log.warning("LLM call failed for doc %s: %s", doc.id, exc)
            return []
        try:
            payload = json.loads(_FENCE.sub("", raw.strip()))
            items = payload["signals"]
            if not isinstance(items, list):
                raise TypeError("'signals' is not a list")
        except (ValueError, KeyError, TypeError) as exc:
            log.warning("LLM returned malformed JSON for doc %s: %s", doc.id, exc)
            return []
        hits = []
        for item in items:
            hit = self._validate(doc, item)
            if hit:
                hits.append(hit)
        return hits

    def _validate(self, doc: RawDocument, item: Any) -> Hit | None:
        if not isinstance(item, dict):
            log.warning("LLM signal for doc %s is not an object: %r", doc.id, item)
            return None
        try:
            signal_type = SignalType(item.get("signal_type"))
        except ValueError:
            log.warning("LLM unknown signal_type for doc %s: %r", doc.id, item.get("signal_type"))
            return None
        strength = item.get("strength")
        if isinstance(strength, bool) or not isinstance(strength, int | float):
            log.warning("LLM strength not a number for doc %s: %r", doc.id, strength)
            return None
        if not 0.0 <= strength <= 1.0:
            log.warning("LLM strength out of range for doc %s: %r", doc.id, strength)
            return None
        if strength < self.min_confidence:
            return None
        evidence = item.get("evidence")
        evidence = evidence.strip() if isinstance(evidence, str) else ""
        source = next((s for s in (doc.text, doc.title) if evidence and evidence in s), None)
        if source is None:
            log.warning("LLM evidence not verbatim in doc %s: %r", doc.id, evidence[:80])
            return None
        return Hit(signal_type, float(strength), make_snippet(evidence, 0, self.max_snippet_chars))
