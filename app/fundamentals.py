"""SEC EDGAR Company Facts (US-GAAP XBRL) -> point-in-time EquityFundamentals.

Discipline:
- point-in-time: only facts whose ``filed`` date is on or before the cutoff are used;
- no derivation without recording the formula and its input fact IDs;
- anything that cannot be composed defensibly stays MISSING with a reason.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.fixtures import decimal_string
from app.models import (
    AccountingBasis,
    AnalysisSubject,
    Completeness,
    DataProvider,
    DataStatus,
    DelayClass,
    EquityFundamentals,
    FilingRef,
    FundamentalFact,
    FundamentalMetricName,
    MarketScope,
    SourceEnvelope,
)

DURATION_CONCEPTS: dict[FundamentalMetricName, tuple[str, ...]] = {
    FundamentalMetricName.REVENUE: (
        "RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
    ),
    FundamentalMetricName.NET_INCOME: ("NetIncomeLoss",),
    FundamentalMetricName.OPERATING_CASH_FLOW: ("NetCashProvidedByUsedInOperatingActivities",),
    FundamentalMetricName.CAPITAL_EXPENDITURE: (
        "PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
    ),
    FundamentalMetricName.DILUTED_SHARES: (
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "WeightedAverageNumberOfDilutedSharesOutstandingBasicAndDiluted",
    ),
}
INSTANT_CONCEPTS: dict[FundamentalMetricName, tuple[str, ...]] = {
    FundamentalMetricName.CASH_AND_EQUIVALENTS: (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ),
}
DEBT_COMPONENTS = ("LongTermDebtNoncurrent", "LongTermDebtCurrent", "CommercialPaper", "ShortTermBorrowings")
ANNUAL_MIN_DAYS, ANNUAL_MAX_DAYS = 300, 400
# TTM EPS policy (agreed with the user): sum the last four *single-quarter* diluted EPS facts.
# Anything that is not a clean chain of four ~3-month periods stays MISSING — YTD/半年 figures are
# never sliced or interpolated into quarters.
EPS_CONCEPTS = ("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted")
QUARTER_MIN_DAYS, QUARTER_MAX_DAYS = 80, 100
NINE_MONTH_MIN_DAYS, NINE_MONTH_MAX_DAYS = 260, 280
TTM_SPAN_MIN_DAYS, TTM_SPAN_MAX_DAYS = 350, 380
# Q4 is never tagged as a standalone duration in XBRL (the 10-K reports the full year and the
# 9-month year-to-date), so Q4 must be derived as FY - 9M and cross-checked against Q1+Q2+Q3.
NINE_MONTH_CROSSCHECK_TOLERANCE = 0.02
# Some issuers tag share counts in MILLIONS (MCD: 716.4 for ~716M shares) instead of absolutely
# (AAPL: 15,004,697,000). A value below this threshold is read as millions, and the reading is
# written into the derivation instead of being applied silently.
SHARES_MILLIONS_THRESHOLD = 1_000_000
TTM_EPS_POLICY = ("TTM_EPS = Q1 + Q2 + Q3 + Q4，其中 Q1–Q3 取单季摊薄 EPS，"
                  "Q4（XBRL 不单独披露）由 FY − 9M YTD 推导，并用 9M ≈ Q1+Q2+Q3 交叉校验（±2%）")

LIMITATIONS = [
    "SEC EDGAR Company Facts are GAAP XBRL as filed; non-GAAP measures are not mixed in.",
    "Company Facts has no acceptance timestamp: filed date is used at 00:00 UTC and acceptance time is unavailable.",
    "Total debt is only reported when an explicit composition of XBRL debt tags exists; it is never guessed.",
    "Market scope is UNKNOWN (single-source filing data, not a market feed).",
    "MARKET_CAP 的股数按「小于 100 万即视为百万股」换算（原始值与判定写进 derivation）；"
    "P/E 不受影响，因为 EPS 本身是每股口径。",
    "TTM EPS 为 GAAP 摊薄口径（Q4 由 FY − 9M 推导）；未剔除一次性项目，PE_TTM 可能被一次性损益"
    "（如按市值计价的投资收益、诉讼一次性收支）扭曲，非 GAAP/调整后 EPS 未纳入。",
]


def _rows(payload: dict, concept: str) -> tuple[str, list[dict]]:
    item = (payload.get("facts", {}).get("us-gaap", {}) or {}).get(concept)
    if not item:
        return "", []
    units = item.get("units", {}) or {}
    if not units:
        return "", []
    unit = sorted(units)[0]
    return unit, [row for row in units[unit] if isinstance(row, dict)]


def _filed_ok(row: dict, cutoff: date) -> bool:
    filed = row.get("filed")
    if not filed:
        return False
    try:
        return date.fromisoformat(str(filed)) <= cutoff
    except ValueError:
        return False


def _duration_days(row: dict) -> int | None:
    start, end = row.get("start"), row.get("end")
    if not start or not end:
        return None
    try:
        return (date.fromisoformat(str(end)) - date.fromisoformat(str(start))).days
    except ValueError:
        return None


def _pick_duration(rows: list[dict], cutoff: date) -> dict | None:
    """Prefer the latest annual (10-K/FY) fact; fall back to the latest quarterly one."""
    annual = [row for row in rows if row.get("form") == "10-K" and _filed_ok(row, cutoff)
              and (_duration_days(row) or 0) >= ANNUAL_MIN_DAYS
              and (_duration_days(row) or 0) <= ANNUAL_MAX_DAYS]
    if annual:
        return max(annual, key=lambda row: (str(row.get("end")), str(row.get("filed"))))
    quarter = [row for row in rows if row.get("form") in {"10-K", "10-Q"} and _filed_ok(row, cutoff)
               and row.get("start") and row.get("end")]
    return max(quarter, key=lambda row: (str(row.get("end")), str(row.get("filed")))) if quarter else None


def _pick_instant(rows: list[dict], cutoff: date) -> dict | None:
    instants = [row for row in rows if _filed_ok(row, cutoff) and row.get("end") and not row.get("start")
                and row.get("form") in {"10-K", "10-Q"}]
    return max(instants, key=lambda row: (str(row.get("end")), str(row.get("filed")))) if instants else None


def _pick_across(payload: dict, concepts: tuple[str, ...], cutoff: date, *, instant: bool
                 ) -> tuple[str | None, str, dict | None]:
    """Pick the most recent fact across ALL candidate tags.

    Companies switch XBRL tags over time (NVDA revenue moved from ``Revenues`` to
    ``RevenueFromContractWithCustomerExcludingAssessedTax``, capex tags changed too).
    Taking "the first tag that has rows" silently returns stale data from an
    abandoned tag, so every candidate is evaluated and the latest period wins.
    """
    best_row, best_key, best_concept, best_unit = None, ("", ""), None, ""
    for concept in concepts:
        unit, rows = _rows(payload, concept)
        if not rows:
            continue
        row = _pick_instant(rows, cutoff) if instant else _pick_duration(rows, cutoff)
        if row is None:
            continue
        key = (str(row.get("end") or ""), str(row.get("filed") or ""))
        if best_row is None or key > best_key:
            best_row, best_key, best_concept, best_unit = row, key, concept, unit
    return best_concept, best_unit, best_row


def _fact(name, symbol: str, unit: str | None, row: dict | None, *, reason: str, derivation: str | None = None,
          input_fact_ids: list[str] | None = None) -> FundamentalFact:
    if row is None:
        return FundamentalFact(
            fact_id=f"fact-{symbol.lower()}-{name.value.lower()}", name=name, value=None, unit=unit,
            status=DataStatus.MISSING, accounting_basis=AccountingBasis.GAAP, fiscal_period=None,
            period_start=None, period_end=None, filed_at=None, accepted_at=None,
            source_ref=f"src-sec-{symbol.lower()}-companyfacts", missing_reason=reason,
        )
    filed = date.fromisoformat(str(row["filed"])) if row.get("filed") else None
    fp, fy = str(row.get("fp") or "").strip(), str(row.get("fy") or "").strip()
    fiscal = (f"FY{fy}" if fp == "FY" and fy else f"{fp} FY{fy}".strip() if fy else (fp or None))
    return FundamentalFact(
        fact_id=f"fact-{symbol.lower()}-{name.value.lower()}-{(row.get('fp') or 'na').lower()}{row.get('fy') or ''}",
        name=name,
        value=decimal_string(float(row["val"]), 2),
        unit=unit or None,
        status=DataStatus.AVAILABLE,
        accounting_basis=AccountingBasis.GAAP,
        fiscal_period=fiscal,
        period_start=date.fromisoformat(str(row["start"])) if row.get("start") else None,
        period_end=date.fromisoformat(str(row["end"])) if row.get("end") else None,
        filed_at=filed,
        accepted_at=datetime(filed.year, filed.month, filed.day, tzinfo=UTC) if filed else None,
        source_ref=f"src-sec-{symbol.lower()}-companyfacts",
        missing_reason=None,
        derivation=derivation,
        input_fact_ids=input_fact_ids or [],
    )


def _eps_rows(payload: dict, cutoff_date: date, min_days: int, max_days: int) -> tuple[str | None, list[dict]]:
    """EPS rows of one duration class, newest filing per (start, end) period, filed before the cutoff."""
    for concept in EPS_CONCEPTS:
        _, rows = _rows(payload, concept)
        keep = [row for row in rows
                if row.get("start") and row.get("end")
                and min_days <= (_duration_days(row) or 0) <= max_days
                and _filed_ok(row, cutoff_date)]
        if not keep:
            continue
        latest_period: dict[tuple[str, str], dict] = {}
        for row in sorted(keep, key=lambda item: str(item.get("filed") or "")):
            latest_period[(str(row["start"]), str(row["end"]))] = row
        return concept, list(latest_period.values())
    return None, []


def _net_income_rows(payload: dict, cutoff_date: date, min_days: int, max_days: int) -> list[dict]:
    """Net-income rows of one duration class — used to prove the Q4 periods line up exactly."""
    _, rows = _rows(payload, DURATION_CONCEPTS[FundamentalMetricName.NET_INCOME][0])
    keep = [row for row in rows
            if row.get("start") and row.get("end")
            and min_days <= (_duration_days(row) or 0) <= max_days
            and _filed_ok(row, cutoff_date)]
    latest_period: dict[tuple[str, str], dict] = {}
    for row in sorted(keep, key=lambda item: str(item.get("filed") or "")):
        latest_period[(str(row["start"]), str(row["end"]))] = row
    return list(latest_period.values())


def _ni_close(first: float, second: float, tolerance: float) -> bool:
    scale = max(abs(first), abs(second), 1.0)
    return abs(first - second) / scale <= tolerance


def _quarterly_eps_rows(payload: dict, cutoff_date: date) -> tuple[str | None, list[dict]]:
    """Single-quarter diluted EPS rows (kept for diagnostics and for Q1–Q3 of a fiscal year)."""
    return _eps_rows(payload, cutoff_date, QUARTER_MIN_DAYS, QUARTER_MAX_DAYS)


def build_ttm_eps(payload: dict, cutoff_date: date, symbol: str, source_id: str
                  ) -> tuple[FundamentalFact | None, str]:
    """Four consecutive quarters -> TTM diluted EPS, with formula and inputs recorded.

    Q4 is derived as ``FY - 9M YTD`` because XBRL tags only the annual and year-to-date durations
    in a 10-K. The derivation is cross-checked against Q1+Q2+Q3 (2% tolerance) before it is used;
    a mismatch is reported instead of being averaged away.
    """
    concept, quarters = _quarterly_eps_rows(payload, cutoff_date)
    if concept is None:
        return None, ("no single-quarter diluted EPS facts (80–100 day periods) at or before the cutoff; "
                      "year-to-date figures are never split into quarters by hand")
    _, nine_month_rows = _eps_rows(payload, cutoff_date, NINE_MONTH_MIN_DAYS, NINE_MONTH_MAX_DAYS)
    _, annual_rows = _eps_rows(payload, cutoff_date, ANNUAL_MIN_DAYS, ANNUAL_MAX_DAYS)
    # A fiscal year and its 9-month year-to-date share the same START date (the year-to-date ends at
    # the third quarter), so the pairing key is ``start``, not ``end``.
    fiscal_by_start = {str(row["start"]): row for row in annual_rows}

    periods: dict[str, dict] = {}
    for row in quarters:
        periods[str(row["end"])] = {"start": str(row["start"]), "end": str(row["end"]), "val": float(row["val"]),
                                    "filed": str(row.get("filed") or ""), "derived": False,
                                    "fact": f"EPS-Q-{row['end']}"}
    derived_q4: dict[str, dict] = {}
    q4_reasons: list[str] = []
    # Newest fiscal year first: the most recent complete quarter chain is what TTM needs.
    for row in sorted(nine_month_rows, key=lambda item: str(item["end"]), reverse=True):
        fiscal = fiscal_by_start.get(str(row["start"]))
        if fiscal is None:
            continue
        span = (date.fromisoformat(str(fiscal["end"])) - date.fromisoformat(str(row["end"]))).days
        if not QUARTER_MIN_DAYS <= span <= QUARTER_MAX_DAYS:
            continue
        prior = [item for item in quarters
                 if str(row["end"]) >= str(item["end"]) > str(fiscal["start"])]
        prior.sort(key=lambda item: str(item["end"]))
        if len(prior) < 3:
            continue
        first_three = [item for item in prior[-3:]]
        ytd = float(row["val"])
        # Cross-check the period structure with net income first: income is additive, while a
        # per-share ratio is not (a buyback changes the denominator every quarter).
        quarter_ni = _net_income_rows(payload, cutoff_date, QUARTER_MIN_DAYS, QUARTER_MAX_DAYS)
        nine_month_ni = _net_income_rows(payload, cutoff_date, NINE_MONTH_MIN_DAYS, NINE_MONTH_MAX_DAYS)
        annual_ni = _net_income_rows(payload, cutoff_date, ANNUAL_MIN_DAYS, ANNUAL_MAX_DAYS)
        ni_for_period = {(str(item["start"]), str(item["end"])): float(item["val"]) for item in quarter_ni}
        nine_ni_row = next((item for item in nine_month_ni
                            if str(item["start"]) == str(fiscal["start"]) and str(item["end"]) == str(row["end"])), None)
        annual_ni_row = next((item for item in annual_ni
                              if str(item["start"]) == str(fiscal["start"]) and str(item["end"]) == str(fiscal["end"])),
                             None)
        first_three_ni = [ni_for_period.get((str(item["start"]), str(item["end"]))) for item in first_three]
        check = None
        if (nine_ni_row is not None and annual_ni_row is not None
                and all(value is not None for value in first_three_ni)):
            if _ni_close(float(nine_ni_row["val"]), sum(first_three_ni), 0.01):
                check = "net_income±1%"
        if check is None and ytd != 0 and abs(sum(float(item["val"]) for item in first_three) - ytd) / abs(ytd) <= 0.10:
            # Fallback: per-share ratios drift with the share count, so the EPS-only check is looser.
            check = "eps±10%"
        if check is None:
            q4_reasons.append(f"FY ending {fiscal['end']}: Q1+Q2+Q3 does not reconcile with the 9M figure")
            continue
        derived_q4[str(fiscal["end"])] = {
            "start": (date.fromisoformat(str(row["end"])) + timedelta(days=1)).isoformat(),
            "end": str(fiscal["end"]), "val": float(fiscal["val"]) - ytd,
            "filed": str(fiscal.get("filed") or ""), "derived": True, "check": check,
            "fact": f"Q4-FY-{fiscal['end']}-minus-9M-{row['end']}",
        }
    periods.update(derived_q4)

    ordered = sorted(periods.values(), key=lambda item: item["end"])
    if len(ordered) < 4:
        detail = ("; ".join(q4_reasons[:2]) if q4_reasons
                  else "no fiscal year with a reconcilable 9-month year-to-date figure")
        return None, f"needs four quarters after deriving Q4, found {len(ordered)} ({detail})"
    window = ordered[-4:]
    ends = [date.fromisoformat(item["end"]) for item in window]
    starts = [date.fromisoformat(item["start"]) for item in window]
    gaps = [(ends[index + 1] - ends[index]).days for index in range(3)]
    if any(not QUARTER_MIN_DAYS <= gap <= QUARTER_MAX_DAYS for gap in gaps):
        return None, f"quarter ends are not consecutive ({gaps} days between ends)"
    span = (ends[-1] - starts[0]).days
    if not TTM_SPAN_MIN_DAYS <= span <= TTM_SPAN_MAX_DAYS:
        return None, f"four quarters do not tile one year (span {span} days, expected 350–380)"
    ttm = sum(item["val"] for item in window)
    latest_filed = max(item["filed"] for item in window if item["filed"])
    derived_items = [item for item in window if item["derived"]]
    used_derivation = bool(derived_items)
    check_note = ""
    if used_derivation:
        checks = sorted({item.get("check", "unverified") for item in derived_items})
        check_note = f"，其中 Q4 = FY − 9M YTD（校验：{'/'.join(checks)}）"
    return FundamentalFact(
        fact_id=f"fact-{symbol.lower()}-ttm_eps-{ends[-1].isoformat()}",
        name=FundamentalMetricName.TTM_EPS, value=decimal_string(ttm, 6), unit="USD/shares",
        status=DataStatus.AVAILABLE, accounting_basis=AccountingBasis.GAAP,
        fiscal_period=f"TTM ending {ends[-1].isoformat()}",
        period_start=starts[0], period_end=ends[-1],
        filed_at=date.fromisoformat(latest_filed),
        accepted_at=datetime(*(date.fromisoformat(latest_filed).timetuple()[:3]), tzinfo=UTC),
        source_ref=source_id, missing_reason=None,
        derivation=("TTM_EPS = SUM(4 个单季 " + concept + ")" + check_note
                    + f"；期间 {starts[0].isoformat()}..{ends[-1].isoformat()}"),
        input_fact_ids=[item["fact"] for item in window],
    ), ""



def normalize_company_facts(
    payload: dict,
    response_sha256: str,
    subject: AnalysisSubject,
    *,
    cik: int,
    cutoff: datetime,
    last_close: float | None = None,
) -> EquityFundamentals:
    symbol = subject.symbol
    cutoff_date = cutoff.date()
    source_id = f"src-sec-{symbol.lower()}-companyfacts"
    source = SourceEnvelope(
        source_id=source_id,
        provider=DataProvider.SEC_EDGAR,
        dataset="sec_edgar_company_facts_xbrl",
        endpoint_id=f"GET /api/xbrl/companyfacts/CIK{cik:010d}.json",
        source_at=datetime.now(UTC),
        received_at=datetime.now(UTC),
        delay_class=DelayClass.END_OF_DAY,
        market_scope=MarketScope.UNKNOWN,
        completeness=Completeness.PARTIAL,
        is_full_market=False,
        price_basis=None,
        timezone="America/New_York",
        terms_version="verify-at-enable-time",
        raw_sha256=response_sha256,
        limitations=LIMITATIONS,
    )

    facts: list[FundamentalFact] = []
    missing_items: list[str] = []
    filings_seen: dict[str, dict] = {}

    selected: dict[FundamentalMetricName, FundamentalFact] = {}
    selected_concepts: dict[FundamentalMetricName, str | None] = {}
    for name, concepts in {**DURATION_CONCEPTS, **INSTANT_CONCEPTS}.items():
        concept, unit, row = _pick_across(payload, concepts, cutoff_date, instant=name in INSTANT_CONCEPTS)
        selected_concepts[name] = concept
        if row is None:
            reason = (
                f"no GAAP XBRL fact filed on or before {cutoff_date.isoformat()} for "
                + " / ".join(concepts)
            )
            missing_items.append(f"{name.value}: {reason}")
        fact = _fact(name, symbol, unit or None, row, reason="NO_FACT_AT_OR_BEFORE_CUTOFF")
        selected[name] = fact
        facts.append(fact)
        if row is not None and row.get("accn"):
            filings_seen[str(row["accn"])] = row

    # --- derived: free cash flow (same fiscal period required) --------------------
    ocf, capex = selected[FundamentalMetricName.OPERATING_CASH_FLOW], selected[FundamentalMetricName.CAPITAL_EXPENDITURE]
    if ocf.value is not None and capex.value is not None and ocf.period_end == capex.period_end:
        value = float(ocf.value) - float(capex.value)
        facts.append(FundamentalFact(
            fact_id=f"fact-{symbol.lower()}-free_cash_flow-{(ocf.fiscal_period or 'na').replace(' ', '').lower()}",
            name=FundamentalMetricName.FREE_CASH_FLOW, value=decimal_string(value, 2), unit="USD",
            status=DataStatus.AVAILABLE, accounting_basis=AccountingBasis.GAAP,
            fiscal_period=ocf.fiscal_period, period_start=ocf.period_start, period_end=ocf.period_end,
            filed_at=ocf.filed_at, accepted_at=ocf.accepted_at, source_ref=source_id, missing_reason=None,
            derivation="FREE_CASH_FLOW = OPERATING_CASH_FLOW - CAPITAL_EXPENDITURE",
            input_fact_ids=[ocf.fact_id, capex.fact_id],
        ))
    else:
        mismatch = ocf.value is not None and capex.value is not None and ocf.period_end != capex.period_end
        reason = ("operating cash flow and capex come from different fiscal periods; not subtracted across periods"
                  if mismatch else "needs both operating cash flow and capex at or before the cutoff")
        facts.append(_fact(FundamentalMetricName.FREE_CASH_FLOW, symbol, "USD", None, reason=reason,
                           derivation="FREE_CASH_FLOW = OPERATING_CASH_FLOW - CAPITAL_EXPENDITURE"))
        missing_items.append(f"FREE_CASH_FLOW: {reason}")

    # --- derived: total debt (explicit composition only) --------------------------
    components: list[tuple[str, float]] = []
    instant_periods: set[str] = set()
    component_rows: list[dict] = []
    for concept in DEBT_COMPONENTS:
        unit, rows = _rows(payload, concept)
        row = _pick_instant(rows, cutoff_date) if rows else None
        if row is not None:
            components.append((concept, float(row["val"])))
            instant_periods.add(str(row.get("end")))
            component_rows.append(row)
            filings_seen.setdefault(str(row.get("accn")), row)
    if components and len(instant_periods) == 1:
        total_debt = sum(value for _, value in components)
        # Point-in-time discipline: inherit the filing timestamps of the components.
        with_stamp = [row for row in component_rows if row.get("filed")]
        debt_filed = date.fromisoformat(str(max(with_stamp, key=lambda r: str(r["filed"]))["filed"])) if with_stamp else None
        facts.append(FundamentalFact(
            fact_id=f"fact-{symbol.lower()}-total_debt-{sorted(instant_periods)[0]}",
            name=FundamentalMetricName.TOTAL_DEBT, value=decimal_string(total_debt, 2), unit="USD",
            status=DataStatus.AVAILABLE, accounting_basis=AccountingBasis.GAAP,
            fiscal_period="instant", period_start=None,
            period_end=date.fromisoformat(sorted(instant_periods)[0]), filed_at=debt_filed,
            accepted_at=datetime(debt_filed.year, debt_filed.month, debt_filed.day, tzinfo=UTC) if debt_filed else None,
            source_ref=source_id, missing_reason=None,
            derivation="TOTAL_DEBT = " + " + ".join(concept for concept, _ in components),
            input_fact_ids=[f"fact-{symbol.lower()}-{concept.lower()}" for concept, _ in components],
        ))
    else:
        facts.append(_fact(FundamentalMetricName.TOTAL_DEBT, symbol, "USD", None,
                           reason="no single-instant composition of debt tags at or before the cutoff; not guessed"))
        missing_items.append("TOTAL_DEBT: debt composition not available as a single-instant GAAP set")

    # --- derived: year-over-year growth, same tag and adjacent fiscal periods -----
    # NOTE: in companyfacts, ``fy``/``fp`` describe the FILING, not the fact period:
    # the prior-year comparative inside one 10-K also carries fy=<filing year>. So
    # adjacency must be derived from the period end dates (~365 days apart).
    growth_sources = {
        FundamentalMetricName.REVENUE_GROWTH: selected_concepts[FundamentalMetricName.REVENUE],
        FundamentalMetricName.NET_INCOME_GROWTH: selected_concepts[FundamentalMetricName.NET_INCOME],
    }
    for name, concept in growth_sources.items():
        annual: list[dict] = []
        if concept:
            _, rows = _rows(payload, concept)
            annual = [row for row in rows
                      if row.get("form") == "10-K" and row.get("end")
                      and (_duration_days(row) or 0) >= ANNUAL_MIN_DAYS
                      and (_duration_days(row) or 0) <= ANNUAL_MAX_DAYS
                      and _filed_ok(row, cutoff_date)]
        annual.sort(key=lambda row: str(row["end"]))
        latest = annual[-1] if annual else None
        previous = None
        if latest is not None:
            latest_end = date.fromisoformat(str(latest["end"]))
            candidates = [row for row in annual
                          if 300 <= (latest_end - date.fromisoformat(str(row["end"]))).days <= 400]
            previous = max(candidates, key=lambda row: str(row["end"])) if candidates else None
        if latest is not None and previous is not None and float(previous["val"]):
            latest_end = date.fromisoformat(str(latest["end"]))
            previous_end = date.fromisoformat(str(previous["end"]))
            growth = float(latest["val"]) / float(previous["val"]) - 1
            facts.append(FundamentalFact(
                fact_id=f"fact-{symbol.lower()}-{name.value.lower()}-fy{latest_end.year}",
                name=name, value=decimal_string(growth, 6), unit="fraction",
                status=DataStatus.AVAILABLE, accounting_basis=AccountingBasis.GAAP,
                fiscal_period=f"FY{latest_end.year}",
                period_start=date.fromisoformat(str(latest["start"])) if latest.get("start") else None,
                period_end=latest_end,
                filed_at=date.fromisoformat(str(latest["filed"])),
                accepted_at=datetime(*(date.fromisoformat(str(latest["filed"])).timetuple()[:3]), tzinfo=UTC),
                source_ref=source_id, missing_reason=None,
                derivation=f"{name.value} = {concept} FY{latest_end.year} / FY{previous_end.year} - 1",
                input_fact_ids=[f"FY{latest_end.year}", f"FY{previous_end.year}"],
            ))
        else:
            reason = ("needs two consecutive annual periods (~365 days apart) from the same XBRL tag"
                      if len(annual) >= 2 else "needs two annual periods from the same XBRL tag before the cutoff")
            facts.append(_fact(name, symbol, "fraction", None, reason=reason,
                               derivation=f"{name.value} = latest annual / prior annual - 1"))
            missing_items.append(f"{name.value}: {reason}")

    # --- market cap needs a price; PE_TTM needs a TTM EPS policy -------------------
    shares = selected[FundamentalMetricName.DILUTED_SHARES]
    if last_close is not None and shares.value is not None:
        # XBRL tags share counts either absolutely (AAPL: 15,004,697,000) or in MILLIONS
        # (MCD: 716.4 for ~716M shares). A value far below any real share count is a millions tag;
        # the reading is stated in the derivation instead of being applied silently.
        raw_shares = float(shares.value)
        scale_note = ""
        share_count = raw_shares
        if 0 < raw_shares < SHARES_MILLIONS_THRESHOLD:
            share_count = raw_shares * 1_000_000
            scale_note = f"（原始 XBRL 值 {raw_shares:g} 判定为「百万股」，已 ×1e6）"
        facts.append(FundamentalFact(
            fact_id=f"fact-{symbol.lower()}-market_cap-{(shares.fiscal_period or 'na').replace(' ', '').lower()}",
            name=FundamentalMetricName.MARKET_CAP,
            value=decimal_string(last_close * share_count, 2), unit="USD",
            status=DataStatus.AVAILABLE, accounting_basis=AccountingBasis.GAAP,
            fiscal_period=shares.fiscal_period, period_start=None, period_end=shares.period_end,
            filed_at=shares.filed_at, accepted_at=shares.accepted_at, source_ref=source_id, missing_reason=None,
            derivation="MARKET_CAP = last confirmed close * weighted-average diluted shares" + scale_note,
            input_fact_ids=[shares.fact_id],
        ))
    else:
        facts.append(_fact(FundamentalMetricName.MARKET_CAP, symbol, "USD", None,
                           reason="needs a confirmed close and diluted shares",
                           derivation="MARKET_CAP = last close * diluted shares"))
        missing_items.append("MARKET_CAP: missing last close or diluted shares")
    # --- TTM EPS policy (agreed): four single quarters, then PE = close / TTM EPS -------
    ttm_fact, ttm_reason = build_ttm_eps(payload, cutoff_date, symbol, source_id)
    if ttm_fact is not None:
        facts.append(ttm_fact)
    else:
        facts.append(_fact(FundamentalMetricName.TTM_EPS, symbol, "USD/shares", None, reason=ttm_reason,
                           derivation=TTM_EPS_POLICY))
        missing_items.append(f"TTM_EPS: {ttm_reason}")
    if ttm_fact is not None and ttm_fact.value is not None and last_close is not None:
        ttm_eps = float(ttm_fact.value)
        if ttm_eps > 0:
            facts.append(FundamentalFact(
                fact_id=f"fact-{symbol.lower()}-pe_ttm-{ttm_fact.period_end.isoformat()}",
                name=FundamentalMetricName.PE_TTM, value=decimal_string(last_close / ttm_eps, 4),
                unit="fraction", status=DataStatus.AVAILABLE, accounting_basis=AccountingBasis.GAAP,
                fiscal_period=ttm_fact.fiscal_period, period_start=None, period_end=ttm_fact.period_end,
                filed_at=ttm_fact.filed_at, accepted_at=ttm_fact.accepted_at, source_ref=source_id,
                missing_reason=None,
                derivation="PE_TTM = last confirmed close / TTM_EPS (4 个单季摊薄 EPS 之和)",
                input_fact_ids=[ttm_fact.fact_id],
            ))
        else:
            reason = "TTM EPS is not positive; a negative or zero P/E is not a valuation multiple"
            facts.append(_fact(FundamentalMetricName.PE_TTM, symbol, "fraction", None, reason=reason,
                               derivation="PE_TTM = close / TTM_EPS"))
            missing_items.append(f"PE_TTM: {reason}")
    else:
        reason = ("needs a confirmed close and four consecutive single-quarter EPS facts"
                  if ttm_fact is not None else ttm_reason)
        facts.append(_fact(FundamentalMetricName.PE_TTM, symbol, "fraction", None, reason=reason,
                           derivation="PE_TTM = close / TTM_EPS (4 个单季摊薄 EPS 之和)"))
        missing_items.append(f"PE_TTM: {reason}")

    filings = [
        FilingRef(
            accession_number=accession,
            form=str(row.get("form") or ""),
            filed_at=date.fromisoformat(str(row["filed"])),
            accepted_at=datetime(*(date.fromisoformat(str(row["filed"])).timetuple()[:3]), tzinfo=UTC),
            report_period=date.fromisoformat(str(row["end"])) if row.get("end") else None,
            source_url=f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/",
            source_ref=source_id,
        )
        for accession, row in sorted(filings_seen.items(), key=lambda item: str(item[1].get("filed")), reverse=True)[:8]
        if row.get("filed")
    ]

    return EquityFundamentals(
        subject=subject,
        accepted_before=cutoff,
        facts=facts,
        filings=filings,
        sources=[source],
        missing_items=sorted(set(missing_items)),
    )
