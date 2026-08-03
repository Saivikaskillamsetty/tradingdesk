"""Mapping from plain-English line items to US-GAAP XBRL concepts.

Companies do not agree on which concept represents a given line item, and an
individual company changes its choice over time. Apple reported revenue under
`Revenues` until 2018 and `RevenueFromContractWithCustomerExcludingAssessedTax`
after; NVIDIA went the other way. Picking a single concept therefore returns
silently stale numbers -- a naive `Revenues` lookup yields $62.9B for Apple
(a 2018 figure) instead of $416.2B.

The fix is to declare every plausible concept per line item and let the
resolver pick whichever one actually carries current data. Order within a list
is a tie-break preference only; recency always wins (see facts.resolve).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PeriodType(Enum):
    """Whether a concept measures a flow over time or a balance at a point."""

    DURATION = "duration"  # revenue, net income -- has start and end
    INSTANT = "instant"  # total assets, cash -- end only


@dataclass(frozen=True)
class LineItem:
    key: str
    label: str
    period_type: PeriodType
    concepts: tuple[str, ...]
    units: tuple[str, ...] = ("USD",)


_ITEMS: tuple[LineItem, ...] = (
    # ---- Income statement -------------------------------------------------
    LineItem(
        key="revenue",
        label="Revenue",
        period_type=PeriodType.DURATION,
        concepts=(
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
            "SalesRevenueGoodsNet",
        ),
    ),
    LineItem(
        key="cost_of_revenue",
        label="Cost of revenue",
        period_type=PeriodType.DURATION,
        concepts=(
            "CostOfRevenue",
            "CostOfGoodsAndServicesSold",
            "CostOfGoodsSold",
            "CostOfServices",
        ),
    ),
    LineItem(
        key="gross_profit",
        label="Gross profit",
        period_type=PeriodType.DURATION,
        concepts=("GrossProfit",),
    ),
    LineItem(
        key="operating_income",
        label="Operating income",
        period_type=PeriodType.DURATION,
        concepts=(
            "OperatingIncomeLoss",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        ),
    ),
    LineItem(
        key="net_income",
        label="Net income",
        period_type=PeriodType.DURATION,
        concepts=(
            "NetIncomeLoss",
            "ProfitLoss",
            "NetIncomeLossAvailableToCommonStockholdersBasic",
        ),
    ),
    LineItem(
        key="rnd_expense",
        label="R&D expense",
        period_type=PeriodType.DURATION,
        concepts=("ResearchAndDevelopmentExpense",),
    ),
    LineItem(
        key="eps_diluted",
        label="Diluted EPS",
        period_type=PeriodType.DURATION,
        concepts=("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"),
        units=("USD/shares",),
    ),
    LineItem(
        key="shares_diluted",
        label="Diluted shares outstanding",
        period_type=PeriodType.DURATION,
        concepts=(
            "WeightedAverageNumberOfDilutedSharesOutstanding",
            "WeightedAverageNumberOfSharesOutstandingBasicAndDiluted",
        ),
        units=("shares",),
    ),
    # ---- Cash flow --------------------------------------------------------
    LineItem(
        key="operating_cash_flow",
        label="Cash from operations",
        period_type=PeriodType.DURATION,
        concepts=(
            "NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        ),
    ),
    LineItem(
        key="capex",
        label="Capital expenditure",
        period_type=PeriodType.DURATION,
        concepts=(
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsToAcquireProductiveAssets",
        ),
    ),
    # ---- Balance sheet ----------------------------------------------------
    LineItem(
        key="total_assets",
        label="Total assets",
        period_type=PeriodType.INSTANT,
        concepts=("Assets",),
    ),
    LineItem(
        key="total_liabilities",
        label="Total liabilities",
        period_type=PeriodType.INSTANT,
        concepts=("Liabilities",),
    ),
    LineItem(
        key="stockholders_equity",
        label="Stockholders equity",
        period_type=PeriodType.INSTANT,
        concepts=(
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        ),
    ),
    LineItem(
        key="cash_and_equivalents",
        label="Cash and equivalents",
        period_type=PeriodType.INSTANT,
        concepts=(
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        ),
    ),
    LineItem(
        key="long_term_debt",
        label="Long-term debt",
        period_type=PeriodType.INSTANT,
        concepts=(
            "LongTermDebtNoncurrent",
            "LongTermDebt",
            "LongTermNotesPayable",
        ),
    ),
    LineItem(
        key="inventory",
        label="Inventory",
        period_type=PeriodType.INSTANT,
        concepts=("InventoryNet",),
    ),
    LineItem(
        key="accounts_receivable",
        label="Accounts receivable",
        period_type=PeriodType.INSTANT,
        concepts=("AccountsReceivableNetCurrent", "ReceivablesNetCurrent"),
    ),
)

BY_KEY: dict[str, LineItem] = {item.key: item for item in _ITEMS}

ALL_KEYS: tuple[str, ...] = tuple(item.key for item in _ITEMS)


def get(key: str) -> LineItem:
    try:
        return BY_KEY[key]
    except KeyError:
        raise KeyError(
            f"unknown line item {key!r}; known items: {', '.join(ALL_KEYS)}"
        ) from None
