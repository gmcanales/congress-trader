"""Goosebase Plotly theme.

Generated reports (`generate_report.py`, `generate_report_v2.py`) emit
Plotly figures and embed Plotly via CDN. Plotly hover labels, gridlines,
and axes are styled in the layout dict — none of that is reachable from
the frontend tokens.css palette. This module is the canonical place
to keep Plotly + report-page colors aligned with the warmMarsh palette
in `frontend/css/tokens.css`.

Reports are static artifacts: they bake in whatever palette is current
at generation time. We pin to warmMarsh so every report looks like a
Goosebase document regardless of the visitor's chosen palette.
"""

# ── Palette (mirrors frontend/css/tokens.css :root warmMarsh) ──────

BG_PAGE       = "#0f0d0a"
BG_PLOT       = "#18130d"
SURFACE_HOVER = "#221a12"
BORDER        = "#2e251a"
BORDER_STRONG = "#3d3122"
TEXT          = "#ede4d3"
TEXT_MUTED    = "#8a7d68"
TEXT_FAINT    = "#5a4f3e"
ACCENT        = "#5fb4a8"
ACCENT_SOFT   = "rgba(95,180,168,0.18)"
BUY           = "#6fbf72"
SELL          = "#e87060"
WARN          = "#d6a936"

FONT_FAMILY = ("Satoshi, -apple-system, BlinkMacSystemFont, "
               "'Segoe UI', Roboto, sans-serif")

# Strategy line colors — used by report figures in lieu of the
# old Tableau-ish defaults. Order roughly matches the trader's
# usage: primary strategy in teal, semantic colors next, then
# muted neutrals for fill-out series.
COLORWAY = [
    ACCENT,        # #5fb4a8 — teal
    BUY,           # #6fbf72 — green
    WARN,          # #d6a936 — amber
    SELL,          # #e87060 — coral
    "#a78bfa",     # soft purple
    "#7fb2c9",     # dusty blue
    TEXT_MUTED,    # warm gray
]

# Benchmark lines (SPY / RSP / etc) get a muted neutral so they
# read as backdrops rather than competing with strategy traces.
BENCHMARK_PRIMARY   = TEXT_MUTED
BENCHMARK_SECONDARY = TEXT_FAINT


# ── Layout helper ──────────────────────────────────────────────────

def _theme_layout() -> dict:
    """Goosebase-styled Plotly layout defaults. Built fresh each call
    so callers can't mutate the shared template."""
    axis = {
        "gridcolor":     BORDER,
        "linecolor":     BORDER_STRONG,
        "zerolinecolor": BORDER_STRONG,
        "tickfont":      {"color": TEXT_MUTED, "family": FONT_FAMILY},
        "titlefont":     {"color": TEXT_MUTED, "family": FONT_FAMILY},
    }
    return {
        "paper_bgcolor": BG_PAGE,
        "plot_bgcolor":  BG_PLOT,
        "font":          {"family": FONT_FAMILY, "color": TEXT, "size": 13},
        "colorway":      list(COLORWAY),
        "hoverlabel": {
            "bgcolor":     SURFACE_HOVER,
            "bordercolor": BORDER_STRONG,
            "font":        {"family": FONT_FAMILY, "color": TEXT, "size": 12},
        },
        "xaxis": dict(axis),
        "yaxis": dict(axis),
        "legend": {
            "bgcolor":     "rgba(0,0,0,0)",
            "bordercolor": BORDER,
            "font":        {"color": TEXT, "family": FONT_FAMILY},
        },
        "title": {"font": {"color": TEXT, "family": FONT_FAMILY, "size": 17}},
    }


def apply_theme(layout: dict) -> dict:
    """Merge a caller's layout dict over Goosebase defaults.

    Top-level keys: caller wins.
    Axis sub-dicts (xaxis/yaxis): deep-merged so callers can set
    `title` / `tickformat` without losing the theme's gridcolor/font.
    """
    base = _theme_layout()
    out = {**base, **layout}
    for axis_key in ("xaxis", "yaxis"):
        if axis_key in layout:
            out[axis_key] = {**base[axis_key], **layout[axis_key]}
    return out


# ── Report-page CSS ────────────────────────────────────────────────

# Embedded <style> block for generated report HTML. Matches the
# Plotly figure surface so charts and prose share a visual frame.
REPORT_CSS = f"""
* {{ box-sizing: border-box; }}
body {{
  font-family: {FONT_FAMILY};
  background: {BG_PAGE};
  color: {TEXT};
  max-width: 1100px;
  margin: 2rem auto;
  padding: 0 1.5rem;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}}
header {{ border-bottom: 1px solid {BORDER}; padding-bottom: 1rem; margin-bottom: 2rem; }}
header h1 {{ margin: 0 0 0.25rem; font-size: 1.8rem; font-weight: 800; letter-spacing: -0.01em; }}
header .meta {{ color: {TEXT_MUTED}; font-size: 0.9rem; }}
h2 {{ margin-top: 2.4rem; padding-top: 0.8rem; border-top: 1px solid {BORDER};
     font-size: 1.35rem; font-weight: 700; color: {TEXT}; letter-spacing: -0.01em; }}
h3 {{ margin-top: 1.4rem; font-size: 1.05rem; font-weight: 700; color: {TEXT}; }}
em {{ color: {TEXT}; }}
.callout {{ background: {BG_PLOT}; border-left: 4px solid {ACCENT};
           padding: 0.8rem 1rem; border-radius: 4px; margin: 1rem 0; color: {TEXT}; }}
.callout.warn {{ border-left-color: {WARN}; }}
.callout.crit {{ border-left-color: {SELL}; }}
.callout.good {{ border-left-color: {BUY}; }}
table {{ width: 100%; border-collapse: collapse; margin: 0.5rem 0; font-size: 0.92rem; }}
th, td {{ text-align: left; padding: 0.45rem 0.6rem; border-bottom: 1px solid {BORDER}; }}
th {{ background: {BG_PLOT}; font-weight: 600; color: {TEXT}; }}
td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.pos {{ color: {BUY}; }}
.neg {{ color: {SELL}; }}
.kvtable td.key {{ font-weight: 500; color: {TEXT_MUTED}; width: 30%; }}
code, kbd {{ font-family: ui-monospace, 'JetBrains Mono', 'SF Mono', Menlo, monospace;
            background: {SURFACE_HOVER}; padding: 1px 4px; border-radius: 3px;
            font-size: 0.88em; color: {TEXT}; }}
.chart {{ margin: 1rem 0 1.5rem; background: {BG_PLOT}; border: 1px solid {BORDER};
         border-radius: 8px; padding: 8px; }}
ul.tight li, ol.tight li {{ margin: 0.2rem 0; }}
a {{ color: {ACCENT}; text-decoration: none; }}
a:hover {{ color: #7ed1c5; }}
footer {{ margin-top: 3rem; padding-top: 1rem; border-top: 1px solid {BORDER};
         color: {TEXT_MUTED}; font-size: 0.85rem; }}
"""


# Goosebase Satoshi from Fontshare CDN; reports use it inline for
# consistency with the live site.
FONT_LINK = (
    '<link rel="preconnect" href="https://api.fontshare.com" crossorigin>'
    '<link href="https://api.fontshare.com/v2/css?f[]=satoshi@900,800,700,500,400'
    '&display=swap" rel="stylesheet">'
)
