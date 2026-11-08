"""Pull the decision-relevant details out of the newest per-symbol analysis report."""
from __future__ import annotations

import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
symbol = (sys.argv[1] if len(sys.argv) > 1 else "MCD").lower()
reports = sorted(pathlib.Path("reports").glob(f"{symbol}-analysis-*.json"), key=lambda p: p.stat().st_mtime)
data = json.loads(reports[-1].read_text(encoding="utf-8"))
print("file:", reports[-1].name)
print("\n--- outlook ---")
outlook = data["outlook"]
print("primary:", outlook["primary"], "| paths:", {p["key"]: p["weight_pct"] for p in outlook["paths"]},
      "| structure", outlook["structure_score"], "momentum", outlook["momentum_score"])
print("levels:", json.dumps(outlook["levels"], ensure_ascii=False))
print("cycle rule hit:", outlook["cycle_rule"])
print("structure hits:", [(h["rule"], h["points"]) for h in [] ])
print("\n--- narrative ---")
print(outlook["narrative"])
print("\n--- divergence (daily) ---")
div = data["divergence_1d"]
print("counts:", div["counts"], "| retired_top:", div["retired_top"], "| live_top:", div["live_top_sentence"])
for label in ("most_recent_top", "most_recent_bottom"):
    for item in div.get(label, [])[:1]:
        print(f"  {label}: {item['kind']}/{item['oscillator']} {item['first_price']}@{item['first_anchor_at'][:10]}"
              f" -> {item['second_price']}@{item['second_anchor_at'][:10]} bars_since={item['bars_since_second_anchor']}"
              f" retired={item['retired']} mattered={item['mattered']}")
print("\n--- indicators (daily) ---")
ind = data["indicators"]["1d"]
print("trend:", ind["trend"], "| vpvr:", ind["vpvr"])
for name, values in ind["indicators"].items():
    if values["primary"] is not None:
        print(f"  {name:24s} primary={values['primary']} signal={values['signal']} hist={values['histogram']} "
              f"+DI={values['plus_di']} -DI={values['minus_di']}")
print("  S/R:", [(l["kind"], l["price"], l["role"], l["touches"]) for l in ind["support_resistance"]])
print("\n--- valuation ---")
print(json.dumps(data["valuation"], ensure_ascii=False, indent=2))
print("\n--- fundamentals facts ---")
for name, fact in data["fundamentals"]["facts"].items():
    print(f"  {name:20s} {str(fact['value']):>18s} {str(fact['unit']):12s} {str(fact['period_end'])} "
          f"{(fact['derivation'] or '')[:70]}")
print("  missing:", data["fundamentals"]["missing_items"])
print("\n--- reference ---")
print(json.dumps(data.get("reference"), ensure_ascii=False))
print("\n--- bars ---")
print(json.dumps(data["bars"], ensure_ascii=False))
