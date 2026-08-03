"""HTTP access to SEC EDGAR.

EDGAR needs no API key, but it does require a descriptive User-Agent and
enforces a 10 req/sec ceiling. Exceeding it gets the caller IP-banned, so the
rate limiter here is deliberately conservative.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import httpx

SEC_UA = os.environ.get(
    "SEC_USER_AGENT", "TradingDesk Research saivikas34@gmail.com"
)

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

# SEC's published ceiling is 10/sec. We use 5/sec for headroom.
_MIN_INTERVAL = 0.2

CACHE_DIR = Path(
    os.environ.get("DESK_CACHE_DIR", Path(__file__).resolve().parents[2] / ".cache")
)

# Company facts change only when a new filing lands; a day is a safe TTL.
DEFAULT_TTL = 86_400


class EdgarError(RuntimeError):
    """EDGAR request failed or returned something unusable."""


class _RateLimiter:
    """Process-wide minimum spacing between SEC requests."""

    def __init__(self, min_interval: float) -> None:
        self._min_interval = min_interval
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last = time.monotonic()


_limiter = _RateLimiter(_MIN_INTERVAL)


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def _read_cache(key: str, ttl: int) -> Any | None:
    path = _cache_path(key)
    if not path.exists():
        return None
    if ttl >= 0 and time.time() - path.stat().st_mtime > ttl:
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        # A truncated cache file should never be fatal — just refetch.
        return None


def _write_cache(key: str, payload: Any) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(key)
    # Write via temp file so a crash mid-write can't leave corrupt JSON behind.
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(path)


def fetch_json(url: str, cache_key: str, ttl: int = DEFAULT_TTL) -> Any:
    """GET a JSON document from SEC, using the on-disk cache when fresh."""
    cached = _read_cache(cache_key, ttl)
    if cached is not None:
        return cached

    _limiter.wait()
    try:
        response = httpx.get(
            url,
            headers={
                "User-Agent": SEC_UA,
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=30.0,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        raise EdgarError(f"request to {url} failed: {exc}") from exc

    if response.status_code == 404:
        raise EdgarError(f"not found: {url}")
    if response.status_code != 200:
        raise EdgarError(f"{url} returned HTTP {response.status_code}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise EdgarError(f"{url} returned non-JSON body") from exc

    _write_cache(cache_key, payload)
    return payload


def resolve_cik(ticker: str) -> int:
    """Map a ticker symbol to its SEC CIK number."""
    symbol = ticker.strip().upper()
    mapping = fetch_json(TICKER_MAP_URL, "company_tickers", ttl=7 * 86_400)

    for row in mapping.values():
        if row["ticker"].upper() == symbol:
            return int(row["cik_str"])

    raise EdgarError(f"no CIK found for ticker {symbol!r}")


def company_facts(cik: int) -> dict[str, Any]:
    """Full XBRL fact set for a company."""
    return fetch_json(
        COMPANYFACTS_URL.format(cik=cik), f"companyfacts_{cik:010d}"
    )


def submissions(cik: int) -> dict[str, Any]:
    """Filing history for a company."""
    return fetch_json(
        SUBMISSIONS_URL.format(cik=cik), f"submissions_{cik:010d}", ttl=3_600
    )
