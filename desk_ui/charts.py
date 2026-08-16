"""Chart builders for the desk.

Each function here picks its form from the data's job rather than from what
looks impressive: magnitude gets a bar, change over time gets a line,
above-and-below-a-baseline gets a diverging bar, and a single headline number
gets a stat tile instead of a one-bar chart.

Every chart ships a hover layer. An HTML chart is interactive whether or not
anyone planned for it, and a reader who cannot interrogate a mark is left
guessing at values the tooltip could simply have told them.
"""

from __future__ import annotations

from typing import Any

import altair as alt
import pandas as pd

from desk_ui import theme

# Reused so every tooltip in the app behaves the same way.
_HOVER = alt.selection_point(on="pointerover", nearest=True, empty=False, clear="pointerout")


def _empty(message: str) -> alt.Chart:
    """A placeholder that says why there is nothing, rather than a blank box."""
    return (
        alt.Chart(pd.DataFrame({"m": [message]}))
        .mark_text(color=theme.INK_MUTED, fontSize=12, font=theme.FONT)
        .encode(text="m:N")
        .properties(height=90, width="container")
    )


def price(
    bars: list[dict[str, Any]],
    support: list[float] | None = None,
    resistance: list[float] | None = None,
    title: str = "",
) -> alt.LayerChart:
    """Close over time, with the levels a stop or entry would sit on.

    A single series, so no legend: the title names what is plotted. Levels are
    reference rules rather than a second series -- they are annotation on one
    line, not two things being compared.
    """
    if not bars:
        return _empty("No price history available.")

    frame = pd.DataFrame(bars)
    frame["date"] = pd.to_datetime(frame["date"])

    # A price axis must not start at zero: the question is how price moved,
    # and anchoring to zero spends most of the plot on empty space no reader
    # is asking about. The domain covers the data and any level drawn on it,
    # so a rule never falls outside the frame.
    span_values = list(frame["close"]) + list(support or []) + list(resistance or [])
    low, high = min(span_values), max(span_values)
    margin = (high - low) * 0.08 or 1.0
    y_scale = alt.Scale(domain=[low - margin, high + margin], nice=False)

    base = alt.Chart(frame).encode(
        x=alt.X("date:T", title=None, axis=alt.Axis(format="%b %y")),
    )

    # A line, not an area: area implies the filled quantity means something,
    # and the area under a price line does not.
    line = base.mark_line(color=theme.ACCENT).encode(
        y=alt.Y("close:Q", title=None, scale=y_scale)
    )

    # Crosshair plus tooltip: the reader can read any session exactly.
    hover = (
        base.mark_rule(color=theme.INK_MUTED)
        .encode(
            opacity=alt.condition(_HOVER, alt.value(0.45), alt.value(0)),
            tooltip=[
                alt.Tooltip("date:T", title="Date", format="%d %b %Y"),
                alt.Tooltip("close:Q", title="Close", format=",.2f"),
                alt.Tooltip("volume:Q", title="Volume", format="~s"),
            ],
        )
        .add_params(_HOVER)
    )

    layers = [line, hover]

    # Levels already carrying a label, so a second one drawn close to the
    # first does not print on top of it. Two labels a hair apart are less
    # readable than one label and an unlabelled rule, and the levels table
    # below the chart carries every value regardless.
    labelled: list[float] = []
    min_gap = (high - low) * 0.06

    def rules(values: list[float], color: str, prefix: str) -> None:
        # Two levels a side. More rules than that on one pane stop reading as
        # levels and start reading as a grid.
        for value in values[:2]:
            frame_rule = pd.DataFrame(
                {"level": [value], "label": [f"{prefix} {value:,.2f}"]}
            )
            layers.append(
                alt.Chart(frame_rule)
                .mark_rule(color=color, strokeWidth=1, opacity=0.7)
                .encode(y=alt.Y("level:Q", scale=y_scale))
            )
            if any(abs(value - seen) < min_gap for seen in labelled):
                continue
            labelled.append(value)
            # The label rides the right end of its own rule rather than the
            # left edge, where every level's label lands in the same place
            # and they overlap each other.
            layers.append(
                alt.Chart(frame_rule)
                .mark_text(
                    align="right",
                    baseline="bottom",
                    dx=-4,
                    dy=-3,
                    fontSize=10,
                    color=theme.INK_MUTED,
                    font=theme.FONT,
                )
                .encode(
                    y=alt.Y("level:Q", scale=y_scale),
                    x=alt.value("width"),
                    text="label:N",
                )
            )

    rules(support or [], theme.SERIES[2], "S")
    rules(resistance or [], theme.SERIES[1], "R")

    return alt.layer(*layers).properties(
        title=title, **theme.chart_size(300)
    ).resolve_scale(y="shared")


