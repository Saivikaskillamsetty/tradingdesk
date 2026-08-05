#!/usr/bin/env python3
"""Verify API credentials are present and working.

Deliberately never prints a secret. Reports presence, length and a masked
prefix only -- enough to spot a truncated paste or a swapped pair, and not
enough to leak anything into a terminal transcript or a screen share.

Usage:  uv run python scripts/check_keys.py
"""

from __future__ import annotations

import os
import sys

import httpx

PAPER_ACCOUNT_URL = "https://paper-api.alpaca.markets/v2/account"
PAPER_CLOCK_URL = "https://paper-api.alpaca.markets/v2/clock"
STOCK_BARS_URL = "https://data.alpaca.markets/v2/stocks/AAPL/bars"
FRED_SERIES_URL = "https://api.stlouisfed.org/fred/series/observations"

OK, FAIL, WARN = "  OK  ", " FAIL ", " WARN "


def mask(value: str) -> str:
    """Show only enough to identify which key this is."""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-2:]} ({len(value)} chars)"


def check_alpaca() -> bool:
    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_SECRET_KEY", "")

    print("Alpaca (paper trading)")
    if not key or not secret:
        missing = [
            name
            for name, value in (
                ("ALPACA_API_KEY", key),
                ("ALPACA_SECRET_KEY", secret),
            )
            if not value
        ]
        print(f"[{FAIL}] not set: {', '.join(missing)}")
        return False

    print(f"[{OK}] ALPACA_API_KEY     {mask(key)}")
    print(f"[{OK}] ALPACA_SECRET_KEY  {mask(secret)}")

    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}

    try:
        response = httpx.get(PAPER_ACCOUNT_URL, headers=headers, timeout=20)
    except httpx.HTTPError as exc:
        print(f"[{FAIL}] could not reach Alpaca: {exc}")
        return False

    if response.status_code == 401:
        print(f"[{FAIL}] rejected (401). Common causes:")
        print("         - keys generated in the Live environment, not Paper")
        print("         - secret truncated on copy")
        return False
    if response.status_code != 200:
        print(f"[{FAIL}] HTTP {response.status_code}: {response.text[:120]}")
        return False

    account = response.json()
    print(
        f"[{OK}] authenticated  status={account.get('status')}  "
        f"equity=${float(account.get('equity', 0)):,.2f}"
    )

    # Guard against live keys being used by mistake.
    if account.get("account_number", "").startswith("PA"):
        print(f"[{OK}] confirmed PAPER account (no real money at risk)")
    else:
        print(f"[{WARN}] account number does not look like a paper account.")
        print("         Verify you generated these under the Paper environment.")

    try:
        clock = httpx.get(PAPER_CLOCK_URL, headers=headers, timeout=20).json()
        state = "open" if clock.get("is_open") else "closed"
        print(f"[{OK}] market is {state}  (next open {clock.get('next_open', '?')})")
    except (httpx.HTTPError, ValueError):
        pass

    # Market data is a separate entitlement from trading -- verify it too,
    # since the desk needs bars far more than it needs order placement.
    try:
        bars = httpx.get(
            STOCK_BARS_URL,
            headers=headers,
            params={"timeframe": "1Day", "limit": 3, "feed": "iex"},
            timeout=20,
        )
        if bars.status_code == 200:
            rows = bars.json().get("bars", []) or []
            if rows:
                latest = rows[-1]
                print(
                    f"[{OK}] market data  AAPL {latest['t'][:10]} "
                    f"close={latest['c']} volume={latest['v']:,}"
                )
            else:
                print(f"[{WARN}] market data reachable but returned no bars")
        else:
            print(f"[{WARN}] market data HTTP {bars.status_code} — trading works, "
                  f"data entitlement may differ")
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        print(f"[{WARN}] market data check inconclusive: {exc}")

    return True


def check_fred() -> bool:
    key = os.environ.get("FRED_API_KEY", "")

    print("\nFRED (macro data — used by the `macro` agent)")
    if not key:
        print(f"[{WARN}] FRED_API_KEY not set — the `macro` agent cannot run")
        print("         Free key: https://fredaccount.stlouisfed.org/apikeys")
        print("         Every other agent works without it.")
        return True

    print(f"[{OK}] FRED_API_KEY       {mask(key)}")

    try:
        response = httpx.get(
            FRED_SERIES_URL,
            params={
                "series_id": "DGS10",
                "api_key": key,
                "file_type": "json",
                "limit": 1,
                "sort_order": "desc",
            },
            timeout=20,
        )
    except httpx.HTTPError as exc:
        print(f"[{FAIL}] could not reach FRED: {exc}")
        return False

    if response.status_code != 200:
        print(f"[{FAIL}] HTTP {response.status_code} — key likely invalid")
        return False

    observations = response.json().get("observations", [])
    if observations:
        point = observations[0]
        print(f"[{OK}] authenticated  10y treasury {point['value']}% "
              f"({point['date']})")
    return True


def check_sec() -> bool:
    """EDGAR needs no key, but a descriptive User-Agent is mandatory."""
    print("\nSEC EDGAR (no key required)")
    agent = os.environ.get("SEC_USER_AGENT", "")
    if agent:
        print(f"[{OK}] SEC_USER_AGENT set")
    else:
        print(f"[{WARN}] SEC_USER_AGENT not set — falling back to the default "
              f"in .mcp.json")
    return True


def main() -> int:
    print("=" * 62)
    print("Trading desk credential check")
    print("Secrets are never printed — only presence and a masked prefix.")
    print("=" * 62 + "\n")

    results = [check_alpaca(), check_fred(), check_sec()]

    print("\n" + "=" * 62)
    if all(results):
        print("All required credentials working.")
        return 0
    print("Some checks failed — see above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
