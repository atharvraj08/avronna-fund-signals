import random
from datetime import UTC, date, datetime

import pytest

from fundsignals.config import load_settings
from fundsignals.models import Signal, SignalType
from fundsignals.scoring import reference_now, score_funds

WEEK_OF = date(2026, 10, 19)  # Monday; reference "now" = 2026-10-26 00:00 UTC
SETTINGS = {
    "scoring": {
        "half_life_days": 21,
        "max_signals_per_story": 1,
        "weights": {
            "fpga_hiring": 3.0,
            "hardware_leadership_hire": 3.0,
            "gpu_buildout": 2.5,
            "latency_statement": 2.5,
            "low_latency_hiring": 2.0,
            "colocation_or_network": 2.0,
            "new_quant_strategy": 1.0,
        },
    }
}
NAMES = {"alpha": "Alpha Capital", "beta": "Beta Partners", "gamma": "Gamma Quant"}


def sig(fund, stype, strength, when, doc="d1", snippet=None, url=None):
    return Signal(
        fund_id=fund,
        doc_id=doc,
        signal_type=stype,
        strength=strength,
        evidence_snippet=snippet or f"{fund} {stype} evidence from {doc}",
        url=url or f"https://news.example.com/{doc}",
        published_at=when,
    )


def at(y, m, d, h=0):
    return datetime(y, m, d, h, tzinfo=UTC)


def test_reference_now_is_end_of_week():
    assert reference_now(WEEK_OF) == at(2026, 10, 26)


def test_scenario_1_two_signal_types():
    # fpga_hiring: 3.0 * 1.0 * 0.5**(0/21)  = 3.0      (published at "now", age 0)
    # gpu_buildout: 2.5 * 0.8 * 0.5**(21/21) = 1.0     (2026-10-05, age 21 = one half-life)
    # total = 4.0
    out = score_funds(
        [
            sig("alpha", SignalType.FPGA_HIRING, 1.0, at(2026, 10, 26), doc="a1"),
            sig("alpha", SignalType.GPU_BUILDOUT, 0.8, at(2026, 10, 5), doc="a2"),
        ],
        NAMES,
        WEEK_OF,
        SETTINGS,
    )
    assert len(out) == 1
    row = out[0]
    assert (row.fund_id, row.fund_name) == ("alpha", "Alpha Capital")
    assert (row.rank, row.signal_count) == (1, 2)
    assert row.score == 4.0
    assert row.week_of == WEEK_OF
    assert [s.signal_type for s in row.top_signals] == [
        SignalType.FPGA_HIRING,
        SignalType.GPU_BUILDOUT,
    ]


def test_scenario_2_fractional_decay():
    # latency_statement: 2.5 * 0.5 * 0.5**(42/21) = 2.5 * 0.5 * 0.25 = 0.3125  (2026-09-14, age 42)
    # new_quant_strategy: 1.0 * 1.0 * 0.5**(7/21) = 0.5**(1/3) = 0.79370053  (2026-10-19, age 7)
    # total = 1.10620053 -> round 4 -> 1.1062
    out = score_funds(
        [
            sig("beta", SignalType.LATENCY_STATEMENT, 0.5, at(2026, 9, 14), doc="b1"),
            sig("beta", SignalType.NEW_QUANT_STRATEGY, 1.0, at(2026, 10, 19), doc="b2"),
        ],
        NAMES,
        WEEK_OF,
        SETTINGS,
    )
    assert out[0].score == 1.1062
    # higher contribution first
    assert out[0].top_signals[0].signal_type == SignalType.NEW_QUANT_STRATEGY


