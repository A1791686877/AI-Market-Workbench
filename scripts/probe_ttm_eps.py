"""Live check: does the agreed TTM-EPS policy actually resolve for the Mag 7 in real SEC filings?

Prints, per symbol, the single-quarter EPS facts SEC actually tags and the resulting TTM_EPS / PE_TTM.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.fundamentals import _duration_days, _quarterly_eps_rows  # noqa: E402  (diagnostic tool)


def main() -> None:
    from app.fundamentals import normalize_company_facts
    from app.instruments import MAG7_CIK, equity_subject
    from app.providers import SecCompanyFactsProvider

    symbols = ["AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "NVDA", "META", "TSLA"]
    cutoff = datetime.now(UTC)
    provider = SecCompanyFactsProvider()
    out = []
    try:
        for symbol in symbols:
            cik = MAG7_CIK[symbol]
            response = provider.fetch_raw(cik)
            concept, quarters = _quarterly_eps_rows(response.payload, cutoff.date())
            quarters = sorted(quarters, key=lambda row: str(row["end"]))
            fundamentals = normalize_company_facts(response.payload, response.sha256, equity_subject(symbol),
                                                   cik=cik, cutoff=cutoff, last_close=100.0)
            facts = {fact.name.value: fact for fact in fundamentals.facts}
            out.append({
                "symbol": symbol,
                "eps_concept": concept,
                "single_quarters_found": len(quarters),
                "last_six_quarters": [{"start": row.get("start"), "end": row.get("end"),
                                       "days": _duration_days(row), "val": row.get("val"),
                                       "form": row.get("form"), "filed": row.get("filed")}
                                      for row in quarters[-6:]],
                "TTM_EPS": {"value": facts["TTM_EPS"].value, "status": facts["TTM_EPS"].status.value,
                            "period_end": facts["TTM_EPS"].period_end.isoformat() if facts["TTM_EPS"].period_end else None,
                            "derivation": facts["TTM_EPS"].derivation,
                            "missing_reason": facts["TTM_EPS"].missing_reason},
                "PE_TTM_note": "PE uses last_close=100 for comparability; the real close comes from bars",
            })
            print(f"{symbol}: quarters={len(quarters)} ttm_eps={facts['TTM_EPS'].value} "
                  f"status={facts['TTM_EPS'].status.value}", flush=True)
    finally:
        provider.http.close()
    target = ROOT / "reports" / f"ttm-eps-live-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\nreport: {target}")


if __name__ == "__main__":
    main()
