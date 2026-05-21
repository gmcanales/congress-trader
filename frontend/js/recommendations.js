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

  const openTbody     = document.getElementById('rec-open-tbody');
  const holdTbody     = document.getElementById('rec-hold-tbody');
  const closeTbody    = document.getElementById('rec-close-tbody');
  const openCount     = document.getElementById('rec-open-count');
  const holdCount     = document.getElementById('rec-hold-count');
  const closeCount    = document.getElementById('rec-close-count');

  let initialized = false;

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

  // ── init ───────────────────────────────────────────────────────────────────
  function init() {
    asOfInput.value = today();
    goBtn.addEventListener('click', run);
  }

  // ── render ─────────────────────────────────────────────────────────────────
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

  async function run() {
    setStatus('Computing recommendations…');
    goBtn.disabled = true;
    try {
      const params = {
        as_of: asOfInput.value,
        risk_profile: profileSel.value,
        new_window_days: parseInt(windowInput.value) || 14,
      };
      const data = await API.getRecommendations(params);
      renderConfig(data);
      renderOpen(data.open, data.as_of);
      renderHold(data.holding);
      renderClose(data.close);
      setStatus(
        `Open ${data.summary.n_open_today} · Holding ${data.summary.n_holding} · Close ${data.summary.n_close_today}`,
        'ok'
      );
    } catch (e) {
      console.error(e);
      setStatus(`Error: ${e.message}`, 'error');
    } finally {
      goBtn.disabled = false;
    }
  }
})();
