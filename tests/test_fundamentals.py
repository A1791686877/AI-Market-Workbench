"""SEC Company Facts normalizer: point-in-time discipline, derivations, explicit missing."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.fundamentals import normalize_company_facts
from app.instruments import equity_subject

CUTOFF = datetime(2026, 9, 12, tzinfo=UTC)
SHA = "0" * 64
SUBJECT = equity_subject("AAPL")


def annual(end: str, value: float, fy: int, filed: str, accn: str = "0000320193-25-000001") -> dict:
    start = (date.fromisoformat(end) - timedelta(days=364)).isoformat()
    return {"start": start, "end": end, "val": value, "accn": accn, "fy": fy, "fp": "FY",
            "form": "10-K", "filed": filed}


def instant(end: str, value: float, fy: int, filed: str, form: str = "10-Q", accn: str = "a-1") -> dict:
    return {"end": end, "val": value, "accn": accn, "fy": fy, "fp": "Q3", "form": form, "filed": filed}


def payload(concepts: dict[str, tuple[str, list[dict]]]) -> dict:
    return {"cik": 320193, "entityName": "Test Inc.",
            "facts": {"us-gaap": {name: {"units": {unit: rows}} for name, (unit, rows) in concepts.items()}}}


def fact_map(fundamentals):
    return {fact.name.value: fact for fact in fundamentals.facts}


def test_point_in_time_excludes_filings_after_cutoff() -> None:
    data = payload({"Revenues": ("USD", [
        annual("2025-09-27", 100.0, 2025, "2025-10-31"),
        annual("2026-09-26", 200.0, 2026, "2026-10-31"),  # filed after the cutoff
    ])})
    facts = fact_map(normalize_company_facts(data, SHA, SUBJECT, cik=320193, cutoff=CUTOFF))
    assert facts["REVENUE"].value == "100"
    assert facts["REVENUE"].fiscal_period == "FY2025"


def test_annual_is_preferred_over_quarterly() -> None:
    quarterly = {"start": "2026-03-29", "end": "2026-06-27", "val": 50.0, "accn": "q-1", "fy": 2026,
                 "fp": "Q3", "form": "10-Q", "filed": "2026-07-31"}
    data = payload({"RevenueFromContractWithCustomerExcludingAssessedTax": ("USD", [
        annual("2025-09-27", 100.0, 2025, "2025-10-31"), quarterly,
    ])})
    facts = fact_map(normalize_company_facts(data, SHA, SUBJECT, cik=320193, cutoff=CUTOFF))
    assert facts["REVENUE"].value == "100"
    assert facts["REVENUE"].fiscal_period == "FY2025"


def test_derived_free_cash_flow_records_formula_and_inputs() -> None:
    data = payload({
        "NetCashProvidedByUsedInOperatingActivities": ("USD", [annual("2025-09-27", 111.0, 2025, "2025-10-31")]),
        "PaymentsToAcquirePropertyPlantAndEquipment": ("USD", [annual("2025-09-27", 11.0, 2025, "2025-10-31")]),
    })
    facts = fact_map(normalize_company_facts(data, SHA, SUBJECT, cik=320193, cutoff=CUTOFF))
    fcf = facts["FREE_CASH_FLOW"]
    assert fcf.value == "100"
    assert fcf.derivation and "OPERATING_CASH_FLOW" in fcf.derivation
    assert len(fcf.input_fact_ids) == 2


def test_total_debt_requires_a_single_instant_composition() -> None:
    same = payload({
        "LongTermDebtNoncurrent": ("USD", [instant("2026-06-27", 70.0, 2026, "2026-07-31", accn="d-1")]),
        "LongTermDebtCurrent": ("USD", [instant("2026-06-27", 10.0, 2026, "2026-07-31", accn="d-1")]),
    })
    facts = fact_map(normalize_company_facts(same, SHA, SUBJECT, cik=320193, cutoff=CUTOFF))
    assert facts["TOTAL_DEBT"].value == "80"
    assert facts["TOTAL_DEBT"].filed_at is not None  # inherits the component filing date

    mixed = payload({
        "LongTermDebtNoncurrent": ("USD", [instant("2026-06-27", 70.0, 2026, "2026-07-31")]),
        "CommercialPaper": ("USD", [instant("2026-03-28", 5.0, 2026, "2026-05-01")]),
    })
    facts = fact_map(normalize_company_facts(mixed, SHA, SUBJECT, cik=320193, cutoff=CUTOFF))
    assert facts["TOTAL_DEBT"].status.value == "MISSING"
    assert "composition" in (facts["TOTAL_DEBT"].missing_reason or "")


def test_growth_and_market_cap_derivations_and_explicit_pe_missing() -> None:
    data = payload({
        "Revenues": ("USD", [annual("2024-09-28", 100.0, 2024, "2024-11-01", accn="r-24"),
                             annual("2025-09-27", 110.0, 2025, "2025-10-31", accn="r-25")]),
        "WeightedAverageNumberOfDilutedSharesOutstanding": ("shares", [annual("2025-09-27", 2e9, 2025, "2025-10-31")]),
    })
    fundamentals = normalize_company_facts(data, SHA, SUBJECT, cik=320193, cutoff=CUTOFF, last_close=100.0)
    facts = fact_map(fundamentals)
    assert facts["REVENUE_GROWTH"].value == "0.1"
    assert "FY2025 / FY2024" in (facts["REVENUE_GROWTH"].derivation or "")
    assert facts["MARKET_CAP"].value == "200000000000"
    assert facts["PE_TTM"].status.value == "MISSING"
    assert any("PE_TTM" in item for item in fundamentals.missing_items)


def test_revenue_missing_before_first_filing_is_explicit() -> None:
    data = payload({"Revenues": ("USD", [annual("2026-09-26", 200.0, 2026, "2026-10-31")])})
    fundamentals = normalize_company_facts(data, SHA, SUBJECT, cik=320193, cutoff=CUTOFF)
    facts = fact_map(fundamentals)
    assert facts["REVENUE"].status.value == "MISSING"
    assert facts["REVENUE"].missing_reason
    assert any("REVENUE" in item for item in fundamentals.missing_items)


def test_latest_tag_wins_and_cross_period_cash_flow_is_refused() -> None:
    """Regression: NVDA-style tag switches produced FY2011 capex and a nonsense FCF."""
    data = payload({
        "Revenues": ("USD", [annual("2022-01-30", 26.9, 2022, "2022-03-18", accn="old-1")]),
        "RevenueFromContractWithCustomerExcludingAssessedTax": (
            "USD", [annual("2026-01-25", 210.0, 2026, "2026-02-25", accn="new-1")]),
        "NetCashProvidedByUsedInOperatingActivities": (
            "USD", [annual("2026-01-25", 102.0, 2026, "2026-02-25", accn="new-1")]),
        "PaymentsToAcquirePropertyPlantAndEquipment": (
            "USD", [annual("2011-01-30", 0.1, 2011, "2012-03-13", accn="stale-1")]),
    })
    fundamentals = normalize_company_facts(data, SHA, SUBJECT, cik=320193, cutoff=CUTOFF)
    facts = fact_map(fundamentals)
    assert facts["REVENUE"].fiscal_period == "FY2026"      # not the abandoned FY2022 tag
    assert facts["CAPITAL_EXPENDITURE"].fiscal_period == "FY2011"
    assert facts["FREE_CASH_FLOW"].status.value == "MISSING"
    assert "different fiscal periods" in (facts["FREE_CASH_FLOW"].missing_reason or "")
    assert any("FREE_CASH_FLOW" in item for item in fundamentals.missing_items)


def test_market_cap_reads_millions_scale_share_counts() -> None:
    """Regression (found via MCD): some issuers tag shares in millions, others absolutely."""
    millions = payload({
        "WeightedAverageNumberOfDilutedSharesOutstanding": (
            "shares", [annual("2025-12-31", 716.4, 2025, "2026-02-20")]),
    })
    facts = fact_map(normalize_company_facts(millions, SHA, SUBJECT, cik=320193, cutoff=CUTOFF, last_close=252.78))
    assert facts["MARKET_CAP"].value == "181091592000"       # 716.4e6 * 252.78 = 181.09B
    assert "百万股" in (facts["MARKET_CAP"].derivation or "")

    absolute = payload({
        "WeightedAverageNumberOfDilutedSharesOutstanding": (
            "shares", [annual("2025-12-31", 15004697000.0, 2025, "2026-02-20")]),
    })
    facts = fact_map(normalize_company_facts(absolute, SHA, SUBJECT, cik=320193, cutoff=CUTOFF, last_close=252.78))
    assert facts["MARKET_CAP"].value == "3792887307660"      # used as-is
    assert "百万股" not in (facts["MARKET_CAP"].derivation or "")


def test_growth_needs_two_consecutive_years_of_the_same_tag() -> None:
    data = payload({
        "RevenueFromContractWithCustomerExcludingAssessedTax": (
            "USD", [annual("2024-09-28", 100.0, 2024, "2024-11-01", accn="a"),
                    annual("2026-09-26", 150.0, 2026, "2026-10-31", accn="b")]),  # FY2025 missing
    })
    fundamentals = normalize_company_facts(data, SHA, SUBJECT, cik=320193, cutoff=datetime(2027, 1, 1, tzinfo=UTC))
    facts = fact_map(fundamentals)
    assert facts["REVENUE_GROWTH"].status.value == "MISSING"
    assert "consecutive" in (facts["REVENUE_GROWTH"].missing_reason or "")


def quarter(end: str, value: float, filed: str, accn: str = "q-1") -> dict:
    start = (date.fromisoformat(end) - timedelta(days=90)).isoformat()
    return {"start": start, "end": end, "val": value, "accn": accn, "fy": 2026, "fp": "Q1",
            "form": "10-Q", "filed": filed}


def test_pe_ttm_sums_four_single_quarters_and_records_the_policy() -> None:
    """TTM EPS policy (agreed with the user): the last four single-quarter diluted EPS facts."""
    data = payload({
        "EarningsPerShareDiluted": ("USD/shares", [
            quarter("2025-09-27", 2.0, "2025-10-31", "q-2025q4"),
            quarter("2025-12-27", 2.5, "2026-01-31", "q-2026q1"),
            quarter("2026-03-28", 3.0, "2026-05-01", "q-2026q2"),
            quarter("2026-06-27", 3.5, "2026-07-31", "q-2026q3"),
        ]),
        "WeightedAverageNumberOfDilutedSharesOutstanding": ("shares", [annual("2025-09-27", 2e9, 2025, "2025-10-31")]),
    })
    fundamentals = normalize_company_facts(data, SHA, SUBJECT, cik=320193, cutoff=CUTOFF, last_close=110.0)
    facts = fact_map(fundamentals)

    assert facts["TTM_EPS"].value == "11"
    assert facts["TTM_EPS"].period_end == date(2026, 6, 27)
    assert "SUM(4 个单季" in (facts["TTM_EPS"].derivation or "")
    assert len(facts["TTM_EPS"].input_fact_ids) == 4
    assert facts["PE_TTM"].value == "10"                     # 110 / 11
    assert facts["PE_TTM"].status.value == "AVAILABLE"
    assert facts["PE_TTM"].input_fact_ids == [facts["TTM_EPS"].fact_id]
    assert not any("PE_TTM" in item for item in fundamentals.missing_items)


def test_pe_ttm_refuses_ytd_figures_and_gapped_quarters() -> None:
    """A 9-month YTD row is not a quarter, and a missing quarter is not interpolated."""
    ytd = {"start": "2025-09-28", "end": "2026-06-27", "val": 9.0, "accn": "ytd", "fy": 2026, "fp": "Q3",
           "form": "10-Q", "filed": "2026-07-31"}
    data = payload({"EarningsPerShareDiluted": ("USD/shares", [
        quarter("2025-09-27", 2.0, "2025-10-31"),
        quarter("2025-12-27", 2.5, "2026-01-31"),
        ytd,                                                   # excluded: 273-day duration
        quarter("2026-06-27", 3.5, "2026-07-31"),
    ])})
    fundamentals = normalize_company_facts(data, SHA, SUBJECT, cik=320193, cutoff=CUTOFF, last_close=110.0)
    facts = fact_map(fundamentals)
    assert facts["TTM_EPS"].status.value == "MISSING"
    assert "found 3" in (facts["TTM_EPS"].missing_reason or "")
    assert facts["PE_TTM"].status.value == "MISSING"

    gap = payload({"EarningsPerShareDiluted": ("USD/shares", [
        quarter("2025-03-29", 2.0, "2025-05-01"),
        quarter("2025-06-28", 2.5, "2025-07-31"),
        # 2025-09 quarter missing
        quarter("2025-12-27", 3.0, "2026-01-31"),
        quarter("2026-03-28", 3.5, "2026-05-01"),
    ])})
    gapped = fact_map(normalize_company_facts(gap, SHA, SUBJECT, cik=320193, cutoff=CUTOFF, last_close=110.0))
    assert gapped["TTM_EPS"].status.value == "MISSING"
    assert "consecutive" in (gapped["TTM_EPS"].missing_reason or "")


def test_pe_ttm_derives_q4_from_fy_minus_nine_months() -> None:
    """XBRL never tags a standalone Q4: it must come from FY - 9M YTD, cross-checked with Q1-Q3."""
    def ytd(start: str, end: str, value: float, filed: str) -> dict:
        return {"start": start, "end": end, "val": value, "accn": "k-1", "fy": 2025, "fp": "FY",
                "form": "10-K", "filed": filed}

    data = payload({"EarningsPerShareDiluted": ("USD/shares", [
        # FY2025: Q1-Q3 tagged as single quarters, Q4 absent, 9M + FY as cumulative durations.
        quarter("2024-12-28", 1.5, "2025-01-31"),
        quarter("2025-03-29", 1.8, "2025-05-01"),
        quarter("2025-06-28", 2.1, "2025-07-31"),
        ytd("2024-09-29", "2025-06-28", 5.4, "2025-07-31"),
        ytd("2024-09-29", "2025-09-27", 8.0, "2025-10-31"),
        # FY2026: the three quarters that follow.
        quarter("2025-12-27", 2.0, "2026-01-31"),
        quarter("2026-03-28", 2.2, "2026-05-01"),
        quarter("2026-06-27", 2.4, "2026-07-31"),
    ])})
    fundamentals = normalize_company_facts(data, SHA, SUBJECT, cik=320193, cutoff=CUTOFF, last_close=66.0)
    facts = fact_map(fundamentals)

    assert facts["TTM_EPS"].value == "9.2"          # 2.6 (derived Q4) + 2.0 + 2.2 + 2.4
    assert facts["TTM_EPS"].period_end == date(2026, 6, 27)
    assert "Q4 = FY − 9M YTD" in (facts["TTM_EPS"].derivation or "")
    assert any(item.startswith("Q4-FY-") for item in facts["TTM_EPS"].input_fact_ids)
    assert facts["PE_TTM"].value == "7.1739"        # 66 / 9.2


def test_pe_ttm_refuses_q4_when_nine_month_figures_do_not_reconcile() -> None:
    def ytd(start: str, end: str, value: float, filed: str) -> dict:
        return {"start": start, "end": end, "val": value, "accn": "k-1", "fy": 2025, "fp": "FY",
                "form": "10-K", "filed": filed}

    data = payload({"EarningsPerShareDiluted": ("USD/shares", [
        quarter("2024-12-28", 1.5, "2025-01-31"),
        quarter("2025-03-29", 1.8, "2025-05-01"),
        quarter("2025-06-28", 2.1, "2025-07-31"),
        ytd("2024-09-29", "2025-06-28", 9.0, "2025-07-31"),   # inconsistent with Q1-Q3 (5.4)
        ytd("2024-09-29", "2025-09-27", 8.0, "2025-10-31"),
    ])})
    facts = fact_map(normalize_company_facts(data, SHA, SUBJECT, cik=320193, cutoff=CUTOFF, last_close=66.0))
    assert facts["TTM_EPS"].status.value == "MISSING"
    assert "does not reconcile" in (facts["TTM_EPS"].missing_reason or "")
    assert facts["PE_TTM"].status.value == "MISSING"


def test_q4_derivation_tolerates_share_count_drift_when_net_income_reconciles() -> None:
    """Regression: EPS is a ratio, so quarterly EPS do not sum to the 9M EPS under buybacks.

    The period structure is therefore proven with net income (additive), and the EPS-only
    fallback is only used — with a looser tolerance — when income facts are unavailable.
    """
    def ytd(start: str, end: str, value: float, filed: str, unit_days: int = 0) -> dict:
        return {"start": start, "end": end, "val": value, "accn": "k-1", "fy": 2025, "fp": "FY",
                "form": "10-K", "filed": filed}

    data = payload({
        "EarningsPerShareDiluted": ("USD/shares", [
            quarter("2024-12-28", 1.50, "2025-01-31"),
            quarter("2025-03-29", 1.80, "2025-05-01"),
            quarter("2025-06-28", 2.10, "2025-07-31"),
            ytd("2024-09-29", "2025-06-28", 5.10, "2025-07-31"),   # 5.10 vs Q1-Q3 = 5.40 (-5.6%)
            ytd("2024-09-29", "2025-09-27", 8.00, "2025-10-31"),
            quarter("2025-12-27", 2.00, "2026-01-31"),
            quarter("2026-03-28", 2.20, "2026-05-01"),
            quarter("2026-06-27", 2.40, "2026-07-31"),
        ]),
        "NetIncomeLoss": ("USD", [
            {"start": "2024-09-29", "end": "2024-12-28", "val": 30.0, "accn": "q1", "fy": 2025, "fp": "Q1",
             "form": "10-Q", "filed": "2025-01-31"},
            {"start": "2024-12-29", "end": "2025-03-29", "val": 36.0, "accn": "q2", "fy": 2025, "fp": "Q2",
             "form": "10-Q", "filed": "2025-05-01"},
            {"start": "2025-03-30", "end": "2025-06-28", "val": 42.0, "accn": "q3", "fy": 2025, "fp": "Q3",
             "form": "10-Q", "filed": "2025-07-31"},
            ytd("2024-09-29", "2025-06-28", 108.0, "2025-07-31"),
            ytd("2024-09-29", "2025-09-27", 160.0, "2025-10-31"),
        ]),
    })
    facts = fact_map(normalize_company_facts(data, SHA, SUBJECT, cik=320193, cutoff=CUTOFF, last_close=66.0))

    assert facts["TTM_EPS"].status.value == "AVAILABLE"
    assert facts["TTM_EPS"].value == "9.5"                 # Q4 = 8.00 - 5.10 = 2.90, then +2.0+2.2+2.4
    assert "net_income±1%" in (facts["TTM_EPS"].derivation or "")
def test_pe_ttm_needs_a_positive_multiple_and_a_price() -> None:
    loss = payload({"EarningsPerShareDiluted": ("USD/shares", [
        quarter("2025-09-27", -1.0, "2025-10-31"),
        quarter("2025-12-27", -1.0, "2026-01-31"),
        quarter("2026-03-28", -1.0, "2026-05-01"),
        quarter("2026-06-27", -1.0, "2026-07-31"),
    ])})
    facts = fact_map(normalize_company_facts(loss, SHA, SUBJECT, cik=320193, cutoff=CUTOFF, last_close=110.0))
    assert facts["TTM_EPS"].value == "-4"
    assert facts["PE_TTM"].status.value == "MISSING"
    assert "not positive" in (facts["PE_TTM"].missing_reason or "")

    no_price = payload({"EarningsPerShareDiluted": ("USD/shares", [
        quarter("2025-09-27", 2.0, "2025-10-31"),
        quarter("2025-12-27", 2.5, "2026-01-31"),
        quarter("2026-03-28", 3.0, "2026-05-01"),
        quarter("2026-06-27", 3.5, "2026-07-31"),
    ])})
    missing = fact_map(normalize_company_facts(no_price, SHA, SUBJECT, cik=320193, cutoff=CUTOFF))
    assert missing["TTM_EPS"].status.value == "AVAILABLE"
    assert missing["PE_TTM"].status.value == "MISSING"
    assert "confirmed close" in (missing["PE_TTM"].missing_reason or "")
