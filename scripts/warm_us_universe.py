"""Warm the on-demand cache for the Magnificent Seven (bounded, not "all US stocks").

There is no full-market pre-download by design: bars are fetched per symbol on
first request and then incrementally refreshed. This script just pays that cost
once for the starter set so the pages/MCP are fast afterwards.

    . .\\scripts\\load-provider-env.ps1
    $env:AMW_DATA_MODE='live'; $env:AMW_NETWORK_ENABLED='true'
    python -B scripts\\warm_us_universe.py                 # Mag 7 x {1d,15m}
    python -B scripts\\warm_us_universe.py --intervals 1d   # daily only
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from app.instruments import MAG7_SYMBOLS  # noqa: E402
from app.market_cache import market_cache  # noqa: E402
from app.models import Interval  # noqa: E402
from app.service import MarketWorkbenchService  # noqa: E402


def cache_dir_size() -> int:
    root = market_cache.root
    return sum(path.stat().st_size for path in root.rglob("*.json")) if root.exists() else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Warm the Mag 7 cache (small, bounded, on-demand design).")
    parser.add_argument("--symbols", default=",".join(MAG7_SYMBOLS))
    parser.add_argument("--intervals", default="1d,15m", help="comma list drawn from 15m,1h,195m,4h,1d,1w,1mo")
    parser.add_argument("--limit", type=int, default=5000)
    args = parser.parse_args()

    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    intervals = [Interval(item.strip()) for item in args.intervals.split(",") if item.strip()]
    service = MarketWorkbenchService()
    before = cache_dir_size()
    results: list[dict] = []
    for symbol in symbols:
        subject_id = f"us-{symbol.lower()}"
        instrument_id = f"US:{symbol}"
        for interval in intervals:
            started = time.perf_counter()
            entry: dict = {"symbol": symbol, "subject_id": subject_id, "interval": interval.value}
            try:
                page = service.get_bars(instrument_id, interval, None, args.limit, None)
                source = page.sources[0] if page.sources else None
                entry.update({
                    "status": "SUCCESS",
                    "bars_returned": len(page.bars),
                    "first_bar": page.bars[0].open_at.isoformat() if page.bars else None,
                    "last_bar": page.bars[-1].close_at.isoformat() if page.bars else None,
                    "last_close": page.bars[-1].close if page.bars else None,
                    "missing_bar_count": page.quality.missing_bar_count,
                    "history_complete": page.quality.history_complete,
                    "provider": source.provider.value if source else None,
                    "dataset": source.dataset if source else None,
                    "delay_class": source.delay_class.value if source else None,
                    "market_scope": source.market_scope.value if source else None,
                    "price_basis": source.price_basis.value if source and source.price_basis else None,
                })
            except Exception as exc:
                entry.update({"status": "FAILED", "error_type": type(exc).__name__, "error": str(exc)[:200]})
            entry["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
            results.append(entry)
            print(f"{symbol:6s} {interval.value:5s} {entry['status']:8s} "
                  f"bars={entry.get('bars_returned')} last={entry.get('last_bar')} "
                  f"missing={entry.get('missing_bar_count')} {entry.get('elapsed_ms')}ms")

    after = cache_dir_size()
    report = {
        "phase": "us-universe-cache-warm",
        "created_at": datetime.now(UTC).isoformat(),
        "design_note": "No full-market download: bars are fetched per symbol and cached incrementally.",
        "symbols": symbols,
        "intervals": [item.value for item in intervals],
        "cache_bytes_before": before,
        "cache_bytes_after": after,
        "cache_bytes_added": after - before,
        "results": results,
    }
    target = ROOT / "reports" / f"us-universe-warm-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\ncache added {after - before} bytes; report: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
