"""Tests for upgraded market dynamics, VPVR peak clustering, 3x1 forming divergences,
user strategy engine, and workbench API endpoints."""

from __future__ import annotations

import asyncio
import httpx

from app.divergence_audit import audit as audit_divergences, detect_forming_divergences
from app.main import app
from app.market_dynamics import compute_indicator_dynamics, extract_vpvr_zones
from app.models import Interval
from app.service import service
from app.strategy_engine import evaluate_strategy


def test_vpvr_zone_clustering_and_indicator_dynamics() -> None:
    bundle = service.get_indicator_bundle("crypto-btc", Interval.DAY_1)
    page = service.get_bars(bundle.instrument.instrument_id, Interval.DAY_1, None, 5000, None)
    bars = page.bars
    assert bars

    current_price = float(bars[-1].close)
    zones = extract_vpvr_zones(bundle.vpvr, current_price)
    assert float(zones["poc"]) == float(bundle.vpvr.poc)
    assert float(zones["vah"]) == float(bundle.vpvr.vah)
    assert float(zones["val"]) == float(bundle.vpvr.val)
    assert zones["hvn_zones"], "48-bin VPVR must cluster into at least one HVN peak zone"

    dyn = compute_indicator_dynamics(bars, bundle)
    assert abs(float(dyn["last_close"]) - current_price) < 1e-3
    assert dyn["ma"]["alignment"] in {
        "BULL_ALIGNMENT",
        "BEAR_ALIGNMENT",
        "ABOVE_200_PULLBACK_OR_CONSOLIDATION",
        "BELOW_200_REBOUND_OR_WEAK",
        "PARTIAL_MA",
    }
    assert dyn["rsi"]["zone"] in {"OVERBOUGHT", "OVERSOLD", "BULLISH_NEUTRAL", "WEAK_NEUTRAL", "UNKNOWN"}
    assert dyn["trajectory_20b"]["last_candle_pattern"]


def test_dual_track_divergence_summary_and_forming_candidates() -> None:
    bundle = service.get_indicator_bundle("crypto-btc", Interval.HOUR_4)
    page = service.get_bars(bundle.instrument.instrument_id, Interval.HOUR_4, None, 5000, None)
    summary = audit_divergences(bundle.divergences, page.bars, "4h")
    assert summary["interval"] == "4h"
    assert "live_bottom_sentence" in summary
    assert "forming_sentence" in summary
    forming = detect_forming_divergences(page.bars)
    assert isinstance(forming, list)


def test_strategy_engine_modes_and_unambiguous_verdict() -> None:
    btc_inst = service.get_subject("crypto-btc").primary_instrument_id
    daily_page = service.get_bars(btc_inst, Interval.DAY_1, None, 5000, None)
    h4_page = service.get_bars(btc_inst, Interval.HOUR_4, None, 5000, None)
    h1_page = service.get_bars(btc_inst, Interval.HOUR_1, None, 5000, None)

    plan = evaluate_strategy(
        daily_page.bars,
        symbol="BTC",
        instrument_id=btc_inst,
        market="CRYPTO",
        intraday_bars=h1_page.bars,
        h4_bars=h4_page.bars,
    )
    assert plan["macro_direction"]["mode"] in {"MODE_A_REVERSAL", "MODE_B_REBOUND", "MODE_WAIT_HOLD"}
    assert plan["execution_plan"]["action_code"] in {
        "TAKE_PROFIT_SCALE_OUT",
        "BUY_HEAD_TRANCHE_1_3",
        "BUY_DIP_VPVR_PEAK",
        "HOLD_OR_ADD_ON_BREAKOUT",
        "WAIT_FOR_DAILY_DIVERGENCE_OR_VPVR",
        "LEFT_SIDE_TRIAL_1_3_ONLY",
        "WAIT_NO_NEW_ENTRY",
    }
    assert len(plan["execution_plan"]["action_verdict"]) > 15
    assert len(plan["risk_checklist"]) == 4


def test_workbench_endpoints_return_overview_detail_and_registry() -> None:
    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            overview_res = await client.get("/v1/workbench/overview")
            assert overview_res.status_code == 200
            overview = overview_res.json()
            assert overview["subject_count"] >= 3
            symbols = {item["symbol"] for item in overview["subjects"]}
            assert {"BTC", "ETH", "AAPL"}.issubset(symbols)

            detail_res = await client.get("/v1/workbench/subjects/crypto-btc?interval=1d")
            assert detail_res.status_code == 200
            detail = detail_res.json()
            assert detail["subject"]["symbol"] == "BTC"
            assert detail["strategy_plan"] is not None
            assert detail["active_timeframe_detail"] is not None
            assert "vpvr_zones" in detail["active_timeframe_detail"]
            assert "indicator_dynamics" in detail["active_timeframe_detail"]

            reg_res = await client.get("/v1/workbench/registry")
            assert reg_res.status_code == 200
            reg = reg_res.json()
            assert "FORWARD" in reg["scoreboard"]
            assert "REPLAY" in reg["scoreboard"]

    asyncio.run(scenario())
