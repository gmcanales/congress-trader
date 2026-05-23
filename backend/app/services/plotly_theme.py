"""Goosebase Plotly theme.

Generated reports (`generate_report.py`, `generate_report_v2.py`) emit
Plotly figures and embed Plotly via CDN. Reports are static artifacts
but they need to follow the visitor's currently-chosen palette — so the
report ships both the warmMarsh (dark) and frankli (light) palettes,
reads the active palette from localStorage at load time, and uses
Plotly.relayout to swap chart colors when the user toggles theme.

The Python side of this module is consumed by the report generators:

  - `COLORWAY`, `BENCHMARK_PRIMARY/SECONDARY`, semantic color constants
    are used at generation time to color the *traces* (which are static
    once baked in — see note below).
  - `apply_theme(layout)` merges the static (warmMarsh) layout defaults
    into a Plotly layout dict.
  - `REPORT_CSS` is the full embedded stylesheet for the report page,
    written with CSS custom properties so theme switching works.
  - `FONT_LINK` + `THEME_SCRIPT` go into the report's <head>.
  - `THEME_TOGGLE_HTML` is the sun/moon button injected into the
    report header.

Trace colors: changing Plotly's `colorway` after `newPlot` doesn't
recolor already-drawn traces, so trace colors stay baked at the
warmMarsh palette. The semantic colors we use (teal, green, coral,
amber) read acceptably on both the cream and the warm-charcoal page.
Only the chart *frame* (bg, gridlines, fonts, hoverlabel) swaps.
"""

# ── Palette: warmMarsh (default dark) ──────────────────────────────

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

COLORWAY = [
    ACCENT,        # teal
    BUY,           # green
    WARN,          # amber
    SELL,          # coral
    "#a78bfa",     # soft purple
    "#7fb2c9",     # dusty blue
    TEXT_MUTED,    # warm gray
]

BENCHMARK_PRIMARY   = TEXT_MUTED
BENCHMARK_SECONDARY = TEXT_FAINT


def _theme_layout() -> dict:
    """Plotly layout defaults for trace-generation time (warmMarsh)."""
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
    """Merge a caller's layout dict over Goosebase warmMarsh defaults.

    Axis sub-dicts deep-merge so callers can set per-figure
    title/tickformat without losing the gridcolor/font defaults.
    """
    base = _theme_layout()
    out = {**base, **layout}
    for axis_key in ("xaxis", "yaxis"):
        if axis_key in layout:
            out[axis_key] = {**base[axis_key], **layout[axis_key]}
    return out


# ── Report-page CSS (uses CSS variables, both palettes shipped) ────

