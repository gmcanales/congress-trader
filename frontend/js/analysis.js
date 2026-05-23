(() => {
  // ── View toggle ────────────────────────────────────────────────────────────
  const tradesView = document.getElementById('trades-view');
  const analysisView = document.getElementById('analysis-view');
  const navTrades = document.getElementById('nav-trades');
  const navAnalysis = document.getElementById('nav-analysis');
  let loaded = false;

  function setView(isAnalysis) {
    analysisView.classList.toggle('hidden', !isAnalysis);
    tradesView.classList.toggle('hidden', isAnalysis);
    navAnalysis.classList.toggle('active', isAnalysis);
    navTrades.classList.toggle('active', !isAnalysis);
    if (isAnalysis && !loaded) {
      loaded = true;
      loadDatasets();
      loadRuns();
    }
  }
  navTrades.addEventListener('click', () => setView(false));
  navAnalysis.addEventListener('click', () => setView(true));

  // ── Helpers ────────────────────────────────────────────────────────────────
  const val = id => document.getElementById(id).value;
  const ints = id => val(id).split(',').map(s => parseInt(s.trim())).filter(n => !isNaN(n));
  const cls = x => (x > 0 ? 'pos' : x < 0 ? 'neg' : '');
  const fmtPct = x =>
    (x === null || x === undefined) ? '—' : `${x >= 0 ? '+' : ''}${(x * 100).toFixed(2)}%`;
  const statusBadge = s =>
    `<span class="badge badge-${s}">${s}</span>`;

  function runStatus(msg, kind = '') {
    const el = document.getElementById('run-status');
    el.textContent = msg;
    el.className = 'scrape-status ' + kind;
  }

  // ── Datasets ───────────────────────────────────────────────────────────────
  let datasets = [];

  async function loadDatasets() {
    try {
      datasets = await API.getDatasets();
    } catch (e) {
      datasets = [];
    }
    const list = document.getElementById('datasets-list');
    if (!datasets.length) {
      list.innerHTML = '<div class="empty">No datasets — snapshot the current data to create one.</div>';
    } else {
      list.innerHTML = datasets.map(d => {
        const s = d.stats || {};
        return `<div class="dataset-card">
          <div class="dataset-name">${d.name}</div>
          <div class="dataset-meta">${(d.created_at || '').slice(0, 10)} ·
            ${s.transaction_count ?? 0} txns (${s.ticker_transaction_count ?? 0} w/ ticker) ·
            ${d.run_count} run${d.run_count === 1 ? '' : 's'}</div>
          ${d.description ? `<div class="dataset-desc">${d.description}</div>` : ''}
          <div class="dataset-meta muted">years ${(d.spec?.years || []).join(', ') || '—'} ·
            ${(d.spec?.chambers || []).join(' + ') || '—'}</div>
        </div>`;
      }).join('');
    }
    const sel = document.getElementById('run-dataset');
    const prev = sel.value;
    sel.innerHTML = datasets.map(d => `<option value="${d.id}">${d.name}</option>`).join('');
    if (prev) sel.value = prev;
  }

  document.getElementById('ds-snapshot-btn').addEventListener('click', async function () {
    const name = document.getElementById('ds-name').value.trim();
    if (!name) { runStatus('Enter a dataset name.', 'err'); return; }
    this.disabled = true;
    try {
      await API.createDataset(name, document.getElementById('ds-desc').value.trim());
      document.getElementById('ds-name').value = '';
      document.getElementById('ds-desc').value = '';
      await loadDatasets();
      runStatus(`Dataset "${name}" snapshotted.`, 'ok');
    } catch (e) {
      runStatus('Snapshot failed: ' + e.message, 'err');
    } finally {
      this.disabled = false;
    }
  });

  // ── Runs list ──────────────────────────────────────────────────────────────
  let runs = [];
  const RUNS_PAGE_SIZE = 5;
  let runsPage = 0;

  async function loadRuns() {
    try {
      runs = await API.getRuns();
    } catch (e) {
      runs = [];
    }
    // default to last page (most recent 5)
    runsPage = Math.max(0, Math.ceil(runs.length / RUNS_PAGE_SIZE) - 1);
    renderRunsPage();
  }

  function renderRunsPage() {
    const tb = document.getElementById('runs-tbody');
    const pgEl = document.getElementById('runs-pagination');
    const totalPages = Math.max(1, Math.ceil(runs.length / RUNS_PAGE_SIZE));

    if (!runs.length) {
      tb.innerHTML = '<tr><td colspan="9" class="empty">No analysis runs yet.</td></tr>';
      if (pgEl) pgEl.innerHTML = '';
      return;
    }

    const start = runsPage * RUNS_PAGE_SIZE;
    const pageRuns = runs.slice(start, start + RUNS_PAGE_SIZE);
    tb.innerHTML = pageRuns.map(r => {
      const s = r.scores || {};
      const sel = s.selected_params
        ? `${s.selected_params.horizon_days}d / ≥${s.selected_params.min_members}` : '—';
      const dsName = (datasets.find(d => d.id === r.dataset_id) || {}).name || `#${r.dataset_id}`;
      return `<tr class="run-row" data-id="${r.id}">
        <td>${r.id}</td>
        <td>${(r.created_at || '').slice(0, 16).replace('T', ' ')}</td>
        <td>${dsName}</td>
        <td>${r.params?.date_field || '—'}</td>
        <td>${sel}</td>
        <td>${fmtPct(s.train_excess)}</td>
        <td class="${cls(s.oos_excess)}">${fmtPct(s.oos_excess)}</td>
        <td>${fmtPct(s.overfit_gap)}</td>
        <td>${statusBadge(r.status)}</td>
      </tr>`;
    }).join('');

    if (pgEl) {
      pgEl.innerHTML = `
        <button id="runs-prev" ${runsPage === 0 ? 'disabled' : ''}>← Prev</button>
        <span>Page ${runsPage + 1} of ${totalPages}</span>
        <button id="runs-next" ${runsPage >= totalPages - 1 ? 'disabled' : ''}>Next →</button>`;
      document.getElementById('runs-prev').addEventListener('click', () => { runsPage--; renderRunsPage(); });
      document.getElementById('runs-next').addEventListener('click', () => { runsPage++; renderRunsPage(); });
    }
  }

  document.getElementById('runs-tbody').addEventListener('click', e => {
    const tr = e.target.closest('.run-row');
    if (tr) openRun(parseInt(tr.dataset.id));
  });

  // ── Run analysis ───────────────────────────────────────────────────────────
  document.getElementById('run-analysis-btn').addEventListener('click', async function () {
    const dsId = parseInt(document.getElementById('run-dataset').value);
    if (!dsId) { runStatus('Snapshot a dataset first.', 'err'); return; }
    const payload = {
      dataset_id: dsId,
      date_field: val('run-datefield'),
      train_start: val('run-train-start'),
      train_end: val('run-train-end'),
      test_start: val('run-test-start'),
      test_end: val('run-test-end'),
      event_horizons: ints('run-horizons'),
      hold_days: ints('run-holddays'),
      min_members: ints('run-minmembers'),
    };
    this.disabled = true;
    runStatus('Queuing analysis…');
    try {
      const res = await API.runAnalysis(payload);
      runStatus(`Run #${res.run_id} queued — fetching prices + grid search, a few minutes…`);
      pollRun(res.run_id);
    } catch (e) {
      runStatus('Error: ' + e.message, 'err');
      this.disabled = false;
    }
  });

  function pollRun(id) {
    const tick = async () => {
      await loadRuns();
      const r = runs.find(x => x.id === id);
      if (!r || r.status === 'running') { setTimeout(tick, 5000); return; }
      document.getElementById('run-analysis-btn').disabled = false;
      if (r.status === 'done') {
        runStatus(`Run #${id} complete.`, 'ok');
        openRun(id);
      } else {
        runStatus(`Run #${id} failed: ${r.error || 'unknown error'}`, 'err');
      }
    };
    setTimeout(tick, 5000);
  }

  // ── Run detail ─────────────────────────────────────────────────────────────
  async function openRun(id) {
    const detail = document.getElementById('run-detail');
    const body = document.getElementById('run-detail-body');
    document.getElementById('run-detail-title').textContent = `Run #${id}`;
    body.innerHTML = '<div class="empty">Loading…</div>';
    detail.classList.remove('hidden');
    let run;
    try {
      run = await API.getRun(id);
    } catch (e) {
      body.innerHTML = `<div class="empty">Failed to load: ${e.message}</div>`;
      return;
    }
    document.getElementById('run-detail-title').textContent =
      `Run #${id} — anchor ${run.params?.date_field}`;
    if (run.status !== 'done' || !run.full_results) {
      body.innerHTML = `<div class="empty">Run is ${run.status}` +
        (run.error ? `: ${run.error}` : '') + '</div>';
      return;
    }
    renderDetail(body, run.full_results);
    detail.scrollIntoView({ behavior: 'smooth' });
  }

  function esTable(es, title) {
    let rows = '';
    for (const side of ['buy', 'sell']) {
      for (const [h, groups] of Object.entries(es.results[side])) {
        const a = groups.all;
        if (!a || !a.n) continue;
        rows += `<tr>
          <td>${side}</td><td>${h}</td><td>${a.n}</td>
          <td class="${cls(a.mean_excess)}">${fmtPct(a.mean_excess)}</td>
          <td>${fmtPct(a.median_excess)}</td>
          <td>${(a.win_rate * 100).toFixed(0)}%</td>
          <td>${a.t_stat ?? '—'}</td></tr>`;
      }
    }
    return `<h4>${title}
      <span class="muted">${es.window.start}..${es.window.end} ·
      ${es.events_total} events</span></h4>
      <table class="mini-table"><thead><tr>
        <th>Side</th><th>Horizon</th><th>n</th><th>Mean excess</th>
        <th>Median</th><th>Win</th><th>t-stat</th></tr></thead>
      <tbody>${rows}</tbody></table>`;
  }

  function gridTable(fr) {
    const sel = fr.selected_params;
    const rows = fr.grid_search_train.map(g => {
      const p = g.params, res = g.result;
      const isSel = p.horizon_days === sel.horizon_days && p.min_members === sel.min_members;
      return `<tr class="${isSel ? 'sel-row' : ''}">
        <td>${p.horizon_days}d</td><td>≥${p.min_members}</td>
        <td>${res.positions_opened}</td>
        <td>${fmtPct(res.strategy_return)}</td>
        <td>${fmtPct(res.benchmark_return)}</td>
        <td class="${cls(res.excess_return)}">${fmtPct(res.excess_return)}</td></tr>`;
    }).join('');
    const oos = fr.out_of_sample_test;
    return `<h4>Grid search — train (selected row highlighted)</h4>
      <table class="mini-table"><thead><tr>
        <th>Hold</th><th>Min members</th><th>Positions</th>
        <th>Strategy</th><th>Benchmark</th><th>Excess</th></tr></thead>
      <tbody>${rows}</tbody></table>
      <div class="oos-summary">Out-of-sample test —
        strategy <b class="${cls(oos.strategy_return)}">${fmtPct(oos.strategy_return)}</b>
        vs benchmark <b>${fmtPct(oos.benchmark_return)}</b> →
        excess <b class="${cls(oos.excess_return)}">${fmtPct(oos.excess_return)}</b></div>`;
  }

  function renderDetail(body, fr) {
    body.innerHTML =
      esTable(fr.event_study.train, 'Event study — TRAIN') +
      esTable(fr.event_study.test, 'Event study — TEST') +
      gridTable(fr) +
      '<h4>Out-of-sample NAV — strategy vs benchmark</h4>' +
      '<div class="nav-legend"><span class="k-strat">— strategy</span>' +
      '<span class="k-bench">— benchmark</span></div>' +
      '<svg id="nav-chart"></svg>';
    drawNav(fr.out_of_sample_test.nav_curve || []);
  }

  function drawNav(curve) {
    const svg = d3.select('#nav-chart');
    svg.selectAll('*').remove();
    if (!curve.length) return;
    const W = svg.node().clientWidth || 800, H = 280;
    const m = { t: 12, r: 16, b: 26, l: 52 };
    svg.attr('viewBox', `0 0 ${W} ${H}`);
    const data = curve.map(d => ({
      date: new Date(d.date), strategy: d.strategy, benchmark: d.benchmark,
    }));
    const xOrig = d3.scaleTime().domain(d3.extent(data, d => d.date)).range([m.l, W - m.r]);
    const vals = data.flatMap(d => [d.strategy, d.benchmark]);
    const yOrig = d3.scaleLinear().domain([d3.min(vals), d3.max(vals)]).nice().range([H - m.b, m.t]);
    let x = xOrig.copy(), y = yOrig.copy();

    svg.append('defs').append('clipPath').attr('id', 'nav-clip')
      .append('rect').attr('x', m.l).attr('y', m.t)
      .attr('width', W - m.l - m.r).attr('height', H - m.t - m.b);

    const xAxisG = svg.append('g').attr('class', 'axis')
      .attr('transform', `translate(0,${H - m.b})`).call(d3.axisBottom(x).ticks(6));
    const yAxisG = svg.append('g').attr('class', 'axis')
      .attr('transform', `translate(${m.l},0)`)
      .call(d3.axisLeft(y).ticks(5).tickFormat(d3.format('.2f')));

    const g = svg.append('g').attr('clip-path', 'url(#nav-clip)');
    const benchPath = g.append('path').datum(data).attr('class', 'nav-bench');
    const stratPath = g.append('path').datum(data).attr('class', 'nav-strat');

    function redraw() {
      const mkL = key => d3.line().x(d => x(d.date)).y(d => y(d[key]));
      benchPath.attr('d', mkL('benchmark'));
      stratPath.attr('d', mkL('strategy'));
      xAxisG.call(d3.axisBottom(x).ticks(6));
      yAxisG.call(d3.axisLeft(y).ticks(5).tickFormat(d3.format('.2f')));
    }
    redraw();

    const zoom = d3.zoom().scaleExtent([0.5, 50])
      .on('zoom', event => {
        x = event.transform.rescaleX(xOrig);
        y = event.transform.rescaleY(yOrig);
        redraw();
      });

    svg.append('rect').attr('class', 'zoom-capture')
      .attr('x', m.l).attr('y', m.t)
      .attr('width', W - m.l - m.r).attr('height', H - m.t - m.b)
      .attr('fill', 'transparent').call(zoom);
  }
})();
