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
  const portForm      = document.getElementById('port-form');
  const portEditId    = document.getElementById('port-edit-id');
  const portTicker    = document.getElementById('port-ticker');
  const portShares    = document.getElementById('port-shares');
  const portCost      = document.getElementById('port-cost');
  const portBuyDate   = document.getElementById('port-buydate');
  const portTgtPx     = document.getElementById('port-tgt-px');
  const portTgtDate   = document.getElementById('port-tgt-date');
  const portNotes     = document.getElementById('port-notes');
  const portSaveBtn   = document.getElementById('port-save-btn');
  const portCancelBtn = document.getElementById('port-cancel-btn');
  const portStatus    = document.getElementById('port-status');
  const portTbody     = document.getElementById('port-tbody');
  const portCount     = document.getElementById('port-count');
  const portCsvFile   = document.getElementById('port-csv-file');
  const portCsvPick   = document.getElementById('port-csv-pick-btn');
  const portCsvMode   = document.getElementById('port-csv-mode');
  const portCsvSample = document.getElementById('port-csv-sample');
  const footerReports = document.getElementById('footer-reports');
  const footerDocs    = document.getElementById('footer-docs');

  const disclosureModal     = document.getElementById('portfolio-disclosure-modal');
  const disclosureAcceptBtn = document.getElementById('disclosure-accept-btn');
  const disclosureCancelBtn = document.getElementById('disclosure-cancel-btn');

  const DISCLOSURE_KEY = 'goosebase_portfolio_disclosure_v1';

  function showDisclosureModal(onAccept) {
    disclosureModal.classList.remove('hidden');
    disclosureAcceptBtn.onclick = () => {
      localStorage.setItem(DISCLOSURE_KEY, '1');
      disclosureModal.classList.add('hidden');
      onAccept();
    };
    disclosureCancelBtn.onclick = () => {
      disclosureModal.classList.add('hidden');
    };
  }

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
  let lastHoldings = [];      // user holdings
  let lastPortNav = null;     // last portfolio NAV series

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

  function setStatus(msg, kind = '') {
    statusEl.textContent = msg;
    statusEl.className = `scrape-status ${kind}`.trim();
  }
  function setPortStatus(msg, kind = '') {
    portStatus.textContent = msg;
    portStatus.className = `scrape-status ${kind}`.trim();
  }

  // ── init ───────────────────────────────────────────────────────────────────
  function init() {
    asOfInput.value = today();
    goBtn.addEventListener('click', runRecs);
    stratReload.addEventListener('click', loadStrategyChart);
    portForm.addEventListener('submit', onPortSave);
    portCancelBtn.addEventListener('click', resetPortForm);
    portTbody.addEventListener('click', onPortRowClick);
    refreshCacheBtn.addEventListener('click', onRefreshCache);
    portCsvPick.addEventListener('click', () => portCsvFile.click());
    portCsvFile.addEventListener('change', onCsvSelected);
    portCsvSample.addEventListener('click', onDownloadSample);
    window.addEventListener('resize', () => {
      if (lastStratData) drawStrategyChart(lastStratData);
    });
    loadStrategyChart();
    loadHoldings();
    loadFooterLinks();
    runRecs(); // pre-load today's recs (will hit the cache once it's warm)
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
  async function onCsvSelected(e) {
    const file = e.target.files[0];
    if (!file) return;
    if (!localStorage.getItem(DISCLOSURE_KEY)) {
      showDisclosureModal(() => doUploadCsv(file));
      portCsvFile.value = '';
      return;
    }
    doUploadCsv(file);
  }

  async function doUploadCsv(file) {
    portCsvPick.disabled = true;
    setPortStatus(`Uploading ${file.name}…`);
    try {
      const res = await API.uploadCsv(file, portCsvMode.value);
      const okMsg = `Uploaded ${file.name}: ${res.inserted} inserted, ${res.failed} failed.`;
      if (res.failed) {
        const errs = res.results.filter(r => !r.ok).slice(0, 5)
          .map(r => `row ${r.row}: ${r.error}`).join(' · ');
        setPortStatus(`${okMsg} ${errs}`, res.inserted ? 'ok' : 'error');
      } else {
        setPortStatus(okMsg, 'ok');
      }
      await loadHoldings();
    } catch (err) {
      setPortStatus(`Upload failed: ${err.message}`, 'error');
    } finally {
      portCsvFile.value = '';
      portCsvPick.disabled = false;
    }
  }

  async function onDownloadSample(e) {
    e.preventDefault();
    try {
      const sample = await API.getCsvSample();
      const blob = new Blob([sample.content], { type: 'text/csv' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = sample.filename || 'portfolio_template.csv';
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setPortStatus(`Sample download failed: ${err.message}`, 'error');
    }
  }

  async function onRefreshCache() {
    refreshCacheBtn.disabled = true;
    setStatus('Rebuilding cache in background — this can take ~30s…');
    try {
      await API.refreshCache();
      // Poll until cache row count changes for today; refresh display after.
      setTimeout(async () => {
        try {
          await runRecs();
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

  function renderCacheTag(cache) {
    if (!cache) {
      cacheTag.className = 'cache-tag cache-live';
      cacheTag.textContent = 'live compute';
      return;
    }
    if (cache.hit && cache.stale) {
      cacheTag.className = 'cache-tag cache-stale';
      cacheTag.textContent = `cached · stale · ${(cache.created_at || '').slice(0, 16).replace('T', ' ')}`;
    } else if (cache.hit) {
      cacheTag.className = 'cache-tag cache-hit';
      cacheTag.textContent = `cached · ${(cache.created_at || '').slice(0, 16).replace('T', ' ')}`;
    } else {
      cacheTag.className = 'cache-tag cache-live';
      cacheTag.textContent = 'live compute';
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
    holdCount.textContent = items.length ? `(${items.length})` : '';
    holdTbody.innerHTML = items.map(p => `
      <tr>
        <td><b>${p.ticker}</b></td>
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
    setStatus('Computing recommendations…');
    goBtn.disabled = true;
    try {
      const params = {
        as_of: asOfInput.value,
        risk_profile: profileSel.value,
        new_window_days: parseInt(windowInput.value) || 14,
      };
      const data = await API.getRecommendations(params);
      lastRecData = data;
      renderConfig(data);
      renderCacheTag(data.cache);
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
      renderHoldings(); // refresh strategy-view pills
    } catch (e) {
      console.error(e);
      setStatus(`Error: ${e.message}`, 'error');
    } finally {
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
      // Fetch user portfolio NAV across the same window for the overlay.
      const dates0 = data.profiles[0].dates;
      const stratStart = dates0[0];
      const stratEnd = dates0[dates0.length - 1];
      try {
        lastPortNav = await API.getPortfolioNav(stratStart, stratEnd);
      } catch (e) {
        console.warn('portfolio NAV unavailable:', e.message);
        lastPortNav = null;
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
    if (lastPortNav && lastPortNav.series && lastPortNav.series.length) {
      const s = lastPortNav.series;
      const lastNav = s[s.length - 1].nav;
      const ret = ((lastNav - 1) * 100).toFixed(2);
      const retClass = lastNav > 1 ? 'pos' : lastNav < 1 ? 'neg' : '';
      parts.push(`<span class="legend-item">
        <span class="legend-swatch" style="background:#ff3b3b;height:4px"></span>
        <span><b style="color:#ff3b3b">Your portfolio</b> · ${lastPortNav.n_holdings} holdings</span>
        <span class="legend-summary ${retClass}">${lastNav > 1 ? '+' : ''}${ret}%</span>
      </span>`);
    } else if (lastPortNav && (!lastPortNav.series || !lastPortNav.series.length)) {
      parts.push(`<span class="legend-item muted">
        portfolio overlay: add a holding to see your NAV vs the strategies</span>`);
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
    if (lastPortNav && lastPortNav.series) {
      lastPortNav.series.forEach(d => d.nav != null && yVals.push(d.nav));
    }
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

    // User portfolio overlay — bold red, drawn last so it sits on top
    const userPts = (lastPortNav && lastPortNav.series && lastPortNav.series.length)
      ? lastPortNav.series.map(d => ({ date: new Date(d.date), v: d.nav })).filter(d => d.v != null)
      : [];
    const userPath = userPts.length > 1
      ? plot.append('path').datum(userPts).attr('class', 'strat-user')
      : null;

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

      if (userPath) {
        const userLineFn = d3.line().x(d => x(d.date)).y(d => y(d.v));
        userPath.attr('d', userLineFn);
      }
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
      if (lastPortNav && lastPortNav.series && lastPortNav.series.length) {
        const sDates = lastPortNav.series.map(d => new Date(d.date));
        const j = Math.min(
          d3.bisector(d => d).left(sDates, xVal),
          sDates.length - 1,
        );
        const pt = lastPortNav.series[j];
        if (pt) {
          const ret = ((pt.nav - 1) * 100).toFixed(2) + '%';
          lines.push(`<div><span style="color:#ff3b3b">●</span> <b style="color:#ff3b3b">Your portfolio</b>: ${pt.nav.toFixed(3)} ${ret} <span style="color:var(--text-muted)">· $${Math.round(pt.market_value).toLocaleString()}</span></div>`);
        }
      }
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

  // ── user portfolio ─────────────────────────────────────────────────────────
  async function loadHoldings() {
    try {
      lastHoldings = await API.getHoldings();
    } catch (e) {
      console.error(e);
      lastHoldings = [];
      setPortStatus(`Error loading portfolio: ${e.message}`, 'error');
      return;
    }
    renderHoldings();
    // Refresh the chart overlay so the bold-red line reflects the change.
    if (lastStratData) {
      const dates0 = lastStratData.profiles[0]?.dates;
      if (dates0 && dates0.length) {
        try {
          lastPortNav = await API.getPortfolioNav(dates0[0], dates0[dates0.length - 1]);
          renderStratLegend(lastStratData);
          drawStrategyChart(lastStratData);
        } catch (e) {
          console.warn('overlay refresh failed:', e.message);
        }
      }
    }
  }

  function buildRecLookup() {
    // Map ticker → {bucket, items} from lastRecData so we can show
    // each user holding's strategy view.
    const lookup = { close: {}, holding: {}, open: {} };
    if (!lastRecData) return lookup;
    (lastRecData.close || []).forEach(p => {
      lookup.close[p.ticker] = (lookup.close[p.ticker] || []).concat(p);
    });
    (lastRecData.holding || []).forEach(p => {
      lookup.holding[p.ticker] = (lookup.holding[p.ticker] || []).concat(p);
    });
    (lastRecData.open || []).forEach(p => {
      lookup.open[p.ticker] = (lookup.open[p.ticker] || []).concat(p);
    });
    return lookup;
  }

  function strategyViewCell(ticker, recLookup) {
    if (!lastRecData) {
      return '<span class="pill-none">run recs ↑</span>';
    }
    const pills = [];
    if (recLookup.close[ticker]) {
      pills.push(`<span class="strategy-view-pill pill-close" title="Strategy fires an exit signal today on this ticker">CLOSE (${recLookup.close[ticker].length})</span>`);
    }
    if (recLookup.holding[ticker]) {
      pills.push(`<span class="strategy-view-pill pill-hold" title="Strategy currently holds this ticker">HOLD (${recLookup.holding[ticker].length})</span>`);
    }
    if (recLookup.open[ticker]) {
      pills.push(`<span class="strategy-view-pill pill-buy" title="Strategy opens a new position in this ticker">BUY (${recLookup.open[ticker].length})</span>`);
    }
    return pills.length ? pills.join('') : '<span class="pill-none">no signal</span>';
  }

  function renderHoldings() {
    portCount.textContent = lastHoldings.length ? `(${lastHoldings.length})` : '';
    if (!lastHoldings.length) {
      portTbody.innerHTML = '<tr><td colspan="11" class="empty">No holdings yet — add one above.</td></tr>';
      return;
    }
    const recLookup = buildRecLookup();
    portTbody.innerHTML = lastHoldings.map(h => {
      const tgtParts = [];
      if (h.target_exit_price != null) tgtParts.push(`$${Number(h.target_exit_price).toFixed(2)}`);
      if (h.target_exit_date) tgtParts.push(h.target_exit_date);
      const tgt = tgtParts.join(' / ') || '—';
      return `<tr data-id="${h.id}">
        <td><b>${h.ticker}</b></td>
        <td>${fmtNum(h.shares, 4)}</td>
        <td>${fmtMoney(h.cost_basis)}</td>
        <td>${fmtMoney(h.current_price)}</td>
        <td>${fmtMoney(h.current_value)}</td>
        <td>${fmtPct(h.unrealized_pl_pct)}</td>
        <td>${h.days_held ?? '—'}</td>
        <td>${tgt}</td>
        <td>${strategyViewCell(h.ticker, recLookup)}</td>
        <td>${h.notes ? `<span title="${h.notes.replace(/"/g, '&quot;')}">${h.notes.length > 24 ? h.notes.slice(0, 24) + '…' : h.notes}</span>` : ''}</td>
        <td>
          <button class="port-action-btn" data-action="edit">Edit</button>
          <button class="port-action-btn danger" data-action="del">Del</button>
        </td>
      </tr>`;
    }).join('');
  }

  function onPortRowClick(e) {
    const btn = e.target.closest('button.port-action-btn');
    if (!btn) return;
    const tr = btn.closest('tr');
    const id = parseInt(tr.dataset.id);
    const h = lastHoldings.find(x => x.id === id);
    if (!h) return;
    if (btn.dataset.action === 'edit') {
      portEditId.value = h.id;
      portTicker.value = h.ticker;
      portShares.value = h.shares;
      portCost.value = h.cost_basis;
      portBuyDate.value = h.buy_date || '';
      portTgtPx.value = h.target_exit_price ?? '';
      portTgtDate.value = h.target_exit_date || '';
      portNotes.value = h.notes || '';
      portSaveBtn.textContent = 'Update Holding';
      portCancelBtn.classList.remove('hidden');
      portForm.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } else if (btn.dataset.action === 'del') {
      if (!confirm(`Delete holding ${h.ticker} (${h.shares} sh)?`)) return;
      API.deleteHolding(h.id).then(() => {
        setPortStatus(`Deleted ${h.ticker}.`, 'ok');
        loadHoldings();
      }).catch(e => setPortStatus(`Delete failed: ${e.message}`, 'error'));
    }
  }

  function resetPortForm() {
    portEditId.value = '';
    portForm.reset();
    portSaveBtn.textContent = 'Add Holding';
    portCancelBtn.classList.add('hidden');
  }

  async function onPortSave(e) {
    e.preventDefault();
    const isNew = !portEditId.value;
    if (isNew && !localStorage.getItem(DISCLOSURE_KEY)) {
      showDisclosureModal(() => doPortSave());
      return;
    }
    doPortSave();
  }

  async function doPortSave() {
    const body = {
      ticker: portTicker.value.trim().toUpperCase(),
      shares: parseFloat(portShares.value),
      cost_basis: parseFloat(portCost.value),
      buy_date: portBuyDate.value,
      notes: portNotes.value.trim() || null,
      target_exit_price: portTgtPx.value === '' ? null : parseFloat(portTgtPx.value),
      target_exit_date: portTgtDate.value || null,
    };
    if (!body.ticker || isNaN(body.shares) || isNaN(body.cost_basis) || !body.buy_date) {
      setPortStatus('Ticker, shares, cost basis, and buy date are required.', 'error');
      return;
    }
    portSaveBtn.disabled = true;
    try {
      if (portEditId.value) {
        await API.updateHolding(parseInt(portEditId.value), body);
        setPortStatus(`Updated ${body.ticker}.`, 'ok');
      } else {
        await API.createHolding(body);
        setPortStatus(`Added ${body.ticker}.`, 'ok');
      }
      resetPortForm();
      loadHoldings();
    } catch (err) {
      setPortStatus(`Save failed: ${err.message}`, 'error');
    } finally {
      portSaveBtn.disabled = false;
    }
  }
})();
