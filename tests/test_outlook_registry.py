"""Conclusion registry + post-hoc verification loop for the rule-based outlook engine."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.fixtures import decimal_string
from app.models import Bar, Interval, PriceBasis, SessionType
from app.outlook import build_outlook
from app.outlook_registry import (
    UNRESOLVED_AFTER_BARS,
    OutlookRegistryEntry,
    append_entries,
    data_fingerprint,
    entry_from_report,
    load_entries,
    rank_closest_to_path_a,
    scoreboard,
    verify_entries,
    verify_entry,
)

START = datetime(2025, 1, 1, tzinfo=UTC)
INSTRUMENT = "US:TEST"


def make_bars(rows: list[tuple[float, float, float]], instrument: str = INSTRUMENT,
              start: datetime = START) -> list[Bar]:
    """rows = (high, low, close) per day; open continues from the previous close, clamped into range."""
    bars = []
    previous = rows[0][2]
    for index, (high, low, close) in enumerate(rows):
        open_at = start + timedelta(days=index)
        open_price = min(max(previous, low), high)
        bars.append(Bar(
            bar_id=f"test-{index:04d}", instrument_id=instrument, interval=Interval.DAY_1,
            open_at=open_at, close_at=open_at + timedelta(days=1),
            open=decimal_string(open_price, 6), high=decimal_string(high, 6), low=decimal_string(low, 6),
            close=decimal_string(close, 6), volume="1000", session=SessionType.US_RTH,
            price_basis=PriceBasis.RAW, confirmed=True, source_ref="src-test",
        ))
        previous = close
    return bars


def make_entry(**overrides) -> OutlookRegistryEntry:
    base = {
        "outlook_id": "outlook-test",
        "recorded_at": START,
        "rules_version": "btc-outlook-rules-v1",
        "market": "EQUITY",
        "symbol": "TEST",
        "instrument_id": INSTRUMENT,
        "as_of": START,
        "price": "100",
        "primary": "A",
        "paths": [{"key": "A", "label": "A", "score": 40.0, "weight_pct": 40},
                  {"key": "B", "label": "B", "score": 35.0, "weight_pct": 35},
                  {"key": "C", "label": "C", "score": 25.0, "weight_pct": 25}],
        "structure_score": 70.0,
        "momentum_score": 50.0,
        "levels": {"price": "100", "structural_support": "90", "confirm_reclaim": "100",
                   "confirm_breakout": "120", "dip_zone_low": "95", "dip_zone_high": "96",
                   "swing_low_20d": "94", "target_1": "130", "target_2": None, "cycle_floor": None},
        "triggers": {},
        "bars_used": 10,
        "data_fingerprint": "0" * 64,
    }
    return OutlookRegistryEntry(**{**base, **overrides})


def test_fingerprint_is_stable_and_price_sensitive() -> None:
    bars = make_bars([(101, 99, 100), (102, 100, 101)])
    assert data_fingerprint(bars) == data_fingerprint(list(bars))
    changed = make_bars([(101, 99, 100), (106, 100, 105)])
    assert data_fingerprint(bars) != data_fingerprint(changed)


def test_append_dedupes_identical_conclusions(tmp_path) -> None:
    path = tmp_path / "registry.jsonl"
    bars = make_bars([(101, 99, 100)])
    entry = make_entry(as_of=bars[-1].close_at)
    assert append_entries([entry], path) == [entry]
    assert append_entries([entry], path) == []
    assert len(load_entries(path)) == 1
    # A different frozen date is a genuinely new conclusion and must be kept.
    other = make_entry(outlook_id="outlook-test-2", as_of=bars[-1].close_at + timedelta(days=1))
    assert len(append_entries([other], path)) == 1
    assert len(load_entries(path)) == 2


def test_verification_classifies_breakdown_as_c() -> None:
    entry = make_entry()
    bars = make_bars([(101, 89, 89)], start=START + timedelta(days=1))
    verification = verify_entry(entry, bars)
    assert verification.path_realized == "C"
    assert verification.invalidate_at == bars[0].close_at
    assert verification.primary_correct is False


def test_verification_classifies_breakout_without_dip_as_b() -> None:
    entry = make_entry(primary="B")
    bars = make_bars([(125, 100, 121)], start=START + timedelta(days=1))
    verification = verify_entry(entry, bars)
    assert verification.path_realized == "B"
    assert verification.dip_zone_touched_at is None
    assert verification.primary_correct is True


def test_verification_classifies_dip_then_reclaim_as_a() -> None:
    entry = make_entry(primary="A")
    bars = make_bars([(97, 95, 96), (102, 96, 101)], start=START + timedelta(days=1))
    verification = verify_entry(entry, bars)
    assert verification.path_realized == "A"
    assert verification.dip_zone_touched_at == bars[0].close_at
    assert verification.confirm_reclaim_at == bars[1].close_at
    assert verification.primary_correct is True


def test_first_confirmation_wins_and_same_bar_prefers_c() -> None:
    """The frozen plan asks what happens next: the earliest confirmation decides, C first within a bar."""
    entry = make_entry(primary="B")
    bars = make_bars([(125, 100, 121), (110, 80, 85)], start=START + timedelta(days=1))
    assert verify_entry(entry, bars).path_realized == "B"

    # support 200 above breakout 110: one bar can satisfy both, and C must win.
    ambiguous = make_entry(levels={**entry.levels, "structural_support": "200"})
    same_bar = make_bars([(160, 140, 150)], start=START + timedelta(days=1))
    assert verify_entry(ambiguous, same_bar).path_realized == "C"


def test_verification_marks_unresolved_after_the_horizon() -> None:
    entry = make_entry()
    bars = make_bars([(101, 97, 99)] * UNRESOLVED_AFTER_BARS, start=START + timedelta(days=1))
    verification = verify_entry(entry, bars)
    assert verification.path_realized == "UNRESOLVED"
    assert verification.horizon_expired is True
    assert verification.primary_correct is None
    assert any("126" in note for note in verification.notes)


def test_verification_records_horizon_returns_and_excursions() -> None:
    entry = make_entry()
    rows = [(101, 97, 99)] * 21 + [(150, 99, 140)] + [(101, 97, 99)] * 42
    verification = verify_entry(entry, make_bars(rows, start=START + timedelta(days=1)))
    assert verification.bars_observed == 64
    assert verification.horizon_returns["21d"] == pytest.approx(99 / 100 - 1, abs=1e-9)
    assert verification.horizon_returns["63d"] == pytest.approx(99 / 100 - 1, abs=1e-9)
    assert verification.horizon_returns["126d"] is None
    assert float(verification.max_close) == 140.0
    assert float(verification.min_low) == 97.0


def test_verify_entries_reports_instruments_it_cannot_check() -> None:
    entry = make_entry()
    updated, skipped = verify_entries([entry], {})
    assert updated[0].verification is None
    assert skipped and "no bar source" in skipped[0]


def test_entry_with_no_elapsed_bars_stays_open() -> None:
    """A conclusion frozen on the latest bar has nothing to verify yet — it must not look 'verified'."""
    entry = make_entry(as_of=START + timedelta(days=10))
    bars = make_bars([(101, 97, 99)], start=START)
    updated, skipped = verify_entries([entry], {INSTRUMENT: bars})
    assert updated[0].verification is None
    assert skipped and "stays OPEN" in skipped[0]
    board = scoreboard(updated)
    assert board["verified"] == 0 and board["open"] == 1


def test_unconfirmed_bars_never_decide_a_verdict() -> None:
    """The cache holds the in-progress daily candle; it must not resolve (or break) a conclusion."""
    entry = make_entry()
    raw = make_bars([(80, 70, 75)], start=START + timedelta(days=1))  # would be a C breakdown
    unconfirmed = raw[0].model_copy(update={"confirmed": False})
    verification = verify_entry(entry, [unconfirmed])
    assert verification.bars_observed == 0
    assert verification.path_realized == "UNRESOLVED"
    assert verification.primary_correct is None
    updated, skipped = verify_entries([entry], {INSTRUMENT: [unconfirmed]})
    assert updated[0].verification is None and skipped


def test_verification_is_never_downgraded_by_a_shorter_bar_set() -> None:
    entry = make_entry()
    long_bars = make_bars([(125, 100, 121)], start=START + timedelta(days=1))
    first, _ = verify_entries([entry], {INSTRUMENT: long_bars})
    assert first[0].verification is not None
    again, _ = verify_entries(first, {INSTRUMENT: []})
    assert again[0].verification is not None
    assert again[0].verification.bars_observed == 1


def test_scoreboard_reports_counts_without_a_hit_rate() -> None:
    entry = make_entry()
    bars = make_bars([(125, 100, 121)], start=START + timedelta(days=1))
    verified, _ = verify_entries([entry], {INSTRUMENT: bars})
    board = scoreboard(verified + [make_entry(outlook_id="outlook-open", as_of=START - timedelta(days=1))])
    assert board["entries"] == 2 and board["verified"] == 1 and board["open"] == 1
    assert board["realized_counts"]["B"] == 1
    assert board["primary_incorrect"] == 1
    assert any("不是胜率" in item for item in board["caveats"])
    assert "hit_rate" not in board


def test_rank_closest_to_path_a_orders_by_a_weight_then_structure() -> None:
    strongest = make_entry(outlook_id="a", symbol="AAA",
                           paths=[{"key": "A", "label": "A", "score": 50.0, "weight_pct": 50},
                                  {"key": "B", "label": "B", "score": 30.0, "weight_pct": 30},
                                  {"key": "C", "label": "C", "score": 20.0, "weight_pct": 20}],
                           structure_score=60.0)
    same_weight_better_structure = make_entry(outlook_id="b", symbol="BBB", structure_score=90.0,
                                              paths=strongest.paths)
    weakest = make_entry(outlook_id="c", symbol="CCC",
                         paths=[{"key": "A", "label": "A", "score": 20.0, "weight_pct": 20},
                                {"key": "B", "label": "B", "score": 40.0, "weight_pct": 40},
                                {"key": "C", "label": "C", "score": 40.0, "weight_pct": 40}],
                         structure_score=99.0)
    ranking = rank_closest_to_path_a([weakest, strongest, same_weight_better_structure])
    assert [row["symbol"] for row in ranking] == ["BBB", "AAA", "CCC"]


def test_entry_from_report_round_trips_through_the_registry(tmp_path) -> None:
    from tests.test_outlook import make_bars as crypto_bars, structure_above_200d_then_weak

    bars = crypto_bars(structure_above_200d_then_weak())
    report = build_outlook(bars)
    entry = entry_from_report(report, bars)
    path = tmp_path / "registry.jsonl"
    append_entries([entry], path)
    loaded = load_entries(path)[0]
    assert loaded.outlook_id == entry.outlook_id
    assert loaded.origin == "FORWARD"          # the default: frozen before the outcome existed
    assert loaded.data_fingerprint == data_fingerprint(bars)
    assert loaded.levels == report.levels.model_dump()
    assert loaded.paths[0]["key"] == report.paths[0].key
    assert loaded.verification is None

    replay = entry_from_report(report, bars, origin="REPLAY")
    assert replay.origin == "REPLAY"


def test_scoreboard_never_mixes_forward_and_replay_samples() -> None:
    """Replaying history can inflate a count fast; the scoreboard must not merge the two origins."""
    entry = make_entry(primary="B")
    bars = make_bars([(125, 100, 121)], start=START + timedelta(days=1))
    verified, _ = verify_entries([entry], {INSTRUMENT: bars})
    forward = verified[0].model_copy(update={"origin": "FORWARD"})
    replay_same_date = verified[0].model_copy(update={"outlook_id": "replay-1", "origin": "REPLAY",
                                                      "symbol": "OTHER"})
    replay_other_date = verified[0].model_copy(update={"outlook_id": "replay-2", "origin": "REPLAY",
                                                       "symbol": "THIRD",
                                                       "as_of": START + timedelta(days=30)})

    board = scoreboard([forward, replay_same_date, replay_other_date])

    assert board["by_origin"]["FORWARD"]["entries"] == 1
    assert board["by_origin"]["REPLAY"]["entries"] == 2
    assert board["by_origin"]["FORWARD"]["primary_correct"] == 1
    assert board["by_origin"]["REPLAY"]["primary_correct"] == 2
    # Two replay rows on two distinct freeze dates: that is the only honest independence signal we have.
    assert board["by_origin"]["REPLAY"]["distinct_freeze_dates"] == 2
    assert any("样本内" in item for item in board["caveats"])
    assert any("distinct_freeze_dates" in item for item in board["caveats"])


def test_equity_market_disables_the_four_year_cycle_rules() -> None:
    from tests.test_outlook import make_bars as crypto_bars, structure_above_200d_then_weak

    bars = crypto_bars(structure_above_200d_then_weak(), instrument="US:AAPL")
    equity = build_outlook(bars, market="EQUITY", symbol="AAPL", instrument_id="US:AAPL")
    crypto = build_outlook(bars)

    assert equity.market == "EQUITY" and crypto.market == "CRYPTO"
    assert equity.levels.cycle_floor is None
    assert "不适用" in (equity.triggers["cycle_turn"] or "")
    assert crypto.levels.cycle_floor is not None
    disabled = [hit for hit in equity.structure_hits if hit.rule == "cycle_bottom_window"]
    assert disabled and disabled[0].points == 0.0 and "disabled" in disabled[0].detail
    assert any("四年减半周期不适用" in item for item in equity.limitations)
    # The same tape scores the same structure once the cycle rule is neutral on both sides.
    assert abs((equity.structure_score - crypto.structure_score)
               - (crypto.structure_hits and [h for h in crypto.structure_hits
                                             if h.rule == "cycle_bottom_window"][0].points or 0.0)) < 1e-9
