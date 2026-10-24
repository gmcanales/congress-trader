/* What Congress Is Trading — buy/sell composition by analytical dimension.
 *
 * Form: diverging bars centred on zero, sells left and buys right.
 *
 * That is not a style choice. Buy-vs-sell is polarity, which is what a
 * diverging bar is for — but it also fixes an accessibility problem the
 * palette has. Validating the project's --buy/--sell pair across all ten
 * palettes gives a deutan dE of 2.2-6.5 in four of them (nightSky 2.2,
 * frankli 2.5, default 4.4, cleanmeter 6.5) against a floor of 6 and a
 * target of 8. Red-vs-green is the classic CVD trap. Here side-of-axis
 * carries buy/sell, every bar is directly labelled, and colour is only
 * reinforcement — so the chart reads correctly even where the hues don't
 * separate.
 */
(() => {
  const wrap     = document.getElementById('bd-chart-wrap');
  if (!wrap) return;
  const winSel   = document.getElementById('bd-window');
  const dimSel   = document.getElementById('bd-dim');
  const toggle   = document.getElementById('bd-toggle-all');
  const summary  = document.getElementById('bd-summary');
  const emptyEl  = document.getElementById('bd-empty');
  const noteEl   = document.getElementById('bd-note');

  let data = null;
  let showAll = false;
  let loaded = false;

  const esc = s => String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // ── one diverging-bar panel ───────────────────────────────────────────────
  function drawDimension(dim, width, compact) {
    const buckets = dim.buckets.filter(b => b.total > 0);
    if (!buckets.length) return '';

    const max = Math.max(1, ...buckets.map(b => Math.max(b.buys, b.sells)));
    const rowH   = compact ? 22 : 28;
    const labelW = compact ? 104 : 150;
    // Three reserved gutters on the right, or the longest bar's value label
    // collides with the net column: netW for the net figure, valueW for the
    // count that sits just past each bar's end.
    const netW   = compact ? 38 : 42;
    const valueW = compact ? 28 : 30;
    const plotW  = Math.max(80, width - labelW - netW - valueW);
    const mid    = labelW + plotW / 2;
    const scale  = v => (v / max) * (plotW / 2);
    const h      = buckets.length * rowH + (compact ? 20 : 26);

    const rows = buckets.map((b, i) => {
      const y = i * rowH + (compact ? 16 : 20);
      const barH = compact ? 9 : 12;
      const by = y - barH / 2;
      const sellW = scale(b.sells);
      const buyW  = scale(b.buys);
      // 4px rounded data-ends, square against the zero baseline; a 2px gap at
      // the axis keeps the two fills from touching.
      const sell = b.sells ? `<path class="bd-bar bd-sell" d="${roundedLeft(mid - 2 - sellW, by, sellW, barH)}"><title>${esc(b.bucket)} — ${b.sells} sells</title></path>` : '';
      const buy  = b.buys  ? `<path class="bd-bar bd-buy"  d="${roundedRight(mid + 2, by, buyW, barH)}"><title>${esc(b.bucket)} — ${b.buys} buys</title></path>` : '';
      const netCls = b.net > 0 ? 'pos' : b.net < 0 ? 'neg' : '';
      return `
        <text class="bd-cat" x="${labelW - 10}" y="${y + 4}" text-anchor="end">${esc(trim(b.bucket, compact ? 16 : 26))}<title>${esc(b.bucket)}</title></text>
        ${sell}${buy}
        ${b.sells ? `<text class="bd-val" x="${mid - 6 - sellW}" y="${y + 4}" text-anchor="end">${b.sells}</text>` : ''}
        ${b.buys  ? `<text class="bd-val" x="${mid + 6 + buyW}" y="${y + 4}">${b.buys}</text>` : ''}
        <text class="bd-net ${netCls}" x="${width - 2}" y="${y + 4}" text-anchor="end">${b.net > 0 ? '+' : ''}${b.net}</text>`;
    }).join('');

    return `
      <svg class="bd-svg" viewBox="0 0 ${width} ${h}" width="100%" height="${h}" role="img"
           aria-label="${esc(dim.label)}: buys and sells by bucket">
        <line class="bd-axis" x1="${mid}" y1="4" x2="${mid}" y2="${h - 10}" />
        ${rows}
      </svg>`;
  }

  const trim = (s, n) => (s.length > n ? s.slice(0, n - 1) + '…' : s);

  // Rounded on the outer end only, so both bars sit flush to the axis.
  function roundedRight(x, y, w, h) {
    const r = Math.min(4, w);
    return `M${x},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h - r} Q${x + w},${y + h} ${x + w - r},${y + h} H${x} Z`;
  }
  function roundedLeft(x, y, w, h) {
    const r = Math.min(4, w);
    return `M${x + w},${y} H${x + r} Q${x},${y} ${x},${y + r} V${y + h - r} Q${x},${y + h} ${x + r},${y + h} H${x + w} Z`;
  }

  function legend() {
    return `<div class="bd-legend">
      <span><i class="bd-key bd-sell-key"></i>sells (left)</span>
      <span><i class="bd-key bd-buy-key"></i>buys (right)</span>
      <span class="bd-legend-net">net = buys − sells</span>
    </div>`;
  }

  function render() {
    if (!data || !data.n_trades) {
      wrap.innerHTML = '';
      emptyEl.classList.remove('hidden');
      return;
    }
    emptyEl.classList.add('hidden');

    const dims = Object.values(data.dimensions);
    if (showAll) {
      const w = Math.max(250, Math.floor(wrap.clientWidth / 2) - 18);
      wrap.innerHTML = legend() + `<div class="bd-grid">` + dims.map(d => `
        <div class="bd-cell">
          <div class="bd-cell-title">${esc(d.label)}</div>
          ${drawDimension(d, w, true)}
        </div>`).join('') + `</div>`;
    } else {
      const d = data.dimensions[dimSel.value] || dims[0];
      wrap.innerHTML = legend() + drawDimension(d, Math.max(320, wrap.clientWidth - 8), false);
    }
  }

  async function load() {
    wrap.innerHTML = '<div class="bd-loading">Loading…</div>';
    try {
      data = await API.getBreakdown({ window_days: parseInt(winSel.value) || 180 });
      if (!dimSel.options.length) {
        dimSel.innerHTML = Object.values(data.dimensions)
          .map(d => `<option value="${d.key}">${esc(d.label)}</option>`).join('');
      }
      summary.textContent =
        `${data.n_trades.toLocaleString()} disclosed trades filed ${data.start} → ${data.as_of}`
        + ` · ${data.n_buys.toLocaleString()} buys, ${data.n_sells.toLocaleString()} sells.`
        + ` Counts are trades, not dollars — disclosure amounts are coarse ranges.`;
      noteEl.textContent = data.market_cap_is_current
        ? 'Market cap and turnover use a current snapshot, so a company that has since grown is bucketed by what it is today. Attention is point-in-time, resolved against the month before each filing.'
        : '';
      render();
    } catch (e) {
      wrap.innerHTML = `<div class="bd-loading">Could not load breakdown: ${esc(e.message)}</div>`;
    }
  }

  winSel.addEventListener('change', load);
  dimSel.addEventListener('change', render);
  toggle.addEventListener('click', () => {
    showAll = !showAll;
    toggle.classList.toggle('is-active', showAll);
    toggle.textContent = showAll ? 'Show one' : 'Compare all';
    dimSel.disabled = showAll;
    render();
  });
  window.addEventListener('resize', () => { if (data) render(); });

  // Load lazily — only when the Recommendations view is actually opened.
  const navRecs = document.getElementById('nav-recs');
  if (navRecs) {
    navRecs.addEventListener('click', () => {
      if (!loaded) { loaded = true; load(); }
    });
  }
})();