REPORT_CSS = f"""
:root {{
  --r-bg:        #0f0d0a;
  --r-plot:      #18130d;
  --r-surface:   #221a12;
  --r-border:    #2e251a;
  --r-text:      #ede4d3;
  --r-muted:     #8a7d68;
  --r-accent:    #5fb4a8;
  --r-buy:       #6fbf72;
  --r-sell:      #e87060;
  --r-warn:      #d6a936;
}}
:root[data-palette="frankli"] {{
  --r-bg:        #fdf6ec;
  --r-plot:      #ffffff;
  --r-surface:   #f5ecdb;
  --r-border:    #e6dac3;
  --r-text:      #1a1f2e;
  --r-muted:     #5d6373;
  --r-accent:    #f56c3a;
  --r-buy:       #2f9e44;
  --r-sell:      #d63b3b;
  --r-warn:      #d68a1f;
}}

* {{ box-sizing: border-box; }}
body {{
  font-family: {FONT_FAMILY};
  background: var(--r-bg);
  color: var(--r-text);
  max-width: 1100px;
  margin: 2rem auto;
  padding: 0 1.5rem;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
  transition: background 0.25s, color 0.25s;
}}
.report-header-inner {{
  display: flex;
  align-items: flex-start;
  gap: 16px;
  border-bottom: 1px solid var(--r-border);
  padding-bottom: 1rem;
  margin-bottom: 2rem;
}}
.report-header-inner .titles {{ flex: 1; }}
.report-header-inner h1 {{ margin: 0 0 0.25rem; font-size: 1.8rem; font-weight: 800; letter-spacing: -0.01em; color: var(--r-text); }}
.report-header-inner .meta {{ color: var(--r-muted); font-size: 0.9rem; }}
h2 {{ margin-top: 2.4rem; padding-top: 0.8rem; border-top: 1px solid var(--r-border);
     font-size: 1.35rem; font-weight: 700; color: var(--r-text); letter-spacing: -0.01em; }}
h3 {{ margin-top: 1.4rem; font-size: 1.05rem; font-weight: 700; color: var(--r-text); }}
em {{ color: var(--r-text); }}
.callout {{ background: var(--r-plot); border-left: 4px solid var(--r-accent);
           padding: 0.8rem 1rem; border-radius: 4px; margin: 1rem 0; color: var(--r-text); }}
.callout.warn {{ border-left-color: var(--r-warn); }}
.callout.crit {{ border-left-color: var(--r-sell); }}
.callout.good {{ border-left-color: var(--r-buy); }}
table {{ width: 100%; border-collapse: collapse; margin: 0.5rem 0; font-size: 0.92rem; }}
th, td {{ text-align: left; padding: 0.45rem 0.6rem; border-bottom: 1px solid var(--r-border); }}
th {{ background: var(--r-plot); font-weight: 600; color: var(--r-text); }}
td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.pos {{ color: var(--r-buy); }}
.neg {{ color: var(--r-sell); }}
.kvtable td.key {{ font-weight: 500; color: var(--r-muted); width: 30%; }}
code, kbd {{ font-family: ui-monospace, 'JetBrains Mono', 'SF Mono', Menlo, monospace;
            background: var(--r-surface); padding: 1px 4px; border-radius: 3px;
            font-size: 0.88em; color: var(--r-text); }}
.chart {{ margin: 1rem 0 1.5rem; background: var(--r-plot); border: 1px solid var(--r-border);
         border-radius: 8px; padding: 8px; }}
ul.tight li, ol.tight li {{ margin: 0.2rem 0; }}
a {{ color: var(--r-accent); text-decoration: none; }}
a:hover {{ filter: brightness(1.15); }}
footer {{ margin-top: 3rem; padding-top: 1rem; border-top: 1px solid var(--r-border);
         color: var(--r-muted); font-size: 0.85rem; }}

.theme-toggle {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px; height: 36px;
  padding: 0;
  background: transparent;
  border: 1px solid var(--r-border);
  border-radius: 8px;
  color: var(--r-muted);
  cursor: pointer;
  transition: color .15s, border-color .15s, background .15s;
  flex-shrink: 0;
}}
.theme-toggle:hover {{ color: var(--r-text); border-color: var(--r-muted); }}
.theme-toggle .icon-sun  {{ display: block; }}
.theme-toggle .icon-moon {{ display: none; }}
:root[data-palette="frankli"] .theme-toggle .icon-sun  {{ display: none; }}
:root[data-palette="frankli"] .theme-toggle .icon-moon {{ display: block; }}
"""


# ── Font + theme bootstrap script + toggle button ──────────────────

FONT_LINK = (
    '<link rel="preconnect" href="https://api.fontshare.com" crossorigin>'
    '<link href="https://api.fontshare.com/v2/css?f[]=satoshi@900,800,700,500,400'
    '&display=swap" rel="stylesheet">'
)

