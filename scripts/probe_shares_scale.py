"""Scale probe for the MARKET_CAP derivation discovered while adding MCD.

`WeightedAverageNumberOfDilutedSharesOutstanding` is tagged in absolute shares by some issuers
(AAPL, NVDA) and in MILLIONS by others (MCD tags 716.4 for ~716M shares). The raw value alone is
ambiguous, so print what each catalogued name reports plus the implied market cap under both readings.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    from app.fundamentals import _filed_ok, _pick_duration, _rows
    from app.instruments import EQUITY_CIK, equity_subject
    from app.providers import SecCompanyFactsProvider

    symbols = sys.argv[1:] or sorted(EQUITY_CIK)
    provider = SecCompanyFactsProvider()
    cutoff = datetime.now(UTC)
    rows = []
    try:
        for symbol in symbols:
            cik = EQUITY_CIK[symbol]
            payload = provider.fetch_raw(cik).payload
            concept = "WeightedAverageNumberOfDilutedSharesOutstanding"
            try:
                _, candidates = _rows(payload, concept)
            except Exception:
                candidates = []
            row = _pick_duration(candidates, cutoff.date()) if candidates else None
            value = float(row["val"]) if row else None
            rows.append({"symbol": symbol, "cik": cik, "concept": concept,
                         "end": row.get("end") if row else None,
                         "unit_key": list((payload.get("facts", {}).get("us-gaap", {}).get(concept, {})
                                           .get("units", {}) or {}).keys()),
                         "raw_value": value,
                         "implied_market_cap_if_absolute": round(value * 252.78 / 1e9, 4) if value else None,
                         "implied_market_cap_if_millions": round(value * 1e6 * 252.78 / 1e9, 4) if value else None,
                         "looks_like_millions": bool(value and value < 1_000_000)})
    finally:
        provider.http.close()
    target = ROOT / "reports" / f"shares-scale-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    target.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    for row in rows:
        print(f"{row['symbol']:6s} raw={row['raw_value']} unit={row['unit_key']} "
              f"as_absolute={row['implied_market_cap_if_absolute']}B as_millions={row['implied_market_cap_if_millions']}B "
              f"-> millions={row['looks_like_millions']}")
    print(f"\nreport: {target}")


if __name__ == "__main__":
    main()
