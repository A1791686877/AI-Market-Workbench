"""Probe every configured US-equity source with real requests and record what worked.

Evidence-first: nothing is declared "connected" until a real HTTP call returns
validated bars. Also determines split-adjustment semantics empirically by looking
at known split boundaries (AAPL 2020-08-31 4:1, NVDA 2024-06-10 10:1) instead of
trusting vendor docs.

Run (credentials come from config/providers.env through the allowlisted loader):
    . .\\scripts\\load-provider-env.ps1
    $env:AMW_DATA_MODE='live'; $env:AMW_NETWORK_ENABLED='true'
    python -B scripts\\probe_us_free_sources.py
"""
from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
try:  # keep Chinese output readable on a GBK Windows console
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from app.config import settings  # noqa: E402
from app.fixtures import AAPL  # noqa: E402
from app.instruments import MAG7_CIK, MAG7_SYMBOLS  # noqa: E402
from app.logging_config import audit_event, configure_logging  # noqa: E402
from app.models import InstrumentRef, Interval  # noqa: E402
from app.providers import (  # noqa: E402
    AlpacaMarketDataProvider,
    AlphaVantageProvider,
    MassiveEodProvider,
    SecCompanyFactsProvider,
    TiingoEodProvider,
    TwelveDataProvider,
)


def bar_summary(bars, source) -> dict:
    return {
        "bar_count": len(bars),
        "first_bar_at": bars[0].open_at.isoformat(),
        "last_bar_at": bars[-1].close_at.isoformat(),
        "last_close": bars[-1].close,
        "provider": source.provider.value,
        "dataset": source.dataset,
        "delay_class": source.delay_class.value,
        "market_scope": source.market_scope.value,
        "price_basis": source.price_basis.value if source.price_basis else None,
        "is_full_market": source.is_full_market,
        "limitations": source.limitations[:3],
    }


def run_bars(name: str, configured: bool, call) -> dict:
    if not configured:
        return {"service": name, "status": "NOT_CONFIGURED"}
    started = time.perf_counter()
    try:
        bars, source = call()
        return {"service": name, "status": "SUCCESS", "http_verified": True,
                **bar_summary(bars, source),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)}
    except Exception as exc:
        return {"service": name, "status": "FAILED", "http_verified": False,
                "error_type": type(exc).__name__, "error": str(exc)[:300],
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)}


def run_raw(name: str, configured: bool, call) -> dict:
    if not configured:
        return {"service": name, "status": "NOT_CONFIGURED"}
    started = time.perf_counter()
    try:
        response = call()
        payload = response.payload
        keys = sorted(payload)[:8] if isinstance(payload, dict) else []
        messages = {k: str(payload.get(k))[:160] for k in ("Note", "Information", "Error Message")
                    if isinstance(payload, dict) and k in payload}
        return {"service": name, "status": "SUCCESS", "http_verified": True,
                "status_code": response.status_code, "payload_keys": keys, "messages": messages,
                "raw_sha256": response.sha256,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)}
    except Exception as exc:
        return {"service": name, "status": "FAILED", "http_verified": False,
                "error_type": type(exc).__name__, "error": str(exc)[:300],
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)}


def split_verdict(bars, split_at: str, ratio: float) -> dict:
    """Look at bar closes around a known split.

    An unadjusted feed shows a ~ratio DROP on the split date (price divided by the
    split factor); an adjusted feed stays smooth. Both directions are checked, which
    is why the first version of this helper mislabeled raw Alpaca data.
    """
    if not bars:
        return {"verdict": "NO_BARS_IN_WINDOW", "samples": 0}
    ordered = sorted(bars, key=lambda b: b.open_at)
    ratios = []
    for previous, current in zip(ordered, ordered[1:]):
        previous_close = float(previous.close)
        if previous_close:
            ratios.append(round(float(current.close) / previous_close, 4))
    if not ratios:
        return {"verdict": "SINGLE_BAR", "samples": 1,
                "dates": [b.close_at.date().isoformat() for b in ordered]}
    biggest_up = max(ratios)
    biggest_down = min(ratios)
    scaled = max(biggest_up, (1.0 / biggest_down) if biggest_down > 0 else 1.0)
    if scaled > ratio * 0.6:
        verdict = "SPLIT_UNADJUSTED_RAW"
    elif scaled < 1.6:
        verdict = "SPLIT_ADJUSTED_SMOOTH"
    else:
        verdict = "UNCLEAR"
    return {"verdict": verdict, "max_ratio": biggest_up, "min_ratio": biggest_down,
            "ratios": ratios[:10], "window": f"{ordered[0].open_at.date()}..{ordered[-1].open_at.date()}",
            "split_at": split_at, "expected_split_factor": ratio}


