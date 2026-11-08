"""Read-only US equity universe.

The catalog is intentionally small and explicit: the platform does not download
"all US stocks" up front. Bars are fetched on demand and cached incrementally, so
adding a symbol costs nothing until someone looks at it.

Adding a symbol is a reviewable act with evidence:
  * the venue comes from the pinned reference catalog's MIC (`scripts/verify_symbol.py`),
  * the CIK comes from SEC's official `company_tickers.json` (never guessed).
"""
from __future__ import annotations

from app.models import AnalysisSubject, AssetClass, InstrumentRef, SubjectType

MAG7_SYMBOLS = ("AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA")

# Opt-in extras: nothing is downloaded until someone asks for the symbol.
EXTRA_EQUITY_SYMBOLS = ("GOOG", "MCD")

# Venue per symbol (verified against the pinned MIC, not assumed from the listing headline).
# Anything not listed here is NASDAQ, which is true for all Magnificent Seven members.
EQUITY_VENUE = {
    "AAPL": "NASDAQ", "MSFT": "NASDAQ", "GOOGL": "NASDAQ", "AMZN": "NASDAQ",
    "NVDA": "NASDAQ", "META": "NASDAQ", "TSLA": "NASDAQ",
    "GOOG": "NASDAQ",
    "MCD": "NYSE",       # FinanceDatabase MIC = XNYS, exchange code NYQ
}


def venue_for(symbol: str) -> str:
    return EQUITY_VENUE.get(symbol.upper(), "NASDAQ")


def equity_instrument(symbol: str, venue: str | None = None) -> InstrumentRef:
    symbol = symbol.upper()
    return InstrumentRef(
        instrument_id=f"US:{symbol}",
        asset_class=AssetClass.US_EQUITY,
        symbol=symbol,
        venue=venue or venue_for(symbol),
        quote_currency="USD",
        timezone="America/New_York",
        contract_type="COMMON_STOCK",
    )


def equity_subject(symbol: str, venue: str | None = None) -> AnalysisSubject:
    instrument = equity_instrument(symbol, venue)
    return AnalysisSubject(
        subject_id=f"us-{symbol.lower()}",
        subject_type=SubjectType.EQUITY,
        symbol=instrument.symbol,
        primary_instrument_id=instrument.instrument_id,
        instruments=[instrument],
    )


MAG7_INSTRUMENTS = [equity_instrument(symbol) for symbol in MAG7_SYMBOLS]
EQUITY_INSTRUMENTS = MAG7_INSTRUMENTS + [equity_instrument(s) for s in EXTRA_EQUITY_SYMBOLS]
MAG7_SUBJECTS = {subject.subject_id: subject for subject in (equity_subject(s) for s in MAG7_SYMBOLS)}
EQUITY_SUBJECTS = {subject.subject_id: subject
                   for subject in (equity_subject(s) for s in MAG7_SYMBOLS + EXTRA_EQUITY_SYMBOLS)}

# Verified CIK map for every catalogued equity (Mag 7 + extras).
# `scripts/probe_us_free_sources.py` re-verifies each against SEC submissions; `scripts/verify_symbol.py`
# resolves a new ticker against https://www.sec.gov/files/company_tickers.json before it is added here.
EQUITY_CIK = {
    "AAPL": 320193,
    "MSFT": 789019,
    "GOOGL": 1652044,
    "GOOG": 1652044,   # same issuer as GOOGL: one CIK, two share classes
    "AMZN": 1018724,
    "NVDA": 1045810,
    "META": 1326801,
    "TSLA": 1318605,
    "MCD": 63908,      # "MCDONALDS CORP" per SEC company_tickers.json (sha256 82cd5fd9…)
}
# Backwards-compatible alias: the map outgrew its original name once extras were added.
MAG7_CIK = EQUITY_CIK
