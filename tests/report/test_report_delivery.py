import logging

import httpx
import pytest

from fundsignals.report import deliver, write_report
from fundsignals.report import delivery as delivery_mod
from tests.report.helpers import WEEK_OF, build

SMTP_ENV = {
    "SMTP_HOST": "smtp.example.com",
    "SMTP_PORT": "2525",
    "SMTP_USER": "bot@example.com",
    "SMTP_PASSWORD": "placeholder-password",
    "REPORT_RECIPIENTS": "a@example.com; b@example.com",
}
SLACK_ENV = {"SLACK_WEBHOOK_URL": "https://hooks.example.com/services/PLACEHOLDER"}


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.calls, self.sent = [], []
        FakeSMTP.instances.append(self)

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, msg, to_addrs=None):
        self.calls.append("send_message")
        self.sent.append((msg, to_addrs))

    def quit(self):
        self.calls.append("quit")


class BrokenSMTP(FakeSMTP):
    def send_message(self, msg, to_addrs=None):
        raise OSError("connection reset")

    def quit(self):
        raise OSError("already closed")


@pytest.fixture
def report(tmp_path):
    scores, signals = build()
    return write_report(scores, signals, WEEK_OF, tmp_path), scores


@pytest.fixture(autouse=True)
def _reset():
    FakeSMTP.instances.clear()


def _no_network(request):
    raise AssertionError(f"unexpected HTTP call to {request.url}")


def test_all_skipped_without_keys(report, caplog):
    paths, scores = report
    client = httpx.Client(transport=httpx.MockTransport(_no_network))
    with caplog.at_level(logging.INFO):
        result = deliver(paths, scores, {}, smtp_factory=FakeSMTP, http_client=client)
    assert result == {"email": "skipped", "slack": "skipped"}
    assert FakeSMTP.instances == []
    assert "email delivery skipped" in caplog.text and "slack delivery skipped" in caplog.text


def test_email_skipped_without_recipients(report):
    paths, scores = report
    env = {k: v for k, v in SMTP_ENV.items() if k != "REPORT_RECIPIENTS"}
    assert deliver(paths, scores, env, smtp_factory=FakeSMTP)["email"] == "skipped"
    env = dict(SMTP_ENV, REPORT_RECIPIENTS=" , ;")
    assert deliver(paths, scores, env, smtp_factory=FakeSMTP)["email"] == "skipped"
    assert FakeSMTP.instances == []


def test_email_sent(report):
    paths, scores = report
    result = deliver(paths, scores, SMTP_ENV, smtp_factory=FakeSMTP)
    assert result["email"] == "sent"
    (smtp,) = FakeSMTP.instances
    assert (smtp.host, smtp.port, smtp.timeout) == ("smtp.example.com", 2525, 30)
    assert smtp.calls == [
        "starttls", ("login", "bot@example.com", "placeholder-password"), "send_message", "quit"
    ]
    msg, to_addrs = smtp.sent[0]
    assert to_addrs == ["a@example.com", "b@example.com"]
    assert msg["Subject"] == "Avronna Fund Signals: week of 2026-10-19"
    assert msg["From"] == "bot@example.com"
    body = msg.get_body(preferencelist=("plain",)).get_content()
    assert "Hudson River Trading" in body and "[source](" in body
    assert "<a href=" in msg.get_body(preferencelist=("html",)).get_content()
    names = sorted(a.get_filename() for a in msg.iter_attachments())
    assert names == ["ranking.csv", "report.md"]


def test_email_without_auth_uses_default_port(report):
    paths, scores = report
    env = {"SMTP_HOST": "smtp.example.com", "REPORT_RECIPIENTS": "a@example.com"}
    assert deliver(paths, scores, env, smtp_factory=FakeSMTP)["email"] == "sent"
    (smtp,) = FakeSMTP.instances
    assert smtp.port == 587
    assert smtp.calls == ["send_message", "quit"]
    assert smtp.sent[0][0]["From"] == "fundsignals@localhost"


def test_email_failure_is_logged_not_raised(report, caplog):
    paths, scores = report
    result = deliver(paths, scores, SMTP_ENV, smtp_factory=BrokenSMTP)
    assert result["email"] == "failed"
    assert "email delivery failed: OSError" in caplog.text
    assert "placeholder-password" not in caplog.text


def test_email_factory_failure(report):
    paths, scores = report

    def boom(*a, **kw):
        raise ConnectionRefusedError

    assert deliver(paths, scores, SMTP_ENV, smtp_factory=boom)["email"] == "failed"
    bad_port = dict(SMTP_ENV, SMTP_PORT="not-a-port")
    assert deliver(paths, scores, bad_port, smtp_factory=FakeSMTP)["email"] == "failed"


def test_slack_sent(report):
    paths, scores = report
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, text="ok")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = deliver(paths, scores, SLACK_ENV, http_client=client)
    assert result == {"email": "skipped", "slack": "sent"}
    (req,) = seen
    assert req.method == "POST" and str(req.url) == SLACK_ENV["SLACK_WEBHOOK_URL"]
    text = httpx.Response(200, content=req.content).json()["text"]
    lines = text.splitlines()
    assert lines[0] == "*Avronna Fund Signals: week of 2026-10-19*"
    assert lines[1].startswith("1. Hudson River Trading (score 7.12): Hiring FPGA engineers")
    assert "<https://boards.greenhouse.io/hrt/jobs/1|source>" in lines[1]
    assert len(lines) == 4


def test_slack_top5_only(report):
    paths, scores = report
    from fundsignals.models import FundScore

    many = [
        FundScore(fund_id=f"f{i}", fund_name=f"Fund {i}", score=10 - i, rank=i, signal_count=0,
                  top_signals=[], week_of=WEEK_OF)
        for i in range(1, 9)
    ]
    text = delivery_mod.slack_summary(many, "2026-10-19")
    assert text.count("\n") == 5 and "Fund 6" not in text
    assert "No funds showed" in delivery_mod.slack_summary([], "2026-10-19")


def test_slack_failure_is_logged_not_raised(report, caplog):
    paths, scores = report
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert deliver(paths, scores, SLACK_ENV, http_client=client)["slack"] == "failed"
    assert "slack delivery failed: HTTPStatusError" in caplog.text
    assert "PLACEHOLDER" not in caplog.text


def test_slack_creates_and_closes_own_client(report, monkeypatch):
    paths, scores = report
    created = []
    real_client = httpx.Client

    def factory(**kw):
        c = real_client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
        created.append(c)
        return c

    monkeypatch.setattr(delivery_mod.httpx, "Client", factory)
    assert deliver(paths, scores, SLACK_ENV)["slack"] == "sent"
    assert created[0].is_closed


def test_week_label_from_paths_when_no_scores(tmp_path):
    from datetime import date

    paths = write_report([], [], date(2026, 10, 26), tmp_path)
    assert delivery_mod._week_label(paths, []) == "2026-10-26"
    assert delivery_mod._week_label({}, []) == "unknown week"
