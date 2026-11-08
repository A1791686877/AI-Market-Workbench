"""Reference catalog over a pinned FinanceDatabase snapshot (sector/industry/exchange metadata).

Scope discipline
----------------
* This is **metadata**, not market data: never price evidence, never a valuation input, never
  cited as an authoritative fact about a company. Every read states which pinned commit it came from.
* Community-maintained source (MIT). A snapshot is versioned by commit; the loader refuses to guess
  when the snapshot is missing instead of silently degrading.
* Only allowlisted columns are exposed, so a schema change upstream is visible rather than silent.
"""
from __future__ import annotations

import bz2
import csv
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Literal

from app.config import settings

SNAPSHOT_ROOT = "reference/financedatabase"
AssetName = Literal["equities", "etfs", "indices"]
EXPOSED_COLUMNS: dict[AssetName, tuple[str, ...]] = {
    "equities": ("symbol", "name", "currency", "sector", "industry_group", "industry",
                 "exchange", "mic", "market", "country", "market_cap", "delisted"),
    "etfs": ("symbol", "name", "currency", "category_group", "category", "family", "exchange", "mic"),
    "indices": ("symbol", "name", "currency", "category_group", "category", "exchange", "mic"),
}
US_MICS = {"XNAS", "XNYS", "ARCX", "BATS", "XNGS", "XNMS", "XNCM", "IEXG", "OTC"}


class CatalogUnavailableError(RuntimeError):
    """No verified local snapshot: fail closed instead of serving an unversioned guess."""


@dataclass(frozen=True)
class CatalogRecord:
    asset: AssetName
    fields: dict[str, str]
    commit: str

    def get(self, key: str) -> str | None:
        value = self.fields.get(key)
        return value or None


class ReferenceCatalog:
    """Read-only access to one pinned snapshot directory."""

    def __init__(self, snapshot_dir: Path | None = None) -> None:
        self.snapshot_dir = snapshot_dir or self._discover()
        self.commit = self.snapshot_dir.name
        provenance = self.snapshot_dir / "provenance.json"
        if not provenance.exists():
            raise CatalogUnavailableError(f"missing provenance for snapshot {self.snapshot_dir}")
        self.provenance = json.loads(provenance.read_text(encoding="utf-8"))
        if self.provenance.get("commit", "").startswith(self.commit) is False:
            raise CatalogUnavailableError("provenance commit does not match the snapshot directory")

    @staticmethod
    def _discover() -> Path:
        root = settings.root / SNAPSHOT_ROOT
        if not root.exists():
            raise CatalogUnavailableError(
                f"no FinanceDatabase snapshot under {root}; run scripts/fetch_financedatabase.py first"
            )
        candidates = sorted((item for item in root.iterdir() if (item / "provenance.json").exists()),
                            key=lambda item: (item / "provenance.json").stat().st_mtime)
        if not candidates:
            raise CatalogUnavailableError(f"no snapshot with provenance.json under {root}")
        return candidates[-1]

    def path_for(self, asset: AssetName) -> Path:
        path = self.snapshot_dir / f"{asset}.bz2"
        if not path.exists():
            raise CatalogUnavailableError(f"asset {asset} is not part of snapshot {self.commit}")
        return path

    def rows(self, asset: AssetName) -> Iterator[CatalogRecord]:
        with bz2.open(self.path_for(asset), "rt", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            missing = [column for column in ("symbol",) if column not in (reader.fieldnames or [])]
            if missing:
                raise CatalogUnavailableError(f"{asset} snapshot lacks required column(s): {missing}")
            for row in reader:
                fields = {column: (row.get(column) or "") for column in EXPOSED_COLUMNS[asset]}
                yield CatalogRecord(asset=asset, fields=fields, commit=self.commit)

    def lookup(self, symbol: str, asset: AssetName = "equities") -> CatalogRecord | None:
        wanted = symbol.strip().upper()
        for record in self.rows(asset):
            if record.fields["symbol"].strip().upper() == wanted:
                return record
        return None

    def search(self, query: str, asset: AssetName = "equities", limit: int = 20,
               country: str | None = None, exclude_delisted: bool = True) -> list[CatalogRecord]:
        needle = query.strip().lower()
        if not needle:
            return []
        found: list[CatalogRecord] = []
        for record in self.rows(asset):
            if country and (record.fields.get("country") or "").lower() != country.lower():
                continue
            if exclude_delisted and asset == "equities" and (record.fields.get("delisted") or "").lower() in {"1", "true", "yes"}:
                continue
            haystack = f"{record.fields.get('symbol','')} {record.fields.get('name','')}".lower()
            if needle in haystack:
                found.append(record)
                if len(found) >= limit:
                    break
        return found

    def stats(self, asset: AssetName = "equities") -> dict:
        total = 0
        by_sector: dict[str, int] = {}
        by_country: dict[str, int] = {}
        delisted = 0
        for record in self.rows(asset):
            total += 1
            sector = record.fields.get("sector") or "UNKNOWN"
            country = record.fields.get("country") or "UNKNOWN"
            by_sector[sector] = by_sector.get(sector, 0) + 1
            by_country[country] = by_country.get(country, 0) + 1
            if (record.fields.get("delisted") or "").lower() in {"1", "true", "yes"}:
                delisted += 1
        return {"asset": asset, "commit": self.commit, "rows": total, "delisted": delisted,
                "sectors": dict(sorted(by_sector.items(), key=lambda item: -item[1])[:25]),
                "countries": dict(sorted(by_country.items(), key=lambda item: -item[1])[:15])}

    def us_universe(self, asset: AssetName = "equities", limit: int = 0) -> list[CatalogRecord]:
        """US-listed rows (by MIC), delisted excluded. `limit=0` means no cap."""
        found: list[CatalogRecord] = []
        for record in self.rows(asset):
            mic = (record.fields.get("mic") or "").upper()
            country = (record.fields.get("country") or "").upper()
            if mic not in US_MICS and country != "UNITED STATES":
                continue
            if asset == "equities" and (record.fields.get("delisted") or "").lower() in {"1", "true", "yes"}:
                continue
            found.append(record)
            if limit and len(found) >= limit:
                break
        return found


@lru_cache(maxsize=1)
def catalog() -> ReferenceCatalog:
    return ReferenceCatalog()
