"""Diagnose the TTM EPS window for one symbol: which four periods were summed, and were any derived?"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.fundamentals import (  # noqa: E402
    ANNUAL_MAX_DAYS,
    ANNUAL_MIN_DAYS,
    NINE_MONTH_MAX_DAYS,
    NINE_MONTH_MIN_DAYS,
    QUARTER_MAX_DAYS,
    QUARTER_MIN_DAYS,
    _duration_days,
    _eps_rows,
    _net_income_rows,
    build_ttm_eps,
)


def main() -> None:
    from app.instruments import MAG7_CIK
    from app.providers import SecCompanyFactsProvider

    symbols = sys.argv[1:] or ["GOOGL", "AMZN", "NVDA", "AAPL"]
    cutoff = datetime.now(UTC).date()
    provider = SecCompanyFactsProvider()
    out = []
    try:
        for symbol in symbols:
            payload = provider.fetch_raw(MAG7_CIK[symbol]).payload
            concept, quarters = _eps_rows(payload, cutoff, QUARTER_MIN_DAYS, QUARTER_MAX_DAYS)
            _, nine = _eps_rows(payload, cutoff, NINE_MONTH_MIN_DAYS, NINE_MONTH_MAX_DAYS)
            _, annual = _eps_rows(payload, cutoff, ANNUAL_MIN_DAYS, ANNUAL_MAX_DAYS)
            _, ni_quarters = None, _net_income_rows(payload, cutoff, QUARTER_MIN_DAYS, QUARTER_MAX_DAYS)
            ni_nine = _net_income_rows(payload, cutoff, NINE_MONTH_MIN_DAYS, NINE_MONTH_MAX_DAYS)
            ni_annual = _net_income_rows(payload, cutoff, ANNUAL_MIN_DAYS, ANNUAL_MAX_DAYS)
            fact, reason = build_ttm_eps(payload, cutoff, symbol, "src-test")
            out.append({
                "symbol": symbol,
                "concept": concept,
                "quarterly_eps": [{"start": r["start"], "end": r["end"], "days": _duration_days(r),
                                   "val": r["val"], "form": r.get("form"), "filed": r.get("filed")}
                                  for r in sorted(quarters, key=lambda r: str(r["end"]))[-6:]],
                "nine_month": [{"start": r["start"], "end": r["end"], "days": _duration_days(r), "val": r["val"],
                                "form": r.get("form"), "filed": r.get("filed")} for r in nine[-3:]],
                "annual": [{"start": r["start"], "end": r["end"], "days": _duration_days(r), "val": r["val"],
                            "form": r.get("form"), "filed": r.get("filed")} for r in annual[-3:]],
                "ni_quarterly": [{"end": r["end"], "val": r["val"]} for r in sorted(
                    ni_quarters, key=lambda r: str(r["end"]))[-4:]],
                "ni_nine": [{"start": r["start"], "end": r["end"], "val": r["val"]} for r in ni_nine[-3:]],
                "ni_annual": [{"start": r["start"], "end": r["end"], "val": r["val"]} for r in ni_annual[-2:]],
                "ttm_eps": getattr(fact, "value", None),
                "ttm_fact_id": getattr(fact, "fact_id", None),
                "ttm_inputs": getattr(fact, "input_fact_ids", None),
                "ttm_period": getattr(fact, "fiscal_period", None),
                "derivation": getattr(fact, "derivation", None),
                "reason": reason,
            })
    finally:
        provider.http.close()
    target = ROOT / "reports" / f"ttm-eps-diagnose-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    for row in out:
        print(f"\n=== {row['symbol']} ttm={row['ttm_eps']} period={row['ttm_period']}")
        print(f"    inputs={row['ttm_inputs']}")
        print(f"    reason={row['reason']}")
        for item in row["quarterly_eps"]:
            print(f"    Q {item['start']} .. {item['end']} days={item['days']} val={item['val']} {item['form']} filed={item['filed']}")
        for item in row["nine_month"]:
            print(f"    9M {item['start']} .. {item['end']} days={item['days']} val={item['val']} filed={item['filed']}")
        for item in row["annual"]:
            print(f"    FY {item['start']} .. {item['end']} days={item['days']} val={item['val']} filed={item['filed']}")
    print(f"\nreport: {target}")


if __name__ == "__main__":
    main()
