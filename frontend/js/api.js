const API = (() => {
  const BASE = '/api';

  async function get(path, params = {}) {
    const url = new URL(BASE + path, window.location.origin);
    Object.entries(params).forEach(([k, v]) => {
      if (v !== null && v !== undefined && v !== '') url.searchParams.set(k, v);
    });
    const res = await fetch(url);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${res.status}`);
    }
    return res.json();
  }

  async function post(path, body) {
    const res = await fetch(BASE + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const b = await res.json().catch(() => ({}));
      throw new Error(b.detail || `HTTP ${res.status}`);
    }
    return res.json();
  }

  return {
    getTickers: () => get('/transactions/tickers'),
    getTrending: (months) => get('/transactions/trending', { months, limit: 30 }),
    getMembers: (q) => get('/members/', { q }),
    getTransactions: (params) => get('/transactions/', params),
    getPrices: (ticker, startDate, endDate) =>
      get(`/prices/${encodeURIComponent(ticker)}`, {
        start_date: startDate,
        end_date: endDate,
      }),
    triggerScrape: (year, maxDocs, chamber) =>
      post('/scrape/', { year, max_docs: maxDocs || null, chamber }),
    getDatasets: () => get('/datasets/'),
    createDataset: (name, description) => post('/datasets/', { name, description }),
    getRuns: () => get('/model/runs'),
    getRun: (id) => get(`/model/runs/${id}`),
    runAnalysis: (payload) => post('/model/run', payload),
    getRecommendationProfiles: () => get('/recommendations/profiles'),
    getRecommendations: (params) => get('/recommendations/', params),
    getDataFreshness: () => get('/recommendations/freshness'),
    getCacheStatus: () => get('/recommendations/cache'),
    refreshCache: () => post('/recommendations/refresh', {}),
    getStrategyComparison: (params) => get('/recommendations/strategies', params),
    getHoldings: () => get('/holdings/'),
    getPortfolioNav: (start, end) => get('/holdings/nav', { start, end }),
    getCsvSample: () => get('/holdings/csv/sample'),
    uploadCsv: (file, mode) => {
      const fd = new FormData();
      fd.append('file', file);
      return fetch(`/api/holdings/upload?mode=${encodeURIComponent(mode)}`, {
        method: 'POST', body: fd,
      }).then(async r => {
        if (!r.ok) {
          const b = await r.json().catch(() => ({}));
          throw new Error(b.detail || `HTTP ${r.status}`);
        }
        return r.json();
      });
    },
    listReports: () => get('/reports/'),
    listDocs: () => get('/docs/'),
    createHolding: (body) => post('/holdings/', body),
    updateHolding: (id, body) => fetch(`/api/holdings/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(async r => {
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        throw new Error(b.detail || `HTTP ${r.status}`);
      }
      return r.json();
    }),
    deleteHolding: (id) => fetch(`/api/holdings/${id}`, { method: 'DELETE' })
      .then(async r => {
        if (!r.ok) {
          const b = await r.json().catch(() => ({}));
          throw new Error(b.detail || `HTTP ${r.status}`);
        }
        return r.json();
      }),
  };
})();