def cumulative_r(closed: list[dict[str, Any]], title: str = "") -> alt.LayerChart:
    """Running total of realised R, in the order calls resolved.

    An equity curve in R rather than currency, so calls of different sizes are
    comparable. Change over time on one series: a line, no legend.
    """
    rows = []
    running = 0.0
    for entry in sorted(closed, key=lambda t: t.get("closed_at") or ""):
        realised = (entry.get("outcome") or {}).get("realised_r")
        if realised is None:
            continue
        running += float(realised)
        rows.append(
            {
                "closed": entry.get("closed_at", "")[:10],
                "ticker": entry.get("ticker"),
                "r": float(realised),
                "cumulative": round(running, 2),
            }
        )

    if not rows:
        return _empty("No closed call carries a realised R yet.")

    frame = pd.DataFrame(rows)
    frame["closed"] = pd.to_datetime(frame["closed"])

    base = alt.Chart(frame).encode(x=alt.X("closed:T", title=None))

    zero = (
        alt.Chart(pd.DataFrame({"y": [0]}))
        .mark_rule(color=theme.AXIS, strokeWidth=1)
        .encode(y="y:Q")
    )
    line = base.mark_line(color=theme.ACCENT).encode(
        y=alt.Y("cumulative:Q", title="Cumulative R")
    )
    points = base.mark_point(
        color=theme.ACCENT,
        # A surface-coloured ring keeps overlapping dots legible.
        stroke=theme.SURFACE,
        strokeWidth=2,
        size=theme.MARKER_SIZE,
    ).encode(
        y="cumulative:Q",
        tooltip=[
            alt.Tooltip("ticker:N", title="Ticker"),
            alt.Tooltip("closed:T", title="Closed", format="%d %b %Y"),
            alt.Tooltip("r:Q", title="Realised R", format="+.2f"),
            alt.Tooltip("cumulative:Q", title="Running", format="+.2f"),
        ],
    )

    return alt.layer(zero, line, points).properties(title=title, **theme.chart_size(240))


def bucket_expectancy(buckets: dict[str, dict[str, Any]], title: str = "") -> alt.LayerChart:
    """Expectancy per bucket as a diverging bar around zero.

    Above and below a baseline is polarity, so this is diverging rather than
    categorical: the question is not which bucket is which, it is which ones
    made money.
    """
    rows = [
        {"bucket": name, "expectancy": stats["expectancy_r"], "count": stats["count"]}
        for name, stats in buckets.items()
        if stats.get("expectancy_r") is not None
    ]
    if not rows:
        return _empty("Nothing scored in these buckets yet.")

    frame = pd.DataFrame(rows)

    # Pad the domain so a value label placed outside a bar end has somewhere to
    # go. Without this a small negative bar ends flush against the axis and its
    # label lands on top of the category name.
    low = min(0.0, float(frame["expectancy"].min()))
    high = max(0.0, float(frame["expectancy"].max()))
    pad = max((high - low) * 0.18, 0.35)
    domain = [low - pad, high + pad]

    bars = (
        alt.Chart(frame)
        .mark_bar(cornerRadiusEnd=theme.CORNER_RADIUS, height=alt.RelativeBandSize(0.55))
        .encode(
            y=alt.Y("bucket:N", title=None, sort="-x"),
            x=alt.X("expectancy:Q", title="Expectancy (R)", scale=alt.Scale(domain=domain)),
            color=alt.condition(
                alt.datum.expectancy >= 0,
                alt.value(theme.POSITIVE),
                alt.value(theme.NEGATIVE),
            ),
            tooltip=[
                alt.Tooltip("bucket:N", title="Bucket"),
                alt.Tooltip("expectancy:Q", title="Expectancy", format="+.2f"),
                alt.Tooltip("count:Q", title="Calls"),
            ],
        )
    )

    # Value at the tip, sparingly -- one label per bar, nothing else.
    labels = (
        alt.Chart(frame)
        .mark_text(
            align=alt.expr("datum.expectancy >= 0 ? 'left' : 'right'"),
            dx=alt.expr("datum.expectancy >= 0 ? 5 : -5"),
            fontSize=11,
            color=theme.INK_SECONDARY,
            font=theme.FONT,
        )
        .encode(
            y=alt.Y("bucket:N", sort="-x"),
            x=alt.X("expectancy:Q", scale=alt.Scale(domain=domain)),
            text=alt.Text("expectancy:Q", format="+.2f"),
        )
    )

    zero = (
        alt.Chart(pd.DataFrame({"x": [0]}))
        .mark_rule(color=theme.AXIS, strokeWidth=1)
        .encode(x="x:Q")
    )

    return alt.layer(zero, bars, labels).properties(
        title=title, **theme.chart_size(max(120, 42 * len(rows)))
    )


