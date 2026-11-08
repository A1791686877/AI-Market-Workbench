"""Market dynamics & VPVR peak zone extractor.

Bridges the gap between static single-bar indicator snapshots and dynamic multi-bar
market reading required for both deterministic strategy execution and AI reasoning:
1. Aggregates 48-bin VPVR into concrete HVN (High Volume Node) peak zones and LVN vacuum zones.
2. Computes multi-bar indicator slopes, MACD golden/dead cross states, RSI momentum trajectories,
   and moving average alignment.
3. Summarizes 20-bar candle structure (swing highs/lows, upper/lower wick rejections, volume surges).
"""
from __future__ import annotations

from typing import Any

from app.divergence_audit import audit as audit_divergences
from app.fixtures import decimal_string
from app.indicators import (
    adx as adx_series,
    atr as atr_series,
    bollinger as bollinger_series,
    compute_divergences,
    compute_fvg,
    compute_support_resistance_and_trend,
    compute_vpvr,
    ema as ema_series,
    macd as macd_series,
    rsi as rsi_series,
    sma as sma_series,
)
from app.models import Bar, IndicatorBundle, VpvrResult


def _f(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _last_valid(values: list[float | None], count: int = 5) -> list[float]:
    valid = [float(v) for v in values if v is not None]
    return valid[-count:] if valid else []


def extract_vpvr_zones(vpvr: VpvrResult, current_price: float) -> dict[str, Any]:
    """Extract actionable HVN main peak zones and LVN vacuum zones from 48-bin VPVR."""
    bins = vpvr.bins
    if not bins:
        return {
            "poc": vpvr.poc,
            "vah": vpvr.vah,
            "val": vpvr.val,
            "current_position": vpvr.current_price_position.value,
            "in_main_peak_zone": False,
            "hvn_zones": [],
            "lvn_zones": [],
            "current_peak_zone": None,
            "next_upper_peak": None,
            "next_lower_peak": None,
        }

    total_vol = sum(float(b.allocated_volume) for b in bins) or 1.0
    poc_price = float(vpvr.poc)
    val_price = float(vpvr.val)
    vah_price = float(vpvr.vah)

    # Group contiguous or 1-bin-separated HVN / POC / top-volume bins into HVN peak clusters
    avg_bin_vol = total_vol / len(bins)
    peak_indices = [
        b.bin_index for b in bins
        if b.is_hvn or b.is_poc or float(b.allocated_volume) >= avg_bin_vol * 1.25
    ]

    clusters: list[list[int]] = []
    for idx in sorted(set(peak_indices)):
        if clusters and idx - clusters[-1][-1] <= 1:
            clusters[-1].append(idx)
        else:
            clusters.append([idx])

    hvn_zones: list[dict[str, Any]] = []
    for c_idx, cluster in enumerate(clusters):
        cluster_bins = [bins[i] for i in cluster]
        c_low = min(float(b.lower) for b in cluster_bins)
        c_high = max(float(b.upper) for b in cluster_bins)
        c_vol = sum(float(b.allocated_volume) for b in cluster_bins)
        # Volume-weighted center of the cluster
        weighted_center = (
            sum(((float(b.lower) + float(b.upper)) / 2.0) * float(b.allocated_volume) for b in cluster_bins) / c_vol
            if c_vol > 0 else (c_low + c_high) / 2.0
        )
        contains_poc = any(b.is_poc for b in cluster_bins)
        # Check if current price is inside or within 0.6% of this HVN zone
        margin = max((c_high - c_low) * 0.25, current_price * 0.005)
        if c_low - margin <= current_price <= c_high + margin:
            role = "CURRENT_PEAK"
        elif weighted_center < current_price:
            role = "SUPPORT_PEAK"
        else:
            role = "RESISTANCE_PEAK"

        hvn_zones.append({
            "zone_id": f"hvn_{c_idx + 1}",
            "lower": decimal_string(c_low, 4),
            "upper": decimal_string(c_high, 4),
            "center_price": decimal_string(weighted_center, 4),
            "center_float": round(weighted_center, 6),
            "volume_share_pct": round(c_vol / total_vol * 100.0, 2),
            "contains_poc": contains_poc,
            "role": role,
            "distance_pct": round((weighted_center / current_price - 1.0) * 100.0, 2) if current_price else 0.0,
        })

    # Sort by volume share descending to identify top peaks, and also keep price-ordered list
    top_hvn_by_vol = sorted(hvn_zones, key=lambda z: (-z["volume_share_pct"], abs(z["distance_pct"])))[:6]
    price_ordered_hvn = sorted(top_hvn_by_vol, key=lambda z: z["center_float"])

    # Extract LVN vacuum zones
    lvn_indices = [b.bin_index for b in bins if b.is_lvn]
    lvn_clusters: list[list[int]] = []
    for idx in sorted(set(lvn_indices)):
        if lvn_clusters and idx - lvn_clusters[-1][-1] <= 1:
            lvn_clusters[-1].append(idx)
        else:
            lvn_clusters.append([idx])

    lvn_zones: list[dict[str, Any]] = []
    for c_idx, cluster in enumerate(lvn_clusters):
        cluster_bins = [bins[i] for i in cluster]
        c_low = min(float(b.lower) for b in cluster_bins)
        c_high = max(float(b.upper) for b in cluster_bins)
        c_vol = sum(float(b.allocated_volume) for b in cluster_bins)
        lvn_zones.append({
            "zone_id": f"lvn_{c_idx + 1}",
            "lower": decimal_string(c_low, 4),
            "upper": decimal_string(c_high, 4),
            "midpoint": decimal_string((c_low + c_high) / 2.0, 4),
            "volume_share_pct": round(c_vol / total_vol * 100.0, 2),
        })

    current_peak = next((z for z in top_hvn_by_vol if z["role"] == "CURRENT_PEAK"), None)
    in_value_or_poc = (
        current_peak is not None
        or abs(current_price - poc_price) / max(current_price, 1e-9) <= 0.018
        or (val_price <= current_price <= poc_price * 1.01)
    )

    upper_peaks = [z for z in price_ordered_hvn if z["center_float"] > current_price * 1.003]
    lower_peaks = [z for z in reversed(price_ordered_hvn) if z["center_float"] < current_price * 0.997]

    # Fallback upper/lower peaks using VAH/VAL/POC if discrete HVN clusters are all on one side
    next_upper = upper_peaks[0] if upper_peaks else (
        {
            "zone_id": "vpvr_vah_target",
            "lower": decimal_string(max(poc_price, current_price * 1.005), 4),
            "upper": decimal_string(max(vah_price, current_price * 1.02), 4),
            "center_price": decimal_string(vah_price if vah_price > current_price else current_price * 1.03, 4),
            "center_float": round(vah_price if vah_price > current_price else current_price * 1.03, 6),
            "volume_share_pct": 15.0,
            "contains_poc": False,
            "role": "RESISTANCE_PEAK",
            "distance_pct": round(((vah_price if vah_price > current_price else current_price * 1.03) / current_price - 1.0) * 100.0, 2),
        }
    )
    second_upper = upper_peaks[1] if len(upper_peaks) > 1 else None

    next_lower = lower_peaks[0] if lower_peaks else (
        {
            "zone_id": "vpvr_val_support",
            "lower": decimal_string(min(val_price, current_price * 0.97), 4),
            "upper": decimal_string(min(poc_price, current_price * 0.99), 4),
            "center_price": decimal_string(val_price if val_price < current_price else current_price * 0.97, 4),
            "center_float": round(val_price if val_price < current_price else current_price * 0.97, 6),
            "volume_share_pct": 15.0,
            "contains_poc": False,
            "role": "SUPPORT_PEAK",
            "distance_pct": round(((val_price if val_price < current_price else current_price * 0.97) / current_price - 1.0) * 100.0, 2),
        }
    )

    return {
        "poc": vpvr.poc,
        "vah": vpvr.vah,
        "val": vpvr.val,
        "current_position": vpvr.current_price_position.value,
        "in_main_peak_zone": bool(in_value_or_poc),
        "hvn_zones": price_ordered_hvn,
        "lvn_zones": lvn_zones[:4],
        "current_peak_zone": current_peak,
        "next_upper_peak": next_upper,
        "second_upper_peak": second_upper,
        "next_lower_peak": next_lower,
    }


def compute_indicator_dynamics(bars: list[Bar], bundle: IndicatorBundle | None = None) -> dict[str, Any]:
    """Compute multi-bar slopes, cross states, and price action trajectory from confirmed bars."""
    if not bars:
        return {}
    ordered = sorted(bars, key=lambda b: b.close_at)
    closes = [float(b.close) for b in ordered]
    highs = [float(b.high) for b in ordered]
    lows = [float(b.low) for b in ordered]
    volumes = [float(b.volume) for b in ordered]
    price = closes[-1]

    # Moving averages
    sma20_list = sma_series(closes, 20)
    ema20_list = ema_series(closes, 20)
    sma50_list = sma_series(closes, 50)
    sma200_list = sma_series(closes, 200)

    sma20_vals = _last_valid(sma20_list, 6)
    ema20_vals = _last_valid(ema20_list, 6)
    sma50_vals = _last_valid(sma50_list, 6)
    sma200_vals = _last_valid(sma200_list, 21)

    sma20_now = sma20_vals[-1] if sma20_vals else None
    ema20_now = ema20_vals[-1] if ema20_vals else None
    sma50_now = sma50_vals[-1] if sma50_vals else None
    sma200_now = sma200_vals[-1] if sma200_vals else None

    sma20_slope = (sma20_vals[-1] - sma20_vals[0]) / max(abs(sma20_vals[0]), 1e-9) * 100 if len(sma20_vals) >= 2 else 0.0
    sma200_slope = (sma200_vals[-1] - sma200_vals[0]) / max(abs(sma200_vals[0]), 1e-9) * 100 if len(sma200_vals) >= 2 else 0.0

    if sma20_now and sma50_now and sma200_now:
        if price > sma20_now > sma50_now > sma200_now:
            ma_alignment = "BULL_ALIGNMENT"
        elif price < sma20_now < sma50_now < sma200_now:
            ma_alignment = "BEAR_ALIGNMENT"
        elif price > sma200_now:
            ma_alignment = "ABOVE_200_PULLBACK_OR_CONSOLIDATION"
        else:
            ma_alignment = "BELOW_200_REBOUND_OR_WEAK"
    else:
        ma_alignment = "PARTIAL_MA"

    # MACD dynamics
    macd_line, sig_line, hist_line = macd_series(closes)
    dif_vals = _last_valid(macd_line, 5)
    dea_vals = _last_valid(sig_line, 5)
    hist_vals = _last_valid(hist_line, 5)

    dif_now = dif_vals[-1] if dif_vals else None
    dea_now = dea_vals[-1] if dea_vals else None
    hist_now = hist_vals[-1] if hist_vals else None
    hist_prev = hist_vals[-2] if len(hist_vals) >= 2 else None
    hist_prev3 = hist_vals[0] if len(hist_vals) >= 3 else hist_prev

    macd_rising = False
    if hist_now is not None and hist_prev is not None:
        macd_rising = (hist_now > hist_prev) or (dif_now is not None and len(dif_vals) >= 2 and dif_now > dif_vals[-2] and hist_now > 0)

    if hist_now is not None and hist_prev is not None:
        if hist_now > 0 and hist_now >= hist_prev:
            hist_state = "EXPANDING_BULL"
        elif hist_now > 0 and hist_now < hist_prev:
            hist_state = "SHRINKING_BULL"
        elif hist_now <= 0 and hist_now > hist_prev:
            hist_state = "SHRINKING_BEAR"
        else:
            hist_state = "EXPANDING_BEAR"
    else:
        hist_state = "INSUFFICIENT"

    cross_state = "NONE"
    if len(hist_vals) >= 3:
        if hist_vals[-1] > 0 and any(h <= 0 for h in hist_vals[:-1]):
            cross_state = "GOLDEN_CROSS_RECENT"
        elif hist_vals[-1] < 0 and any(h >= 0 for h in hist_vals[:-1]):
            cross_state = "DEAD_CROSS_RECENT"
        elif hist_vals[-1] > 0:
            cross_state = "BULL_ALIGNED"
        else:
            cross_state = "BEAR_ALIGNED"

    zero_axis = "ABOVE_ZERO" if (dif_now or 0) > 0 and (dea_now or 0) > 0 else "BELOW_ZERO" if (dif_now or 0) < 0 and (dea_now or 0) < 0 else "CROSSING_ZERO"

    # RSI dynamics
    rsi_list = rsi_series(closes, 14)
    rsi_vals = _last_valid(rsi_list, 5)
    rsi_now = rsi_vals[-1] if rsi_vals else None
    rsi_delta5 = (rsi_vals[-1] - rsi_vals[0]) if len(rsi_vals) >= 2 else 0.0
    if rsi_now is None:
        rsi_zone = "UNKNOWN"
    elif rsi_now >= 70:
        rsi_zone = "OVERBOUGHT"
    elif rsi_now <= 30:
        rsi_zone = "OVERSOLD"
    elif rsi_now >= 50:
        rsi_zone = "BULLISH_NEUTRAL"
    else:
        rsi_zone = "WEAK_NEUTRAL"

    # ADX & ATR
    adx_list, plus_di_list, minus_di_list = adx_series(ordered, 14)
    adx_vals = _last_valid(adx_list, 3)
    plus_vals = _last_valid(plus_di_list, 3)
    minus_vals = _last_valid(minus_di_list, 3)
    atr_vals = _last_valid(atr_series(ordered, 14), 3)

    # Bollinger Bands
    bb_mid, bb_up, bb_lo = bollinger_series(closes, 20, 2.0)
    bb_width_pct = (
        (float(bb_up[-1]) - float(bb_lo[-1])) / float(bb_mid[-1]) * 100.0
        if bb_up[-1] is not None and bb_lo[-1] is not None and bb_mid[-1]
        else None
    )

    # VPVR & Divergences
    vpvr = bundle.vpvr if bundle else compute_vpvr(ordered)
    vpvr_zones = extract_vpvr_zones(vpvr, price)
    divs = bundle.divergences if bundle else compute_divergences(ordered)
    div_audit = audit_divergences(divs, ordered, ordered[-1].interval.value)
    _, trend = (bundle.support_resistance, bundle.trend_structure) if bundle else compute_support_resistance_and_trend(ordered)

    # 20-bar trajectory summary
    recent20 = ordered[-min(20, len(ordered)):]
    high20 = max(float(b.high) for b in recent20)
    low20 = min(float(b.low) for b in recent20)
    ret5 = (closes[-1] / closes[-min(6, len(closes))] - 1.0) * 100.0 if len(closes) >= 2 else 0.0
    ret20 = (closes[-1] / float(recent20[0].open) - 1.0) * 100.0 if recent20 else 0.0
    vol_avg20 = sum(volumes[-20:]) / min(20, len(volumes)) if volumes else 1.0
    last_vol_ratio = volumes[-1] / vol_avg20 if vol_avg20 > 0 else 1.0

    last_bar = ordered[-1]
    b_open, b_high, b_low, b_close = float(last_bar.open), float(last_bar.high), float(last_bar.low), float(last_bar.close)
    b_range = max(b_high - b_low, 1e-9)
    upper_wick_ratio = (b_high - max(b_open, b_close)) / b_range
    lower_wick_ratio = (min(b_open, b_close) - b_low) / b_range

    candle_note = "普通实体柱"
    if lower_wick_ratio >= 0.45 and b_close >= b_open:
        candle_note = "长下影探底收阳（下方承接较强）"
    elif lower_wick_ratio >= 0.45:
        candle_note = "长下影探底线"
    elif upper_wick_ratio >= 0.45 and b_close <= b_open:
        candle_note = "长上影受阻收阴（上方抛压明显）"
    elif upper_wick_ratio >= 0.45:
        candle_note = "长上影试盘线"
    elif abs(b_close - b_open) / b_range >= 0.75:
        candle_note = "饱满大阳线突破" if b_close > b_open else "饱满大阴线杀跌"

    return {
        "interval": ordered[-1].interval.value,
        "as_of": ordered[-1].close_at.isoformat(),
        "last_close": decimal_string(price, 4),
        "trend_state": trend.state.value,
        "ma": {
            "sma20": None if sma20_now is None else round(sma20_now, 4),
            "ema20": None if ema20_now is None else round(ema20_now, 4),
            "sma50": None if sma50_now is None else round(sma50_now, 4),
            "sma200": None if sma200_now is None else round(sma200_now, 4),
            "sma20_slope_5b_pct": round(sma20_slope, 3),
            "sma200_slope_20b_pct": round(sma200_slope, 3),
            "alignment": ma_alignment,
        },
        "macd": {
            "dif": None if dif_now is None else round(dif_now, 4),
            "dea": None if dea_now is None else round(dea_now, 4),
            "histogram": None if hist_now is None else round(hist_now, 4),
            "hist_prev": None if hist_prev is None else round(hist_prev, 4),
            "hist_prev3": None if hist_prev3 is None else round(hist_prev3, 4),
            "hist_state": hist_state,
            "cross_state": cross_state,
            "zero_axis": zero_axis,
            "macd_rising": macd_rising,
        },
        "rsi": {
            "value": None if rsi_now is None else round(rsi_now, 2),
            "delta_5b": round(rsi_delta5, 2),
            "zone": rsi_zone,
            "not_overbought": bool(rsi_now is not None and rsi_now < 70.0),
        },
        "adx_atr": {
            "adx": round(adx_vals[-1], 2) if adx_vals else None,
            "plus_di": round(plus_vals[-1], 2) if plus_vals else None,
            "minus_di": round(minus_vals[-1], 2) if minus_vals else None,
            "atr14": round(atr_vals[-1], 4) if atr_vals else None,
            "bb_width_pct": round(bb_width_pct, 2) if bb_width_pct is not None else None,
        },
        "trajectory_20b": {
            "high_20b": decimal_string(high20, 4),
            "low_20b": decimal_string(low20, 4),
            "return_5b_pct": round(ret5, 2),
            "return_20b_pct": round(ret20, 2),
            "last_volume_ratio_20b": round(last_vol_ratio, 2),
            "last_candle_pattern": candle_note,
        },
        "vpvr_zones": vpvr_zones,
        "divergence_audit": div_audit,
    }
