"""The desk's visual system: one palette, one chart theme, one stylesheet.

Colours are not chosen here by eye. The categorical slots and their ordering
come from a validated palette, and the ordering is the colour-blind-safety
mechanism rather than a preference -- the first three slots clear the
all-pairs gate on this surface, which is why no chart in this app seats a
fourth series without folding the tail into "other".

Two consequences worth knowing before editing anything below:

**Status green and status red are indistinguishable under deutan CVD**
(ΔE 4.1 against each other). They are still the right colours for
target-hit versus stopped-out, because the alternative -- inventing
neutral hues for outcomes everyone already reads as good and bad -- is
worse. The mitigation is that every status mark in this app carries an
icon and a label, so colour never carries the meaning alone. Do not
remove those labels.

**Text never wears a series colour.** Marks carry hue; labels, values and
axis text wear ink tokens. A light categorical step is illegible as text
on this surface, and identity comes from the coloured mark beside the
text rather than from the text itself.
"""

from __future__ import annotations

from typing import Any

import altair as alt

# --- Surfaces ---------------------------------------------------------------
# The chart surface is the card the chart sits on, not the page behind it;
# contrast was validated against this value, so a chart moved onto a
# different background needs revalidating.
PAGE = "#0d0d0d"
SURFACE = "#1a1a19"

INK = "#ffffff"
INK_SECONDARY = "#c3c2b7"
INK_MUTED = "#898781"
GRID = "#2c2c2a"
AXIS = "#383835"
BORDER = "rgba(255,255,255,0.10)"

# --- Categorical, in fixed order --------------------------------------------
# Never cycled, never extended by generating a hue. A ninth series folds into
# "other" or becomes small multiples.
SERIES = [
    "#3987e5",  # 1 blue
    "#d95926",  # 2 orange
    "#199e70",  # 3 aqua
    "#c98500",  # 4 yellow
    "#d55181",  # 5 magenta
    "#008300",  # 6 green
    "#9085e9",  # 7 violet
    "#e66767",  # 8 red
]

# Charts where every pair is visually adjacent (scatter, small multiples)
# cap here: past three, the gate fails on this surface.
SERIES_ALL_PAIRS_CAP = 3

ACCENT = SERIES[0]

# --- Status, reserved -------------------------------------------------------
# Never reused as "series 4". Always shipped with an icon and a label.
GOOD = "#0ca30c"
WARNING = "#fab219"
SERIOUS = "#ec835a"
CRITICAL = "#d03b3b"

# --- Diverging --------------------------------------------------------------
# Blue and red read as opposite; the midpoint is neutral so zero reads as
# "nothing" rather than as a third category.
POSITIVE = "#3987e5"
NEGATIVE = "#d03b3b"
NEUTRAL_MID = "#383835"

# --- Sequential -------------------------------------------------------------
# One hue, light to dark. Used for magnitude, never for identity.
SEQUENTIAL = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95"]

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'

# Mark specs, fixed across every chart in the app.
BAR_MAX_THICKNESS = 24
LINE_WIDTH = 2
MARKER_SIZE = 80  # Vega area units; ≈ 9px diameter
CORNER_RADIUS = 4


@alt.theme.register("desk", enable=True)
def _desk_theme() -> alt.theme.ThemeConfig:
    """Recessive chrome, thin marks, data the only loud thing."""
    return alt.theme.ThemeConfig(
        {
            "config": {
                "background": SURFACE,
                "font": FONT,
                "view": {"stroke": None, "continuousWidth": 640, "continuousHeight": 280},
                "arc": {"stroke": SURFACE, "strokeWidth": 2},
                "bar": {"cornerRadiusEnd": CORNER_RADIUS},
                "line": {"strokeWidth": LINE_WIDTH, "strokeCap": "round", "strokeJoin": "round"},
                "point": {"size": MARKER_SIZE, "filled": True},
                "axis": {
                    "labelFont": FONT,
                    "titleFont": FONT,
                    "labelColor": INK_MUTED,
                    "titleColor": INK_MUTED,
                    "labelFontSize": 11,
                    "titleFontSize": 11,
                    "titleFontWeight": "normal",
                    # Hairline, solid, one step off surface. Never dashed.
                    "gridColor": GRID,
                    "gridWidth": 1,
                    "domainColor": AXIS,
                    "tickColor": AXIS,
                    "tickSize": 4,
                    "labelPadding": 6,
                },
                "axisY": {"domain": False, "ticks": False, "titlePadding": 8},
                "axisX": {"grid": False},
                "legend": {
                    "labelFont": FONT,
                    "titleFont": FONT,
                    "labelColor": INK_SECONDARY,
                    "titleColor": INK_MUTED,
                    "labelFontSize": 11,
                    "titleFontSize": 11,
                    "titleFontWeight": "normal",
                    "symbolType": "circle",
                    "symbolSize": 80,
                    "orient": "top",
                    "direction": "horizontal",
                    "offset": 4,
                },
                "title": {
                    "font": FONT,
                    "color": INK,
                    "fontSize": 13,
                    "fontWeight": 600,
                    "anchor": "start",
                    "offset": 10,
                    "subtitleColor": INK_MUTED,
                    "subtitleFontSize": 11,
                },
                "text": {"font": FONT, "fontSize": 11},
                "range": {"category": SERIES, "ramp": SEQUENTIAL},
            }
        }
    )


