"""Verify a candidate symbol before adding it to the catalog: venue (MIC), SEC CIK, sector.

Never guess a CIK: this resolves the ticker against SEC's official company_tickers.json and prints
what it found so the value can be pinned with evidence.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.providers import SecCompanyFactsProvider  # noqa: E402


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    symbol = (sys.argv[1] if len(sys.argv) > 1 else "MCD").upper()
    out: dict = {"symbol": symbol, "checked_at": datetime.now(UTC).isoformat()}

    # 1) venue / sector / industry from the pinned reference catalog
    try:
        from app.catalog import catalog
        record = catalog.lookup(symbol)
        out["catalog"] = None if record is None else {
            "name": record.fields["name"], "mic": record.fields["mic"], "exchange_code": record.fields["exchange"],
            "sector": record.fields["sector"], "industry_group": record.fields["industry_group"],
            "industry": record.fields["industry"], "country": record.fields["country"],
            "market_cap_bucket": record.fields["market_cap"], "commit": record.commit,
        }
    except Exception as exc:
        out["catalog_error"] = f"{type(exc).__name__}: {exc}"

    # 2) SEC CIK from the official ticker map
    provider = SecCompanyFactsProvider()
    try:
        response = provider.http.get_json("https://www.sec.gov/files/company_tickers.json",
                                          headers={"User-Agent": __import__("app.config", fromlist=["settings"]).settings.sec_user_agent})
        rows = response.payload.values() if isinstance(response.payload, dict) else []
        matches = [{"cik": int(row["cik_str"]), "ticker": row["ticker"], "title": row["title"]}
                   for row in rows if str(row.get("ticker", "")).upper() == symbol]
        out["sec_cik_matches"] = matches
    finally:
        provider.http.close()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    target = ROOT / "reports" / f"symbol-verification-{symbol.lower()}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nreport: {target}")


if __name__ == "__main__":
    main()
