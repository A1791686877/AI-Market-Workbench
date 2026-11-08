"""Last N confirmed daily bars for a symbol (context for the write-up)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

symbol = (sys.argv[1] if len(sys.argv) > 1 else "MCD").upper()
count = int(sys.argv[2]) if len(sys.argv) > 2 else 10
from app.market_cache import MarketCache
from app.models import DataProvider, Interval

document = MarketCache().load(DataProvider.ALPACA, f"US:{symbol}", Interval.DAY_1)
bars = [b for b in document.bars if b.confirmed][-count:]
previous = None
for bar in bars:
    change = "" if previous is None else f"{(float(bar.close) / float(previous) - 1) * 100:+.2f}%"
    print(f"{bar.close_at.date()}  open={bar.open:>10s} high={bar.high:>10s} low={bar.low:>10s} "
          f"close={bar.close:>10s} vol={bar.volume:>14s} {change}")
    previous = bar.close