def test_scenario_3_duplicates_collapse():
    # colocation A (b1, age 0, 1.0):              2.0 * 1.0 * 1      = 2.0   kept
    # colocation A' (b2, near-identical, 0.9):    syndicated copy      -> dropped
    # colocation A (b1 again, same fund/doc/type) -> dropped
    # colocation B (b3, different story, age 21, 0.5): 2.0 * 0.5 * 0.5 = 0.5 kept
    # total = 2.5, signal_count = 2
    snippet = "Gamma Quant signs colocation deal at NY4 to cut latency to the exchange."
    out = score_funds(
        [
            sig("gamma", SignalType.COLOCATION_OR_NETWORK, 1.0, at(2026, 10, 26), "g1", snippet),
            sig(
                "gamma",
                SignalType.COLOCATION_OR_NETWORK,
                0.9,
                at(2026, 10, 25),
                "g2",
                "Gamma Quant signs co-location deal at NY4 to cut latency to the exchange!",
            ),
            sig("gamma", SignalType.COLOCATION_OR_NETWORK, 1.0, at(2026, 10, 26), "g1", snippet),
            sig(
                "gamma",
                SignalType.COLOCATION_OR_NETWORK,
                0.5,
                at(2026, 10, 5),
                "g3",
                "Gamma leases dark fibre microwave link between Chicago and New Jersey.",
            ),
        ],
        NAMES,
        WEEK_OF,
        SETTINGS,
    )
    assert out[0].score == 2.5
    assert out[0].signal_count == 2
    assert [s.doc_id for s in out[0].top_signals] == ["g1", "g3"]


def test_near_identical_snippets_different_type_both_count():
    snippet = "Alpha hires FPGA engineers for its low latency trading stack."
    out = score_funds(
        [
            sig("alpha", SignalType.FPGA_HIRING, 1.0, at(2026, 10, 26), "a1", snippet),
            sig("alpha", SignalType.LOW_LATENCY_HIRING, 1.0, at(2026, 10, 26), "a2", snippet),
        ],
        NAMES,
        WEEK_OF,
        SETTINGS,
    )
    assert out[0].score == 5.0  # 3.0 + 2.0
    assert out[0].signal_count == 2


def test_max_signals_per_story_allows_more_copies():
    snippet = "Alpha hires FPGA engineers for its low latency trading stack."
    signals = [
        sig("alpha", SignalType.FPGA_HIRING, 1.0, at(2026, 10, 26), f"a{i}", snippet)
        for i in range(3)
    ]
    cfg = {"scoring": {**SETTINGS["scoring"], "max_signals_per_story": 2}}
    assert score_funds(signals, NAMES, WEEK_OF, cfg)[0].score == 6.0
    assert score_funds(signals, NAMES, WEEK_OF, SETTINGS)[0].score == 3.0


def test_older_signals_score_lower():
    out = score_funds(
        [
            sig("alpha", SignalType.GPU_BUILDOUT, 1.0, at(2026, 10, 20), "a1"),
            sig("beta", SignalType.GPU_BUILDOUT, 1.0, at(2026, 8, 20), "b1"),
        ],
        NAMES,
        WEEK_OF,
        SETTINGS,
    )
    assert [r.fund_id for r in out] == ["alpha", "beta"]
    assert out[0].score > out[1].score > 0


def test_duplicates_do_not_inflate_rank():
    # beta has one real story syndicated 5x; alpha has two distinct stories.
    syndicated = "Beta Partners is building a new GPU cluster for its research team."
    signals = [
        sig("beta", SignalType.GPU_BUILDOUT, 1.0, at(2026, 10, 26), f"b{i}", syndicated)
        for i in range(5)
    ] + [
        sig("alpha", SignalType.GPU_BUILDOUT, 1.0, at(2026, 10, 26), "a1", "Alpha buys H200s."),
        sig("alpha", SignalType.LOW_LATENCY_HIRING, 0.5, at(2026, 10, 26), "a2"),
    ]
    out = score_funds(signals, NAMES, WEEK_OF, SETTINGS)
    assert [r.fund_id for r in out] == ["alpha", "beta"]
    assert out[1].score == 2.5
    assert out[1].signal_count == 1


