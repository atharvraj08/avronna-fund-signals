import json
import logging

import httpx
import pytest

from fundsignals.models import FundMatch, SignalType
from fundsignals.signals import HttpxLLMClient, LLMClassifier, extract_signals
from fundsignals.signals.llm import DEFAULT_ENDPOINT

ENV = {"LLM_API_KEY": "test-key-placeholder", "LLM_MODEL": "test-model"}


class FakeClient:
    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply(prompt) if callable(self.reply) else self.reply


def _reply(*signals):
    return json.dumps({"signals": list(signals)})


@pytest.fixture
def bridgewater(docs):
    doc = next(d for d in docs if d.title == "Bridgewater Associates names new head of people")
    match = FundMatch(
        doc_id=doc.id,
        fund_id="bridgewater",
        fund_name="Bridgewater",
        matched_text="x",
        confidence=1,
    )
    return doc, [match]


@pytest.fixture
def drw(docs):
    doc = next(d for d in docs if d.title.startswith("DRW says compute"))
    match = FundMatch(
        doc_id=doc.id, fund_id="drw", fund_name="DRW", matched_text="DRW", confidence=1
    )
    return doc, [match]


def test_llm_disabled_without_key(drw, settings):
    doc, ms = drw
    client = FakeClient(_reply())
    extract_signals([doc], ms, settings, env={}, llm_client=client)
    assert client.prompts == []


def test_llm_disabled_by_setting(drw, settings):
    doc, ms = drw
    settings["signals"]["llm"]["enabled_if_key"] = False
    client = FakeClient(_reply())
    extract_signals([doc], ms, settings, env=ENV, llm_client=client)
    assert client.prompts == []


def test_llm_adds_new_signal_type(drw, settings):
    doc, ms = drw
    evidence = "it is evaluating custom hardware"
    client = FakeClient(
        _reply({"signal_type": "gpu_buildout", "strength": 0.7, "evidence": evidence})
    )
    signals = extract_signals([doc], ms, settings, env=ENV, llm_client=client)
    assert {s.signal_type for s in signals} == {
        SignalType.LATENCY_STATEMENT,
        SignalType.GPU_BUILDOUT,
    }
    gpu = next(s for s in signals if s.signal_type is SignalType.GPU_BUILDOUT)
    assert gpu.evidence_snippet == evidence and gpu.url == doc.url
    assert doc.title in client.prompts[0]


def test_llm_merge_keeps_max_strength(drw, settings):
    doc, ms = drw
    client = FakeClient(
        _reply({"signal_type": "latency_statement", "strength": 0.95, "evidence": doc.title})
    )
    signals = extract_signals([doc], ms, settings, env=ENV, llm_client=client)
    assert len(signals) == 1
    assert signals[0].strength == 0.95 and signals[0].evidence_snippet == doc.title

    client = FakeClient(
        _reply({"signal_type": "latency_statement", "strength": 0.7, "evidence": doc.title})
    )
    signals = extract_signals([doc], ms, settings, env=ENV, llm_client=client)
    assert len(signals) == 1 and signals[0].strength == 0.85


@pytest.mark.parametrize(
    "reply",
    [
        "not json at all",
        '{"signals": [',
        json.dumps({"nope": []}),
        json.dumps({"signals": "x"}),
        json.dumps(["list"]),
        _reply("not-an-object"),
        _reply({"signal_type": "mind_reading", "strength": 0.9, "evidence": "Bridgewater"}),
        _reply({"signal_type": "gpu_buildout", "strength": 1.5, "evidence": "Bridgewater"}),
        _reply({"signal_type": "gpu_buildout", "strength": "high", "evidence": "Bridgewater"}),
        _reply({"signal_type": "gpu_buildout", "strength": True, "evidence": "Bridgewater"}),
        _reply({"signal_type": "gpu_buildout", "strength": 0.9, "evidence": "10,000 H100s"}),
        _reply({"signal_type": "gpu_buildout", "strength": 0.9, "evidence": ""}),
        _reply({"signal_type": "gpu_buildout", "strength": 0.9}),
        _reply({"signal_type": "gpu_buildout", "strength": 0.3, "evidence": "Bridgewater"}),
    ],
)
def test_bad_llm_output_is_ignored(bridgewater, settings, reply, caplog):
    doc, ms = bridgewater
    with caplog.at_level(logging.WARNING):
        signals = extract_signals([doc], ms, settings, env=ENV, llm_client=FakeClient(reply))
    assert signals == []


def test_malformed_json_logs_warning(bridgewater, settings, caplog):
    doc, ms = bridgewater
    with caplog.at_level(logging.WARNING):
        extract_signals([doc], ms, settings, env=ENV, llm_client=FakeClient("{oops"))
    assert "malformed" in caplog.text


def test_client_exception_is_swallowed(drw, settings, caplog):
    doc, ms = drw
    with caplog.at_level(logging.WARNING):
        signals = extract_signals(
            [doc], ms, settings, env=ENV, llm_client=FakeClient(RuntimeError("boom"))
        )
    assert [s.signal_type for s in signals] == [SignalType.LATENCY_STATEMENT]
    assert "LLM call failed" in caplog.text


def test_fenced_json_and_long_evidence_truncated(drw):
    doc, _ = drw
    reply = (
        "```json\n"
        + _reply({"signal_type": "latency_statement", "strength": 0.9, "evidence": doc.text})
        + "\n```"
    )
    hits = LLMClassifier(FakeClient(reply), max_snippet_chars=50).classify(doc)
    assert len(hits) == 1
    assert len(hits[0].evidence) <= 50 and hits[0].evidence in doc.text


def test_httpx_client_posts_and_parses():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"completion": '{"signals": []}'})

    client = HttpxLLMClient("k", model="m", transport=httpx.MockTransport(handler))
    assert client.complete("hello") == '{"signals": []}'
    assert seen == {
        "auth": "Bearer k",
        "body": {"model": "m", "prompt": "hello"},
        "url": DEFAULT_ENDPOINT,
    }


def test_httpx_client_http_error_does_not_crash_pipeline(drw, settings, monkeypatch):
    doc, ms = drw
    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    original = httpx.Client.__init__

    def patched(self, *args, **kwargs):
        kwargs["transport"] = transport
        original(self, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "__init__", patched)
    signals = extract_signals([doc], ms, settings, env=ENV)
    assert [s.signal_type for s in signals] == [SignalType.LATENCY_STATEMENT]
