"""Divergence audit: which confirmed divergences are still worth caring about right now.

The engine reports *confirmed* 3x3 pivot divergences (no repaint). That alone answers nothing about
today: a five-year daily window contains 20-30 bearish divergences for any liquid name, so "there is
a top divergence" is noise. What matters is:

1. **recency** — how many bars since the divergence's second price anchor;
2. **whether price already exceeded that anchor's extreme** — a regular bearish divergence whose
   second high has since been broken has been *retired* by the market, not confirmed;
3. **how many oscillators agree** (RSI line and/or MACD histogram).

This module turns those three questions into a flag plus a short human sentence, and is used by
`scripts/divergence_check.py` and by the equity outlook ranking. It never predicts: it reports what
the confirmed patterns still say and what has already been invalidated.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Literal

from statistics import pstdev
from app.indicators import _pivots, atr, macd, rsi
from app.models import Bar, DivergenceResult, OscillatorComponent

BEARISH_KINDS = ("REGULAR_BEARISH", "HIDDEN_BEARISH")
BULLISH_KINDS = ("REGULAR_BULLISH", "HIDDEN_BULLISH")
DEFAULT_RELEVANCE_BARS = {"1d": 20, "15m": 60, "1h": 36, "4h": 24, "1w": 8, "1mo": 6}
PRESSURE_KINDS: dict[str, Literal["TOP", "BOTTOM"]] = {
    "REGULAR_BEARISH": "TOP", "HIDDEN_BEARISH": "TOP",
    "REGULAR_BULLISH": "BOTTOM", "HIDDEN_BULLISH": "BOTTOM",
}


def _f(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError, InvalidOperation):
        return None


@dataclass(frozen=True)
class AuditedDivergence:
    kind: str
    pressure: Literal["TOP", "BOTTOM"]
    oscillator: str
    first_anchor_at: str
    second_anchor_at: str
    first_price: str
    second_price: str
    first_oscillator: str
    second_oscillator: str
    bars_since_second_anchor: int
    signal_at: str
    retired: bool          # price already exceeded the second anchor's extreme
    recent: bool
    mattered: bool         # recent AND not retired → this one is still worth mentioning

    def sentence(self) -> str:
        label = "顶背离" if self.pressure == "TOP" else "底背离"
        state = "已被价格否定" if self.retired else "尚未被否定"
        when = f"{self.bars_since_second_anchor} 根前"
        return (f"{label}({self.kind}/{self.oscillator}) {when}，第二锚 {self.second_price}（{state}）")


def describe(divergence: DivergenceResult, bars: list[Bar], relevance_bars: int) -> AuditedDivergence:
    ordered = sorted(bars, key=lambda bar: bar.close_at)
    price_anchors = divergence.price_anchors
    oscillator_anchors = divergence.oscillator_anchors
    second_price = _f(price_anchors[1].value)
    after = [bar for bar in ordered if bar.close_at > price_anchors[1].at]
    retired = False
    if second_price is not None and after:
        if divergence.kind.value in BEARISH_KINDS:
            retired = max(float(bar.high) for bar in after) > second_price
        else:
            retired = min(float(bar.low) for bar in after) < second_price
    bars_since = len(after)
    recent = bars_since <= relevance_bars
    return AuditedDivergence(
        kind=divergence.kind.value,
        pressure=PRESSURE_KINDS[divergence.kind.value],
        oscillator=divergence.oscillator_component.value,
        first_anchor_at=price_anchors[0].at.isoformat(),
        second_anchor_at=price_anchors[1].at.isoformat(),
        first_price=price_anchors[0].value,
        second_price=price_anchors[1].value,
        first_oscillator=oscillator_anchors[0].value,
        second_oscillator=oscillator_anchors[1].value,
        bars_since_second_anchor=bars_since,
        signal_at=divergence.signal_at.isoformat(),
        retired=retired,
        recent=recent,
        mattered=recent and not retired,
    )


def detect_forming_divergences(bars: list[Bar]) -> list[dict[str, Any]]:
    """Detect right-1 or right-2 forming pivot divergences inside the latest 3 bars.

    Confirmed 3x3 pivots require 3 bars to the right, creating a 3-bar blind spot at the
    right edge. Here we check pivots with left=3, right=1 where the second anchor falls in
    the last 3 bars (so it is not yet a 3x3 confirmed divergence) and price has already
    turned for 1–2 bars without breaching the second anchor extreme.
    """
    if len(bars) < 20:
        return []
    ordered = sorted(bars, key=lambda bar: bar.close_at)
    closes = [float(bar.close) for bar in ordered]
    rsi_values = rsi(closes, 14)
    _, _, histogram = macd(closes)
    atr_values = atr(ordered, 14)
    forming: list[dict[str, Any]] = []
    components = [
        (OscillatorComponent.RSI_LINE, rsi_values),
        (OscillatorComponent.MACD_HISTOGRAM, histogram),
    ]
    n = len(ordered)
    for component, oscillator in components:
        for use_high in (False, True):
            price_values = [float(bar.high if use_high else bar.low) for bar in ordered]
            # First anchor is a fully confirmed 3x3 pivot; candidate second anchor is a 3x1 pivot near the right edge
            confirmed_pivots = _pivots(price_values, left=3, right=3, high=use_high)
            candidate_pivots = [
                idx for idx in _pivots(price_values, left=3, right=1, high=use_high)
                if idx >= n - 3 and idx not in confirmed_pivots
            ]
            osc_candidates = _pivots(oscillator, left=2, right=1, high=use_high)
            if not confirmed_pivots or not candidate_pivots:
                continue
            for second_index in candidate_pivots:
                prior_pivots = [idx for idx in confirmed_pivots if 5 <= second_index - idx <= 60]
                if not prior_pivots or atr_values[second_index] is None:
                    continue
                first_index = prior_pivots[-1]
                matches = []
                for target in (first_index, second_index):
                    nearby = [idx for idx in osc_candidates if abs(idx - target) <= 2]
                    if not nearby:
                        matches = []
                        break
                    matches.append(min(nearby, key=lambda idx: (abs(idx - target), idx)))
                if len(matches) != 2:
                    continue
                p1, p2 = price_values[first_index], price_values[second_index]
                o1, o2 = float(oscillator[matches[0]]), float(oscillator[matches[1]])
                if abs(p2 - p1) < 0.1 * float(atr_values[second_index]):
                    continue
                valid_osc = [float(v) for v in oscillator[max(0, second_index - 59): second_index + 1] if v is not None]
                threshold = 2.5 if component is OscillatorComponent.RSI_LINE else 0.08 * (pstdev(valid_osc) if len(valid_osc) > 2 else 999.0)
                if abs(o2 - o1) < threshold:
                    continue
                kind: str | None = None
                if not use_high and p2 < p1 and o2 > o1:
                    kind = "REGULAR_BULLISH"
                elif not use_high and p2 > p1 and o2 < o1:
                    kind = "HIDDEN_BULLISH"
                elif use_high and p2 > p1 and o2 < o1:
                    kind = "REGULAR_BEARISH"
                elif use_high and p2 < p1 and o2 > o1:
                    kind = "HIDDEN_BEARISH"
                if kind is None:
                    continue
                # Verify subsequent 1-2 bars haven't retired the candidate anchor
                after = ordered[second_index + 1:]
                if use_high and any(float(b.high) > p2 for b in after):
                    continue
                if not use_high and any(float(b.low) < p2 for b in after):
                    continue
                pressure = "TOP" if use_high else "BOTTOM"
                right_bars = n - 1 - second_index
                forming.append({
                    "kind": kind,
                    "pressure": pressure,
                    "oscillator": component.value,
                    "first_anchor_at": ordered[first_index].close_at.isoformat(),
                    "second_anchor_at": ordered[second_index].close_at.isoformat(),
                    "first_price": f"{p1:.4f}".rstrip("0").rstrip("."),
                    "second_price": f"{p2:.4f}".rstrip("0").rstrip("."),
                    "first_oscillator": f"{o1:.4f}",
                    "second_oscillator": f"{o2:.4f}",
                    "right_bars_confirmed": right_bars,
                    "right_bars_needed_for_3x3": max(0, 3 - right_bars),
                    "status": "FORMING_CANDIDATE",
                })
    return forming


def audit(divergences: Iterable[DivergenceResult], bars: list[Bar], interval_name: str) -> dict[str, Any]:
    relevance = DEFAULT_RELEVANCE_BARS.get(interval_name, 20)
    audited = sorted((describe(item, bars, relevance) for item in divergences),
                     key=lambda item: item.second_anchor_at)
    counts: dict[str, int] = {}
    for item in audited:
        counts[item.kind] = counts.get(item.kind, 0) + 1
    top = [item for item in audited if item.pressure == "TOP"]
    bottom = [item for item in audited if item.pressure == "BOTTOM"]
    live_top = [item for item in top if item.mattered]
    live_bottom = [item for item in bottom if item.mattered]

    def fetch(items: list[AuditedDivergence], flag: str) -> list[dict]:
        return [{**item.__dict__, "flag": flag} for item in items[-2:][::-1]]

    # Two oscillators on the same second anchor = stronger evidence; report it in the sentence.
    live_top_sentence = None
    if live_top:
        latest_anchor = live_top[-1].second_anchor_at
        same_anchor = [item for item in live_top if item.second_anchor_at == latest_anchor]
        oscillators = "+".join(sorted({item.oscillator for item in same_anchor}))
        live_top_sentence = (f"{len(same_anchor)} 个未失效顶背离（{oscillators}），"
                             f"{same_anchor[0].bars_since_second_anchor} 根前，压制位 {same_anchor[0].second_price}")

    live_bottom_sentence = None
    if live_bottom:
        latest_anchor = live_bottom[-1].second_anchor_at
        same_anchor = [item for item in live_bottom if item.second_anchor_at == latest_anchor]
        oscillators = "+".join(sorted({item.oscillator for item in same_anchor}))
        live_bottom_sentence = (f"{len(same_anchor)} 个未失效底背离（{oscillators}），"
                                f"{same_anchor[0].bars_since_second_anchor} 根前，支撑锚 {same_anchor[0].second_price}")

    forming = detect_forming_divergences(bars)
    forming_top = [item for item in forming if item["pressure"] == "TOP"]
    forming_bottom = [item for item in forming if item["pressure"] == "BOTTOM"]
    forming_sentence = None
    if forming_bottom or forming_top:
        parts = []
        if forming_bottom:
            oscs = "+".join(sorted({x["oscillator"] for x in forming_bottom}))
            parts.append(f"正在形成底背离候选({oscs}，右侧已确认{forming_bottom[-1]['right_bars_confirmed']}/3根，锚点 {forming_bottom[-1]['second_price']})")
        if forming_top:
            oscs = "+".join(sorted({x["oscillator"] for x in forming_top}))
            parts.append(f"正在形成顶背离候选({oscs}，右侧已确认{forming_top[-1]['right_bars_confirmed']}/3根，锚点 {forming_top[-1]['second_price']})")
        forming_sentence = "；".join(parts)

    return {
        "interval": interval_name,
        "relevance_bars": relevance,
        "counts": counts,
        "retired_top": sum(1 for item in top if item.retired),
        "retired_bottom": sum(1 for item in bottom if item.retired),
        "live_top": fetch(live_top, "LIVE_TOP"),
        "live_bottom": fetch(live_bottom, "LIVE_BOTTOM"),
        "most_recent_top": fetch(top, "RECENT_TOP"),
        "most_recent_bottom": fetch(bottom, "RECENT_BOTTOM"),
        "live_top_sentence": live_top_sentence,
        "live_bottom_sentence": live_bottom_sentence,
        "forming_top": forming_top,
        "forming_bottom": forming_bottom,
        "forming_sentence": forming_sentence,
        "caveats": [
            "引擎只报已确认的 3×3 枢轴背离：最新 3 根内“正在形成”的背离永远不会出现在这里（不重绘的代价）；新增 forming_* 单独提示 3×1 右侧候选。",
            "顶背离只在“距今 ≤ %d 根 且 第二锚未被价格突破”时才算还有压制力；被突破的算已失效。" % relevance,
            "背离是历史已确认形态，不是实时信号；不构成买卖建议。",
        ],
    }