def test_deterministic_under_shuffle():
    signals = [
        sig(f, t, s, at(2026, 10, d), doc=f"{f}{i}")
        for i, (f, t, s, d) in enumerate(
            [
                ("alpha", SignalType.FPGA_HIRING, 0.7, 1),
                ("alpha", SignalType.GPU_BUILDOUT, 0.4, 10),
                ("alpha", SignalType.LATENCY_STATEMENT, 0.9, 12),
                ("alpha", SignalType.NEW_QUANT_STRATEGY, 0.9, 12),
                ("beta", SignalType.FPGA_HIRING, 0.7, 1),
                ("beta", SignalType.COLOCATION_OR_NETWORK, 0.3, 20),
                ("gamma", SignalType.HARDWARE_LEADERSHIP_HIRE, 1.0, 3),
                ("gamma", SignalType.FPGA_HIRING, 1.0, 3),
            ]
        )
    ]
    expected = score_funds(signals, NAMES, WEEK_OF, SETTINGS)
    rng = random.Random(42)
    for _ in range(20):
        shuffled = signals[:]
        rng.shuffle(shuffled)
        assert score_funds(shuffled, NAMES, WEEK_OF, SETTINGS) == expected
    assert [r.rank for r in expected] == [1, 2, 3]
    assert all(len(r.top_signals) <= 3 for r in expected)
    assert len(expected[0].top_signals) == 3 or len(expected[1].top_signals) == 3


def test_tie_break_signal_count_then_recency_then_fund_id():
    when = at(2026, 10, 26)
    signals = [
        # "zeta": score 2.0 from two signals -> beats one-signal funds on signal_count
        sig("zeta", SignalType.NEW_QUANT_STRATEGY, 1.0, when, "z1"),
        sig("zeta", SignalType.NEW_QUANT_STRATEGY, 1.0, when, "z2", "A different story."),
        # "beta" and "alpha": 2.0 from one signal, both age 0 (same day, clamped), but
        # beta's is more recent -> beta first
        sig("beta", SignalType.LOW_LATENCY_HIRING, 1.0, at(2026, 10, 26, 23), "b"),
        sig("alpha", SignalType.LOW_LATENCY_HIRING, 1.0, at(2026, 10, 26, 1), "a"),
        # "delta" and "gamma": identical in every respect except fund_id
        sig("delta", SignalType.COLOCATION_OR_NETWORK, 1.0, when, "d"),
        sig("gamma", SignalType.COLOCATION_OR_NETWORK, 1.0, when, "g"),
    ]
    out = score_funds(signals, NAMES, WEEK_OF, SETTINGS)
    assert {r.score for r in out} == {2.0}
    assert [r.fund_id for r in out] == ["zeta", "beta", "alpha", "delta", "gamma"]
    assert [r.rank for r in out] == [1, 2, 3, 4, 5]
    assert out[3].fund_name == "delta"  # missing name falls back to fund_id


def test_zero_score_funds_excluded_and_empty_input():
    assert score_funds([], NAMES, WEEK_OF, SETTINGS) == []
    out = score_funds(
        [
            sig("alpha", SignalType.FPGA_HIRING, 0.0, at(2026, 10, 26)),
            sig("beta", SignalType.FPGA_HIRING, 1.0, at(2026, 10, 26)),
        ],
        NAMES,
        WEEK_OF,
        {"scoring": {"weights": {"fpga_hiring": 3.0}}},
    )
    assert [r.fund_id for r in out] == ["beta"]
    # unknown/unweighted type contributes 0
    out = score_funds(
        [sig("alpha", SignalType.GPU_BUILDOUT, 1.0, at(2026, 10, 26))],
        NAMES,
        WEEK_OF,
        {"scoring": {"weights": {}}},
    )
    assert out == []


def test_future_and_naive_datetimes():
    # Published after "now" -> age clamped to 0; naive datetime treated as UTC.
    out = score_funds(
        [
            sig("alpha", SignalType.FPGA_HIRING, 1.0, at(2026, 11, 30), "a1"),
            sig("beta", SignalType.FPGA_HIRING, 1.0, datetime(2026, 10, 5), "b1"),
        ],
        NAMES,
        WEEK_OF,
        SETTINGS,
    )
    assert {r.fund_id: r.score for r in out} == {"alpha": 3.0, "beta": 1.5}


def test_accepts_scoring_section_and_repo_settings():
    s = [sig("alpha", SignalType.FPGA_HIRING, 1.0, at(2026, 10, 26))]
    assert score_funds(s, NAMES, WEEK_OF, SETTINGS["scoring"])[0].score == 3.0
    assert score_funds(s, NAMES, WEEK_OF, load_settings())[0].score == 3.0


def test_invalid_half_life_raises():
    with pytest.raises(ValueError):
        score_funds([], NAMES, WEEK_OF, {"scoring": {"half_life_days": 0}})
