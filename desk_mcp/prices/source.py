"""Price data access, behind an interface the rest of the desk codes against.

Swapping data vendors -- or adding a non-US broker later -- should not require
touching indicators, agents or the UI. Everything downstream depends on `Bar`
and `PriceSource`, never on a vendor SDK.

Feed choice, measured against Alpaca's free tier on 2026-08-03:

    IEX carries ~3.2-3.7% of consolidated volume, and its daily closes differ
    from the tape by up to $0.36. Volume confirmation on a breakout is
    meaningless against 3% of the real number, so IEX is not a viable default.

    SIP (the full consolidated tape) is available on the free tier for
    anything older than 15 minutes, with history back to at least 2016.
    Requests inside that window return 403.

The desk trades swing and positional timeframes, so a 15-minute delay costs
nothing and SIP is the default. IEX remains selectable for the rare case where
a very recent print matters more than accuracy.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol

import httpx

ALPACA_DATA_URL = "https://data.alpaca.markets/v2/stocks/{symbol}/bars"

# Free-tier SIP embargo. Requests ending inside this window are rejected.
SIP_EMBARGO = timedelta(minutes=15)


class PriceError(RuntimeError):
    """Price data could not be retrieved."""


@dataclass(frozen=True)
class Bar:
    """One OHLCV period."""

    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    trade_count: int | None = None
    vwap: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BarSet:
    """Bars plus the provenance needed to judge whether to trust them."""

    symbol: str
    timeframe: str
    feed: str
    bars: list[Bar]
    retrieved_at: str

    @property
    def closes(self) -> list[float]:
        return [b.close for b in self.bars]

    @property
    def highs(self) -> list[float]:
        return [b.high for b in self.bars]

    @property
    def lows(self) -> list[float]:
        return [b.low for b in self.bars]

    @property
    def volumes(self) -> list[int]:
        return [b.volume for b in self.bars]

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "feed": self.feed,
            "feed_note": (
                "SIP: full consolidated tape, all US exchanges"
                if self.feed == "sip"
                else "IEX only: roughly 3% of consolidated volume; "
                "volume-based signals are unreliable"
            ),
            "retrieved_at": self.retrieved_at,
            "bar_count": len(self.bars),
            "bars": [b.to_dict() for b in self.bars],
        }


class PriceSource(Protocol):
    """What the desk needs from any price vendor."""

    def daily_bars(
        self, symbol: str, start: date, end: date | None = None
    ) -> BarSet: ...


class AlpacaSource:
    """Alpaca market data. Defaults to the consolidated SIP tape."""

    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        feed: str = "sip",
    ) -> None:
        self._key = api_key or os.environ.get("ALPACA_API_KEY", "")
        self._secret = secret_key or os.environ.get("ALPACA_SECRET_KEY", "")
        self.feed = feed

        if not self._key or not self._secret:
            raise PriceError(
                "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set. "
                "Run `uv run python scripts/check_keys.py` to verify."
            )

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._key,
            "APCA-API-SECRET-KEY": self._secret,
        }

    def daily_bars(
        self, symbol: str, start: date, end: date | None = None
    ) -> BarSet:
        """Daily OHLCV bars, oldest first.

        The end of the range is clamped clear of the SIP embargo, so a caller
        asking for "up to now" gets the most recent permitted data rather than
        a 403.
        """
        symbol = symbol.strip().upper()
        latest_allowed = datetime.now(timezone.utc) - SIP_EMBARGO - timedelta(minutes=1)
        end_dt = (
            datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc)
            if end
            else latest_allowed
        )
        if self.feed == "sip":
            end_dt = min(end_dt, latest_allowed)

        if end_dt.date() < start:
            raise PriceError(
                f"{symbol}: end {end_dt.date()} precedes start {start}"
            )

        collected: list[Bar] = []
        page_token: str | None = None

        while True:
            params: dict[str, Any] = {
                "timeframe": "1Day",
                "start": start.isoformat(),
                "end": end_dt.isoformat(),
                "feed": self.feed,
                "adjustment": "split",
                "limit": 10_000,
            }
            if page_token:
                params["page_token"] = page_token

            try:
                response = httpx.get(
                    ALPACA_DATA_URL.format(symbol=symbol),
                    headers=self._headers,
                    params=params,
                    timeout=30.0,
                )
            except httpx.HTTPError as exc:
                raise PriceError(f"{symbol}: request failed: {exc}") from exc

            if response.status_code == 403:
                raise PriceError(
                    f"{symbol}: {response.json().get('message', 'forbidden')}. "
                    f"The free tier embargoes SIP data from the last 15 minutes."
                )
            if response.status_code != 200:
                raise PriceError(
                    f"{symbol}: HTTP {response.status_code}: {response.text[:200]}"
                )

            payload = response.json()
            for row in payload.get("bars") or []:
                collected.append(
                    Bar(
                        date=row["t"][:10],
                        open=row["o"],
                        high=row["h"],
                        low=row["l"],
                        close=row["c"],
                        volume=int(row["v"]),
                        trade_count=row.get("n"),
                        vwap=row.get("vw"),
                    )
                )

            page_token = payload.get("next_page_token")
            if not page_token:
                break

        if not collected:
            raise PriceError(
                f"{symbol}: no bars between {start} and {end_dt.date()}. "
                f"Check the symbol is a listed US equity."
            )

        return BarSet(
            symbol=symbol,
            timeframe="1Day",
            feed=self.feed,
            bars=collected,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
        )

    def recent_daily_bars(self, symbol: str, lookback_days: int = 400) -> BarSet:
        """Bars covering roughly the last `lookback_days` calendar days.

        Defaults to 400 so a 200-day moving average has enough trading days
        behind it (roughly 252 sessions a year).
        """
        return self.daily_bars(
            symbol, start=date.today() - timedelta(days=lookback_days)
        )


def alpaca_credentials() -> tuple[str, str]:
    """Alpaca key and secret from the environment.

    Shared with the screener, which hits Alpaca endpoints that return no bars
    and so has no use for a `PriceSource`, but needs the same credentials and
    the same error when they are missing.
    """
    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        raise PriceError(
            "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set. "
            "Run `uv run python scripts/check_keys.py` to verify."
        )
    return key, secret


def default_source() -> PriceSource:
    """The price source the desk uses unless told otherwise."""
    return AlpacaSource(feed=os.environ.get("DESK_PRICE_FEED", "sip"))
