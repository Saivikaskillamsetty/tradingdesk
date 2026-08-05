"""Order placement, reachable only through a risk-approved, journalled thesis.

Every other layer of this desk can be argued with. An agent can be persuaded
that a setup is exceptional, that the limit is unreasonable today, that this
one is different. So the gate is not advice given to an agent -- it is the
shape of the function.

`place_order` takes a thesis id and nothing else. There is no symbol
parameter, no quantity, no price. Everything the broker receives is read back
out of the journal entry, which only exists because the risk officer approved
it and only carries a share count the risk officer computed. An agent that
wants to place an order it invented has no way to express that here.

Three further guarantees, in the order they are checked:

    The base URL is the paper endpoint, hardcoded. There is no environment
    variable that redirects it, because a configuration mistake must not be
    able to reach a live account.

    The account number is verified to carry Alpaca's `PA` paper prefix before
    any order is sent, so live keys against the paper URL fail closed.

    A thesis records its order once. A retried call finds the order already
    attached and refuses, rather than opening a second position in the name.

Orders go out as brackets where a target exists: the stop the risk officer
sized against is submitted with the entry, not left as something to add
afterwards.
"""

from __future__ import annotations

from typing import Any

import httpx

from desk_mcp import journal
from desk_mcp.prices.source import PriceError, alpaca_credentials

# Paper only, deliberately not configurable. See the module docstring.
PAPER_BASE = "https://paper-api.alpaca.markets/v2"

# Alpaca prefixes paper account numbers with PA.
PAPER_ACCOUNT_PREFIX = "PA"

APPROVED_VERDICTS = ("approved", "approved_with_warnings")


class ExecutionError(RuntimeError):
    """An order that must not be sent, or a broker call that failed."""


def _request(method: str, path: str, **kwargs: Any) -> Any:
    key, secret = alpaca_credentials()
    try:
        response = httpx.request(
            method,
            f"{PAPER_BASE}{path}",
            headers={
                "APCA-API-KEY-ID": key,
                "APCA-API-SECRET-KEY": secret,
                "Content-Type": "application/json",
            },
            timeout=30.0,
            **kwargs,
        )
    except httpx.HTTPError as exc:
        raise ExecutionError(f"broker request to {path} failed: {exc}") from exc

    if response.status_code == 401:
        raise ExecutionError(
            "broker rejected the credentials (401). Paper keys are generated "
            "separately from live ones — check the dashboard is switched to "
            "Paper before generating them."
        )
    if response.status_code >= 400:
        raise ExecutionError(
            f"broker returned HTTP {response.status_code}: {response.text[:300]}"
        )

    return response.json() if response.content else {}


def account() -> dict[str, Any]:
    """Account state, having first proved this is a paper account.

    Fails closed: anything other than a confirmed paper account number stops
    the call here rather than at the order.
    """
    data = _request("GET", "/account")
    number = str(data.get("account_number", ""))

    if not number.startswith(PAPER_ACCOUNT_PREFIX):
        raise ExecutionError(
            f"account {number!r} does not carry the paper prefix "
            f"{PAPER_ACCOUNT_PREFIX!r}. This desk refuses to trade an account "
            f"it cannot prove is paper."
        )

    return {
        "account_number": number,
        "status": data.get("status"),
        "paper": True,
        "equity": float(data.get("equity", 0)),
        "cash": float(data.get("cash", 0)),
        "buying_power": float(data.get("buying_power", 0)),
        "positions_value": float(data.get("long_market_value", 0) or 0),
    }


def _authorised(thesis: dict[str, Any]) -> None:
    """Every reason a journalled thesis may not be sent to the broker."""
    thesis_id = thesis["id"]

    if thesis.get("status") != "open":
        raise ExecutionError(
            f"thesis {thesis_id} is {thesis.get('status')}; only an open "
            f"thesis can be executed"
        )

    direction = thesis.get("direction")
    if direction not in ("long", "short"):
        raise ExecutionError(
            f"thesis {thesis_id} is a {direction!r} call, which takes no "
            f"position. Nothing to execute."
        )

    sizing = thesis.get("sizing") or {}
    verdict = sizing.get("risk_verdict")
    if verdict not in APPROVED_VERDICTS:
        raise ExecutionError(
            f"thesis {thesis_id} carries risk verdict {verdict!r}. Only "
            f"{' or '.join(APPROVED_VERDICTS)} may be executed — re-run the "
            f"risk officer rather than sending this."
        )

    shares = sizing.get("shares")
    if not shares or int(shares) < 1:
        raise ExecutionError(
            f"thesis {thesis_id} was sized at {shares!r} shares; there is no "
            f"position to open"
        )

    levels = thesis.get("levels") or {}
    if levels.get("entry") is None or levels.get("stop") is None:
        raise ExecutionError(
            f"thesis {thesis_id} is missing an entry or stop; the bracket "
            f"cannot be built without both"
        )

    if thesis.get("execution"):
        raise ExecutionError(
            f"thesis {thesis_id} already has order "
            f"{thesis['execution'].get('broker_order_id')} attached"
        )


def _build_order(thesis: dict[str, Any]) -> dict[str, Any]:
    """The broker payload, derived entirely from the journalled thesis."""
    levels = thesis["levels"]
    sizing = thesis["sizing"]
    long = thesis["direction"] == "long"

    payload: dict[str, Any] = {
        "symbol": thesis["ticker"],
        "qty": str(int(sizing["shares"])),
        "side": "buy" if long else "sell",
        "type": "limit",
        "limit_price": str(levels["entry"]),
        "time_in_force": "day",
        "client_order_id": f"desk-{thesis['id']}",
        "order_class": "bracket",
        "stop_loss": {"stop_price": str(levels["stop"])},
    }

    if levels.get("target") is not None:
        payload["take_profit"] = {"limit_price": str(levels["target"])}
    else:
        # Without a target there is no bracket to build, but the stop still
        # goes out attached to the entry rather than being left to a later
        # call that might never happen.
        payload["order_class"] = "oto"

    return payload


