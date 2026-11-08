"""What the pinned reference catalog buys us: sector labels for the tracked subjects + universe size.

This is the concrete justification for vendoring FinanceDatabase: classification metadata and a
universe list for the (deferred) batch-scan feature. It is metadata, never price evidence.
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
    parser.add_argument("--symbols", default="AAPL,MSFT,GOOGL,GOOG,AMZN,NVDA,META,TSLA")
    parser.add_argument("--outdir", type=Path, default=ROOT / "reports")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    from app.catalog import catalog

    reference = catalog()
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    subjects = []
    for symbol in symbols:
        record = reference.lookup(symbol)
        subjects.append({"symbol": symbol, "found": record is not None, **({
            "name": record.fields["name"], "sector": record.fields["sector"],
            "industry_group": record.fields["industry_group"], "industry": record.fields["industry"],
            "mic": record.fields["mic"], "market_cap_bucket": record.fields["market_cap"],
            "country": record.fields["country"], "currency": record.fields["currency"],
        } if record else {})})

    stats = {asset: reference.stats(asset) for asset in ("equities", "etfs", "indices")}
    universe = {asset: len(reference.us_universe(asset)) for asset in ("equities", "etfs", "indices")}
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "catalog_commit": reference.commit,
        "catalog_provenance": reference.provenance,
        "subjects": subjects,
        "asset_stats": {asset: {key: value for key, value in row.items() if key != "countries"}
                        for asset, row in stats.items()},
        "us_universe": universe,
        "sector_concentration": {
            "distinct_sectors": len({item["sector"] for item in subjects if item.get("sector")}),
            "note": "Mag 7 只有 3 个 GICS 板块（IT / 通信服务 / 可选消费）→ 横截面排名天生集中，不能当分散投资。",
        },
    }
    target = args.outdir / f"reference-catalog-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"catalog commit: {reference.commit} (license: {reference.provenance.get('license')})")
    for item in subjects:
        if item["found"]:
            print(f"  {item['symbol']:6s} {item['sector']:24s} {item['industry_group']:34s} "
                  f"mic={item['mic']} {item['market_cap_bucket']}")
        else:
            print(f"  {item['symbol']:6s} NOT FOUND in catalog")
    print(f"\nuniverse: " + "  ".join(f"{asset}={count:,}" for asset, count in universe.items()))
    print(f"rows: " + "  ".join(f"{asset}={row['rows']:,}" for asset, row in stats.items()))
    print(f"\nreport: {target}")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
