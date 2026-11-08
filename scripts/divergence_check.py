"""Divergence audit CLI: are the ranked names carrying a *live* (recent, un-retired) top divergence?

    python -B scripts/divergence_check.py                     # Mag 7 + GOOG, daily + 15m
    python -B scripts/divergence_check.py --intervals 1d

Logic lives in `app.divergence_audit`; this file only collects bars/bundles and prints.
"""
from __future__ import annotations

import argparse
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="META,AAPL,NVDA,MSFT,GOOG,AMZN,TSLA")
    parser.add_argument("--intervals", default="1d,15m")
    parser.add_argument("--bars", type=int, default=1200)
    parser.add_argument("--outdir", type=Path, default=ROOT / "reports")
    args = parser.parse_args()

    from app.divergence_audit import audit
    from app.models import Interval
    from app.service import MarketWorkbenchService

    service = MarketWorkbenchService()
    interval_map = {"1d": Interval.DAY_1, "15m": Interval.MIN_15}
    results = []
    for symbol in [item.strip().upper() for item in args.symbols.split(",") if item.strip()]:
        instrument_id = f"US:{symbol}"
        for name in [item.strip() for item in args.intervals.split(",") if item.strip()]:
            interval = interval_map[name]
            try:
                bars = service.get_bars(instrument_id, interval, None, args.bars, None).bars
                bundle = service.get_indicator_bundle(f"us-{symbol.lower()}", interval)
            except Exception as exc:
                results.append({"symbol": symbol, "interval": name,
                                "error": f"{type(exc).__name__}: {str(exc)[:160]}"})
                continue
            row = audit(bundle.divergences, bars, name)
            row["symbol"] = symbol
            row["last_bar"] = bars[-1].close_at.isoformat()
            row["last_close"] = bars[-1].close
            results.append(row)
            live = row["live_top_sentence"] or "无（近期且未被否定的顶背离）"
            print(f"{symbol:6s} {name:4s} 顶背离累计={row['counts'].get('REGULAR_BEARISH', 0)}R/"
                  f"{row['counts'].get('HIDDEN_BEARISH', 0)}H（已失效 {row['retired_top']}）| 仍有效: {live}")

    target = args.outdir / f"divergence-audit-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    target.write_text(json.dumps({"generated_at": datetime.now(UTC).isoformat(), "results": results},
                                 ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\nreport: {target}")


if __name__ == "__main__":
    main()
