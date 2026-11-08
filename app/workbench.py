"""Interactive Workbench aggregation layer.

Provides fast, cache-first read-only views across all 11 subjects (BTC, ETH + 9 US equities),
combining:
- Multi-timeframe K-line & indicator dynamics (`app/market_dynamics.py`)
- Confirmed (3x3) + Forming (3x1) divergence radar (`app/divergence_audit.py`)
- VPVR 48-bin HVN peak / LVN vacuum clustering (`app/market_dynamics.py`)
- User's multi-timeframe VPVR + MACD divergence strategy engine (`app/strategy_engine.py`)
- Multi-timeframe structure/momentum outlook (`app/outlook.py`)
- SEC EDGAR point-in-time fundamentals & PE_TTM vs PE_FY dual-anchor probe
- FRED point-in-time macro snapshot & Outlook Registry scoreboard
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.bar_builder import aggregate_bars
from app.config import settings
from app.divergence_audit import audit as audit_divergences
from app.fixtures import BARS, SUBJECTS, equity_fundamentals
from app.indicators import (
    compute_divergences,
    compute_fvg,
    compute_indicators,
    compute_support_resistance_and_trend,
    compute_vpvr,
)
from app.market_cache import _safe_segment
from app.market_dynamics import compute_indicator_dynamics, extract_vpvr_zones
from app.models import (
    AnalysisSubject,
    Bar,
    CachedBarsDocument,
    Completeness,
    DataStatus,
    EquityFundamentals,
    FundamentalMetricName,
    IndicatorBundle,
    InstrumentRef,
    Interval,
    MacroSnapshot,
    MarketScope,
    SubjectType,
)
from app.outlook import PATH_LABELS, build_outlook
from app.outlook_registry import load_entries, registry_path, scoreboard
from app.service import MarketWorkbenchService
from app.strategy_engine import evaluate_strategy


ALL_WORKBENCH_SUBJECT_IDS: tuple[str, ...] = (
    "crypto-btc",
    "crypto-eth",
    "us-nvda",
    "us-aapl",
    "us-msft",
    "us-amzn",
    "us-meta",
    "us-googl",
    "us-goog",
    "us-tsla",
    "us-mcd",
)

SUBJECT_SECTOR_MAP: dict[str, tuple[str, str]] = {
    "crypto-btc": ("Crypto", "Layer-1 Digital Store of Value"),
    "crypto-eth": ("Crypto", "Smart Contract Platform"),
    "us-nvda": ("Information Technology", "Semiconductors & Semicap"),
    "us-aapl": ("Information Technology", "Technology Hardware & Equipment"),
    "us-msft": ("Information Technology", "Software & Services"),
    "us-amzn": ("Consumer Discretionary", "Consumer Discretionary Distribution & Retail"),
    "us-meta": ("Communication Services", "Media & Entertainment"),
    "us-googl": ("Communication Services", "Media & Entertainment (Class A)"),
    "us-goog": ("Communication Services", "Media & Entertainment (Class C)"),
    "us-tsla": ("Consumer Discretionary", "Automobiles & Components"),
    "us-mcd": ("Consumer Discretionary", "Consumer Services (Restaurants)"),
}

SUBJECT_DISPLAY_NAMES: dict[str, str] = {
    "crypto-btc": "Bitcoin",
    "crypto-eth": "Ethereum",
    "us-nvda": "NVIDIA Corp.",
    "us-aapl": "Apple Inc.",
    "us-msft": "Microsoft Corp.",
    "us-amzn": "Amazon.com Inc.",
    "us-meta": "Meta Platforms Inc.",
    "us-googl": "Alphabet Inc. (Class A)",
    "us-goog": "Alphabet Inc. (Class C)",
    "us-tsla": "Tesla Inc.",
    "us-mcd": "McDonald's Corp.",
}


def _safe_seg(value: str) -> str:
    return _safe_segment(value)


def load_bars_cache_first(
    svc: MarketWorkbenchService,
    instrument: InstrumentRef,
    interval: Interval,
) -> tuple[list[Bar], str]:
    """Load confirmed bars for an instrument/interval using local disk cache first for instant UI response."""
    if settings.data_mode == "fixture":
        base = BARS.get(instrument.instrument_id)
        if not base:
            return [], "NONE"
        if interval is Interval.MIN_15:
            bars = [b for b in sorted(base, key=lambda x: x.open_at) if b.confirmed]
        else:
            bars = aggregate_bars(base, interval, None)
        return bars, "FIXTURE_SYNTHETIC"

    is_equity = instrument.asset_class.value == "US_EQUITY"
    base_interval = (
        Interval.DAY_1
        if (is_equity and interval in {Interval.DAY_1, Interval.WEEK_1, Interval.MONTH_1})
        or (not is_equity and interval in {Interval.WEEK_1, Interval.MONTH_1})
        else (Interval.MIN_15 if is_equity or interval is Interval.MIN_195 else interval)
    )

    seg = _safe_seg(instrument.instrument_id)
    candidate_paths: list[tuple[Path, str]] = []
    market_root = settings.root / "cache" / "market"
    if is_equity:
        if base_interval is Interval.DAY_1:
            candidate_paths.append((market_root / "tiingo" / f"{seg}-{base_interval.value}.json", "TIINGO"))
            candidate_paths.append((market_root / "alpaca" / f"{seg}-{base_interval.value}.json", "ALPACA"))
            candidate_paths.append((market_root / "massive" / f"{seg}-{base_interval.value}.json", "MASSIVE"))
        else:
            candidate_paths.append((market_root / "alpaca" / f"{seg}-{base_interval.value}.json", "ALPACA"))
            candidate_paths.append((market_root / "twelve_data" / f"{seg}-{base_interval.value}.json", "TWELVE_DATA"))
    else:
        candidate_paths.append((market_root / "okx-base-volume-v2" / f"{seg}-{base_interval.value}.json", "OKX"))

    for path, provider_label in candidate_paths:
        if path.exists():
            try:
                cached = CachedBarsDocument.model_validate_json(path.read_text(encoding="utf-8"))
                if cached.bars:
                    if base_interval is interval:
                        bars = [b for b in sorted(cached.bars, key=lambda x: x.open_at) if b.confirmed]
                    else:
                        bars = aggregate_bars(cached.bars, interval, None)
                    if bars:
                        return bars, provider_label
            except Exception:
                continue

    try:
        page = svc.get_bars(instrument.instrument_id, interval, None, 5000, None)
        provider_label = page.sources[0].provider.value if page.sources else "LIVE"
        return page.bars, provider_label
    except Exception:
        return [], "UNAVAILABLE"


def build_bundle_from_bars(
    subject: AnalysisSubject,
    instrument: InstrumentRef,
    interval: Interval,
    bars: list[Bar],
) -> IndicatorBundle | None:
    if not bars:
        return None
    market_scope = (
        MarketScope.VENUE_SPECIFIC_CRYPTO
        if subject.subject_type is SubjectType.CRYPTO_ASSET
        else MarketScope.CONSOLIDATED_US_EQUITY
    )
    indicators = compute_indicators(bars)
    fvg = compute_fvg(bars)
    divergences = compute_divergences(bars)
    sr, trend = compute_support_resistance_and_trend(bars)
    vpvr = compute_vpvr(bars, market_scope=market_scope, completeness=Completeness.COMPLETE_FOR_SCOPE)
    return IndicatorBundle(
        bundle_id=f"bundle-{subject.subject_id}-{instrument.instrument_id.replace(':', '-')}-{interval.value}-{int(bars[-1].close_at.timestamp())}",
        subject=subject,
        instrument=instrument,
        interval=interval,
        bars_as_of=bars[-1].close_at,
        indicators=indicators,
        fvg=fvg,
        divergences=divergences,
        vpvr=vpvr,
        support_resistance=sr,
        trend_structure=trend,
        sources=[],
        missing_items=[],
    )


def load_fundamentals_summary(
    svc: MarketWorkbenchService,
    subject: AnalysisSubject,
    latest_close: float | None = None,
) -> dict[str, Any] | None:
    """Load SEC EDGAR fundamentals (cache-first) and compute PE_TTM vs PE_FY dual-anchor probe."""
    if subject.subject_type is not SubjectType.EQUITY:
        return None

    fundamentals: EquityFundamentals | None = None
    if settings.data_mode == "fixture":
        try:
            fundamentals = equity_fundamentals()
        except Exception:
            fundamentals = None
    else:
        fund_dir = settings.root / "cache" / "fundamentals"
        symbol_lower = subject.symbol.lower()
        if fund_dir.exists():
            matches = sorted(fund_dir.glob(f"{symbol_lower}-*.json"), reverse=True)
            for path in matches:
                try:
                    fundamentals = EquityFundamentals.model_validate_json(path.read_text(encoding="utf-8"))
                    break
                except Exception:
                    continue
        if fundamentals is None:
            try:
                fundamentals = svc.get_fundamentals(subject.subject_id)
            except Exception:
                fundamentals = None

    if fundamentals is None:
        return None

    fact_map = {f.name: f for f in fundamentals.facts}

    def _fval(metric: FundamentalMetricName) -> float | None:
        fact = fact_map.get(metric)
        if fact and fact.status is DataStatus.AVAILABLE and fact.value is not None:
            try:
                return float(fact.value)
            except ValueError:
                return None
        return None

    rev = _fval(FundamentalMetricName.REVENUE)
    ni = _fval(FundamentalMetricName.NET_INCOME)
    ocf = _fval(FundamentalMetricName.OPERATING_CASH_FLOW)
    capex = _fval(FundamentalMetricName.CAPITAL_EXPENDITURE)
    fcf = _fval(FundamentalMetricName.FREE_CASH_FLOW)
    cash = _fval(FundamentalMetricName.CASH_AND_EQUIVALENTS)
    debt = _fval(FundamentalMetricName.TOTAL_DEBT)
    shares = _fval(FundamentalMetricName.DILUTED_SHARES)
    mcap = _fval(FundamentalMetricName.MARKET_CAP)
    ttm_eps = _fval(FundamentalMetricName.TTM_EPS)
    pe_ttm = _fval(FundamentalMetricName.PE_TTM)
    rev_yoy = _fval(FundamentalMetricName.REVENUE_GROWTH)
    ni_yoy = _fval(FundamentalMetricName.NET_INCOME_GROWTH)

    if latest_close and ttm_eps and ttm_eps > 0:
        pe_ttm = round(latest_close / ttm_eps, 2)
    if latest_close and shares and shares > 0:
        share_count = shares * 1_000_000 if shares < 1_000_000 else shares
        mcap = latest_close * share_count

    pe_fy: float | None = None
    if mcap and ni and ni > 0:
        pe_fy = round(mcap / ni, 2)

    ttm_fy_ratio: float | None = None
    earnings_quality_flag = "NORMAL"
    earnings_quality_note = "TTM 与最近完整财年盈利锚一致，无显著一次性损益扭曲。"
    if pe_ttm and pe_fy and pe_fy > 0:
        ttm_fy_ratio = round(pe_ttm / pe_fy, 2)
        if abs(ttm_fy_ratio - 1.0) >= 0.15:
            if ttm_fy_ratio < 0.85:
                earnings_quality_flag = "TTM_INFLATED_BY_NON_OPERATING"
                earnings_quality_note = (
                    f"PE_TTM ({pe_ttm:.1f}x) 显著低于 PE_FY ({pe_fy:.1f}x)，比值 {ttm_fy_ratio:.2f}："
                    "近期单季 GAAP 净利可能包含大额一次性或按市值计价投资收益（如未实现公允价值变动），估值需同时参考 PE_FY。"
                )
            else:
                earnings_quality_flag = "TTM_ACCELERATING_OR_ONE_OFF_CHARGE"
                earnings_quality_note = (
                    f"PE_TTM ({pe_ttm:.1f}x) 与 PE_FY ({pe_fy:.1f}x) 偏离超过 15%（比值 {ttm_fy_ratio:.2f}）："
                    "单季盈利与上一完整财年存在结构性跃迁或一次性计提，建议对照单季现金流（OCF/FCF）交叉核实。"
                )

    facts_detail = [
        {
            "name": f.name.value,
            "value": f.value,
            "unit": f.unit,
            "status": f.status.value,
            "fiscal_period": f.fiscal_period,
            "period_end": f.period_end.isoformat() if f.period_end else None,
            "filed_at": f.filed_at.isoformat() if f.filed_at else None,
            "derivation": f.derivation,
            "missing_reason": f.missing_reason,
        }
        for f in fundamentals.facts
    ]

    return {
        "symbol": subject.symbol,
        "accepted_before": fundamentals.accepted_before.isoformat(),
        "pe_ttm": round(pe_ttm, 2) if pe_ttm is not None else None,
        "pe_fy": pe_fy,
        "ttm_fy_ratio": ttm_fy_ratio,
        "ttm_eps": round(ttm_eps, 3) if ttm_eps is not None else None,
        "market_cap_b": round(mcap / 1e9, 2) if mcap is not None else None,
        "revenue_b": round(rev / 1e9, 2) if rev is not None else None,
        "net_income_b": round(ni / 1e9, 2) if ni is not None else None,
        "ocf_b": round(ocf / 1e9, 2) if ocf is not None else None,
        "capex_b": round(capex / 1e9, 2) if capex is not None else None,
        "fcf_b": round(fcf / 1e9, 2) if fcf is not None else None,
        "fcf_billion": round(fcf / 1e9, 2) if fcf is not None else None,
        "cash_b": round(cash / 1e9, 2) if cash is not None else None,
        "debt_b": round(debt / 1e9, 2) if debt is not None else None,
        "revenue_yoy_pct": round(rev_yoy * 100, 2) if rev_yoy is not None else None,
        "net_income_yoy_pct": round(ni_yoy * 100, 2) if ni_yoy is not None else None,
        "earnings_quality_flag": earnings_quality_flag,
        "earnings_quality_note": earnings_quality_note,
        "facts": facts_detail,
        "filings": [
            {
                "form": fil.form,
                "filed_at": fil.filed_at.isoformat(),
                "report_period": fil.report_period.isoformat() if fil.report_period else None,
                "source_url": str(fil.source_url),
            }
            for fil in fundamentals.filings[:5]
        ],
    }


def load_macro_summary(svc: MarketWorkbenchService) -> dict[str, Any] | None:
    """Load FRED point-in-time macro snapshot from disk cache first."""
    macro_dir = settings.root / "cache" / "macro"
    snapshot: MacroSnapshot | None = None
    if macro_dir.exists():
        for path in sorted(macro_dir.glob("fred-*.json"), reverse=True):
            try:
                snapshot = MacroSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
                break
            except Exception:
                continue
    if snapshot is None and settings.data_mode == "live":
        try:
            snapshot = svc.get_macro_context(limit=30)
        except Exception:
            snapshot = None
    if snapshot is None:
        return None

    series_out = []
    kv_map: dict[str, str | None] = {}
    for s in snapshot.series:
        val = s.latest.value if s.latest else None
        kv_map[s.series_id] = val
        series_out.append({
            "series_id": s.series_id,
            "label": s.label,
            "role": s.role,
            "units": s.units,
            "status": s.status.value,
            "latest_value": val,
            "latest_date": s.latest_observation_date.isoformat() if s.latest_observation_date else None,
            "change_1": s.change_1_observation,
            "change_5": s.change_5_observations,
            "change_20": s.change_20_observations,
            "pct_change_20": s.percent_change_20_observations,
        })
    return {
        "vintage_date": snapshot.vintage_date.isoformat(),
        "as_of": snapshot.as_of.isoformat(),
        "series": series_out,
        "kv": kv_map,
        "limitations": snapshot.limitations,
    }


def _format_strategy_for_ui(raw_plan: dict[str, Any]) -> dict[str, Any]:
    """Normalize evaluate_strategy() dict output into flat + nested UI fields."""
    macro_dir = raw_plan.get("macro_direction") or {}
    daily_sig = raw_plan.get("daily_signals") or {}
    exec_plan = raw_plan.get("execution_plan") or {}
    checklist = []
    for item in raw_plan.get("risk_checklist") or []:
        st = item.get("status", "PASS")
        ui_st = "PASS" if st in {"ACTIVE", "CHECKED", "ENFORCED", "CONFIRMED", "PASS"} else "WARN"
        checklist.append({
            "item": item.get("item", ""),
            "status": ui_st,
            "detail": item.get("detail", ""),
        })

    return {
        "symbol": raw_plan.get("symbol"),
        "current_price": float(raw_plan.get("last_close") or 0.0),
        "macro_mode": macro_dir.get("mode", "MODE_WAIT_HOLD"),
        "macro_mode_label": macro_dir.get("mode_label", ""),
        "macro_evidence": f"条件A(周K底背离): {macro_dir.get('condition_a_detail', '')} | 条件B(周/月MACD上行且RSI<70): {macro_dir.get('condition_b_detail', '')}",
        "action_code": exec_plan.get("action_code", "HOLD_OR_WAIT"),
        "action_verdict": exec_plan.get("action_verdict", ""),
        "in_vpvr_main_peak": bool(daily_sig.get("in_vpvr_main_peak")),
        "entry_tranche_1_zone": exec_plan.get("entry_tranche_1_zone"),
        "add_trigger_price": exec_plan.get("add_trigger_price"),
        "tp_target_1": exec_plan.get("tp_target_1"),
        "tp_target_2": exec_plan.get("tp_target_2"),
        "rebound_rule_note": exec_plan.get("tp_rule", ""),
        "daily_divergence_status": f"底背离: {daily_sig.get('bottom_div_summary')} | 顶背离: {daily_sig.get('top_div_summary')}",
        "small_tf_top_div_warning": daily_sig.get("small_tf_top_summary"),
        "risk_checklist": checklist,
        "raw": raw_plan,
    }


def _format_outlook_for_ui(daily: list[Bar], h4: list[Bar] | None, h1: list[Bar] | None, m15: list[Bar] | None,
                           subject: AnalysisSubject, instrument: InstrumentRef) -> dict[str, Any] | None:
    if len(daily) < 60:
        return None
    market_lit = "CRYPTO" if subject.subject_type is SubjectType.CRYPTO_ASSET else "EQUITY"
    try:
        rep = build_outlook(
            daily,
            h4=h4,
            h1=h1,
            m15=m15,
            market=market_lit,
            symbol=subject.symbol,
            instrument_id=instrument.instrument_id,
        )
    except Exception:
        return None

    weights = {p.key: round(p.weight_pct / 100.0, 2) for p in rep.paths}
    lv = rep.levels
    scenarios = [
        {
            "code": "A",
            "title": PATH_LABELS["A"],
            "weight": weights.get("A", 0.33),
            "triggers": [rep.triggers.get("dip_zone") or "", rep.triggers.get("confirm_reclaim") or ""],
            "targets": [lv.target_1 or "", lv.target_2 or ""],
            "invalidation": rep.triggers.get("invalidate_A") or "",
        },
        {
            "code": "B",
            "title": PATH_LABELS["B"],
            "weight": weights.get("B", 0.33),
            "triggers": [rep.triggers.get("confirm_breakout") or ""],
            "targets": [lv.target_1 or "", lv.target_2 or ""],
            "invalidation": rep.triggers.get("invalidate_A") or "",
        },
        {
            "code": "C",
            "title": PATH_LABELS["C"],
            "weight": weights.get("C", 0.34),
            "triggers": [rep.triggers.get("warning_weekly") or ""],
            "targets": [lv.structural_support or "", lv.swing_low_20d or ""],
            "invalidation": rep.triggers.get("confirm_breakout") or "",
        },
    ]
    return {
        "structure_score": rep.structure_score,
        "momentum_score": rep.momentum_score,
        "primary": f"PATH_{rep.primary}",
        "primary_label": PATH_LABELS.get(rep.primary, rep.primary),
        "weights": weights,
        "one_line": rep.narrative.splitlines()[1] if len(rep.narrative.splitlines()) > 1 else rep.narrative,
        "narrative": rep.narrative,
        "levels": lv.model_dump(mode="json"),
        "scenarios": scenarios,
    }


def _format_div_summary(da: dict[str, Any]) -> dict[str, Any]:
    live_top = da.get("live_top") or []
    live_bot = da.get("live_bottom") or []
    form_top = da.get("forming_top") or []
    form_bot = da.get("forming_bottom") or []
    live_parts = []
    if live_bot:
        live_parts.append(da.get("live_bottom_sentence") or f"{len(live_bot)} 个未失效底背离")
    if live_top:
        live_parts.append(da.get("live_top_sentence") or f"{len(live_top)} 个未失效顶背离")
    return {
        "live_top": live_top,
        "live_bottom": live_bot,
        "forming_top": form_top,
        "forming_bottom": form_bot,
        "live_top_count": len(live_top),
        "live_bottom_count": len(live_bot),
        "forming_top_count": len(form_top),
        "forming_bottom_count": len(form_bot),
        "live_sentence": "；".join(live_parts) if live_parts else "无未失效已确认背离",
        "live_bottom_sentence": da.get("live_bottom_sentence") or "无未失效底背离",
        "forming_sentence": da.get("forming_sentence") or "右侧3棒内无正在形成背离",
    }


def get_workbench_overview(svc: MarketWorkbenchService) -> dict[str, Any]:
    """Build the 11-subject multi-timeframe radar, strategy verdict, and valuation leaderboard."""
    macro = load_macro_summary(svc)
    macro_kv = macro.get("kv") if macro else None
    items: list[dict[str, Any]] = []

    for subject_id in ALL_WORKBENCH_SUBJECT_IDS:
        if subject_id not in SUBJECTS:
            continue
        subject = SUBJECTS[subject_id]
        sector_pair = SUBJECT_SECTOR_MAP.get(subject_id, ("Equity", "US Listed"))
        instrument = svc.get_instrument(subject.primary_instrument_id)

        daily_bars, prov = load_bars_cache_first(svc, instrument, Interval.DAY_1)
        if not daily_bars:
            continue
        h4_bars, _ = load_bars_cache_first(svc, instrument, Interval.HOUR_4)
        h1_bars, _ = load_bars_cache_first(svc, instrument, Interval.HOUR_1)
        m15_bars, _ = load_bars_cache_first(svc, instrument, Interval.MIN_15)
        weekly_bars = aggregate_bars(daily_bars, Interval.WEEK_1)

        latest_close = float(daily_bars[-1].close)
        latest_at = daily_bars[-1].close_at.isoformat()
        fund = load_fundamentals_summary(svc, subject, latest_close=latest_close)

        market_str = "CRYPTO" if subject.subject_type is SubjectType.CRYPTO_ASSET else "EQUITY"
        raw_strat = evaluate_strategy(
            daily_bars,
            symbol=subject.symbol,
            instrument_id=instrument.instrument_id,
            market=market_str,
            intraday_bars=h1_bars or m15_bars,
            h4_bars=h4_bars,
            fundamentals_summary=fund,
            macro_summary=macro_kv,
        )
        ui_strat = _format_strategy_for_ui(raw_strat)
        ui_outlook = _format_outlook_for_ui(daily_bars, h4_bars, h1_bars, m15_bars, subject, instrument)

        daily_da = (raw_strat.get("multi_timeframe_dynamics", {}).get("daily") or {}).get("divergence_audit") or audit_divergences(compute_divergences(daily_bars), daily_bars, "1d")
        weekly_da = (raw_strat.get("multi_timeframe_dynamics", {}).get("weekly") or {}).get("divergence_audit") or (audit_divergences(compute_divergences(weekly_bars), weekly_bars, "1w") if weekly_bars else {})

        items.append({
            "subject_id": subject.subject_id,
            "symbol": subject.symbol,
            "display_name": SUBJECT_DISPLAY_NAMES.get(subject.subject_id, subject.symbol),
            "subject_type": subject.subject_type.value,
            "primary_instrument_id": subject.primary_instrument_id,
            "instruments": [i.model_dump(mode="json") for i in subject.instruments],
            "gics_sector": sector_pair[0],
            "gics_industry_group": sector_pair[1],
            "latest_close": latest_close,
            "latest_at": latest_at,
            "data_source": prov,
            "outlook": ui_outlook,
            "strategy": ui_strat,
            "divergence_1d": _format_div_summary(daily_da),
            "divergence_1w": _format_div_summary(weekly_da),
            "valuation": {
                "pe_ttm": fund["pe_ttm"],
                "pe_fy": fund["pe_fy"],
                "ttm_fy_ratio": fund["ttm_fy_ratio"],
                "market_cap_b": fund["market_cap_b"],
                "revenue_yoy_pct": fund["revenue_yoy_pct"],
                "fcf_b": fund["fcf_b"],
                "earnings_quality_flag": fund["earnings_quality_flag"],
                "earnings_quality_note": fund["earnings_quality_note"],
            } if fund else None,
        })

    reg_summary = get_registry_summary()
    return {
        "as_of": datetime.now(UTC).isoformat(),
        "data_mode": settings.data_mode,
        "subject_count": len(items),
        "subjects": items,
        "macro": macro,
        "registry_scoreboard": reg_summary["scoreboard"],
    }


def _adapt_vpvr_zones_for_ui(vz: dict[str, Any]) -> dict[str, Any]:
    hvn_peaks = [
        {
            "label": f"{z['zone_id'].upper()}{'(POC)' if z.get('contains_poc') else ''}",
            "lower": float(z["lower"]),
            "upper": float(z["upper"]),
            "center": float(z["center_price"]),
            "volume_share": float(z["volume_share_pct"]) / 100.0,
            "role": z.get("role"),
        }
        for z in (vz.get("hvn_zones") or [])
    ]
    lvn_zones = [
        {
            "label": z["zone_id"].upper(),
            "lower": float(z["lower"]),
            "upper": float(z["upper"]),
            "volume_share": float(z["volume_share_pct"]) / 100.0,
        }
        for z in (vz.get("lvn_zones") or [])
    ]
    cur_pk = vz.get("current_peak_zone")
    nxt_up = vz.get("next_upper_peak")
    nxt_lo = vz.get("next_lower_peak")
    return {
        "poc": float(vz["poc"]),
        "vah": float(vz["vah"]),
        "val": float(vz["val"]),
        "price_vs_value_area": vz.get("current_position", "INSIDE_VALUE_AREA"),
        "in_main_peak_zone": bool(vz.get("in_main_peak_zone")),
        "active_peak_label": cur_pk["zone_id"].upper() if cur_pk else ("VAL-POC" if vz.get("in_main_peak_zone") else None),
        "next_upper_peak": float(nxt_up["center_price"]) if nxt_up and nxt_up.get("center_price") else None,
        "next_lower_peak": float(nxt_lo["center_price"]) if nxt_lo and nxt_lo.get("center_price") else None,
        "hvn_peaks": hvn_peaks,
        "lvn_zones": lvn_zones,
    }


def _adapt_dynamics_for_ui(dyn: dict[str, Any]) -> dict[str, Any]:
    ma = dyn.get("ma") or {}
    macd_d = dyn.get("macd") or {}
    rsi_d = dyn.get("rsi") or {}
    adx_d = dyn.get("adx_atr") or {}
    traj = dyn.get("trajectory_20b") or {}
    return {
        "latest_close": float(dyn.get("last_close") or 0.0),
        "trend_state": dyn.get("trend_state", "RANGE"),
        "ma_alignment": ma.get("alignment", "MIXED"),
        "sma20": ma.get("sma20"),
        "ema20": ma.get("ema20"),
        "sma50": ma.get("sma50"),
        "sma200": ma.get("sma200"),
        "macd_hist": macd_d.get("histogram"),
        "macd_hist_state": macd_d.get("hist_state", "NONE"),
        "macd_cross": macd_d.get("cross_state", "NONE"),
        "rsi14": rsi_d.get("value"),
        "rsi14_delta_5": rsi_d.get("delta_5b"),
        "rsi_zone": rsi_d.get("zone", "UNKNOWN"),
        "adx14": adx_d.get("adx"),
        "atr14": adx_d.get("atr14"),
        "candle_trajectory_note": (
            f"近20棒区间 {traj.get('low_20b')}–{traj.get('high_20b')} "
            f"(5棒 {traj.get('return_5b_pct', 0):+.2f}%, 20棒 {traj.get('return_20b_pct', 0):+.2f}%) · "
            f"量比 {traj.get('last_volume_ratio_20b', 1)}x · {traj.get('last_candle_pattern', '')}"
        ),
    }


def get_subject_workbench_detail(
    svc: MarketWorkbenchService,
    subject_id: str,
    active_interval: str = "1d",
    instrument_id: str | None = None,
) -> dict[str, Any]:
    """Build full multi-timeframe workbench detail for one subject."""
    subject = svc.get_subject(subject_id)
    selected_inst_id = instrument_id or subject.primary_instrument_id
    instrument = svc.get_instrument(selected_inst_id)
    sector_pair = SUBJECT_SECTOR_MAP.get(subject_id, ("Equity", "US Listed"))

    all_tfs = [
        Interval.MONTH_1,
        Interval.WEEK_1,
        Interval.DAY_1,
        Interval.HOUR_4,
        Interval.HOUR_1,
        Interval.MIN_15,
    ]
    bars_by_tf: dict[str, list[Bar]] = {}
    bundles_by_tf: dict[str, IndicatorBundle] = {}
    dyn_by_tf: dict[str, dict[str, Any]] = {}
    matrix_rows: list[dict[str, Any]] = []

    for tf in all_tfs:
        bars, prov = load_bars_cache_first(svc, instrument, tf)
        if not bars:
            continue
        bars_by_tf[tf.value] = bars
        bundle = build_bundle_from_bars(subject, instrument, tf, bars)
        if not bundle:
            continue
        bundles_by_tf[tf.value] = bundle

        raw_dyn = compute_indicator_dynamics(bars, bundle)
        dyn_by_tf[tf.value] = raw_dyn
        ui_dyn = _adapt_dynamics_for_ui(raw_dyn)
        ui_vz = _adapt_vpvr_zones_for_ui(raw_dyn["vpvr_zones"])
        ui_div = _format_div_summary(raw_dyn["divergence_audit"])

        matrix_rows.append({
            "timeframe": tf.value,
            "bar_count": len(bars),
            "bars_as_of": bars[-1].close_at.isoformat(),
            "latest_close": float(bars[-1].close),
            "provider": prov,
            "trend_state": bundle.trend_structure.state.value,
            "ma_alignment": ui_dyn["ma_alignment"],
            "macd_state": ui_dyn["macd_hist_state"],
            "macd_cross": ui_dyn["macd_cross"],
            "macd_hist": ui_dyn["macd_hist"],
            "rsi14": ui_dyn["rsi14"],
            "rsi_delta_5": ui_dyn["rsi14_delta_5"],
            "rsi_zone": ui_dyn["rsi_zone"],
            "adx14": ui_dyn["adx14"],
            "vpvr_poc": ui_vz["poc"],
            "vpvr_vah": ui_vz["vah"],
            "vpvr_val": ui_vz["val"],
            "price_vs_value_area": ui_vz["price_vs_value_area"],
            "in_main_peak_zone": ui_vz["in_main_peak_zone"],
            "active_peak_label": ui_vz["active_peak_label"],
            "next_upper_peak": ui_vz["next_upper_peak"],
            "next_lower_peak": ui_vz["next_lower_peak"],
            "live_top_count": ui_div["live_top_count"],
            "live_bottom_count": ui_div["live_bottom_count"],
            "forming_top_count": ui_div["forming_top_count"],
            "forming_bottom_count": ui_div["forming_bottom_count"],
            "live_sentence": ui_div["live_sentence"],
            "live_bottom_sentence": ui_div["live_bottom_sentence"],
            "forming_sentence": ui_div["forming_sentence"],
        })

    # If active_interval wasn't in all_tfs (e.g., 2h or 195m), compute it on the fly
    if active_interval not in dyn_by_tf:
        try:
            tf_enum = Interval(active_interval)
            abars, _ = load_bars_cache_first(svc, instrument, tf_enum)
            if abars:
                abundle = build_bundle_from_bars(subject, instrument, tf_enum, abars)
                if abundle:
                    bars_by_tf[active_interval] = abars
                    bundles_by_tf[active_interval] = abundle
                    dyn_by_tf[active_interval] = compute_indicator_dynamics(abars, abundle)
        except Exception:
            pass

    ref_tf = active_interval if active_interval in dyn_by_tf else ("1d" if "1d" in dyn_by_tf else (next(iter(dyn_by_tf.keys())) if dyn_by_tf else None))
    active_detail = None
    if ref_tf:
        rd = dyn_by_tf[ref_tf]
        active_detail = {
            "timeframe": ref_tf,
            "vpvr_zones": _adapt_vpvr_zones_for_ui(rd["vpvr_zones"]),
            "indicator_dynamics": _adapt_dynamics_for_ui(rd),
            "divergence_audit": _format_div_summary(rd["divergence_audit"]),
        }

    daily_bars = bars_by_tf.get("1d") or (next(iter(bars_by_tf.values())) if bars_by_tf else [])
    latest_close = float(daily_bars[-1].close) if daily_bars else None
    fund = load_fundamentals_summary(svc, subject, latest_close=latest_close)
    macro = load_macro_summary(svc)

    strategy_plan = None
    if daily_bars:
        market_str = "CRYPTO" if subject.subject_type is SubjectType.CRYPTO_ASSET else "EQUITY"
        raw_strat = evaluate_strategy(
            daily_bars,
            symbol=subject.symbol,
            instrument_id=instrument.instrument_id,
            market=market_str,
            intraday_bars=bars_by_tf.get("1h") or bars_by_tf.get("15m"),
            h4_bars=bars_by_tf.get("4h"),
            fundamentals_summary=fund,
            macro_summary=macro.get("kv") if macro else None,
        )
        strategy_plan = _format_strategy_for_ui(raw_strat)

    outlook_ui = _format_outlook_for_ui(
        daily_bars,
        bars_by_tf.get("4h"),
        bars_by_tf.get("1h"),
        bars_by_tf.get("15m"),
        subject,
        instrument,
    ) if daily_bars else None

    analyses_page = svc.list_analyses(50, None)
    subject_analyses = []
    real_ai_highlights = []
    for item in analyses_page.items:
        item_sym = item.subject_id.split("-")[-1].upper()
        summary_obj = {
            "analysis_id": item.analysis_id,
            "subject_id": item.subject_id,
            "symbol": item_sym,
            "cutoff_at": item.created_at.isoformat(),
            "provider": item.provider,
            "model": item.model,
            "confidence_score": item.confidence_score,
        }
        if item.subject_id == subject_id:
            subject_analyses.append(summary_obj)
        if item.provider == "CODEX_CLI":
            real_ai_highlights.append(summary_obj)

    return {
        "subject": subject.model_dump(mode="json"),
        "gics_sector": sector_pair[0],
        "gics_industry_group": sector_pair[1],
        "selected_instrument_id": selected_inst_id,
        "active_interval": active_interval,
        "timeframe_matrix": matrix_rows,
        "active_timeframe_detail": active_detail,
        "strategy_plan": strategy_plan,
        "outlook": outlook_ui,
        "fundamentals": fund,
        "macro": macro,
        "subject_analyses": subject_analyses[:10],
        "real_ai_highlights": real_ai_highlights[:10],
    }


def get_registry_summary(path: Path | None = None) -> dict[str, Any]:
    """Load pre-registered outlook entries and compute FORWARD vs REPLAY scoreboard."""
    entries = load_entries(path or registry_path())
    board = scoreboard(entries)
    by_origin = board.get("by_origin") or {}
    fw = by_origin.get("FORWARD") or {"entries": 0, "OPEN": 0, "primary_correct": 0, "primary_incorrect": 0}
    rp = by_origin.get("REPLAY") or {"entries": 0, "OPEN": 0, "primary_correct": 0, "primary_incorrect": 0}

    def _bucket_ui(b: dict[str, Any]) -> dict[str, Any]:
        tot = int(b.get("entries", 0))
        open_c = int(b.get("OPEN", 0))
        matured = tot - open_c
        corr = int(b.get("primary_correct", 0))
        incorr = int(b.get("primary_incorrect", 0))
        denom = corr + incorr
        return {
            "total": tot,
            "matured": matured,
            "pending": open_c,
            "primary_correct": corr,
            "primary_incorrect": incorr,
            "hit_rate": round(corr / denom, 4) if denom > 0 else None,
        }

    entries_ui = []
    for e in entries:
        ver = e.verification
        hr = ver.horizon_returns if ver else {}
        r21 = hr.get("21d")
        verdict = "OPEN"
        if ver:
            if ver.primary_correct is True:
                verdict = f"HIT ({ver.path_realized})"
            elif ver.primary_correct is False:
                verdict = f"MISS ({ver.path_realized})"
            else:
                verdict = ver.path_realized
        entries_ui.append({
            "outlook_id": e.outlook_id,
            "SampleMode": e.origin,
            "symbol": e.symbol,
            "as_of": e.as_of.isoformat(),
            "primary": f"Path {e.primary}",
            "realized_5d_return_pct": round(r21 * 100, 2) if r21 is not None else None,
            "realized_20d_return_pct": round((ver.return_pct or 0) * 100, 2) if ver and ver.return_pct is not None else None,
            "verdict_20d": verdict,
        })

    return {
        "scoreboard": {
            "FORWARD": _bucket_ui(fw),
            "REPLAY": _bucket_ui(rp),
            "raw": board,
        },
        "entries": entries_ui,
    }
