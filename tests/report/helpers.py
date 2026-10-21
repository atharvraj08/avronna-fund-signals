"""Deterministic FundScore/Signal fixtures for report tests."""

from datetime import UTC, date, datetime

from fundsignals.models import FundScore, Signal, SignalType, make_doc_id

WEEK_OF = date(2026, 10, 19)


def sig(fund_id, signal_type, url, day, strength=0.8, snippet="evidence"):
    return Signal(
        fund_id=fund_id,
        doc_id=make_doc_id(url),
        signal_type=signal_type,
        strength=strength,
        evidence_snippet=snippet,
        url=url,
        published_at=datetime(2026, 10, day, 12, 0, tzinfo=UTC),
    )


def build():
    hrt = [
        sig("hrt", SignalType.FPGA_HIRING, "https://boards.greenhouse.io/hrt/jobs/1", 14, 0.9,
            "Hiring an FPGA Engineer to design low-latency trading logic in Verilog."),
        sig("hrt", SignalType.FPGA_HIRING, "https://boards.greenhouse.io/hrt/jobs/2", 12, 0.85,
            "FPGA verification engineer for market data feed handlers."),
        sig("hrt", SignalType.GPU_BUILDOUT, "https://news.example.com/hrt-gpu", 10, 0.7,
            "HRT is expanding its GPU cluster for model research | 2x capacity."),
        sig("hrt", SignalType.LOW_LATENCY_HIRING, "https://boards.greenhouse.io/hrt/jobs/3", 8, 0.6,
            "C++ low-latency developer, kernel bypass networking."),
    ]
    xtx = [
        sig("xtx", SignalType.GPU_BUILDOUT, "https://news.example.com/xtx-(gpu)", 15, 0.95,
            "XTX Markets adds [thousands] of GPUs to its research cluster."),
    ]
    jump = [
        sig("jump", SignalType.LATENCY_STATEMENT, "https://news.example.com/jump", 11, 0.5,
            'CTO said "compute is the bottleneck" for new models.'),
    ]
    scores = [
        FundScore(fund_id="xtx", fund_name="XTX Markets", score=2.375, rank=2, signal_count=1,
                  top_signals=xtx, week_of=WEEK_OF),
        FundScore(fund_id="hrt", fund_name="Hudson River Trading", score=7.12345, rank=1,
                  signal_count=4, top_signals=hrt[:3], week_of=WEEK_OF),
        FundScore(fund_id="jump", fund_name="Jump Trading", score=1.25, rank=3, signal_count=1,
                  top_signals=jump, week_of=WEEK_OF),
    ]
    # Deliberately shuffled to prove output sorting is deterministic.
    signals = [jump[0], hrt[3], xtx[0], hrt[1], hrt[0], hrt[2]]
    return scores, signals
