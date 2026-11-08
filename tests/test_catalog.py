"""Reference catalog: pinned snapshot provenance, streaming reads, fail-closed behaviour.

The tests write their own tiny snapshot, so they never depend on the vendored 15 MB file.
"""
from __future__ import annotations

import bz2
import json
from pathlib import Path

import pytest

from app.catalog import CatalogUnavailableError, ReferenceCatalog


EQUITY_COLUMNS = ["symbol", "name", "summary", "currency", "sector", "industry_group", "industry",
                  "exchange", "mic", "market", "country", "market_cap", "isin", "cusip", "figi",
                  "composite_figi", "shareclass_figi", "delisted"]


def equity_row(**overrides: str) -> str:
    """Build an aligned CSV row from the column list (a misaligned fixture would hide real bugs)."""
    values = {column: "" for column in EQUITY_COLUMNS}
    values.update(overrides)
    return ",".join(values[column] for column in EQUITY_COLUMNS)


def write_snapshot(root: Path, commit: str = "abc123def456", rows: str | None = None) -> Path:
    snapshot = root / commit
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "provenance.json").write_text(json.dumps({
        "repo": "JerBouma/FinanceDatabase", "commit": commit, "license": "MIT",
    }), encoding="utf-8")
    body = rows if rows is not None else (
        ",".join(EQUITY_COLUMNS) + "\n"
        + equity_row(symbol="AAPL", name="Apple Inc.", summary="iPhone maker", currency="USD",
                     sector="Information Technology", industry_group="Technology Hardware & Equipment",
                     industry="Technology Hardware Storage & Peripherals", exchange="NMS", mic="XNAS",
                     market="us_market", country="United States", market_cap="Mega Cap",
                     isin="US0378331005", cusip="037833100", delisted="False") + "\n"
        + equity_row(symbol="MSFT", name="Microsoft Corp.", summary="Software", currency="USD",
                     sector="Information Technology", industry_group="Software & Services",
                     industry="Systems Software", exchange="NMS", mic="XNAS", market="us_market",
                     country="United States", market_cap="Mega Cap", isin="US5949181045",
                     cusip="594918104", delisted="False") + "\n"
        + equity_row(symbol="OLD", name="Delisted Co.", summary="Gone", currency="USD",
                     sector="Financials", industry_group="Diversified Financials",
                     industry="Diversified Financial Services", exchange="NMS", mic="XNAS",
                     market="us_market", country="United States", market_cap="Micro Cap",
                     delisted="True") + "\n"
    )
    with bz2.open(snapshot / "equities.bz2", "wt", encoding="utf-8", newline="") as handle:
        handle.write(body)
    return snapshot


def test_missing_snapshot_fails_closed(tmp_path) -> None:
    with pytest.raises(CatalogUnavailableError):
        ReferenceCatalog(tmp_path / "does-not-exist")


def test_provenance_commit_must_match_the_directory(tmp_path) -> None:
    root = tmp_path / "reference"
    write_snapshot(root)
    mismatched = root / "abc123def456-wrong"
    mismatched.mkdir(parents=True, exist_ok=True)
    (mismatched / "provenance.json").write_text(
        json.dumps({"repo": "x", "commit": "ffffffffffff", "license": "MIT"}), encoding="utf-8")
    with pytest.raises(CatalogUnavailableError):
        ReferenceCatalog(mismatched)
    with pytest.raises(CatalogUnavailableError):
        ReferenceCatalog(root / "no-such-snapshot")


def test_lookup_and_allowlisted_columns(tmp_path) -> None:
    root = tmp_path / "reference"
    write_snapshot(root)
    catalog = ReferenceCatalog(root / "abc123def456")
    record = catalog.lookup("aapl")
    assert record is not None and record.commit == "abc123def456"
    assert record.fields["sector"] == "Information Technology"
    assert record.fields["industry_group"] == "Technology Hardware & Equipment"
    assert record.fields["mic"] == "XNAS"
    # The `exchange` column is a market tier (NMS), not a venue name: MIC is the venue identifier.
    assert record.fields["exchange"] == "NMS"
    assert set(record.fields) <= set(__import__("app.catalog", fromlist=["EXPOSED_COLUMNS"]).EXPOSED_COLUMNS["equities"])
    assert catalog.lookup("NOPE") is None


def test_search_skips_delisted_and_respects_limit(tmp_path) -> None:
    root = tmp_path / "reference"
    write_snapshot(root)
    catalog = ReferenceCatalog(root / "abc123def456")
    assert [item.fields["symbol"] for item in catalog.search("co")] == ["MSFT"]
    assert [item.fields["symbol"] for item in catalog.search("", limit=5)] == []
    assert len(catalog.search("a", limit=1)) == 1
    assert [item.fields["symbol"] for item in catalog.search("old", exclude_delisted=False)] == ["OLD"]


def test_us_universe_filters_by_mic_and_delisted(tmp_path) -> None:
    root = tmp_path / "reference"
    write_snapshot(root)
    catalog = ReferenceCatalog(root / "abc123def456")
    symbols = [item.fields["symbol"] for item in catalog.us_universe()]
    assert symbols == ["AAPL", "MSFT"]


def test_schema_change_is_reported_not_swallowed(tmp_path) -> None:
    root = tmp_path / "reference"
    write_snapshot(root, rows="ticker,name\nAAPL,Apple\n")
    catalog = ReferenceCatalog(root / "abc123def456")
    with pytest.raises(CatalogUnavailableError):
        list(catalog.rows("equities"))


def test_missing_asset_is_explicit(tmp_path) -> None:
    root = tmp_path / "reference"
    write_snapshot(root)
    catalog = ReferenceCatalog(root / "abc123def456")
    with pytest.raises(CatalogUnavailableError):
        list(catalog.rows("etfs"))


def test_stats_counts_rows_and_sectors(tmp_path) -> None:
    root = tmp_path / "reference"
    write_snapshot(root)
    catalog = ReferenceCatalog(root / "abc123def456")
    stats = catalog.stats("equities")
    assert stats["rows"] == 3 and stats["delisted"] == 1
    assert stats["sectors"]["Information Technology"] == 2
    assert stats["commit"] == "abc123def456"
