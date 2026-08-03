#!/usr/bin/env python3
"""Exercise every MCP tool the /analyze skill depends on, against live data.

The unit tests prove the maths; this proves the wiring -- that each tool is
reachable, returns the shape agents expect, and carries provenance. Run it
after changing the server or before trusting a session.

Usage:  uv run python scripts/smoke_test.py [TICKER]
"""

from __future__ import annotations

import asyncio
import sys

from desk_mcp.server import mcp


class SmokeFailure(RuntimeError):
    pass


async def call(name: str, args: dict) -> dict:
    result = await mcp.call_tool(name, args)
    payload = result.structured_content
    if payload is None:
        raise SmokeFailure(f"{name}: no structured content returned")
    if payload.get("error"):
        raise SmokeFailure(f"{name}: {payload['error']} — {payload['message']}")
    return payload


def money(value: float) -> str:
    for scale, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(value) >= scale:
            return f"{value / scale:.1f}{suffix}"
    return f"{value:,.0f}"


async def fundamentals_path(ticker: str) -> None:
    print("\n[fundamentals agent tools]")

    metrics = await call("get_metrics", {"ticker": ticker})
    print(
        f"  get_metrics         {metrics['company_name']} "
        f"FY{metrics['fiscal_year']} — {len(metrics['metrics'])} metrics"
    )
    by_key = {m["key"]: m for m in metrics["metrics"]}
    for key in ("gross_margin", "net_margin", "revenue_yoy", "cash_conversion", "net_debt"):
        metric = by_key.get(key)
        if not metric or metric["value"] is None:
            continue
        shown = (
            money(metric["value"])
            if metric["unit"] == "USD"
            else f"{metric['value']:.1f}{metric['unit']}"
        )
        print(f"      {metric['label']:26s} {shown}")

    statement = await call("get_financials", {"ticker": ticker})
    print(
        f"  get_financials      {len(statement['facts'])} line items, "
        f"{len(statement['unavailable'])} unavailable"
    )

    sample = next(iter(statement["facts"].values()))
    for field in ("concept", "period_end", "accession", "filed"):
        if not sample.get(field):
            raise SmokeFailure(f"provenance field {field!r} missing from facts")
    print(f"      provenance intact  concept={sample['concept'][:44]}")

    history = await call(
        "get_financial_history",
        {"ticker": ticker, "line_item": "revenue", "limit": 4},
    )
    trail = " -> ".join(money(p["value"]) for p in history["points"])
    print(f"  get_financial_hist  revenue {trail}")

    filings = await call(
        "get_filings", {"ticker": ticker, "forms": ["10-K", "8-K"], "limit": 3}
    )
    listed = ", ".join(f"{f['form']} {f['filed']}" for f in filings["filings"])
    print(f"  get_filings         {listed}")

    insider = await call("get_insider_activity", {"ticker": ticker, "limit": 5})
    print(f"  get_insider_activity {insider['form_4_count']} Form 4 filings")


async def chartist_path(ticker: str) -> None:
    print("\n[chartist agent tools]")

    tech = await call("get_technicals", {"ticker": ticker})
    price = tech["price"]
    momentum = tech["momentum"]
    volatility = tech["volatility"]

    if tech["feed"] != "sip":
        print(f"      WARNING feed is {tech['feed']}, not the consolidated tape")

    print(f"  get_technicals      feed={tech['feed']}  bars={price['bars_available']}")
    print(f"      trend           {tech['trend']['structure']}")
    print(
        f"      price           {price['last_close']}  "
        f"vol {price['last_volume']:,} ({price['volume_vs_average']}x avg)"
    )
    print(
        f"      rsi / atr       {momentum['rsi_14']} / "
        f"{volatility['atr_pct_of_price']}% of price"
    )
    rs_key = next(k for k in momentum if k.startswith("relative_strength"))
    print(f"      rel strength    {momentum[rs_key]}")
    print(f"      drawdown 1y     {momentum['drawdown_from_1y_high_pct']}%")

    for label, key in (("support", "nearest_support"), ("resistance", "nearest_resistance")):
        levels = tech["levels"][key]
        if not levels:
            print(f"      {label:15s} none found")
            continue
        nearest = levels[0]
        print(
            f"      {label:15s} {nearest['price']} "
            f"({nearest['distance_pct']:+.1f}%, {nearest['touches']} touches)"
        )

    print(f"      limitations     {tech['limitations'] or 'none'}")


async def main(ticker: str) -> int:
    print("=" * 60)
    print(f"Smoke test — full /analyze data path for {ticker}")
    print("=" * 60)

    try:
        await fundamentals_path(ticker)
        await chartist_path(ticker)
    except SmokeFailure as exc:
        print(f"\nFAILED: {exc}")
        return 1

    print("\n" + "=" * 60)
    print("All tools reachable and returning expected shapes.")
    return 0


if __name__ == "__main__":
    symbol = (sys.argv[1] if len(sys.argv) > 1 else "AMD").upper()
    sys.exit(asyncio.run(main(symbol)))
