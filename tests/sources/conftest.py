import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FETCHED_AT = datetime(2026, 10, 19, 6, tzinfo=UTC)
SINCE = datetime(2026, 7, 21, tzinfo=UTC)


class FakeTime:
    """Injectable monotonic clock + sleep that records sleeps and advances time."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def fake_time():
    return FakeTime()


@pytest.fixture
def clock():
    return lambda: FETCHED_AT


@pytest.fixture
def fixture_index():
    return json.loads((FIXTURES / "fixture_index.json").read_text())


@pytest.fixture
def fixture_docs():
    return json.loads((FIXTURES / "documents.json").read_text())
