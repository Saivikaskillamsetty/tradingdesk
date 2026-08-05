#!/usr/bin/env python3
"""Exercise every MCP tool the /analyze skill depends on, against live data.

The unit tests prove the maths; this proves the wiring -- that each tool is
reachable, returns the shape agents expect, and carries provenance. Run it
after changing the server or before trusting a session.

Usage:  uv run python scripts/smoke_test.py [TICKER]
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile

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


async def chartist_path(ticker: str) -> dict:
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
    return tech


async def risk_and_journal_path(ticker: str, tech: dict) -> None:
    """Exercise sizing and the journal end to end, on a scratch directory.

    The levels here are derived from ATR rather than taken from a real setup:
    the point is that the tools are wired together, not that this is a trade
    anyone should take.
    """
    print("\n[risk officer and journal tools]")

    atr = tech["volatility"]["atr_14"]
    entry = tech["price"]["last_close"]
    if not atr or not entry:
        raise SmokeFailure("no ATR or last close available to size against")

    stop = round(entry - 2 * atr, 2)
    target = round(entry + 5 * atr, 2)

    policy = await call("get_risk_policy", {})
    print(f"  get_risk_policy     {len(policy['rules'])} rules")

    sized = await call(
        "size_position",
        {
            "ticker": ticker,
            "direction": "long",
            "entry": entry,
            "stop": stop,
            "target": target,
            "account_equity": 100_000.0,
            "atr": atr,
        },
    )
    sizing = sized["sizing"]
    print(
        f"  size_position       {sized['verdict']} — {sizing['shares']} shares, "
        f"${sizing['dollar_risk']:,.0f} at risk "
        f"({sizing['pct_equity_at_risk']}% of equity)"
    )
    print(
        f"      reward:risk     {sized['levels']['reward_risk']}:1 at "
        f"{sized['levels']['stop_atr_multiple']}x ATR"
    )
    print(f"      heat after      {sized['portfolio']['heat_after_this_trade_pct']}%")
    if sized["vetoes"]:
        print(f"      vetoes          {sized['vetoes']}")

    recorded = await call(
        "journal_thesis",
        {
            "ticker": ticker,
            "thesis": "Smoke test entry — not a real call.",
            "falsifiers": [f"close below {stop}"],
            "direction": "long",
            "entry": entry,
            "stop": stop,
            "target": target,
            "reward_risk": sized["levels"]["reward_risk"],
            "shares": sizing["shares"],
            "dollar_risk": sizing["dollar_risk"],
            "risk_verdict": sized["verdict"],
            "evidence": [
                {
                    "claim": f"last close {entry}",
                    "source": "get_technicals",
                    "period": tech["price"]["last_date"],
                }
            ],
        },
    )
    thesis_id = recorded["id"]
    print(f"  journal_thesis      {thesis_id}")

    listed = await call("list_theses", {"status": "open"})
    if not any(t["id"] == thesis_id for t in listed["theses"]):
        raise SmokeFailure("journalled thesis did not come back from list_theses")
    print(f"  list_theses         {len(listed['theses'])} open")

    fetched = await call("get_thesis", {"thesis_id": thesis_id})
    if not fetched["falsifiers"]:
        raise SmokeFailure("falsifiers did not survive the round trip")
    print(f"  get_thesis          falsifiers intact ({len(fetched['falsifiers'])})")

    # A second sizing call must now see the first position in portfolio heat.
    reheated = await call(
        "size_position",
        {
            "ticker": ticker,
            "direction": "long",
            "entry": entry,
            "stop": stop,
            "target": target,
            "account_equity": 100_000.0,
            "atr": atr,
        },
    )
    if reheated["portfolio"]["open_positions"] < 1:
        raise SmokeFailure("open thesis did not register in portfolio heat")
    print(
        f"  heat picked up      {reheated['portfolio']['open_positions']} open, "
        f"{reheated['portfolio']['open_heat_pct']}% before this trade"
    )

    closed = await call(
        "close_thesis",
        {
            "thesis_id": thesis_id,
            "outcome": "target_hit",
            "exit_price": target,
            "note": "Smoke test close.",
        },
    )
    print(
        f"  close_thesis        {closed['outcome']['result']} at "
        f"{closed['outcome']['realised_r']}R"
    )


async def main(ticker: str) -> int:
    print("=" * 60)
    print(f"Smoke test — full /analyze data path for {ticker}")
    print("=" * 60)

    scratch = tempfile.mkdtemp(prefix="desk-smoke-theses-")
    os.environ["DESK_THESES_DIR"] = scratch

    try:
        await fundamentals_path(ticker)
        tech = await chartist_path(ticker)
        await risk_and_journal_path(ticker, tech)
    except SmokeFailure as exc:
        print(f"\nFAILED: {exc}")
        return 1
    finally:
        # Never leave smoke-test theses where the real journal can find them.
        shutil.rmtree(scratch, ignore_errors=True)

    print("\n" + "=" * 60)
    print("All tools reachable and returning expected shapes.")
    return 0


if __name__ == "__main__":
    symbol = (sys.argv[1] if len(sys.argv) > 1 else "AMD").upper()
    sys.exit(asyncio.run(main(symbol)))
