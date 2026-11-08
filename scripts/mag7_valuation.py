"""Combine the latest frozen closes with the live TTM EPS to show GAAP valuation per Mag 7 symbol."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    try:  # keep Chinese/mathematical output readable on a GBK Windows console
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    from app import outlook_registry as registry

    reports = sorted((ROOT / "reports").glob("ttm-eps-live-*.json"), key=lambda p: p.stat().st_mtime)
    ttm = {row["symbol"]: row for row in json.loads(reports[-1].read_text(encoding="utf-8"))}
    closes: dict[str, float] = {}
    for entry in registry.load_entries():
        if entry.market != "EQUITY":
            continue
        price = float(entry.price)
        if entry.symbol not in closes or entry.as_of > closes[entry.symbol][1]:
            closes[entry.symbol] = (price, entry.as_of)

    rows = []
    for symbol in ("NVDA", "AMZN", "MSFT", "AAPL", "GOOGL", "GOOG", "META", "TSLA"):
        price = closes.get(symbol, (None, None))[0]
        eps = ttm.get(symbol, {}).get("TTM_EPS", {}).get("value")
        pe = round(price / float(eps), 1) if (price and eps and float(eps) > 0) else None
        rows.append({"symbol": symbol, "close": price, "ttm_eps": eps, "pe_ttm": pe,
                     "ttm_period": ttm.get(symbol, {}).get("TTM_EPS", {}).get("period_end"),
                     "derivation": ttm.get(symbol, {}).get("TTM_EPS", {}).get("derivation")})
    print(json.dumps({"source_report": reports[-1].name,
                      "note": "GAAP TTM EPS; no one-off adjustment applied", "rows": rows},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
