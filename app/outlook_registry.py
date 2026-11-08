"""Conclusion registry + post-hoc verification for the rule-based outlook engine.

Design notes
------------
* Every recorded conclusion is frozen: its levels, triggers, weights and the
  fingerprint of the exact bar set that produced it. The registry is append-only
  JSONL so a conclusion can never be silently rewritten after the fact.
* Verification re-reads *only* bars after the frozen bar and classifies which of
  the three paths actually happened. The rule is "first confirmation wins" (the
  frozen plan asks what happens next, so the earliest confirmation decides), with
  an explicit same-bar precedence of C > B > A. Both facts are printed in the
  report so the classification is reproducible.
* The scoreboard reports raw counts. It is deliberately never converted into a
  "hit rate": the sample is small, the paths are a ranking rather than a
  probability, and no out-of-sample calibration has been done.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from app.config import settings
from app.models import Bar, StrictModel
from app.outlook import OutlookReport

REGISTRY_VERSION = "outlook-registry-v1"
HORIZON_DAYS = {"21d": 21, "63d": 63, "126d": 126}
UNRESOLVED_AFTER_BARS = HORIZON_DAYS["126d"]

REALIZATION_PRECEDENCE = (
    "先到先判：冻结日之后第一根满足条件的日线决定路径，不回看、不重排。",
    "同一根 K 线内优先级 C > B > A：",
    "C: 日线收盘 < 冻结时 SMA200（结构失效）",
    "B: 未先触及回踩区，日线收盘 > 冻结时近 3 日高点（直接上攻）",
    "A: 先触及回踩区（最低价进入回踩区上沿），之后日线收盘 > 冻结时 SMA20（回踩后再上）",
    "UNRESOLVED: 126 个交易日内三条都未成立",
)


class OutlookVerification(StrictModel):
    verified_at: datetime
    bars_observed: int
    first_bar_at: datetime | None = None
    last_bar_at: datetime | None = None
    path_realized: Literal["A", "B", "C", "UNRESOLVED"]
    resolved_at: datetime | None = None
    resolved_close: str | None = None
    dip_zone_touched_at: datetime | None = None
    confirm_reclaim_at: datetime | None = None
    confirm_breakout_at: datetime | None = None
    invalidate_at: datetime | None = None
    target_1_hit_at: datetime | None = None
    max_close: str | None = None
    min_low: str | None = None
    final_close: str | None = None
    return_pct: float | None = None
    horizon_returns: dict[str, float | None]
    horizon_expired: bool = False
    primary_correct: bool | None = None
    notes: list[str]


class OutlookRegistryEntry(StrictModel):
    registry_version: Literal["outlook-registry-v1"] = REGISTRY_VERSION
    outlook_id: str
    recorded_at: datetime
    rules_version: str
    market: Literal["CRYPTO", "EQUITY"]
    # FORWARD = frozen before the outcome existed (the only unbiased sample).
    # REPLAY  = frozen on a past date from data that already existed (fast, but in-sample and
    #           overlapping: several symbols on one date are one market state).
    origin: Literal["FORWARD", "REPLAY"] = "FORWARD"
    symbol: str
    instrument_id: str
    as_of: datetime
    price: str
    primary: Literal["A", "B", "C"]
    paths: list[dict[str, Any]]
    structure_score: float
    momentum_score: float
    levels: dict[str, str | None]
    triggers: dict[str, str | None]
    bars_used: int
    data_fingerprint: str
    verification: OutlookVerification | None = None


def registry_path() -> Path:
    return settings.root / "data" / "outlook" / "registry.jsonl"


def data_fingerprint(bars: list[Bar]) -> str:
    payload = [{"bar_id": b.bar_id, "open_at": b.open_at.isoformat(), "close_at": b.close_at.isoformat(),
                "open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume,
                "price_basis": b.price_basis.value} for b in bars]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def entry_from_report(report: OutlookReport, bars: list[Bar], *, recorded_at: datetime | None = None,
                      origin: str = "FORWARD") -> OutlookRegistryEntry:
    return OutlookRegistryEntry(
        outlook_id=f"outlook-{uuid.uuid4().hex}",
        recorded_at=recorded_at or datetime.now(UTC),
        rules_version=report.rules_version,
        market=report.market,
        origin=origin,
        symbol=report.symbol,
        instrument_id=report.instrument_id,
        as_of=bars[-1].close_at,
        price=report.levels.price or bars[-1].close,
        primary=report.primary,
        paths=[{"key": p.key, "label": p.label, "score": p.score, "weight_pct": p.weight_pct} for p in report.paths],
        structure_score=report.structure_score,
        momentum_score=report.momentum_score,
        levels=report.levels.model_dump(),
        triggers=dict(report.triggers),
        bars_used=len(bars),
        data_fingerprint=data_fingerprint(bars),
    )


def load_entries(path: Path | None = None) -> list[OutlookRegistryEntry]:
    target = path or registry_path()
    if not target.exists():
        return []
    entries: list[OutlookRegistryEntry] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entries.append(OutlookRegistryEntry.model_validate_json(line))
    return entries


def save_entries(entries: list[OutlookRegistryEntry], path: Path | None = None) -> Path:
    """Atomic rewrite: a crash mid-write must not corrupt the registry."""
    target = path or registry_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(entry.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n"
                      for entry in entries)
    temporary = target.with_suffix(".jsonl.tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, target)
    return target


def append_entries(new_entries: list[OutlookRegistryEntry], path: Path | None = None) -> list[OutlookRegistryEntry]:
    """Append-only record. Identical (instrument, as_of, rules_version, primary) rows are de-duplicated."""
    existing = load_entries(path)
    keys = {(e.instrument_id, e.as_of, e.rules_version, e.primary) for e in existing}
    added = []
    for entry in new_entries:
        key = (entry.instrument_id, entry.as_of, entry.rules_version, entry.primary)
        if key in keys:
            continue
        keys.add(key)
        added.append(entry)
    if not added:
        return []
    target = path or registry_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        for entry in added:
            handle.write(json.dumps(entry.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n")
    return added


def _price(value: str | None) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def verify_entry(entry: OutlookRegistryEntry, bars_after: list[Bar], *,
                 verified_at: datetime | None = None) -> OutlookVerification:
    """Classify the realized path for one frozen conclusion (pure function).

    Only *confirmed* bars after the freeze date count: an unclosed candle must never decide a verdict.
    """
    now = verified_at or datetime.now(UTC)
    notes: list[str] = []
    ordered = [b for b in bars_after if b.confirmed and b.close_at > entry.as_of]
    ordered.sort(key=lambda b: b.close_at)
    support = _price(entry.levels.get("structural_support"))
    breakout = _price(entry.levels.get("confirm_breakout"))
    reclaim = _price(entry.levels.get("confirm_reclaim"))
    dip_low = _price(entry.levels.get("dip_zone_low"))
    dip_high = _price(entry.levels.get("dip_zone_high"))
    target_1 = _price(entry.levels.get("target_1"))
    price_at_record = _price(entry.price)

    if dip_high is None or dip_low is None:
        notes.append("no dip zone was frozen; A/B cannot be separated by a retest, only C and B are evaluated")
    elif price_at_record is not None and dip_high > price_at_record:
        notes.append("frozen dip zone sits at/above the frozen price; dip-touch is treated as already satisfied for A")

    dip_touched: datetime | None = None
    reclaim_at: datetime | None = None
    breakout_at: datetime | None = None
    invalidate_at: datetime | None = None
    target_hit: datetime | None = None
    path: str | None = None
    resolved_at: datetime | None = None
    resolved_close: str | None = None

    for bar in ordered:
        close, low, high = float(bar.close), float(bar.low), float(bar.high)
        if dip_touched is None and dip_high is not None and dip_low is not None:
            # Touching the zone means the low reached the zone top; a deeper dive still counts.
            if low <= dip_high and (price_at_record is None or dip_high <= price_at_record):
                dip_touched = bar.close_at
        if target_hit is None and target_1 is not None and high >= target_1:
            target_hit = bar.close_at
        if support is not None and close < support:
            path, invalidate_at, resolved_at, resolved_close = "C", bar.close_at, bar.close_at, bar.close
        elif breakout is not None and close > breakout and dip_touched is None:
            path, breakout_at, resolved_at, resolved_close = "B", bar.close_at, bar.close_at, bar.close
        elif reclaim is not None and close > reclaim and dip_touched is not None:
            path, reclaim_at, resolved_at, resolved_close = "A", bar.close_at, bar.close_at, bar.close
        if path is not None:
            break

    if path is None and len(ordered) >= UNRESOLVED_AFTER_BARS:
        notes.append(f"no path confirmed within {UNRESOLVED_AFTER_BARS} trading days")
    if not ordered:
        notes.append("no bars after the freeze date yet; nothing has been observed")

    horizon_returns: dict[str, float | None] = {}
    base = _price(entry.price)
    for name, days in HORIZON_DAYS.items():
        horizon_returns[name] = (float(ordered[days - 1].close) / base - 1) if base and len(ordered) >= days else None

    realized = path or "UNRESOLVED"
    primary_correct = None if realized == "UNRESOLVED" else (realized == entry.primary)
    return OutlookVerification(
        verified_at=now,
        bars_observed=len(ordered),
        first_bar_at=ordered[0].close_at if ordered else None,
        last_bar_at=ordered[-1].close_at if ordered else None,
        path_realized=realized,
        resolved_at=resolved_at,
        resolved_close=resolved_close,
        dip_zone_touched_at=dip_touched,
        confirm_reclaim_at=reclaim_at,
        confirm_breakout_at=breakout_at,
        invalidate_at=invalidate_at,
        target_1_hit_at=target_hit,
        max_close=str(max((float(b.close) for b in ordered), default=None)) if ordered else None,
        min_low=str(min((float(b.low) for b in ordered), default=None)) if ordered else None,
        final_close=ordered[-1].close if ordered else None,
        return_pct=(float(ordered[-1].close) / base - 1) if base and ordered else None,
        horizon_returns=horizon_returns,
        horizon_expired=path is None and len(ordered) >= UNRESOLVED_AFTER_BARS,
        primary_correct=primary_correct,
        notes=notes,
    )


def verify_entries(entries: list[OutlookRegistryEntry], bars_by_instrument: dict[str, list[Bar]], *,
                   verified_at: datetime | None = None) -> tuple[list[OutlookRegistryEntry], list[str]]:
    """Re-verify every entry that has new bars. Returns (updated entries, skipped notes).

    An entry with no bar after its freeze date stays OPEN (verification=None): recording
    an empty observation as a verdict would misrepresent an unstarted conclusion.
    """
    skipped: list[str] = []
    updated: list[OutlookRegistryEntry] = []
    for entry in entries:
        bars = bars_by_instrument.get(entry.instrument_id)
        if bars is None:
            skipped.append(f"{entry.outlook_id}: no bar source available for {entry.instrument_id}")
            updated.append(entry)
            continue
        verification = verify_entry(entry, bars, verified_at=verified_at)
        if verification.bars_observed == 0:
            skipped.append(f"{entry.outlook_id}: no bars after {entry.as_of.date()} yet; stays OPEN")
            updated.append(entry)
            continue
        already = entry.verification
        if already is not None and already.bars_observed >= verification.bars_observed:
            updated.append(entry)
            continue
        updated.append(entry.model_copy(update={"verification": verification}))
    return updated, skipped


def scoreboard(entries: list[OutlookRegistryEntry]) -> dict[str, Any]:
    """Descriptive counts only — never a hit rate (see module docstring).

    Forward-frozen and replayed samples are reported separately: replaying history can inflate
    the count quickly, but those rows are in-sample and overlapping, so merging the two into one
    number would manufacture confidence that the evidence does not support.
    """

    def empty_bucket() -> dict[str, int]:
        return {"entries": 0, "A": 0, "B": 0, "C": 0, "UNRESOLVED": 0, "OPEN": 0,
                "primary_correct": 0, "primary_incorrect": 0, "undetermined": 0,
                "distinct_freeze_dates": 0}

    counts = {"A": 0, "B": 0, "C": 0, "UNRESOLVED": 0}
    correct = incorrect = 0
    by_market: dict[str, dict[str, int]] = {}
    by_origin: dict[str, dict[str, int]] = {}
    freeze_dates: dict[str, set[str]] = {}
    for entry in entries:
        market = by_market.setdefault(entry.market, empty_bucket())
        origin = by_origin.setdefault(entry.origin, empty_bucket())
        freeze_dates.setdefault(entry.origin, set()).add(entry.as_of.date().isoformat())
        market["entries"] += 1
        origin["entries"] += 1
        if entry.verification is None:
            market["OPEN"] += 1
            origin["OPEN"] += 1
            continue
        realized = entry.verification.path_realized
        counts[realized] += 1
        market[realized] += 1
        origin[realized] += 1
        if entry.verification.primary_correct is True:
            correct += 1
            market["primary_correct"] += 1
            origin["primary_correct"] += 1
        elif entry.verification.primary_correct is False:
            incorrect += 1
            market["primary_incorrect"] += 1
            origin["primary_incorrect"] += 1
        else:
            market["undetermined"] += 1
            origin["undetermined"] += 1
    for origin, dates in freeze_dates.items():
        by_origin[origin]["distinct_freeze_dates"] = len(dates)
    return {
        "registry_version": REGISTRY_VERSION,
        "entries": len(entries),
        "verified": sum(1 for e in entries if e.verification is not None),
        "open": sum(1 for e in entries if e.verification is None),
        "realized_counts": counts,
        "primary_correct": correct,
        "primary_incorrect": incorrect,
        "undetermined": sum(1 for e in entries if e.verification is not None
                            and e.verification.primary_correct is None),
        "by_market": by_market,
        "by_origin": by_origin,
        "realization_precedence": list(REALIZATION_PRECEDENCE),
        "caveats": [
            "这些是事后计数，不是胜率：样本量小、路径权重是规则排序而非校准概率。",
            "验证只用冻结日之后的已确认日线；已登记结论不被改写（registry 只追加，验证结果单独覆盖）。",
            "路径判定为「先到先判」，同根 K 线内优先级 C > B > A；126 个交易日内未成立记为 UNRESOLVED。",
            "FORWARD（下达结论时结果还不存在）与 REPLAY（用历史数据回放）必须分开看：回放能快速堆数量，"
            "但同一天的多只标的是同一个市场状态、且规则本身来自这段历史，属于样本内且高度重叠。",
            "distinct_freeze_dates 才是回放样本的粗略独立度；把同一冻结日的 8 个标的当 8 个独立样本会高估显著性。",
        ],
    }


def rank_closest_to_path_a(entries: list[OutlookRegistryEntry]) -> list[dict[str, Any]]:
    """Rank conclusions by how close they are to the 'A' (dip-then-reclaim) path."""
    rows = []
    for entry in entries:
        weights = {p["key"]: p["weight_pct"] for p in entry.paths}
        rows.append({
            "outlook_id": entry.outlook_id,
            "market": entry.market,
            "symbol": entry.symbol,
            "as_of": entry.as_of.isoformat(),
            "primary": entry.primary,
            "a_weight": weights.get("A", 0),
            "b_weight": weights.get("B", 0),
            "c_weight": weights.get("C", 0),
            "structure_score": entry.structure_score,
            "momentum_score": entry.momentum_score,
            "price": entry.price,
            "dip_zone": [entry.levels.get("dip_zone_low"), entry.levels.get("dip_zone_high")],
            "structural_support": entry.levels.get("structural_support"),
            "confirm_breakout": entry.levels.get("confirm_breakout"),
        })
    return sorted(rows, key=lambda row: (-row["a_weight"], -row["structure_score"], row["symbol"]))
