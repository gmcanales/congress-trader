(() => {
  const recsView      = document.getElementById('recs-view');
  const tradesView    = document.getElementById('trades-view');
  const analysisView  = document.getElementById('analysis-view');
  const navTrades     = document.getElementById('nav-trades');
  const navAnalysis   = document.getElementById('nav-analysis');
  const navRecs       = document.getElementById('nav-recs');

  const profileSel    = document.getElementById('rec-profile');
  const asOfInput     = document.getElementById('rec-asof');
  const windowInput   = document.getElementById('rec-window');
  const goBtn         = document.getElementById('rec-go');
  const statusEl      = document.getElementById('rec-status');
  const configBox     = document.getElementById('rec-config');
  const cacheTag      = document.getElementById('rec-cache-tag');
  const refreshCacheBtn = document.getElementById('rec-refresh-cache');

  // Data-freshness banner
  const freshEl       = document.getElementById('rec-freshness');
  const freshHeadline = freshEl.querySelector('.freshness-headline');
  const freshDetail   = freshEl.querySelector('.freshness-detail');
  const freshMeta     = freshEl.querySelector('.freshness-meta');

  const openTbody     = document.getElementById('rec-open-tbody');
  const holdTbody     = document.getElementById('rec-hold-tbody');
  const closeTbody    = document.getElementById('rec-close-tbody');
  const openCount     = document.getElementById('rec-open-count');
  const holdCount     = document.getElementById('rec-hold-count');
  const closeCount    = document.getElementById('rec-close-count');

  // Strategy chart
  const stratBandK    = document.getElementById('strat-band-k');
  const stratBandWin  = document.getElementById('strat-band-window');
  const stratReload   = document.getElementById('strat-reload');
  const stratLegend   = document.getElementById('strat-legend');
  const stratChartEl  = document.getElementById('strat-chart');
  const stratEmpty    = document.getElementById('strat-empty');
  const stratChartWrap= document.getElementById('strat-chart-wrap');

  // Portfolio
  const footerReports = document.getElementById('footer-reports');
  const footerDocs    = document.getElementById('footer-docs');


  const PROFILE_COLORS = {
    conservative:   '#58a6ff', // blue
    moderate:       '#d6a936', // amber
    aggressive:     '#bc8cff', // purple
    spouse_favored: '#3ed598', // teal-green (v2.5)
  };

  // ── Collapse button universal handler ──────────────────────────────────────
  document.addEventListener('click', function onCollapseClick(e) {
    const btn = e.target.closest('.collapse-btn[data-target]');
    if (!btn) return;
    const target = document.getElementById(btn.dataset.target);
    if (!target) return;
    const nowHidden = target.classList.toggle('hidden');
    btn.classList.toggle('is-collapsed', nowHidden);
  });

  let initialized = false;
  let lastRecData = null;     // last /recommendations payload

  function showRecsView() {
    recsView.classList.remove('hidden');
    tradesView.classList.add('hidden');
    analysisView.classList.add('hidden');
    navTrades.classList.remove('active');
    navAnalysis.classList.remove('active');
    navRecs.classList.add('active');
    if (!initialized) {
      initialized = true;
      init();
    }
  }
  function hideRecsView() {
    recsView.classList.add('hidden');
    navRecs.classList.remove('active');
  }
  navRecs.addEventListener('click', showRecsView);
  navTrades.addEventListener('click', hideRecsView);
  navAnalysis.addEventListener('click', hideRecsView);

  // ── helpers ────────────────────────────────────────────────────────────────
  const cls = x => (x > 0 ? 'pos' : x < 0 ? 'neg' : '');
  const fmtPct = x =>
    (x === null || x === undefined) ? '—' :
    `<span class="${cls(x)}">${x >= 0 ? '+' : ''}${(x * 100).toFixed(2)}%</span>`;
  const fmtNum = (x, d = 2) =>
    (x === null || x === undefined) ? '—' : Number(x).toFixed(d);
  const fmtMoney = x => x === null || x === undefined ? '—' : `$${Number(x).toFixed(2)}`;
  const today = () => new Date().toISOString().slice(0, 10);
  const daysAgo = (d, asOf) => {
    const a = new Date(asOf);
    const b = new Date(d);
    return Math.round((a - b) / (1000 * 60 * 60 * 24));
  };

  const esc = s => String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const MONTH = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  // Parse as UTC parts rather than `new Date(iso)` so a YYYY-MM-DD never
  // shifts a day in negative-offset timezones.
  function fmtDate(iso, withYear = true) {
    if (!iso) return '—';
    const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
    if (!y || !m || !d) return iso;
    return `${MONTH[m - 1]} ${d}${withYear ? ', ' + y : ''}`;
  }

  function setStatus(msg, kind = '') {
    statusEl.textContent = msg;
    statusEl.className = `scrape-status ${kind}`.trim();
  }
  // ── init ───────────────────────────────────────────────────────────────────
  function init() {
    asOfInput.value = today();
    goBtn.addEventListener('click', runRecs);
    stratReload.addEventListener('click', loadStrategyChart);
    refreshCacheBtn.addEventListener('click', onRefreshCache);
    window.addEventListener('resize', () => {
      if (lastStratData) drawStrategyChart(lastStratData);
    });
    loadFreshness();
    loadStrategyChart();
    loadFooterLinks();
    runRecs(); // pre-load today's recs (served from the daily snapshot cache)
  }

  // ── reports + docs footer ─────────────────────────────────────────────────
  async function loadFooterLinks() {
    try {
      const [reports, docs] = await Promise.all([API.listReports(), API.listDocs()]);
      const renderItems = (container, items, urlPrefix) => {
        if (!items.length) {
          container.innerHTML = '<li class="muted">none yet</li>';
          return;
        }
        container.innerHTML = items.slice(0, 8).map(it => {
          const url = `${urlPrefix}/${encodeURIComponent(it.name)}`;
          const date = (it.mtime || '').slice(0, 10);
          const kb = it.size ? `${Math.round(it.size / 1024)}KB` : '';
          return `<li>
            <a href="${url}" target="_blank" rel="noopener">${it.name}</a>
            <span class="file-meta">${date} · ${kb}</span>
          </li>`;
        }).join('');
      };
      renderItems(footerReports, reports.items || [], '/api/reports');
      renderItems(footerDocs, docs.items || [], '/api/docs');
    } catch (e) {
      footerReports.innerHTML = `<li class="muted">error: ${e.message}</li>`;
      footerDocs.innerHTML = `<li class="muted">error: ${e.message}</li>`;
    }
  }

  // ── CSV upload ─────────────────────────────────────────────────────────────
  async function onRefreshCache() {
    refreshCacheBtn.disabled = true;
    setStatus('Rebuilding cache in background — this can take ~30s…');
    try {
      await API.refreshCache();
      // Poll until cache row count changes for today; refresh display after.
      setTimeout(async () => {
        try {
          await runRecs();
          loadFreshness();   // snapshot timestamp in the banner moved too
          setStatus('Cache refreshed.', 'ok');
        } finally {
          refreshCacheBtn.disabled = false;
        }
      }, 5000);
    } catch (e) {
      setStatus(`Cache refresh failed: ${e.message}`, 'error');
      refreshCacheBtn.disabled = false;
    }
  }

  // ── data-freshness banner ─────────────────────────────────────────────────
  // Thresholds are about the disclosure regime, not arbitrary: the STOCK Act
  // gives filers up to 45 days to report, and filings arrive in bursts, so a
  // week of quiet is normal and a month of it means ingestion is probably
  // broken rather than Congress being idle.
  function freshnessAgeClass(days) {
    if (days === null || days === undefined) return 'age-unknown';
    if (days <= 7) return 'age-fresh';
    if (days <= 30) return 'age-aging';
    return 'age-stale';
  }

  function renderFreshness(f) {
    const days = f.latest_filing_days_ago;
    freshEl.className = `freshness-banner ${freshnessAgeClass(days)}`;

    const ago = days === null || days === undefined ? ''
      : days === 0 ? ' (today)'
      : days === 1 ? ' (yesterday)'
      : ` (${days} days ago)`;
    freshHeadline.innerHTML =
      `Disclosures current through <b>${fmtDate(f.latest_filing_date)}</b>${ago}`;

    const t = f.last_trade;
    if (t) {
      const kind = /sale|sell/i.test(t.transaction_type || '') ? 'sell' : 'buy';
      // chamber comes out of the DB lowercase ('house' / 'senate').
      const chamber = t.chamber ? t.chamber[0].toUpperCase() + t.chamber.slice(1) : null;
      const who = [t.party, chamber].filter(Boolean).join(', ');
      freshDetail.innerHTML =
        `Last recorded trade: <span class="fresh-side ${kind}">`
        + `${esc((t.transaction_type || '').toUpperCase())}</span> `
        + `<b>${esc(t.ticker || t.asset_name || '—')}</b> — `
        + `${esc(t.member_name)}${who ? ` <span class="muted">(${esc(who)})</span>` : ''}, `
        + `traded ${fmtDate(t.transaction_date, false)}, filed ${fmtDate(t.filing_date, false)}`;
    } else {
      freshDetail.textContent = 'No disclosed trades in the database yet.';
    }

    const bits = [];
    if (f.n_transactions) bits.push(`${f.n_transactions.toLocaleString()} disclosed trades`);
    if (f.latest_price_bar_date) bits.push(`prices thru ${fmtDate(f.latest_price_bar_date, false)}`);
    if (f.snapshot && f.snapshot.created_at) {
      bits.push(`recs built ${f.snapshot.created_at.slice(0, 16).replace('T', ' ')}`);
    }
    freshMeta.textContent = bits.join(' · ');
  }

  async function loadFreshness() {
    try {
      renderFreshness(await API.getDataFreshness());
    } catch (e) {
      freshEl.className = 'freshness-banner age-unknown';
      freshHeadline.textContent = 'Could not load data freshness';
      freshDetail.textContent = e.message;
      freshMeta.textContent = '';
    }
  }

  function renderCacheTag(data) {
    const cache = data && data.cache;
    if (!cache || !cache.hit) {
      cacheTag.className = 'cache-tag cache-live';
      cacheTag.textContent = 'live compute';
      cacheTag.title = 'Computed on request — no pre-built snapshot matched.';
      return;
    }
    const built = (cache.created_at || '').slice(0, 16).replace('T', ' ');
    if (cache.stale) {
      // The daily job hadn't written a snapshot for the requested date, so
      // we served the most recent one. Name the date we actually served —
      // "stale" on its own leaves the reader guessing by how much.
      cacheTag.className = 'cache-tag cache-stale';
      cacheTag.textContent = `cached ${data.as_of} · built ${built}`;
      cacheTag.title = data.as_of_requested
        ? `No snapshot for ${data.as_of_requested} yet — showing ${data.as_of}.`
        : 'Showing the most recent available snapshot.';
    } else {
      cacheTag.className = 'cache-tag cache-hit';
      cacheTag.textContent = `cached · built ${built}`;
      cacheTag.title = `Snapshot for ${data.as_of}, written by the daily job.`;
    }
  }

  // ── recommendations render ────────────────────────────────────────────────
  function renderConfig(data) {
    const c = data.strategy_config;
    const s = data.summary;
    configBox.innerHTML = `
      <table class="kvtable">
        <tr><td class="key">Profile</td><td><b>${c.label}</b> — ${c.description}</td></tr>
        <tr><td class="key">Universe</td><td><code>${c.universe}</code></td></tr>
        <tr><td class="key">Exit policy</td><td><code>${c.exit_policy}</code></td></tr>
        <tr><td class="key">Trailing stop</td><td>${(c.trailing_stop_pct*100).toFixed(1)}% base × clipped beta (if applicable)</td></tr>
        <tr><td class="key">Time stop</td><td>${c.time_stop_days} trading days</td></tr>
        <tr><td class="key">As of (calendar)</td><td>${data.as_of} &nbsp;·&nbsp; nearest trading day: ${s.as_of_trading_day || '—'}</td></tr>
        <tr><td class="key">Considered</td><td>${s.n_entries_considered} purchase entries in lookback</td></tr>
      </table>`;
  }

  function renderOpen(items, asOf) {
    openCount.textContent = items.length ? `(${items.length})` : '';
    openTbody.innerHTML = items.map(p => `
      <tr>
        <td><b>${p.ticker}</b></td>
        <td>${p.member_name}</td>
        <td>${p.party || '—'}</td>
        <td>${p.entry_filing_date}</td>
        <td>${daysAgo(p.entry_filing_date, asOf)}</td>
        <td>${fmtNum(p.entry_price)}</td>
        <td>${fmtNum(p.current_price)}</td>
        <td>${fmtPct(p.return_since_entry)}</td>
        <td>${fmtNum(p.beta, 2)}</td>
        <td>${p.effective_stop_pct === null ? '—' : (p.effective_stop_pct*100).toFixed(1) + '%'}</td>
      </tr>`).join('') || `<tr><td colspan="10" class="empty">No new entries in the window.</td></tr>`;
  }

  function renderHold(items) {
    // One row per (member, ticker) position rather than per lot. A member can
    // genuinely buy the same name twice — on different days, or on one day in
    // two amount brackets — and listing each lot made real positions look like
    // duplicated rows. Lots are rolled up and counted instead; the entry shown
    // is the earliest, which is the one the holding period runs from.
    const groups = new Map();
    for (const p of items) {
      const key = `${p.member_name}|${p.ticker}`;
      const g = groups.get(key);
      if (!g) { groups.set(key, { ...p, lots: 1 }); continue; }
      g.lots += 1;
      if (p.entry_filing_date < g.entry_filing_date) {
        g.entry_filing_date = p.entry_filing_date;
        g.days_held = p.days_held;
      }
      // Worst drawdown across lots is the honest risk figure for the position.
      if (p.drawdown_from_peak != null &&
          (g.drawdown_from_peak == null || p.drawdown_from_peak < g.drawdown_from_peak)) {
        g.drawdown_from_peak = p.drawdown_from_peak;
      }
    }
    const rows = [...groups.values()];
    holdCount.textContent = rows.length
      ? `(${rows.length}${rows.length !== items.length ? ` from ${items.length} lots` : ''})`
      : '';
    holdTbody.innerHTML = rows.map(p => `
      <tr>
        <td><b>${p.ticker}</b>${p.lots > 1 ? `<span class="lot-badge" title="${p.lots} separate disclosed purchases">×${p.lots}</span>` : ''}</td>
        <td>${p.member_name}</td>
        <td>${p.entry_filing_date}</td>
        <td>${p.days_held}</td>
        <td>${fmtPct(p.return_since_entry)}</td>
        <td>${fmtPct(p.drawdown_from_peak)}</td>
        <td>${p.effective_stop_pct === null ? '—' : (p.effective_stop_pct*100).toFixed(1) + '%'}</td>
      </tr>`).join('') || `<tr><td colspan="7" class="empty">Nothing currently held.</td></tr>`;
  }

  function renderClose(items) {
    closeCount.textContent = items.length ? `(${items.length})` : '';
    closeTbody.innerHTML = items.map(p => `
      <tr>
        <td><b>${p.ticker}</b></td>
        <td>${p.member_name}</td>
        <td><code>${p.exit_reason}</code></td>
        <td>${p.entry_filing_date}</td>
        <td>${fmtNum(p.exit_price)}</td>
        <td>${fmtPct(p.realized_return)}</td>
        <td>${p.days_held}</td>
      </tr>`).join('') || `<tr><td colspan="7" class="empty">No exits firing on this date.</td></tr>`;
  }

  async function runRecs() {
    setStatus('Loading recommendations…');
    goBtn.disabled = true;
    // A cached snapshot comes back in milliseconds. Anything still pending
    // after a beat means we missed the cache and the backend is doing a full
    // live recompute (~75s) — say so, rather than leaving the reader watching
    // a status line that looks hung.
    const slowTimer = setTimeout(() => {
      setStatus('No pre-built snapshot for this configuration — computing '
        + 'live, which takes ~75s. The daily 6am-ET job pre-builds the '
        + 'default 14-day window for each profile.');
    }, 2500);
    try {
      const params = {
        as_of: asOfInput.value,
        risk_profile: profileSel.value,
        new_window_days: parseInt(windowInput.value) || 14,
      };
      const data = await API.getRecommendations(params);
      clearTimeout(slowTimer);
      lastRecData = data;
      renderConfig(data);
      renderCacheTag(data);
      renderOpen(data.open, data.as_of);
      renderHold(data.holding);
      renderClose(data.close);
      const wrap = document.getElementById('recs-results-wrap');
      if (wrap) {
        wrap.classList.remove('hidden');
        // Expand on first load
        const body = document.getElementById('recs-results-body');
        const btn = wrap.querySelector('.collapse-btn');
        if (body) body.classList.remove('hidden');
        if (btn) btn.classList.remove('is-collapsed');
      }
      setStatus(
        `Open ${data.summary.n_open_today} · Holding ${data.summary.n_holding} · Close ${data.summary.n_close_today}`,
        'ok',
      );
    } catch (e) {
      console.error(e);
      setStatus(`Error: ${e.message}`, 'error');
    } finally {
      clearTimeout(slowTimer);
      goBtn.disabled = false;
    }
  }

  // ── strategy comparison chart ──────────────────────────────────────────────
  let lastStratData = null;

  async function loadStrategyChart() {
    stratEmpty.classList.add('hidden');
    try {
      const data = await API.getStrategyComparison({
        band_k: parseFloat(stratBandK.value) || 1,
        band_window: parseInt(stratBandWin.value) || 21,
      });
      lastStratData = data;
      if (!data.profiles || !data.profiles.length) {
        stratEmpty.textContent = 'No backtest runs match the profile filters yet. Run analyses with leadership_only ∈ {false,true} and mirror_sell variants on filing_date.';
        stratEmpty.classList.remove('hidden');
        d3.select(stratChartEl).selectAll('*').remove();
        stratLegend.innerHTML = '';
        return;
      }
      renderStratLegend(data);
      drawStrategyChart(data);
    } catch (e) {
      console.error(e);
      stratEmpty.textContent = `Error loading strategies: ${e.message}`;
      stratEmpty.classList.remove('hidden');
    }
  }

  function renderStratLegend(data) {
    const parts = data.profiles.map(p => {
      const c = PROFILE_COLORS[p.key] || '#888';
      const s = p.summary || {};
      const ex = s.oos_excess;
      const exClass = ex === null || ex === undefined ? '' : (ex > 0 ? 'pos' : ex < 0 ? 'neg' : '');
      const exStr = ex === null || ex === undefined ? '—' :
        `${ex >= 0 ? '+' : ''}${(ex * 100).toFixed(2)}%`;
      return `<span class="legend-item">
        <span class="legend-swatch" style="background:${c}"></span>
        <span><b style="color:${c}">${p.label}</b> · run #${p.run_id}</span>
        <span class="legend-summary ${exClass}">excess ${exStr}</span>
      </span>`;
    });
    parts.push(`<span class="legend-item">
      <span class="legend-swatch" style="background:#8b949e"></span>
      <span>benchmark</span>
    </span>`);
    const b = data.band;
    if (b) {
      parts.push(`<span class="legend-item muted">
        shade: ±${b.k_sigma}σ trailing-${b.window}d daily vol envelope</span>`);
    }
    if (data.missing && data.missing.length) {
      parts.push(`<span class="legend-item muted">
        missing: ${data.missing.map(m => m.label).join(', ')}</span>`);
    }
    stratLegend.innerHTML = parts.join('');
  }

  function drawStrategyChart(data) {
    const svg = d3.select(stratChartEl);
    svg.selectAll('*').remove();

    const profiles = data.profiles;
    if (!profiles.length) return;

    const W = stratChartEl.clientWidth || 800;
    const H = stratChartEl.clientHeight || 300;
    const m = { t: 14, r: 14, b: 26, l: 50 };
    svg.attr('viewBox', `0 0 ${W} ${H}`);

    // Original (un-zoomed) domains
    const allDates = profiles.flatMap(p => p.dates.map(d => new Date(d)));
    const xOrig = d3.scaleTime().domain(d3.extent(allDates)).range([m.l, W - m.r]);

    const yVals = [];
    profiles.forEach(p => {
      p.strategy.forEach(v => v != null && yVals.push(v));
      p.benchmark.forEach(v => v != null && yVals.push(v));
      p.band_lo && p.band_lo.forEach(v => v != null && yVals.push(v));
      p.band_hi && p.band_hi.forEach(v => v != null && yVals.push(v));
    });
    const yOrig = d3.scaleLinear()
      .domain([d3.min(yVals), d3.max(yVals)]).nice()
      .range([H - m.b, m.t]);

    let x = xOrig.copy(), y = yOrig.copy();

    // Clip path so pan/zoom doesn't leak past the plot area
    svg.append('defs').append('clipPath').attr('id', 'strat-clip')
      .append('rect').attr('x', m.l).attr('y', m.t)
      .attr('width', W - m.l - m.r).attr('height', H - m.t - m.b);

    // Axes (refreshed on each redraw)
    const xAxisG = svg.append('g').attr('class', 'axis')
      .attr('transform', `translate(0,${H - m.b})`);
    const yAxisG = svg.append('g').attr('class', 'axis')
      .attr('transform', `translate(${m.l},0)`);

    // Clipped plot group
    const plot = svg.append('g').attr('clip-path', 'url(#strat-clip)');

    // Reference line at NAV = 1 (only drawn if 1 is within original domain)
    const refLine = (yOrig.domain()[0] <= 1 && yOrig.domain()[1] >= 1)
      ? plot.append('line')
        .attr('x1', m.l).attr('x2', W - m.r)
        .attr('stroke', 'var(--border)')
        .attr('stroke-dasharray', '3,3')
      : null;

    // Benchmark (shared SPY across profiles — pick the first)
    const benchData = profiles[0].dates.map((d, i) => ({
      date: new Date(d), v: profiles[0].benchmark[i],
    })).filter(d => d.v != null);
    const benchPath = plot.append('path').datum(benchData)
      .attr('class', 'strat-bench');

    // Per-profile band + line
    const profilePlots = profiles.map(p => {
      const color = PROFILE_COLORS[p.key] || '#888';
      const pts = p.dates.map((d, i) => ({
        date: new Date(d),
        v: p.strategy[i],
        lo: p.band_lo ? p.band_lo[i] : null,
        hi: p.band_hi ? p.band_hi[i] : null,
      }));
      const bandPts = pts.filter(q => q.lo != null && q.hi != null);
      const bandPath = bandPts.length > 1
        ? plot.append('path').datum(bandPts).attr('class', 'strat-band').attr('fill', color)
        : null;
      const linePath = plot.append('path')
        .datum(pts.filter(d => d.v != null))
        .attr('class', 'strat-line')
        .attr('stroke', color);
      return { bandPath, linePath };
    });


    function redraw() {
      xAxisG.call(d3.axisBottom(x).ticks(6));
      yAxisG.call(d3.axisLeft(y).ticks(5).tickFormat(d3.format('.2f')));

      if (refLine) refLine.attr('y1', y(1)).attr('y2', y(1));

      const benchLineFn = d3.line().x(d => x(d.date)).y(d => y(d.v));
      benchPath.attr('d', benchLineFn);

      profilePlots.forEach(pp => {
        if (pp.bandPath) {
          const area = d3.area()
            .x(d => x(d.date))
            .y0(d => y(d.lo))
            .y1(d => y(d.hi));
          pp.bandPath.attr('d', area);
        }
        const lineFn = d3.line().x(d => x(d.date)).y(d => y(d.v));
        pp.linePath.attr('d', lineFn);
      });

    }
    redraw();

    // Hover tooltip + crosshair
    let tip = stratChartWrap.querySelector('.strat-tooltip');
    if (!tip) {
      tip = document.createElement('div');
      tip.className = 'strat-tooltip';
      stratChartWrap.appendChild(tip);
    }
    const focusG = svg.append('g').style('display', 'none');
    focusG.append('line').attr('class', 'crosshair')
      .attr('y1', m.t).attr('y2', H - m.b)
      .attr('stroke', 'var(--text-muted)')
      .attr('stroke-dasharray', '2,3');

    const overlay = svg.append('rect')
      .attr('class', 'zoom-capture')
      .attr('x', m.l).attr('y', m.t)
      .attr('width', W - m.r - m.l).attr('height', H - m.b - m.t)
      .attr('fill', 'transparent');

    overlay.on('mouseenter', () => { focusG.style('display', null); tip.style.display = 'block'; });
    overlay.on('mouseleave', () => { focusG.style('display', 'none'); tip.style.display = 'none'; });
    overlay.on('mousemove', evt => {
      const [mx] = d3.pointer(evt);
      const xVal = x.invert(mx);
      focusG.select('.crosshair').attr('x1', mx).attr('x2', mx);
      const lines = profiles.map(p => {
        const idx = d3.bisector(d => new Date(d)).left(p.dates, xVal);
        const i = Math.min(idx, p.dates.length - 1);
        const c = PROFILE_COLORS[p.key] || '#888';
        const v = p.strategy[i];
        const lo = p.band_lo && p.band_lo[i];
        const hi = p.band_hi && p.band_hi[i];
        const ret = v != null ? ((v - 1) * 100).toFixed(2) + '%' : '—';
        const band = (lo != null && hi != null)
          ? ` (${lo.toFixed(3)}–${hi.toFixed(3)})` : '';
        return `<div><span style="color:${c}">●</span> <b>${p.label}</b>: ${v ? v.toFixed(3) : '—'} ${ret}${band}</div>`;
      });
      const dateLabel = xVal.toISOString().slice(0, 10);
      tip.innerHTML = `<div style="color:var(--text-muted);margin-bottom:3px">${dateLabel}</div>` + lines.join('');
      const rect = stratChartWrap.getBoundingClientRect();
      const xSvgPct = mx / parseFloat(svg.attr('viewBox').split(' ')[2]);
      const xPx = xSvgPct * rect.width;
      const leftSide = xPx > rect.width / 2;
      tip.style.left = leftSide ? '' : `${xPx + 10}px`;
      tip.style.right = leftSide ? `${rect.width - xPx + 10}px` : '';
      tip.style.top = '8px';
    });

    // Zoom: wheel + drag-pan, x and y both rescale — matches Analysis
    const zoom = d3.zoom().scaleExtent([1, 50])
      .on('zoom', event => {
        x = event.transform.rescaleX(xOrig);
        y = event.transform.rescaleY(yOrig);
        redraw();
        data._zoomTransform = event.transform;
      });
    svg.call(zoom);
    // Preserve zoom across redraws (holdings refresh, legend updates)
    if (data._zoomTransform) {
      svg.call(zoom.transform, data._zoomTransform);
    }
  }

})();
