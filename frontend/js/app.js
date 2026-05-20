(() => {
  const chart = new TradeChart('main-chart', 'tooltip');

  // ── State ────────────────────────────────────────────────────────────────
  let allMembers = [];
  let selectedMemberId = null;
  let allTransactions = [];
  let currentTicker = null;

  // ── Init default date range (3 years) ────────────────────────────────────
  const today = new Date();
  const threeYrAgo = new Date();
  threeYrAgo.setFullYear(today.getFullYear() - 3);
  document.getElementById('start-date').value = _isoDate(threeYrAgo);
  document.getElementById('end-date').value   = _isoDate(today);

  // ── Trending tickers ─────────────────────────────────────────────────────
  const trendingList = document.getElementById('trending-list');
  const trendingWindow = document.getElementById('trending-window');

  async function loadTrending() {
    trendingList.innerHTML = '<li class="trending-empty">Loading…</li>';
    try {
      const months = parseInt(trendingWindow.value);
      const tickers = await API.getTrending(months);
      if (!tickers.length) {
        trendingList.innerHTML =
          '<li class="trending-empty">No trades yet — scrape a year via Admin.</li>';
        return;
      }
      trendingList.innerHTML = tickers.map(t => `
        <li class="trending-item" data-ticker="${t.ticker}">
          <div class="trending-row1">
            <span class="trending-ticker">${t.ticker}</span>
            <span class="trending-count">${t.trade_count} trades</span>
          </div>
          <div class="trending-row2">
            <span class="buy-color">▲ ${t.buy_count}</span>
            <span class="sell-color">▼ ${t.sell_count}</span>
            <span>· ${t.member_count} member${t.member_count === 1 ? '' : 's'}</span>
          </div>
        </li>`).join('');
    } catch (err) {
      trendingList.innerHTML =
        `<li class="trending-empty">Failed to load: ${err.message}</li>`;
    }
  }

  trendingList.addEventListener('click', e => {
    const li = e.target.closest('.trending-item');
    if (!li) return;
    document.getElementById('ticker-input').value = li.dataset.ticker;
    loadChart(li.dataset.ticker);
  });

  trendingWindow.addEventListener('change', loadTrending);

  // ── Member search ────────────────────────────────────────────────────────
  const memberSearch = document.getElementById('member-search');
  const memberListEl = document.getElementById('member-list');
  const selectedMemberEl = document.getElementById('selected-member');
  const selectedMemberName = document.getElementById('selected-member-name');
  let memberDebounce;

  memberSearch.addEventListener('input', () => {
    clearTimeout(memberDebounce);
    const q = memberSearch.value.trim();
    if (q.length < 2) { memberListEl.classList.add('hidden'); return; }
    memberDebounce = setTimeout(async () => {
      try {
        allMembers = await API.getMembers(q);
        memberListEl.innerHTML = allMembers.slice(0, 20).map(m =>
          `<li data-id="${m.id}">${m.last_name}, ${m.first_name} <small>${m.state_dst || ''}</small></li>`
        ).join('');
        memberListEl.classList.toggle('hidden', !allMembers.length);
      } catch (_) {}
    }, 300);
  });

  memberListEl.addEventListener('click', e => {
    const li = e.target.closest('li');
    if (!li) return;
    selectedMemberId = parseInt(li.dataset.id);
    const m = allMembers.find(x => x.id === selectedMemberId);
    if (m) {
      selectedMemberName.textContent = `${m.last_name}, ${m.first_name}`;
      selectedMemberEl.classList.remove('hidden');
      memberSearch.value = '';
      memberListEl.classList.add('hidden');
    }
  });

  document.getElementById('clear-member').addEventListener('click', () => {
    selectedMemberId = null;
    selectedMemberEl.classList.add('hidden');
    if (currentTicker) loadChart(currentTicker);
  });

  document.addEventListener('click', e => {
    if (!memberSearch.contains(e.target) && !memberListEl.contains(e.target))
      memberListEl.classList.add('hidden');
  });

  // ── Load chart ───────────────────────────────────────────────────────────
  const tickerInput = document.getElementById('ticker-input');
  document.getElementById('load-btn').addEventListener('click', () => loadChart());
  tickerInput.addEventListener('keydown', e => { if (e.key === 'Enter') loadChart(); });

  async function loadChart(ticker) {
    ticker = (ticker || tickerInput.value).trim().toUpperCase();
    if (!ticker) { tickerInput.focus(); return; }
    currentTicker = ticker;

    _highlightTrending(ticker);
    _showLoading(true);
    _showPlaceholder(false);
    _showError(null);
    document.getElementById('tx-table-wrap').classList.add('hidden');
    document.getElementById('stats-section').classList.add('hidden');

    const startDate = document.getElementById('start-date').value;
    const endDate   = document.getElementById('end-date').value;

    try {
      const [prices, transactions] = await Promise.all([
        API.getPrices(ticker, startDate, endDate),
        API.getTransactions({
          ticker,
          member_id: selectedMemberId || undefined,
          start_date: startDate,
          end_date: endDate,
        }),
      ]);

      if (!prices.length) {
        _showError(`No price data found for "${ticker}".`);
        return;
      }

      allTransactions = transactions;
      chart.render(prices, transactions);
      _updateStats(transactions);
      _populateTable(transactions);
      document.getElementById('tx-table-wrap').classList.remove('hidden');
      document.getElementById('stats-section').classList.remove('hidden');
    } catch (err) {
      _showError(err.message || 'Failed to load data.');
    } finally {
      _showLoading(false);
    }
  }

  function _highlightTrending(ticker) {
    document.querySelectorAll('.trending-item').forEach(li => {
      li.classList.toggle('active', li.dataset.ticker === ticker);
    });
  }

  // ── Table ────────────────────────────────────────────────────────────────
  const CONF_LABEL = { high: 'High', medium: 'Medium', low: 'Low' };

  function _populateTable(transactions) {
    const tbody = document.getElementById('tx-tbody');
    document.getElementById('tx-count').textContent = `(${transactions.length})`;

    tbody.innerHTML = transactions.map(tx => {
      const isBuy = tx.transaction_type === 'purchase';
      const typeClass = isBuy ? 'type-buy' : 'type-sell';
      const typeLabel = isBuy ? '▲ Buy'
        : tx.transaction_type === 'sale_partial' ? '▼ Sell (partial)' : '▼ Sell';
      const member = tx.member ? `${tx.member.last_name}, ${tx.member.first_name}` : '—';
      const owner = { SP: 'Spouse', JT: 'Joint', DC: 'Dep.' }[tx.owner] || 'Self';
      const conf = tx.confidence || 'high';
      return `
        <tr>
          <td>${tx.transaction_date}</td>
          <td>${member}</td>
          <td style="font-family:monospace;font-weight:600">${tx.ticker || '—'}</td>
          <td class="${typeClass}">${typeLabel}</td>
          <td>${_fmtAmount(tx.amount_min, tx.amount_max)}</td>
          <td>${owner}</td>
          <td title="Parse confidence: ${CONF_LABEL[conf]}">
            <span class="conf-badge conf-${conf}"></span>
            <span class="conf-text-${conf}">${CONF_LABEL[conf]}</span>
          </td>
        </tr>`;
    }).join('');
  }

  document.getElementById('tx-filter').addEventListener('input', function () {
    const q = this.value.toLowerCase();
    document.querySelectorAll('#tx-tbody tr').forEach(tr => {
      tr.style.display = tr.textContent.toLowerCase().includes(q) ? '' : 'none';
    });
  });

  // ── Stats ────────────────────────────────────────────────────────────────
  function _updateStats(transactions) {
    const buys  = transactions.filter(t => t.transaction_type === 'purchase').length;
    const sells = transactions.filter(t => t.transaction_type !== 'purchase').length;
    const members = new Set(transactions.map(t => t.member?.id)).size;
    document.getElementById('stat-buys').textContent    = buys;
    document.getElementById('stat-sells').textContent   = sells;
    document.getElementById('stat-members').textContent = members;

    const c = { high: 0, medium: 0, low: 0 };
    transactions.forEach(t => { c[t.confidence || 'high']++; });
    document.getElementById('stat-confidence').innerHTML =
      `<span class="conf-text-high">${c.high}H</span> ` +
      `<span class="conf-text-medium">${c.medium}M</span> ` +
      `<span class="conf-text-low">${c.low}L</span>`;
  }

  // ── Admin panel ──────────────────────────────────────────────────────────
  document.getElementById('admin-toggle').addEventListener('click', () => {
    document.getElementById('admin-panel').classList.toggle('hidden');
  });

  document.getElementById('scrape-btn').addEventListener('click', async () => {
    const year = parseInt(document.getElementById('scrape-year').value);
    const limit = parseInt(document.getElementById('scrape-limit').value) || null;
    const chamber = document.getElementById('scrape-chamber').value;
    const statusEl = document.getElementById('scrape-status');
    const btn = document.getElementById('scrape-btn');

    if (!year || year < 2012 || year > new Date().getFullYear()) {
      statusEl.textContent = 'Enter a valid year (2012–present).';
      statusEl.className = 'scrape-status err';
      return;
    }

    btn.disabled = true;
    statusEl.textContent = `Queuing ${chamber} scrape for ${year}…`;
    statusEl.className = 'scrape-status';

    try {
      const res = await API.triggerScrape(year, limit, chamber);
      statusEl.textContent =
        `${res.chamber} scrape queued for ${res.year}. Watch backend logs; reload trending when done.`;
      statusEl.className = 'scrape-status ok';
    } catch (err) {
      statusEl.textContent = `Error: ${err.message}`;
      statusEl.className = 'scrape-status err';
    } finally {
      btn.disabled = false;
    }
  });

  // ── Helpers ──────────────────────────────────────────────────────────────
  function _showLoading(show) {
    document.getElementById('loading').classList.toggle('hidden', !show);
  }
  function _showPlaceholder(show) {
    document.getElementById('placeholder').classList.toggle('hidden', !show);
  }
  function _showError(msg) {
    const el = document.getElementById('error-msg');
    if (msg) { el.textContent = msg; el.classList.remove('hidden'); }
    else el.classList.add('hidden');
  }
  function _isoDate(d) { return d.toISOString().slice(0, 10); }
  function _fmtAmount(min, max) {
    if (!min && !max) return '—';
    const fmt = v => v >= 1e6 ? `$${(v / 1e6).toFixed(1)}M` : `$${(v / 1e3).toFixed(0)}K`;
    return max ? `${fmt(min)} – ${fmt(max)}` : `> ${fmt(min)}`;
  }

  // ── Boot ─────────────────────────────────────────────────────────────────
  _showPlaceholder(true);
  loadTrending();

  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (currentTicker) loadChart(currentTicker); }, 250);
  });
})();
