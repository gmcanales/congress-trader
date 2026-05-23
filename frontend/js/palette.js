/* Apply the active Goosebase palette as early as possible.
 *
 * Strategy: read a cached palette name from localStorage and set the
 * data-palette attribute synchronously so the first paint uses it.
 * Then fetch the canonical value from the API and update both the
 * attribute and the cache.
 *
 * Include this script in <head> *before* any stylesheet that doesn't
 * need a cached value (it sets the attribute synchronously; the CSS
 * file references the attribute via :root[data-palette=...]).
 */
(function () {
  const KEY = 'goosebase.palette';
  const ALLOWED = new Set(['warmMarsh', 'cleanmeter', 'frankli', 'nightSky', 'sunset']);

  const cached = (() => {
    try { return localStorage.getItem(KEY); } catch (_) { return null; }
  })();
  if (cached && ALLOWED.has(cached)) {
    document.documentElement.setAttribute('data-palette', cached);
  }

  function apply(name) {
    if (!ALLOWED.has(name)) return;
    document.documentElement.setAttribute('data-palette', name);
    try { localStorage.setItem(KEY, name); } catch (_) { /* ignore */ }
  }

  fetch('/api/landing/palette', { cache: 'no-cache' })
    .then(r => r.ok ? r.json() : null)
    .then(data => { if (data && data.name) apply(data.name); })
    .catch(() => { /* keep cached value */ });

  // Expose for the dev tools page to apply previews without a round-trip.
  window.Goosebase = window.Goosebase || {};
  window.Goosebase.applyPalette = apply;
})();