def outcome_odds(
    p_target: float, p_stop: float, p_neither: float, title: str = ""
) -> alt.LayerChart:
    """Where a trade ends up, as three labelled status bars.

    Status colours, so every bar carries its name and its number: target-hit
    green and stopped-out red are the same colour to a deutan reader, and the
    label is what actually distinguishes them.
    """
    frame = pd.DataFrame(
        [
            {"outcome": "✓ Target first", "p": p_target, "c": theme.GOOD, "order": 0},
            {"outcome": "✕ Stop first", "p": p_stop, "c": theme.CRITICAL, "order": 1},
            {"outcome": "◦ Unresolved", "p": p_neither, "c": theme.INK_MUTED, "order": 2},
        ]
    )

    bars = (
        alt.Chart(frame)
        .mark_bar(cornerRadiusEnd=theme.CORNER_RADIUS, height=alt.RelativeBandSize(0.5))
        .encode(
            y=alt.Y("outcome:N", title=None, sort=alt.EncodingSortField("order")),
            x=alt.X(
                "p:Q",
                title="Probability",
                # Ticks stop at 100%; the domain runs past it so the value
                # label on a near-certain bar still has room to sit outside.
                axis=alt.Axis(format="%", values=[0, 0.25, 0.5, 0.75, 1.0]),
                scale=alt.Scale(domain=[0, 1.14], nice=False),
            ),
            color=alt.Color("c:N", scale=None, legend=None),
            tooltip=[
                alt.Tooltip("outcome:N", title="Outcome"),
                alt.Tooltip("p:Q", title="Probability", format=".1%"),
            ],
        )
    )

    labels = (
        alt.Chart(frame)
        .mark_text(align="left", dx=5, fontSize=11, color=theme.INK_SECONDARY, font=theme.FONT)
        .encode(
            y=alt.Y("outcome:N", sort=alt.EncodingSortField("order")),
            x="p:Q",
            text=alt.Text("p:Q", format=".1%"),
        )
    )

    return alt.layer(bars, labels).properties(title=title, **theme.chart_size(150))


def expected_move(
    quantiles: dict[str, float], spot: float, title: str = ""
) -> alt.LayerChart:
    """The forecast distribution as a band, with spot marked.

    One hue at two opacities: the wider band is the same thing as the narrow
    one, less likely, so a second colour would imply a second subject.
    """
    try:
        p5, p25, p50, p75, p95 = (
            quantiles["p5"],
            quantiles["p25"],
            quantiles["p50"],
            quantiles["p75"],
            quantiles["p95"],
        )
    except KeyError:
        return _empty("Distribution unavailable.")

    outer = pd.DataFrame([{"lo": p5, "hi": p95, "band": "5th–95th"}])
    inner = pd.DataFrame([{"lo": p25, "hi": p75, "band": "25th–75th"}])

    wide = (
        alt.Chart(outer)
        .mark_bar(height=54, cornerRadius=theme.CORNER_RADIUS, color=theme.ACCENT, opacity=0.18)
        .encode(
            x=alt.X("lo:Q", title="Price", scale=alt.Scale(zero=False, nice=True)),
            x2="hi:Q",
            tooltip=[
                alt.Tooltip("lo:Q", title="5th percentile", format=",.2f"),
                alt.Tooltip("hi:Q", title="95th percentile", format=",.2f"),
            ],
        )
    )
    narrow = (
        alt.Chart(inner)
        .mark_bar(height=54, cornerRadius=theme.CORNER_RADIUS, color=theme.ACCENT, opacity=0.42)
        .encode(
            x="lo:Q",
            x2="hi:Q",
            tooltip=[
                alt.Tooltip("lo:Q", title="25th percentile", format=",.2f"),
                alt.Tooltip("hi:Q", title="75th percentile", format=",.2f"),
            ],
        )
    )

    marks = pd.DataFrame(
        [
            {"x": spot, "label": f"spot {spot:,.2f}", "color": theme.INK},
            {"x": p50, "label": f"median {p50:,.2f}", "color": theme.SERIES[1]},
        ]
    )
    rules = (
        alt.Chart(marks)
        .mark_rule(strokeWidth=2)
        .encode(x="x:Q", color=alt.Color("color:N", scale=None, legend=None))
    )
    text = (
        alt.Chart(marks)
        .mark_text(align="center", dy=-38, fontSize=10, color=theme.INK_SECONDARY, font=theme.FONT)
        .encode(x="x:Q", text="label:N")
    )

    return alt.layer(wide, narrow, rules, text).properties(
        title=title, **theme.chart_size(150)
    )


