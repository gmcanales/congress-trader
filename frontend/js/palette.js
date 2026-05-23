/* Goosebase palette / dark-light toggle.
 *
 * Two canonical palettes drive the top-bar toggle:
 *   - warmMarsh  → dark mode
 *   - frankli    → light mode
 *
 * Three more are available via the /splash-admin dev tool but the
 * toggle only flips between the canonical pair.
 *
 * Priority for the active palette on page load:
 *   1. localStorage 'goosebase.palette' (per-device choice — toggle or dev tool)
 *   2. server-stored value from /api/landing/palette (site default)
 *   3. prefers-color-scheme media query
 *   4. warmMarsh fallback
 *
 * Step (1) is read synchronously and applied before first paint to
 * avoid FOUC. Steps (2)–(4) run after first paint.
 *
 * Include this script in <head> of every page that uses tokens.css.
 */
(function () {
  const KEY = 'goosebase.palette';
  const ALL = new Set([
    'warmMarsh',
    'sunset',
    'ember-blue',
    'forest-terminal',
    'retro-gold',
    'cyber-neon',
    'solarized-sand',
    'cleanmeter',
    'frankli',
    'nightSky'
  ]);
  const DARK = 'warmMarsh';
  const LIGHT = 'frankli';

  // ── Synchronous (pre-paint) application ────────────────────────

  function readCache() {
    try {
      const v = localStorage.getItem(KEY);
      return v && ALL.has(v) ? v : null;
    } catch (_) { return null; }
  }

  function systemDefault() {
    try {
      return window.matchMedia('(prefers-color-scheme: light)').matches ? LIGHT : DARK;
    } catch (_) { return DARK; }
  }

  function apply(name) {
    if (!ALL.has(name)) return;
    document.documentElement.setAttribute('data-palette', name);
    try { localStorage.setItem(KEY, name); } catch (_) { /* ignore */ }
  }

  // First paint: use cache if available, otherwise the system pref.
  // We deliberately don't write the system default to localStorage —
  // we only persist explicit user choices so a later OS theme change
  // takes effect on the next visit.
  const cached = readCache();
  document.documentElement.setAttribute(
    'data-palette',
    cached || systemDefault()
  );

  // ── Async (post-paint) reconciliation ──────────────────────────

  // If the user hasn't made a choice yet, the server-stored palette
  // (set via the dev tool) wins over the system default. Skip on the
  // landing page — its head script picks a palette from local time.
  if (!cached && location.pathname !== '/') {
    fetch('/api/landing/palette', { cache: 'no-cache' })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data && data.name && ALL.has(data.name) && data.name !== readCache()) {
          // Only override if the user *still* hasn't picked locally.
          if (!readCache()) document.documentElement.setAttribute('data-palette', data.name);
        }
      })
      .catch(() => { /* network is fine, system default stands */ });
  }

  // ── Cross-tab sync ────────────────────────────────────────────

  window.addEventListener('storage', (e) => {
    if (e.key === KEY && e.newValue && ALL.has(e.newValue)) {
      document.documentElement.setAttribute('data-palette', e.newValue);
    }
  });

  // ── Public API ────────────────────────────────────────────────

  window.Goosebase = window.Goosebase || {};

  // Used by the dev tool to apply (and persist) a specific palette.
  window.Goosebase.applyPalette = apply;

  // Used by the dev tool to *preview* without persisting.
  window.Goosebase.previewPalette = (name) => {
    if (ALL.has(name)) document.documentElement.setAttribute('data-palette', name);
  };

  // Top-bar toggle: flip between the canonical dark/light pair.
  window.Goosebase.toggleTheme = () => {
    const cur = document.documentElement.getAttribute('data-palette');
    const next = cur === LIGHT ? DARK : LIGHT;
    apply(next);
    return next;
  };

  window.Goosebase.currentPalette = () =>
    document.documentElement.getAttribute('data-palette');

  window.Goosebase.isDark = () => {
    const p = window.Goosebase.currentPalette();
    return p !== LIGHT;   // every non-frankli palette is treated as dark
  };

  // Click handler for any element with [data-goosebase-toggle]. Used by
  // the dark/light icon in each page's header.
  document.addEventListener('click', (e) => {
    const t = e.target.closest('[data-goosebase-toggle]');
    if (t) {
      e.preventDefault();
      window.Goosebase.toggleTheme();
    }
  });
})();