CSS = f"""
<style>
  /* The app's own chrome, matched to the chart surface so a chart card does
     not float on a different plane from the page. */
  .stApp {{ background: {PAGE}; }}
  section[data-testid="stSidebar"] {{
      background: {SURFACE};
      border-right: 1px solid {BORDER};
  }}

  h1, h2, h3 {{ letter-spacing: -0.01em; }}
  h1 {{ font-size: 2.1rem !important; font-weight: 650 !important; }}
  h2 {{
      font-size: 1.15rem !important;
      font-weight: 600 !important;
      color: {INK_SECONDARY} !important;
      margin-top: 2.2rem !important;
      padding-bottom: .4rem;
      border-bottom: 1px solid {BORDER};
  }}
  h3 {{ font-size: .95rem !important; font-weight: 600 !important; }}

  /* Stat tiles: a card, a muted label, a value that carries the weight.
     Proportional figures deliberately -- tabular-nums makes a large number
     look loose, and these are not in a column that must align. */
  div[data-testid="stMetric"] {{
      background: {SURFACE};
      border: 1px solid {BORDER};
      border-radius: 10px;
      padding: .85rem 1rem;
  }}
  div[data-testid="stMetricLabel"] p {{
      font-size: .74rem !important;
      color: {INK_MUTED} !important;
      text-transform: uppercase;
      letter-spacing: .05em;
      font-weight: 500;
  }}
  div[data-testid="stMetricValue"] {{
      font-size: 1.6rem !important;
      font-weight: 600 !important;
      color: {INK} !important;
  }}

  /* Tables: tabular figures here, where columns must align vertically. */
  div[data-testid="stDataFrame"] {{
      border: 1px solid {BORDER};
      border-radius: 10px;
      font-variant-numeric: tabular-nums;
  }}

  div[data-testid="stVegaLiteChart"] {{
      background: {SURFACE};
      border: 1px solid {BORDER};
      border-radius: 10px;
      padding: .75rem .5rem .25rem .75rem;
  }}

  /* Limitations block: present, readable, never decorative. */
  div[data-testid="stVerticalBlockBorderWrapper"] {{ border-radius: 10px; }}

  div[data-testid="stTabs"] button[role="tab"] {{
      font-size: .88rem;
      font-weight: 500;
  }}

  .stAlert {{ border-radius: 10px; }}

  /* The one hero figure a view is allowed. */
  .desk-hero {{
      font-family: {FONT};
      font-size: 3rem;
      font-weight: 650;
      line-height: 1.05;
      color: {INK};
      margin: .2rem 0 0 0;
  }}
  .desk-hero-label {{
      font-size: .74rem;
      text-transform: uppercase;
      letter-spacing: .05em;
      color: {INK_MUTED};
      font-weight: 500;
  }}
  .desk-hero-sub {{ font-size: .85rem; color: {INK_SECONDARY}; }}

  /* Status chips. The icon and the word carry the meaning; the colour only
     reinforces it, because status green and status red are the same colour
     to a deutan reader. */
  .desk-chip {{
      display: inline-flex;
      align-items: center;
      gap: .4rem;
      padding: .3rem .7rem;
      border-radius: 999px;
      font-size: .8rem;
      font-weight: 600;
      border: 1px solid;
  }}
</style>
"""


def status_color(kind: str) -> str:
    return {
        "good": GOOD,
        "warning": WARNING,
        "serious": SERIOUS,
        "critical": CRITICAL,
    }.get(kind, INK_MUTED)


def chart_size(height: int = 260) -> dict[str, Any]:
    """Container-width charts with a fixed, compact height."""
    return {"width": "container", "height": height}