def top_holdings(positions: list[dict[str, Any]], top: int = 12, title: str = "") -> alt.LayerChart:
    """Largest positions by value. Magnitude by identity: a sequential bar."""
    if not positions:
        return _empty("No positions reported.")

    frame = pd.DataFrame(positions[:top])
    frame["issuer_short"] = frame["issuer"].str.title().str.slice(0, 26)
    frame["billions"] = frame["value_usd"] / 1e9

    # Room at the right for the weight label riding each bar's tip.
    span = [0, float(frame["billions"].max()) * 1.12]

    bars = (
        alt.Chart(frame)
        .mark_bar(cornerRadiusEnd=theme.CORNER_RADIUS, height=alt.RelativeBandSize(0.6))
        .encode(
            y=alt.Y("issuer_short:N", title=None, sort="-x"),
            x=alt.X("billions:Q", title="Value ($B)", scale=alt.Scale(domain=span)),
            # Magnitude, so one hue light-to-dark rather than eight identities.
            color=alt.Color(
                "billions:Q",
                scale=alt.Scale(range=theme.SEQUENTIAL),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("issuer:N", title="Issuer"),
                alt.Tooltip("value_usd:Q", title="Value", format="$,.0f"),
                alt.Tooltip("shares:Q", title="Shares", format=",.0f"),
                alt.Tooltip("portfolio_weight_pct:Q", title="Weight", format=".2f"),
            ],
        )
    )

    labels = (
        alt.Chart(frame)
        .mark_text(align="left", dx=5, fontSize=10, color=theme.INK_SECONDARY, font=theme.FONT)
        .encode(
            y=alt.Y("issuer_short:N", sort="-x"),
            x=alt.X("billions:Q", scale=alt.Scale(domain=span)),
            text=alt.Text("portfolio_weight_pct:Q", format=".1f"),
        )
    )

    return alt.layer(bars, labels).properties(
        title=title, **theme.chart_size(max(140, 26 * min(len(frame), top)))
    )


def position_changes(changes: dict[str, Any], title: str = "") -> alt.LayerChart:
    """Share-count change per name, diverging around zero.

    Added and trimmed is polarity, so blue against red with a neutral zero.
    """
    rows = [
        {"issuer": r["issuer"].title()[:26], "pct": r["share_change_pct"]}
        for r in (changes.get("increased") or []) + (changes.get("reduced") or [])
        if r.get("share_change_pct") is not None
    ]
    if not rows:
        return _empty("No position changed size this quarter.")

    frame = pd.DataFrame(rows).sort_values("pct", ascending=False).head(16)

    bars = (
        alt.Chart(frame)
        .mark_bar(cornerRadiusEnd=theme.CORNER_RADIUS, height=alt.RelativeBandSize(0.6))
        .encode(
            y=alt.Y("issuer:N", title=None, sort="-x"),
            x=alt.X("pct:Q", title="Change in shares (%)"),
            color=alt.condition(
                alt.datum.pct >= 0, alt.value(theme.POSITIVE), alt.value(theme.NEGATIVE)
            ),
            tooltip=[
                alt.Tooltip("issuer:N", title="Issuer"),
                alt.Tooltip("pct:Q", title="Share change", format="+.1f"),
            ],
        )
    )
    zero = (
        alt.Chart(pd.DataFrame({"x": [0]}))
        .mark_rule(color=theme.AXIS, strokeWidth=1)
        .encode(x="x:Q")
    )

    return alt.layer(zero, bars).properties(
        title=title, **theme.chart_size(max(140, 26 * len(frame)))
    )


def macro_changes(readings: dict[str, Any], window: str = "3m", title: str = "") -> alt.LayerChart:
    """Move per macro series over one window, diverging around zero."""
    rows = []
    for key, item in readings.items():
        if not isinstance(item, dict):
            continue
        moved = (item.get("changes") or {}).get(window) or {}
        if moved.get("change") is None:
            continue
        rows.append(
            {
                "series": item.get("label", key)[:28],
                "change": moved["change"],
                "unit": item.get("unit", ""),
            }
        )
    if not rows:
        return _empty("No macro changes available.")

    frame = pd.DataFrame(rows).sort_values("change", ascending=False)

    bars = (
        alt.Chart(frame)
        .mark_bar(cornerRadiusEnd=theme.CORNER_RADIUS, height=alt.RelativeBandSize(0.6))
        .encode(
            y=alt.Y("series:N", title=None, sort="-x"),
            x=alt.X("change:Q", title=f"Change over {window}"),
            color=alt.condition(
                alt.datum.change >= 0, alt.value(theme.POSITIVE), alt.value(theme.NEGATIVE)
            ),
            tooltip=[
                alt.Tooltip("series:N", title="Series"),
                alt.Tooltip("change:Q", title=f"{window} change", format="+.2f"),
                alt.Tooltip("unit:N", title="Unit"),
            ],
        )
    )
    zero = (
        alt.Chart(pd.DataFrame({"x": [0]}))
        .mark_rule(color=theme.AXIS, strokeWidth=1)
        .encode(x="x:Q")
    )

    return alt.layer(zero, bars).properties(
        title=title, **theme.chart_size(max(140, 26 * len(frame)))
    )