def place_order(thesis_id: str) -> dict[str, Any]:
    """Send a journalled, risk-approved thesis to the paper broker.

    Takes no order parameters by design. Everything the broker receives comes
    from the thesis, which exists only because the risk officer approved it.
    """
    thesis = journal.load(thesis_id)
    _authorised(thesis)

    # Prove the account is paper before anything is sent.
    account_state = account()

    payload = _build_order(thesis)
    placed = _request("POST", "/orders", json=payload)

    order = {
        "broker_order_id": placed.get("id"),
        "client_order_id": placed.get("client_order_id"),
        "symbol": placed.get("symbol"),
        "qty": placed.get("qty"),
        "side": placed.get("side"),
        "order_class": placed.get("order_class"),
        "limit_price": placed.get("limit_price"),
        "status": placed.get("status"),
        "submitted_at": placed.get("submitted_at"),
        "account": account_state["account_number"],
        "paper": True,
    }
    journal.attach_order(thesis_id, order)

    return {
        "thesis_id": thesis_id,
        "order": order,
        "bracket": {
            "entry": thesis["levels"]["entry"],
            "stop": thesis["levels"]["stop"],
            "target": thesis["levels"].get("target"),
        },
        "note": (
            "Submitted to the Alpaca paper account. A submitted order is not "
            "a filled one — a limit entry may never fill, and the position "
            "does not exist until it does."
        ),
    }


def positions() -> dict[str, Any]:
    """Open positions at the broker."""
    rows = _request("GET", "/positions")

    return {
        "positions": [
            {
                "symbol": row["symbol"],
                "qty": float(row["qty"]),
                "side": row.get("side"),
                "avg_entry_price": float(row["avg_entry_price"]),
                "market_value": float(row["market_value"]),
                "unrealized_pl": float(row["unrealized_pl"]),
                "unrealized_plpc": round(float(row["unrealized_plpc"]) * 100, 2),
            }
            for row in rows
        ],
        "paper": True,
    }


def orders(status: str = "open", limit: int = 50) -> dict[str, Any]:
    """Orders at the broker, newest first."""
    rows = _request(
        "GET", "/orders", params={"status": status, "limit": limit, "nested": "true"}
    )

    return {
        "orders": [
            {
                "broker_order_id": row["id"],
                "client_order_id": row.get("client_order_id"),
                "symbol": row["symbol"],
                "qty": row.get("qty"),
                "filled_qty": row.get("filled_qty"),
                "side": row.get("side"),
                "type": row.get("type"),
                "limit_price": row.get("limit_price"),
                "status": row.get("status"),
                "submitted_at": row.get("submitted_at"),
            }
            for row in rows
        ],
        "paper": True,
    }


def cancel_order(broker_order_id: str) -> dict[str, Any]:
    """Cancel a working order. Does not close a filled position."""
    _request("DELETE", f"/orders/{broker_order_id}")

    return {
        "cancelled": broker_order_id,
        "note": (
            "Cancellation applies to the working order only. If the entry had "
            "already filled, the position is still open — close it explicitly."
        ),
    }


def close_position(symbol: str) -> dict[str, Any]:
    """Close an open position at market.

    Closing at the broker does not close the thesis. Call `close_thesis` with
    the fill so the call can be scored — an unresolved thesis on a position
    that no longer exists is exactly the gap the journal was built to prevent.
    """
    placed = _request("DELETE", f"/positions/{symbol.strip().upper()}")

    return {
        "symbol": symbol.strip().upper(),
        "closing_order_id": placed.get("id"),
        "status": placed.get("status"),
        "reminder": (
            "The thesis is still open in the journal. Close it with the fill "
            "price so realised R is recorded."
        ),
    }


def reconcile() -> dict[str, Any]:
    """Compare broker positions against open journalled theses.

    This is the check that closes the loop the risk officer keeps declaring:
    portfolio heat is computed from the journal, so a position held without a
    thesis is risk nothing on this desk can see. It reports both directions of
    mismatch, because each means something different -- an untracked position
    is invisible exposure, while a thesis with no position is usually an entry
    that never filled.
    """
    held = {p["symbol"]: p for p in positions()["positions"]}
    open_theses = journal.search(status="open", limit=1000)
    tracked = {
        t["ticker"]: t
        for t in open_theses
        if t.get("direction") in ("long", "short")
    }

    untracked = [held[s] for s in held.keys() - tracked.keys()]
    unfilled = [
        {
            "id": tracked[s]["id"],
            "ticker": s,
            "direction": tracked[s]["direction"],
            "has_order": bool(tracked[s].get("execution")),
            "dollar_risk": (tracked[s].get("sizing") or {}).get("dollar_risk"),
        }
        for s in tracked.keys() - held.keys()
    ]
    matched = sorted(held.keys() & tracked.keys())

    return {
        "matched": matched,
        "untracked_positions": untracked,
        "theses_without_position": unfilled,
        "in_sync": not untracked and not unfilled,
        "note": (
            "An untracked position is exposure the risk checks cannot see, "
            "because portfolio heat is computed from the journal. A thesis "
            "with no position is usually a limit entry that never filled."
        ),
    }
