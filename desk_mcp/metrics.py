"""Derived financial metrics, computed here so agents never have to.

An agent asked to work out a margin from two numbers will usually get it
right, and occasionally not -- and there is no way to tell the two cases
apart from the output. Every ratio and growth rate the desk uses is therefore
computed in Python from resolved facts, and carries the periods and source
concepts it was built from.

Ratios that mix a flow (revenue, net income) with a balance (equity, assets)
use the average of opening and closing balances where history allows it, since
a year-end snapshot understates the denominator for a growing company.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any

from .edgar import facts as edgar_facts
from .edgar.client import EdgarError
from .edgar.facts import Fact


@dataclass(frozen=True)
class Metric:
    """A computed value, with the inputs that produced it."""

    key: str
    label: str
    value: float | None
    unit: str
    period_end: str
    inputs: dict[str, str] = field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_div(numerator: float, denominator: float) -> float | None:
    """Division that yields None rather than raising or returning inf.

    Denominators here are real quantities -- equity, revenue, assets -- and a
    zero or negative one usually means the ratio is meaningless rather than
    infinite. A company with negative equity has no meaningful ROE.
    """
    if denominator == 0:
        return None
    return numerator / denominator


def _pct(value: float | None) -> float | None:
    return None if value is None else value * 100.0


def _get(statement: dict[str, Any], key: str) -> float | None:
    entry = statement.get(key)
    return None if entry is None else float(entry["value"])


def _provenance(statement: dict[str, Any], *keys: str) -> dict[str, str]:
    return {
        k: statement[k]["concept"] for k in keys if statement.get(k) is not None
    }


# ---------------------------------------------------------------------------
# Point-in-time ratios
# ---------------------------------------------------------------------------


def profitability(statement: dict[str, Any]) -> list[Metric]:
    """Margin structure for a single period."""
    f = statement["facts"]
    period = statement["period_end_common"]

    revenue = _get(f, "revenue")
    out: list[Metric] = []

    if revenue is None or revenue == 0:
        return out

    for key, label, numerator_key in (
        ("gross_margin", "Gross margin", "gross_profit"),
        ("operating_margin", "Operating margin", "operating_income"),
        ("net_margin", "Net margin", "net_income"),
    ):
        numerator = _get(f, numerator_key)
        if numerator is None:
            continue
        out.append(
            Metric(
                key=key,
                label=label,
                value=_pct(_safe_div(numerator, revenue)),
                unit="%",
                period_end=period,
                inputs=_provenance(f, numerator_key, "revenue"),
            )
        )

    rnd = _get(f, "rnd_expense")
    if rnd is not None:
        out.append(
            Metric(
                key="rnd_intensity",
                label="R&D as % of revenue",
                value=_pct(_safe_div(rnd, revenue)),
                unit="%",
                period_end=period,
                inputs=_provenance(f, "rnd_expense", "revenue"),
            )
        )

    return out


def cash_quality(statement: dict[str, Any]) -> list[Metric]:
    """Free cash flow and how well earnings convert into cash.

    Conversion persistently below 100% is the classic tell for earnings that
    are an accounting artefact rather than cash.
    """
    f = statement["facts"]
    period = statement["period_end_common"]
    out: list[Metric] = []

    ocf = _get(f, "operating_cash_flow")
    capex = _get(f, "capex")
    net_income = _get(f, "net_income")
    revenue = _get(f, "revenue")

    if ocf is not None and capex is not None:
        fcf = ocf - abs(capex)  # capex is reported as a positive outflow
        out.append(
            Metric(
                key="free_cash_flow",
                label="Free cash flow",
                value=fcf,
                unit="USD",
                period_end=period,
                inputs=_provenance(f, "operating_cash_flow", "capex"),
                note="operating cash flow less capital expenditure",
            )
        )
        if revenue:
            out.append(
                Metric(
                    key="fcf_margin",
                    label="FCF margin",
                    value=_pct(_safe_div(fcf, revenue)),
                    unit="%",
                    period_end=period,
                    inputs=_provenance(f, "operating_cash_flow", "capex", "revenue"),
                )
            )

    if ocf is not None and net_income is not None and net_income > 0:
        out.append(
            Metric(
                key="cash_conversion",
                label="Cash conversion",
                value=_pct(_safe_div(ocf, net_income)),
                unit="%",
                period_end=period,
                inputs=_provenance(f, "operating_cash_flow", "net_income"),
                note="operating cash flow as % of net income; <100% persistently "
                "suggests earnings are not converting to cash",
            )
        )

    return out


def balance_sheet(statement: dict[str, Any]) -> list[Metric]:
    """Leverage and liquidity."""
    f = statement["facts"]
    period = statement["period_end_common"]
    out: list[Metric] = []

    equity = _get(f, "stockholders_equity")
    assets = _get(f, "total_assets")
    debt = _get(f, "long_term_debt")
    cash = _get(f, "cash_and_equivalents")

    if debt is not None and equity is not None:
        out.append(
            Metric(
                key="debt_to_equity",
                label="Debt / equity",
                value=_safe_div(debt, equity),
                unit="x",
                period_end=period,
                inputs=_provenance(f, "long_term_debt", "stockholders_equity"),
                note="negative equity makes this meaningless" if equity < 0 else "",
            )
        )

    if debt is not None and cash is not None:
        out.append(
            Metric(
                key="net_debt",
                label="Net debt",
                value=debt - cash,
                unit="USD",
                period_end=period,
                inputs=_provenance(f, "long_term_debt", "cash_and_equivalents"),
                note="negative means net cash",
            )
        )

    if equity is not None and assets is not None:
        out.append(
            Metric(
                key="equity_ratio",
                label="Equity / assets",
                value=_pct(_safe_div(equity, assets)),
                unit="%",
                period_end=period,
                inputs=_provenance(f, "stockholders_equity", "total_assets"),
            )
        )

    return out


def returns(
    statement: dict[str, Any], prior: dict[str, Any] | None = None
) -> list[Metric]:
    """Return on equity and assets.

    Uses average balances when a prior period is supplied. Year-end balances
    understate the denominator for a company that grew during the year, which
    inflates the return.
    """
    f = statement["facts"]
    period = statement["period_end_common"]
    prior_f = prior["facts"] if prior else None
    out: list[Metric] = []

    net_income = _get(f, "net_income")
    if net_income is None:
        return out

    for key, label, balance_key in (
        ("roe", "Return on equity", "stockholders_equity"),
        ("roa", "Return on assets", "total_assets"),
    ):
        closing = _get(f, balance_key)
        if closing is None:
            continue

        opening = _get(prior_f, balance_key) if prior_f else None
        if opening is not None:
            denominator = (opening + closing) / 2
            basis = "average of opening and closing balance"
        else:
            denominator = closing
            basis = "closing balance only; no prior period available"

        out.append(
            Metric(
                key=key,
                label=label,
                value=_pct(_safe_div(net_income, denominator)),
                unit="%",
                period_end=period,
                inputs=_provenance(f, "net_income", balance_key),
                note=basis,
            )
        )

    return out


# ---------------------------------------------------------------------------
# Growth
# ---------------------------------------------------------------------------


def growth(series: list[Fact], label: str) -> list[Metric]:
    """Year-over-year and compound growth for a line item.

    CAGR is only meaningful from a positive base, so it is omitted rather
    than fabricated when the earliest period is zero or negative.
    """
    out: list[Metric] = []
    if len(series) < 2:
        return out

    latest, previous = series[-1], series[-2]
    if previous.value != 0:
        out.append(
            Metric(
                key=f"{latest.line_item}_yoy",
                label=f"{label} growth YoY",
                value=_pct(_safe_div(latest.value - previous.value, abs(previous.value))),
                unit="%",
                period_end=latest.period_end,
                inputs={latest.line_item: latest.concept},
                note=f"FY{previous.fiscal_year} to FY{latest.fiscal_year}",
            )
        )

    earliest = series[0]
    years = len(series) - 1
    if earliest.value > 0 and latest.value > 0 and years >= 2:
        cagr = (latest.value / earliest.value) ** (1 / years) - 1
        out.append(
            Metric(
                key=f"{latest.line_item}_cagr",
                label=f"{label} CAGR ({years}y)",
                value=_pct(cagr),
                unit="%",
                period_end=latest.period_end,
                inputs={latest.line_item: latest.concept},
                note=f"FY{earliest.fiscal_year} to FY{latest.fiscal_year}",
            )
        )

    return out


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

_GROWTH_ITEMS = (
    ("revenue", "Revenue"),
    ("net_income", "Net income"),
    ("operating_cash_flow", "Operating cash flow"),
)


def _common_period(statement: dict[str, Any]) -> str:
    ends = {f["period_end"] for f in statement["facts"].values()}
    if len(ends) != 1:
        raise EdgarError(
            f"line items span multiple periods {sorted(ends)}; refusing to "
            f"compute ratios across mismatched periods"
        )
    return ends.pop()


def analyse(ticker: str, history_years: int = 5) -> dict[str, Any]:
    """Full derived-metric profile for a company.

    Every value here is computed from resolved facts; nothing is estimated.
    """
    statement = edgar_facts.statement(ticker)
    statement["period_end_common"] = _common_period(statement)

    # The prior year enables average-balance returns.
    prior: dict[str, Any] | None = None
    equity_history = edgar_facts.series(ticker, "stockholders_equity", limit=2)
    assets_history = edgar_facts.series(ticker, "total_assets", limit=2)
    if len(equity_history) == 2 and len(assets_history) == 2:
        prior = {
            "facts": {
                "stockholders_equity": equity_history[0].to_dict(),
                "total_assets": assets_history[0].to_dict(),
            }
        }

    computed: list[Metric] = []
    computed += profitability(statement)
    computed += cash_quality(statement)
    computed += balance_sheet(statement)
    computed += returns(statement, prior)

    for key, label in _GROWTH_ITEMS:
        try:
            computed += growth(
                edgar_facts.series(ticker, key, limit=history_years), label
            )
        except EdgarError:
            continue

    return {
        "ticker": statement["ticker"],
        "company_name": statement["company_name"],
        "period_end": statement["period_end_common"],
        "fiscal_year": statement["facts"]["revenue"]["fiscal_year"],
        "metrics": [m.to_dict() for m in computed],
        "unavailable": statement["unavailable"],
    }
