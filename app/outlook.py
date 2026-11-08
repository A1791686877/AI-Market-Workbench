"""Deterministic BTC outlook rule engine — implements docs/phase-1/BTC-OUTLOOK-FRAMEWORK.md.

Rules version: btc-outlook-rules-v1. Zero model calls, zero cost, no network
(unless the caller refreshes data first). Output is a rule-based ranking of
three conditional paths, explicitly NOT a calibrated probability.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from app.bar_builder import aggregate_bars
from app.fixtures import decimal_string
from app.indicators import (
    adx as adx_series,
    atr as atr_series,
    compute_fvg,
    compute_support_resistance_and_trend,
    compute_vpvr,
    macd as macd_series,
    rsi as rsi_series,
)
from app.models import Bar, Interval, StrictModel

RULES_VERSION = "btc-outlook-rules-v1"
LAST_HALVING = datetime(2024, 4, 19, tzinfo=UTC)
STUDY_HORIZONS = (21, 63, 126)
BOTTOM_WINDOW_DAYS = (270, 480)

PATH_LABELS = {
    "A": "A 先下后上（回踩后再上）",
    "B": "B 直接上（不回踩）",
    "C": "C 破位（结构失效）",
}


class RuleHit(StrictModel):
    rule: str
    points: float
    detail: str


class PathScore(StrictModel):
    key: Literal["A", "B", "C"]
    label: str
    score: float
    weight_pct: int


class OutlookLevels(StrictModel):
    price: str
    confirm_reclaim: str | None
    confirm_breakout: str | None
    dip_zone_low: str | None
    dip_zone_high: str | None
    structural_support: str | None
    swing_low_20d: str | None
    target_1: str | None
    target_2: str | None
    cycle_floor: str | None


class OutlookReport(StrictModel):
    rules_version: str
    generated_at: datetime
    # CRYPTO keeps the four-year-halving rules; EQUITY disables them (a halving cycle
    # has no meaning for a listed company) and says so in the limitations.
    market: Literal["CRYPTO", "EQUITY"] = "CRYPTO"
    symbol: str
    instrument_id: str
    data: dict[str, Any]
    structure_score: float
    momentum_score: float
    structure_hits: list[RuleHit]
    momentum_hits: list[RuleHit]
    paths: list[PathScore]
    primary: Literal["A", "B", "C"]
    levels: OutlookLevels
    triggers: dict[str, str | None]
    narrative: str
    limitations: list[str]


def _f(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _sma(values: list[float], length: int, offset: int = 0) -> float | None:
    end = len(values) - offset
    if length <= 0 or end < length:
        return None
    return sum(values[end - length:end]) / length


def _last(values: list[float | None]) -> float | None:
    return next((v for v in reversed(values) if v is not None), None)


def _ret(closes: list[float], days: int) -> float | None:
    if len(closes) <= days:
        return None
    return closes[-1] / closes[-1 - days] - 1


def _price_str(value: float | None) -> str | None:
    return None if value is None else decimal_string(value, 8)


def forward_study(closes: list[float], dates: list[datetime]) -> dict[str, Any]:
    """Historical base rate for the current-looking setup (same definition as the ad-hoc study)."""
    ath, ath_at = 0.0, dates[0]
    rows: list[dict[str, Any]] = []
    for index, close in enumerate(closes):
        if close > ath:
            ath, ath_at = close, dates[index]
        if index < 220:
            continue
        sma200 = _sma(closes[:index + 1], 200)
        sma200_prev = _sma(closes[:index - 19], 200)
        if sma200 is None or sma200_prev is None:
            continue
        days_since = (dates[index] - ath_at).days
        drawdown = close / ath - 1
        setup = (days_since >= 240 and -0.60 <= drawdown <= -0.25
                 and close > sma200 and sma200 > sma200_prev)
        row: dict[str, Any] = {"i": index, "setup": setup}
        for horizon in STUDY_HORIZONS:
            row[f"f{horizon}"] = (closes[index + horizon] / close - 1) if index + horizon < len(closes) else None
        rows.append(row)

    def stats(items: list[float | None]) -> dict[str, Any] | None:
        values = sorted(v for v in items if v is not None)
        if not values:
            return None
        return {"n": len(values), "median": round(values[len(values) // 2], 4),
                "min": round(values[0], 4), "max": round(values[-1], 4),
                "positive_share": round(sum(1 for v in values if v > 0) / len(values), 3)}

    clusters = 0
    previous_setup = False
    for row in rows:
        if row["setup"] and not previous_setup:
            clusters += 1
        previous_setup = row["setup"]
    out: dict[str, Any] = {"setup_now": bool(rows and rows[-1]["setup"]), "rows": len(rows), "clusters": clusters}
    for horizon in STUDY_HORIZONS:
        out[f"setup_{horizon}d"] = stats([r[f"f{horizon}"] for r in rows if r["setup"]])
    return out


def cycle_metrics(daily: list[Bar]) -> dict[str, Any]:
    closes = [float(b.close) for b in daily]
    highs = [float(b.high) for b in daily]
    lows = [float(b.low) for b in daily]
    dates = [b.close_at for b in daily]
    halving = LAST_HALVING
    post = [(dates[i], closes[i]) for i in range(len(daily)) if dates[i] >= halving]
    ath_at, ath = max(post, key=lambda item: item[1]) if post else (dates[0], closes[0])
    post_lows = [lows[i] for i in range(len(daily)) if dates[i] >= ath_at]
    cycle_floor = min(post_lows) if post_lows else min(lows)
    return {
        "days_since_halving": (dates[-1] - halving).days,
        "close_ath": ath,
        "close_ath_at": ath_at,
        "days_since_close_ath": (dates[-1] - ath_at).days,
        "drawdown_from_close_ath": closes[-1] / ath - 1,
        "cycle_floor": cycle_floor,
        "max_drawdown_from_ath": cycle_floor / ath - 1,
    }


def _trend_state(bars: list[Bar]) -> str:
    try:
        _, trend = compute_support_resistance_and_trend(bars)
        return trend.state.value
    except Exception:
        return "UNKNOWN"


def _vpvr(bars: list[Bar]):
    try:
        return compute_vpvr(bars)
    except Exception:
        return None


def _latest_bullish_fvg_below(bars: list[Bar], price: float):
    try:
        gaps = [g for g in compute_fvg(bars)
                if g.direction.value == "BULLISH" and g.validity_status.value == "VALID"
                and (_f(g.upper) or 0.0) < price]
    except Exception:
        return None
    return gaps[-1] if gaps else None


def build_outlook(
    daily: list[Bar],
    h4: list[Bar] | None = None,
    h1: list[Bar] | None = None,
    m15: list[Bar] | None = None,
    *,
    market: Literal["CRYPTO", "EQUITY"] = "CRYPTO",
    symbol: str = "BTC",
    instrument_id: str = "CRYPTO:BTCUSDT:SPOT",
    context: dict[str, Any] | None = None,
    data_note: dict[str, Any] | None = None,
) -> OutlookReport:
    if len(daily) < 60:
        raise ValueError("outlook needs at least 60 confirmed daily bars")
    equity = market == "EQUITY"
    if equity:
        limitations: list[str] = [
            "权重是规则排序，不是校准概率；无样本外验证，不构成胜率。",
            "四年减半周期不适用于股票：cycle_bottom_window / cycle_floor 规则已禁用（0 分并记录原因）。",
            "股票口径为单一交易日收盘价（复权口径见数据来源）；财报、指引与宏观事件未纳入规则。",
            "不是投资建议，不保证盈利。",
        ]
    else:
        limitations = [
            "权重是规则排序，不是校准概率；无样本外验证，不构成胜率。",
            "OKX 场所级数据（非全市场）；清算/链上/ETF 流/宏观缺失。",
            "不是投资建议，不保证盈利。",
        ]
    if not h4:
        limitations.append("缺少 4h 输入：4h 相关规则已跳过（按中性处理）。")
    if not h1 or not m15:
        limitations.append("缺少 1h/15m 输入：对应规则已跳过（按中性处理）。")

    closes = [float(b.close) for b in daily]
    highs = [float(b.high) for b in daily]
    lows = [float(b.low) for b in daily]
    price = closes[-1]
    sma20, sma50, sma200 = _sma(closes, 20), _sma(closes, 50), _sma(closes, 200)
    sma200_prev = _sma(closes, 200, offset=20)

    weekly = aggregate_bars(daily, Interval.WEEK_1)
    monthly = aggregate_bars(daily, Interval.MONTH_1)
    weekly_closes = [float(b.close) for b in weekly]
    monthly_closes = [float(b.close) for b in monthly]
    sma20m = _sma(monthly_closes, 20)

    cycle = cycle_metrics(daily)
    study = forward_study(closes, [b.close_at for b in daily])

    _, daily_plus, daily_minus = adx_series(daily)
    daily_di_plus, daily_di_minus = _last(daily_plus), _last(daily_minus)
    weekly_di_plus, weekly_di_minus = _last(adx_series(weekly)[1]), _last(adx_series(weekly)[2])
    h4_di_plus, h4_di_minus = (_last(adx_series(h4)[1]), _last(adx_series(h4)[2])) if h4 else (None, None)

    _, _, daily_hist = macd_series(closes)
    _, _, weekly_hist = macd_series(weekly_closes)
    _, _, monthly_hist = macd_series(monthly_closes)
    daily_rsi = _last(rsi_series(closes, 14))
    monthly_rsi = _last(rsi_series(monthly_closes, 14))
    h4_rsi = _last(rsi_series([float(b.close) for b in h4], 14)) if h4 else None

    h4_vpvr = _vpvr(h4) if h4 else None
    m15_vpvr = _vpvr(m15) if m15 else None
    h1_trend = _trend_state(h1) if h1 else "UNKNOWN"
    m15_trend = _trend_state(m15) if m15 else "UNKNOWN"
    weekly_trend = _trend_state(weekly)
    monthly_trend = _trend_state(monthly)

    def hit(rule: str, points: float, detail: str) -> RuleHit:
        return RuleHit(rule=rule, points=points, detail=detail)

    # ---------------- structure score (start 40) ----------------
    structure = 40.0
    structure_hits: list[RuleHit] = []

    def s_add(rule: str, points: float, passed: bool, detail: str) -> None:
        nonlocal structure
        applied = points if passed else 0.0
        structure += applied
        structure_hits.append(hit(rule, applied, detail))

    def s_signed(rule: str, points: float, passed: bool, detail: str) -> None:
        """Structural facts are symmetric: failing them is evidence for the other side."""
        nonlocal structure
        applied = points if passed else -points
        structure += applied
        structure_hits.append(hit(rule, applied, detail))

    sma200_above = sma200 is not None and price > sma200
    s_signed("price>sma200", 12, sma200_above,
             f"price={price:.2f} sma200={sma200 if sma200 is None else round(sma200, 2)}")
    sma200_rising = sma200 is not None and sma200_prev is not None and sma200 > sma200_prev
    s_signed("sma200_rising", 6, sma200_rising,
             f"sma200={sma200 if sma200 is None else round(sma200, 2)} prev20={sma200_prev if sma200_prev is None else round(sma200_prev, 2)}")
    sma50_above = sma50 is not None and price > sma50
    s_signed("price>sma50", 6, sma50_above,
             f"price={price:.2f} sma50={sma50 if sma50 is None else round(sma50, 2)}")
    s_add("daily_DI+>-DI", 6, daily_di_plus is not None and daily_di_minus is not None and daily_di_plus > daily_di_minus,
          f"+DI={daily_di_plus if daily_di_plus is None else round(daily_di_plus, 2)} -DI={daily_di_minus if daily_di_minus is None else round(daily_di_minus, 2)}")
    s_add("weekly_DI+>-DI", 5, weekly_di_plus is not None and weekly_di_minus is not None and weekly_di_plus > weekly_di_minus,
          f"+DI={weekly_di_plus if weekly_di_plus is None else round(weekly_di_plus, 2)} -DI={weekly_di_minus if weekly_di_minus is None else round(weekly_di_minus, 2)}")
    s_add("weekly_macd_hist>0", 5, _last(weekly_hist) is not None and _last(weekly_hist) > 0,
          f"hist={_last(weekly_hist) if _last(weekly_hist) is None else round(_last(weekly_hist), 2)}")
    improving_weekly = None
    if len([v for v in weekly_hist if v is not None]) >= 9:
        valid = [v for v in weekly_hist if v is not None]
        improving_weekly = valid[-1] > valid[-9]
    s_add("weekly_hist_improving", 4, bool(improving_weekly), f"improving={improving_weekly}")
    if equity:
        # A halving cycle has no meaning for a listed company: record the skipped rule
        # instead of quietly scoring it (0 points either way) so the report explains itself.
        structure_hits.append(hit(
            "cycle_bottom_window", 0.0,
            f"equity: four-year cycle rule disabled; days_since_ath={cycle['days_since_close_ath']}",
        ))
    else:
        s_add("cycle_bottom_window", 8, BOTTOM_WINDOW_DAYS[0] <= cycle["days_since_close_ath"] <= BOTTOM_WINDOW_DAYS[1],
              f"days_since_ath={cycle['days_since_close_ath']} window={BOTTOM_WINDOW_DAYS}")
    study63 = study.get("setup_63d") or {}
    s_add("study_63d_median>5%", 6, (study63.get("median") or -1) > 0.05, f"median={study63.get('median')} n={study63.get('n')}")
    s_add("study_63d_positive>60%", 4, (study63.get("positive_share") or 0) > 0.60,
          f"positive_share={study63.get('positive_share')}")
    s_add("weekly_trend_not_down", -6, weekly_trend == "DOWNTREND", f"weekly_trend={weekly_trend}")
    s_add("monthly_trend_not_down", -8, monthly_trend == "DOWNTREND", f"monthly_trend={monthly_trend}")
    s_add("monthly_hist_not_negative", -8, (_last(monthly_hist) or 0) < 0,
          f"monthly_hist={_last(monthly_hist) if _last(monthly_hist) is None else round(_last(monthly_hist), 2)}")
    s_add("monthly_rsi_not_below_50", -4, monthly_rsi is not None and monthly_rsi < 50, f"monthly_rsi={None if monthly_rsi is None else round(monthly_rsi, 2)}")
    ret365 = _ret(closes, 365)
    s_add("365d_not_below_-20%", -4, ret365 is not None and ret365 < -0.20, f"ret365={None if ret365 is None else round(ret365, 4)}")
    structure = max(0.0, min(100.0, structure))

    # ---------------- momentum score (start 50) ----------------
    momentum = 50.0
    momentum_hits: list[RuleHit] = []

    def m_add(rule: str, points: float, passed: bool, detail: str, *, binary: bool = False) -> None:
        nonlocal momentum
        applied = (points if passed else 0.0) if binary else (points if passed else -points)
        momentum += applied
        momentum_hits.append(hit(rule, applied, detail))

    m_add("price>sma20", 8, sma20 is not None and price > sma20, f"price={price:.2f} sma20={sma20 if sma20 is None else round(sma20, 2)}")
    m_add("daily_macd_hist>0", 8, (_last(daily_hist) or 0) > 0, f"hist={_last(daily_hist) if _last(daily_hist) is None else round(_last(daily_hist), 2)}")
    daily_improving = None
    valid_daily_hist = [v for v in daily_hist if v is not None]
    if len(valid_daily_hist) >= 6:
        daily_improving = valid_daily_hist[-1] > valid_daily_hist[-6]
    m_add("daily_hist_improving", 6, bool(daily_improving), f"improving={daily_improving}", binary=True)
    r7 = _ret(closes, 7)
    m_add("return_7d>0", 6, r7 is not None and r7 > 0, f"ret7={None if r7 is None else round(r7, 4)}")
    h4_ready = bool(h4) and len(h4) > 30
    if h4_ready:
        m_add("h4_DI+>-DI", 6, h4_di_plus is not None and h4_di_minus is not None and h4_di_plus > h4_di_minus,
              f"+DI={h4_di_plus} -DI={h4_di_minus}")
    else:
        momentum_hits.append(hit("h4_DI+>-DI", 0.0, "4h input missing; rule skipped"))
    if h4_ready:
        m_add("h4_rsi>50", 5, h4_rsi is not None and h4_rsi > 50, f"rsi={None if h4_rsi is None else round(h4_rsi, 2)}")
    else:
        momentum_hits.append(hit("h4_rsi>50", 0.0, "4h input missing; rule skipped"))
    h4_val = _f(h4_vpvr.val) if h4_vpvr else None
    if h4_val is not None:
        m_add("price>h4_vpvr_val", 5, price > h4_val, f"price={price:.2f} h4_val={h4_val}")
    else:
        momentum_hits.append(hit("price>h4_vpvr_val", 0.0, "4h VPVR unavailable; rule skipped"))
    poc15 = _f(m15_vpvr.poc) if m15_vpvr else None
    if poc15 is not None:
        m_add("price>m15_vpvr_poc", 4, price > poc15, f"price={price:.2f} m15_poc={poc15}")
    else:
        momentum_hits.append(hit("price>m15_vpvr_poc", 0.0, "15m VPVR unavailable; rule skipped"))
    if h1 and m15:
        m_add("h1_and_m15_uptrend", 4, h1_trend == "UPTREND" and m15_trend == "UPTREND",
              f"h1={h1_trend} m15={m15_trend}")
    else:
        momentum_hits.append(hit("h1_and_m15_uptrend", 0.0, "1h/15m input missing; rule skipped"))
    vah15 = _f(m15_vpvr.vah) if m15_vpvr else None
    if vah15 is not None:
        m_add("price>m15_vpvr_vah", 3, price > vah15, f"price={price:.2f} m15_vah={vah15}", binary=True)
    else:
        momentum_hits.append(hit("price>m15_vpvr_vah", 0.0, "15m VPVR unavailable; rule skipped"))
    momentum = max(0.0, min(100.0, momentum))

    # ---------------- path scores + weights ----------------
    raw = {
        "A": 0.55 * structure + 0.45 * (100 - momentum),
        "B": 0.60 * structure + 0.40 * momentum,
        "C": 0.75 * (100 - structure) + 0.25 * (100 - momentum),
    }
    total = sum(raw.values()) or 1.0
    exact = {k: v / total * 100 for k, v in raw.items()}
    weights = {k: int(v) for k, v in exact.items()}
    for key in sorted(exact, key=lambda k: exact[k] - weights[k], reverse=True)[:100 - sum(weights.values())]:
        weights[key] += 1
    paths = [PathScore(key=k, label=PATH_LABELS[k], score=round(raw[k], 2), weight_pct=weights[k])
             for k in ("A", "B", "C")]
    primary = max(weights, key=lambda k: (weights[k], raw[k]))

    # ---------------- levels ----------------
    confirm_reclaim = sma20
    confirm_breakout = max(highs[-3:]) if len(highs) >= 3 else max(highs)
    swing_low = min(lows[-20:]) if len(lows) >= 20 else min(lows)
    target_1 = max(highs[-30:]) if len(highs) >= 30 else max(highs)
    fvg = _latest_bullish_fvg_below(daily, price)
    if fvg is not None:
        dip_low, dip_high = _f(fvg.lower), _f(fvg.upper)
        dip_source = f"daily_BULLISH_FVG {fvg.fvg_id}"
    else:
        fallback = max([v for v in (sma50, h4_val) if v is not None], default=price * 0.97)
        half_atr = 0.5 * (_last(atr_series(daily, 14)) or price * 0.01)
        dip_low, dip_high, dip_source = fallback - half_atr, fallback + half_atr, "fallback max(sma50,h4_val)±0.5ATR"
    levels = OutlookLevels(
        price=_price_str(price),
        confirm_reclaim=_price_str(confirm_reclaim),
        confirm_breakout=_price_str(confirm_breakout),
        dip_zone_low=_price_str(dip_low),
        dip_zone_high=_price_str(dip_high),
        structural_support=_price_str(sma200),
        swing_low_20d=_price_str(swing_low),
        target_1=_price_str(target_1),
        target_2=_price_str(sma20m),
        # The cycle floor is a post-halving construct; equities report no cycle floor.
        cycle_floor=None if equity else _price_str(cycle["cycle_floor"]),
    )
    triggers = {
        "confirm_reclaim": f"日线收盘 > {levels.confirm_reclaim}（SMA20）视为动量修复",
        "confirm_breakout": f"日线收盘 > {levels.confirm_breakout}（近 3 日高点）→ 回踩步骤作废，转 B",
        "dip_zone": f"回踩区 {levels.dip_zone_low}–{levels.dip_zone_high}（{dip_source}）",
        "invalidate_A": f"日线收盘 < {levels.structural_support}（SMA200）→ A 作废",
        "warning_weekly": f"周线收盘 < {levels.swing_low_20d} 且两周收不回 → 转 C",
        "cycle_turn": "不适用（股票口径：四年减半周期规则已禁用）" if equity
        else f"月线收盘 < {levels.cycle_floor} → 周期'还需深洗'剧本成立",
    }

    # The "C" plan needs a downside reference. Crypto uses the post-halving cycle floor; equities
    # have no cycle floor (it is None by design), so they use the 20-day low instead.
    downside_reference = levels.swing_low_20d if equity else levels.cycle_floor
    one_liner = {
        "A": f"先回 {levels.dip_zone_low}–{levels.dip_zone_high} 一次，再上 {levels.target_1}→{levels.target_2}；日线收盘破 {levels.structural_support} 认错。",
        "B": f"不回踩直接上攻：日线收上 {levels.confirm_breakout} 打开 {levels.target_1}→{levels.target_2}；破 {levels.structural_support} 认错。",
        "C": (f"结构已弱：反弹到 {levels.confirm_reclaim} 附近站不上则看 {downside_reference}"
              + ("（20 日低）" if equity else "")
              + f"；收回 {levels.confirm_breakout} 才谈修复。"),
    }
    if equity:
        cycle_line = (f"回撤：距前高 {cycle['days_since_close_ath']} 天，自前高回撤 "
                      f"{cycle['drawdown_from_close_ath'] * 100:.1f}%，顶后最低 "
                      f"{decimal_string(cycle['cycle_floor'], 8)}。"
                      "四年减半周期不适用于股票：相关规则已禁用（见 limitations）。")
    else:
        cycle_line = (f"周期：距减半 {cycle['days_since_halving']} 天，距顶部 {cycle['days_since_close_ath']} 天，"
                      f"回撤 {cycle['drawdown_from_close_ath'] * 100:.1f}%，顶后最低 {levels.cycle_floor}。")
    narrative_lines = [
        f"主判断：{PATH_LABELS[primary]}（规则权重 {weights[primary]}%）",
        f"一句话：{one_liner[primary]}",
        f"结构分 {structure:.0f}/100，动量分 {momentum:.0f}/100（50 为中性）",
        f"确认：日线收盘 > {levels.confirm_reclaim}（SMA20）→ 再看 {levels.confirm_breakout}；收上后者，回踩步骤作废。",
        f"回踩区：{levels.dip_zone_low}–{levels.dip_zone_high}（{dip_source}）",
        f"目标：{levels.target_1}" + (f" → {levels.target_2}" if levels.target_2 else ""),
        f"作废：日线收盘 < {levels.structural_support}（SMA200）；周线 < {levels.swing_low_20d} 两周收不回转 C。",
        cycle_line,
        "边界：权重为规则排序（非概率）；非投资建议；详见报告 limitations。",
    ]
    return OutlookReport(
        rules_version=RULES_VERSION,
        generated_at=datetime.now(UTC),
        market=market,
        symbol=symbol,
        instrument_id=instrument_id,
        data={**(data_note or {}), "context": context or None},
        structure_score=round(structure, 2),
        momentum_score=round(momentum, 2),
        structure_hits=structure_hits,
        momentum_hits=momentum_hits,
        paths=paths,
        primary=primary,
        levels=levels,
        triggers=triggers,
        narrative="\n".join(narrative_lines),
        limitations=limitations,
    )
