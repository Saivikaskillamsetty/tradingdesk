"""Macro conditions from FRED, with the same provenance rules as everything else.

A single stock thesis usually survives or dies on something outside the
company: the rate the market discounts it at, whether the curve is pricing a
recession, what inflation did last month. Those are cheap to look up and
expensive to misremember, and remembered macro figures go stale faster than
anything on this desk -- a rate level recalled from training is wrong by
whole percentage points, not by rounding.

Every series returned carries its FRED series id, the observation date, and
how the change was computed. Changes are measured against the last observation
on or before the comparison date, so a Monday reading is not silently compared
against a holiday with no print.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from desk_mcp import cache

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"

# Macro series update daily at most; six hours keeps a session cheap without
# serving yesterday's print after this morning's release.
CACHE_TTL = 6 * 3_600


class MacroError(RuntimeError):
    """FRED data could not be retrieved or is unusable."""


@dataclass(frozen=True)
class Series:
    """One macro series definition and what it is for."""

    key: str
    series_id: str
    label: str
    unit: str
    reads_as: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SERIES: tuple[Series, ...] = (
    Series(
        key="fed_funds",
        series_id="DFF",
        label="Effective federal funds rate",
        unit="%",
        reads_as="the policy rate actually transacted, daily",
    ),
    Series(
        key="treasury_10y",
        series_id="DGS10",
        label="10-year Treasury yield",
        unit="%",
        reads_as="the discount rate long-duration equities are valued against",
    ),
    Series(
        key="treasury_2y",
        series_id="DGS2",
        label="2-year Treasury yield",
        unit="%",
        reads_as="the market's view of policy over the next two years",
    ),
    Series(
        key="curve_10y_2y",
        series_id="T10Y2Y",
        label="10-year minus 2-year spread",
        unit="%",
        reads_as="negative is an inverted curve; inversion has preceded most "
        "recessions, with long and variable lags",
    ),
    Series(
        key="cpi",
        series_id="CPIAUCSL",
        label="CPI, all items",
        unit="index",
        reads_as="headline inflation; read the 12-month change, not the level",
    ),
    Series(
        key="core_cpi",
        series_id="CPILFESL",
        label="CPI, less food and energy",
        unit="index",
        reads_as="core inflation; what policy actually responds to",
    ),
    Series(
        key="unemployment",
        series_id="UNRATE",
        label="Unemployment rate",
        unit="%",
        reads_as="the other half of the Fed's mandate",
    ),
    Series(
        key="vix",
        series_id="VIXCLS",
        label="VIX",
        unit="index",
        reads_as="implied volatility on the S&P 500; the market's price of fear",
    ),
    Series(
        key="dollar",
        series_id="DTWEXBGS",
        label="Trade-weighted dollar index (broad)",
        unit="index",
        reads_as="a rising dollar is a headwind to overseas revenue",
    ),
)

BY_KEY = {s.key: s for s in SERIES}


def _api_key() -> str:
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        raise MacroError(
            "FRED_API_KEY is not set. Get a free key at "
            "https://fredaccount.stlouisfed.org/apikeys and export it, then "
            "verify with `uv run python scripts/check_keys.py`."
        )
    return key


def observations(series_id: str, start: date | None = None) -> list[dict[str, Any]]:
    """Raw observations for a series, oldest first, missing prints dropped.

    FRED encodes a missing observation as ".", which is not zero and must not
    be treated as one.
    """
    start = start or date.today() - timedelta(days=800)
    cache_key = f"fred_{series_id}_{start.isoformat()}"

    cached = cache.read(cache_key, CACHE_TTL)
    if cached is None:
        try:
            response = httpx.get(
                FRED_URL,
                params={
                    "series_id": series_id,
                    "api_key": _api_key(),
                    "file_type": "json",
                    "observation_start": start.isoformat(),
                },
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            raise MacroError(f"{series_id}: request failed: {exc}") from exc

        if response.status_code == 400:
            raise MacroError(
                f"{series_id}: FRED rejected the request "
                f"({response.json().get('error_message', 'bad request')}). "
                f"An invalid API key reports as a 400."
            )
        if response.status_code != 200:
            raise MacroError(
                f"{series_id}: HTTP {response.status_code}: {response.text[:200]}"
            )

        cached = response.json()
        cache.write(cache_key, cached)

    points = [
        {"date": row["date"], "value": float(row["value"])}
        for row in cached.get("observations", [])
        if row.get("value") not in (".", "", None)
    ]
    if not points:
        raise MacroError(f"{series_id}: no usable observations returned")

    return points


def _value_on_or_before(points: list[dict[str, Any]], cutoff: date) -> dict | None:
    """The last observation at or before a date.

    Comparing against an exact calendar date would miss on every weekend and
    holiday, so the comparison walks back to the most recent real print.
    """
    stamp = cutoff.isoformat()
    for point in reversed(points):
        if point["date"] <= stamp:
            return point
    return None


def _change(points: list[dict[str, Any]], days: int) -> dict[str, Any] | None:
    latest = points[-1]
    reference = _value_on_or_before(
        points[:-1], date.fromisoformat(latest["date"]) - timedelta(days=days)
    )
    if reference is None:
        return None

    absolute = latest["value"] - reference["value"]
    percent = (
        (absolute / reference["value"] * 100.0) if reference["value"] != 0 else None
    )

    return {
        "from_date": reference["date"],
        "from_value": round(reference["value"], 4),
        "change": round(absolute, 4),
        "change_pct": None if percent is None else round(percent, 2),
    }


def series_reading(key: str) -> dict[str, Any]:
    """Latest value for one series, with 1-, 3- and 12-month changes."""
    if key not in BY_KEY:
        raise MacroError(
            f"unknown series {key!r}; available: {', '.join(sorted(BY_KEY))}"
        )

    definition = BY_KEY[key]
    points = observations(definition.series_id)
    latest = points[-1]

    reading: dict[str, Any] = {
        **definition.to_dict(),
        "value": round(latest["value"], 4),
        "as_of": latest["date"],
        "observations_used": len(points),
        "changes": {
            "1m": _change(points, 30),
            "3m": _change(points, 91),
            "12m": _change(points, 365),
        },
    }

    # An index level is meaningless on its own; the year-over-year rate is the
    # number anyone actually means by "inflation".
    if definition.unit == "index" and key in ("cpi", "core_cpi"):
        yoy = reading["changes"]["12m"]
        reading["yoy_pct"] = None if yoy is None else yoy["change_pct"]
        reading["reads_as"] = (
            f"{definition.reads_as}; year-over-year is "
            + ("unavailable" if yoy is None else f"{yoy['change_pct']}%")
        )

    staleness = (date.today() - date.fromisoformat(latest["date"])).days
    reading["days_since_observation"] = staleness
    if staleness > 45:
        reading["staleness_warning"] = (
            f"last observation is {staleness} days old; monthly series lag by "
            f"design, but check the release calendar before leaning on this"
        )

    return reading


def snapshot(keys: list[str] | None = None) -> dict[str, Any]:
    """Current macro conditions across the desk's standard series.

    A series that fails is reported as an error beside the others rather than
    taking the whole snapshot down: knowing nine of ten readings, and which
    one is missing, beats knowing none.
    """
    wanted = keys or [s.key for s in SERIES]
    readings: dict[str, Any] = {}
    errors: dict[str, str] = {}

    for key in wanted:
        try:
            readings[key] = series_reading(key)
        except MacroError as exc:
            errors[key] = str(exc)

    if not readings:
        raise MacroError(
            "no macro series could be retrieved: "
            + "; ".join(f"{k}: {v}" for k, v in errors.items())
        )

    return {
        "retrieved_at": datetime.now().isoformat(timespec="seconds"),
        "source": "FRED (Federal Reserve Bank of St. Louis)",
        "readings": readings,
        "unavailable": errors,
        "note": (
            "Levels and changes as published. Nothing here is a forecast, and "
            "the relationship between any of it and a single stock is a "
            "judgement, not a calculation."
        ),
    }
