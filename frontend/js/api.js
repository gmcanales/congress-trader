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
  };
})();