# Inline script for the report page. Runs synchronously in <head> so
# the data-palette attribute is set before first paint, then re-themes
# every Plotly chart once they've finished rendering.
THEME_SCRIPT = """
<script>
(function () {
  var KEY = 'goosebase.palette';
  var ALL = ['warmMarsh','cleanmeter','frankli','nightSky','sunset'];
  var DARK = 'warmMarsh', LIGHT = 'frankli';

  function readCache() {
    try {
      var v = localStorage.getItem(KEY);
      return ALL.indexOf(v) >= 0 ? v : null;
    } catch (_) { return null; }
  }
  function systemDefault() {
    try { return matchMedia('(prefers-color-scheme: light)').matches ? LIGHT : DARK; }
    catch (_) { return DARK; }
  }
  function setAttr(name) { document.documentElement.setAttribute('data-palette', name); }

  // Pre-paint: apply cached or system default.
  setAttr(readCache() || systemDefault());

  // Plotly relayout per palette. Trace colors stay baked at warmMarsh
  // (semantic teal/green/coral read fine on either page) — only the
  // chart frame switches.
  var THEME = {
    warmMarsh: { paper:'#0f0d0a', plot:'#18130d', text:'#ede4d3', muted:'#8a7d68',
                 border:'#2e251a', borderStrong:'#3d3122', hoverBg:'#221a12' },
    frankli:   { paper:'#fdf6ec', plot:'#ffffff', text:'#1a1f2e', muted:'#5d6373',
                 border:'#e6dac3', borderStrong:'#ccb893', hoverBg:'#ffffff' },
    cleanmeter:{ paper:'#07090e', plot:'#0e131c', text:'#e6edf7', muted:'#758197',
                 border:'#1f2a3c', borderStrong:'#2d3a52', hoverBg:'#161e2b' },
    nightSky:  { paper:'#0d1117', plot:'#161b22', text:'#e6edf3', muted:'#8b949e',
                 border:'#30363d', borderStrong:'#444c56', hoverBg:'#1f2937' },
    sunset:    { paper:'#160a05', plot:'#211008', text:'#fff2dd', muted:'#b89878',
                 border:'#3a2418', borderStrong:'#523222', hoverBg:'#2e1810' }
  };

  function relayoutPlots() {
    if (typeof Plotly === 'undefined') return;
    var t = THEME[document.documentElement.getAttribute('data-palette')] || THEME.warmMarsh;
    var update = {
      'paper_bgcolor': t.paper,
      'plot_bgcolor':  t.plot,
      'font.color': t.text,
      'hoverlabel.bgcolor': t.hoverBg,
      'hoverlabel.bordercolor': t.borderStrong,
      'hoverlabel.font.color': t.text,
      'xaxis.gridcolor': t.border,
      'xaxis.linecolor': t.borderStrong,
      'xaxis.zerolinecolor': t.borderStrong,
      'xaxis.tickfont.color': t.muted,
      'xaxis.titlefont.color': t.muted,
      'yaxis.gridcolor': t.border,
      'yaxis.linecolor': t.borderStrong,
      'yaxis.zerolinecolor': t.borderStrong,
      'yaxis.tickfont.color': t.muted,
      'yaxis.titlefont.color': t.muted,
      'legend.font.color': t.text,
      'title.font.color': t.text
    };
    document.querySelectorAll('.js-plotly-plot').forEach(function (el) {
      try { Plotly.relayout(el, update); } catch (_) { /* skip */ }
    });
  }

  // Plotly graphs are rendered via inline scripts after <body>, so
  // relayout once everything (load event) has settled.
  window.addEventListener('load', function () {
    setTimeout(relayoutPlots, 50);
  });

  // Cross-tab sync: another tab toggled the theme.
  window.addEventListener('storage', function (e) {
    if (e.key === KEY && e.newValue && ALL.indexOf(e.newValue) >= 0) {
      setAttr(e.newValue);
      relayoutPlots();
    }
  });

  // Toggle button click handler.
  window.toggleReportTheme = function () {
    var cur = document.documentElement.getAttribute('data-palette');
    var next = (cur === LIGHT) ? DARK : LIGHT;
    setAttr(next);
    try { localStorage.setItem(KEY, next); } catch (_) {}
    relayoutPlots();
  };
})();
</script>
"""

THEME_TOGGLE_HTML = '''
<button class="theme-toggle" type="button" onclick="toggleReportTheme()" aria-label="Toggle dark/light theme" title="Toggle theme">
  <svg class="icon-sun" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m4.93 19.07 1.41-1.41"/><path d="m17.66 6.34 1.41-1.41"/></svg>
  <svg class="icon-moon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
</button>
'''