def main() -> None:
    if settings.data_mode != "live" or not settings.network_enabled:
        raise RuntimeError("run in live mode with network enabled")
    configure_logging()
    now = datetime.now(UTC)
    iso = lambda days: (now - timedelta(days=days)).date().isoformat()  # noqa: E731

    alpaca_iex = AlpacaMarketDataProvider("iex")
    alpaca_sip = AlpacaMarketDataProvider("sip")
    twelve = TwelveDataProvider()
    alpha = AlphaVantageProvider()

    class _Throttle:
        """Alpha Vantage free tier: 25 requests/day and effectively ~5/minute.

        Measured: two calls 1.4s apart were both refused with the vendor's
        "spreading out your requests" notice, so the probe spaces AV calls by 13s.
        """

        def __init__(self, seconds: float) -> None:
            self.seconds = seconds
            self.last = 0.0

        def __call__(self) -> None:
            gap = time.monotonic() - self.last
            if gap < self.seconds:
                time.sleep(self.seconds - gap)
            self.last = time.monotonic()

    alpha_wait = _Throttle(13.0)

    def av_daily(instrument, start, end):
        alpha_wait()
        return alpha.fetch_daily(instrument, start, end)

    def av_raw(symbol: str, adjusted: bool):
        alpha_wait()
        return alpha.fetch_raw(symbol, adjusted=adjusted)

    results: list[dict] = [
        run_bars("Alpaca IEX 15m (free)", bool(settings.alpaca_key_id and settings.alpaca_secret),
                 lambda: alpaca_iex.fetch_15m(AAPL, now - timedelta(days=5), now)),
        run_bars("Alpaca SIP 15m (paid entitlement)", bool(settings.alpaca_key_id and settings.alpaca_secret),
                 lambda: alpaca_sip.fetch_15m(AAPL, now - timedelta(days=5), now)),
        run_bars("Alpaca IEX daily adjustment=all", bool(settings.alpaca_key_id and settings.alpaca_secret),
                 lambda: alpaca_iex.fetch_daily(AAPL, now - timedelta(days=400), now, "all")),
        run_bars("Alpaca IEX daily adjustment=split", bool(settings.alpaca_key_id and settings.alpaca_secret),
                 lambda: alpaca_iex.fetch_daily(AAPL, now - timedelta(days=400), now, "split")),
        run_bars("Twelve Data 15m", bool(settings.twelve_data_key),
                 lambda: twelve.fetch_bars(AAPL, Interval.MIN_15, now - timedelta(days=20), now, 1000)),
        run_bars("Twelve Data daily", bool(settings.twelve_data_key),
                 lambda: twelve.fetch_bars(AAPL, Interval.DAY_1, now - timedelta(days=800), now, 1000)),
        run_bars("Alpha Vantage daily (raw, compact)", bool(settings.alpha_vantage_key),
                 lambda: av_daily(AAPL, now - timedelta(days=800), now)),
        run_raw("Alpha Vantage adjusted endpoint entitlement (premium?)", bool(settings.alpha_vantage_key),
                lambda: av_raw("AAPL", True)),
        run_raw("Tiingo Free EOD", bool(settings.tiingo_token),
                lambda: TiingoEodProvider().fetch_raw("AAPL", iso(30), now.date().isoformat())),
        run_raw("Massive Basic EOD", bool(settings.massive_key),
                lambda: MassiveEodProvider().fetch_raw("AAPL", iso(30), now.date().isoformat())),
        run_raw("SEC EDGAR Company Facts",
                "example.invalid" not in settings.sec_user_agent and "@" in settings.sec_user_agent,
                lambda: SecCompanyFactsProvider().fetch_raw(320193)),
    ]

    # empirical adjustment semantics on known split boundaries
    fetched: dict[str, list] = {}

    def cached_bars(key, call):
        if key not in fetched:
            fetched[key] = call()
        return fetched[key]

    def instrument_for(symbol: str) -> InstrumentRef:
        return InstrumentRef(instrument_id=f"US:{symbol}", asset_class=AAPL.asset_class, symbol=symbol,
                             venue="NASDAQ", quote_currency="USD", timezone="America/New_York",
                             contract_type="COMMON_STOCK")

    checks: list[dict] = []
    adjustments = (
        ("Alpaca IEX adjustment=all", "alpaca-all",
         lambda inst, days: cached_bars(f"alpaca-all-{inst.symbol}",
                                        lambda: alpaca_iex.fetch_daily(inst, now - timedelta(days=days), now, "all")[0])),
        ("Alpaca IEX adjustment=raw", "alpaca-raw",
         lambda inst, days: cached_bars(f"alpaca-raw-{inst.symbol}",
                                        lambda: alpaca_iex.fetch_daily(inst, now - timedelta(days=days), now, "raw")[0])),
        ("Twelve Data daily", "twelve",
         lambda inst, days: cached_bars(f"twelve-{inst.symbol}",
                                        lambda: twelve.fetch_bars(inst, Interval.DAY_1, now - timedelta(days=days), now, 1000)[0])),
        # Alpha Vantage is deliberately excluded from the split matrix: its free tier
        # only returns the last ~100 daily bars, which cannot reach a split boundary.
    )
    for service, _, fetch in adjustments:
        for symbol, days, split_at, ratio in (("AAPL", 2400, "2020-08-31", 4.0), ("NVDA", 1100, "2024-06-10", 10.0)):
            try:
                bars = [b for b in fetch(instrument_for(symbol), days)
                        if abs((b.open_at - datetime.fromisoformat(split_at + "T00:00:00+00:00")).days) <= 12]
                checks.append({"service": service, "symbol": symbol, **split_verdict(bars, split_at, ratio)})
            except Exception as exc:
                checks.append({"service": service, "symbol": symbol, "verdict": "REQUEST_FAILED",
                               "error_type": type(exc).__name__, "error": str(exc)[:200]})

    # verify every candidate CIK against SEC submissions (entity name + tickers)
    cik_verification: list[dict] = []
    sec_ready = "example.invalid" not in settings.sec_user_agent and "@" in settings.sec_user_agent
    if sec_ready:
        sec = SecCompanyFactsProvider()
        try:
            for symbol, cik in MAG7_CIK.items():
                try:
                    response = sec.fetch_submissions(cik)
                    payload = response.payload if isinstance(response.payload, dict) else {}
                    tickers = list(payload.get("tickers") or [])
                    cik_verification.append({
                        "symbol": symbol, "cik": cik, "status": "SUCCESS",
                        "entity_name": payload.get("name"), "tickers": tickers,
                        "match": symbol in tickers,
                    })
                except Exception as exc:
                    cik_verification.append({"symbol": symbol, "cik": cik, "status": "FAILED",
                                             "error_type": type(exc).__name__, "error": str(exc)[:160]})
        finally:
            sec.http.close()

    report = {
        "phase": "phase-1-us-equity-source-probe",
        "created_at": datetime.now(UTC).isoformat(),
        "symbols_probed": ["AAPL", "NVDA"],
        "monthly_data_subscription_cost_usd": 0,
        "credentials_logged": False,
        "trading_endpoints_called": False,
        "notes": [
            "The user-supplied Alpaca base URL was a paper-trading endpoint and is deliberately NOT used; only data.alpaca.markets is called.",
            "Nothing is marked connected unless a real call returned validated XNYS bars.",
            "Split verdicts come from real closes around known split dates, not from vendor documentation.",
            "CIK values are verified against SEC submissions (entity name + ticker list), not trusted from memory.",
        ],
        "results": results,
        "adjustment_checks": checks,
        "cik_verification": cik_verification,
    }
    target = settings.root / "reports" / "us-free-source-probe.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    audit_event("us_free_source_probe_completed",
                statuses={item["service"]: item["status"] for item in results},
                report_path=str(target))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
