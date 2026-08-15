export const API_BASE = '/api';

const fetchApi = async (endpoint, options = {}) => {
  const url = `${API_BASE}${endpoint}`;
  
  // Retrieve token from localStorage if available
  const token = localStorage.getItem('auth_token');
  
  const headers = {
    'Content-Type': 'application/json',
    ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
    ...(options.headers || {})
  };

  // Don't set Content-Type for FormData
  if (options.body instanceof FormData) {
    delete headers['Content-Type'];
  }

  try {
    const response = await fetch(url, { ...options, headers });
    
    // Auto-redirect to login on 401 Unauthorized if not already on login page
    if (response.status === 401) {
      localStorage.removeItem('auth_token');
      localStorage.removeItem('auth_username');
      if (!window.location.pathname.includes('login.html')) {
        const currentPath = window.location.pathname + window.location.search;
        window.location.href = `/static/login.html?redirect=${encodeURIComponent(currentPath)}`;
        return null;
      }
    }

    // For 204 No Content
    if (response.status === 204) return null;
    
    const data = await response.json();
    
    if (!response.ok) {
      throw new Error(data.detail || data.message || `API Error: ${response.status}`);
    }
    
    return data;
  } catch (error) {
    console.error(`API Call failed: ${endpoint}`, error);
    throw error;
  }
};

export const api = {
  // Auth
  login: async (username, password) => {
    return fetchApi('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password })
    });
  },
  logout: async () => {
    try {
      await fetchApi('/auth/logout', { method: 'POST' });
    } finally {
      localStorage.removeItem('auth_token');
      localStorage.removeItem('auth_username');
      window.location.href = '/static/login.html';
    }
  },
  getMe: () => fetchApi('/auth/me'),
  changePassword: (current_password, new_password) => {
    return fetchApi('/auth/change-password', {
      method: 'POST',
      body: JSON.stringify({ current_password, new_password })
    });
  },
  
  // Admin User Management
  getUsers: () => fetchApi('/auth/users'),
  createUser: (data) => fetchApi('/auth/users', {
    method: 'POST',
    body: JSON.stringify(data)
  }),
  deleteUser: (id) => fetchApi(`/auth/users/${id}`, { method: 'DELETE' }),
  resetUserPassword: (id, new_password) => fetchApi(`/auth/users/${id}/reset-password`, {
    method: 'PUT',
    body: JSON.stringify({ new_password })
  }),

  // Stocks & Deep-Dive
  getStocks: () => fetchApi('/stocks/'),
  addStock: (data) => fetchApi('/stocks/', { method: 'POST', body: JSON.stringify(data) }),
  deleteStock: (id) => fetchApi(`/stocks/${id}`, { method: 'DELETE' }),
  searchStocks: (query) => fetchApi(`/stocks/search?q=${encodeURIComponent(query)}`),
  getStockDetails: (ticker) => fetchApi(`/stocks/${encodeURIComponent(ticker)}/details`),
  getStockCandles: (ticker, timeframe = '1m') => fetchApi(`/stocks/${encodeURIComponent(ticker)}/candles?timeframe=${timeframe}`),
  getStockHistory: (id, days = 7) => fetchApi(`/stocks/${id}/history?days=${days}`),
  
  // Watchlist
  getWatchlist: () => fetchApi('/watchlist/'),
  addToWatchlist: (data) => fetchApi('/watchlist/', { method: 'POST', body: JSON.stringify(data) }),
  updateWatchlistAlert: (id, data) => fetchApi(`/watchlist/${id}/alert`, { method: 'PUT', body: JSON.stringify(data) }),
  removeFromWatchlist: (id) => fetchApi(`/watchlist/${id}`, { method: 'DELETE' }),
  removeWatchlistByTicker: (ticker) => fetchApi(`/watchlist/ticker/${encodeURIComponent(ticker)}`, { method: 'DELETE' }),

  // Portfolio
  getPortfolio: () => fetchApi('/portfolio/'),
  getPortfolioSummary: () => fetchApi('/portfolio/summary'),
  seedDemo: () => fetchApi('/portfolio/seed-demo', { method: 'POST' }),
  getRiskMetrics: (days = 180) => fetchApi(`/portfolio/risk-metrics?days=${days}`),
  getBenchmarks: (days = 90, tickers = null) => fetchApi(`/portfolio/benchmarks?days=${days}${tickers ? `&tickers=${encodeURIComponent(tickers)}` : ''}`),
  addHolding: (data) => fetchApi('/portfolio/holdings', { method: 'POST', body: JSON.stringify(data) }),
  updateHolding: (id, data) => fetchApi(`/portfolio/holdings/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteHolding: (id) => fetchApi(`/portfolio/holdings/${id}`, { method: 'DELETE' }),
  batchUpdateHoldings: (holdings) => fetchApi('/portfolio/batch', {
    method: 'PUT',
    body: JSON.stringify({ holdings })
  }),
  exportPortfolioUrl: (format = 'csv') => `${API_BASE}/portfolio/export?format=${format}`,

  // Rebalancer
  getRebalanceTargets: () => fetchApi('/portfolio/rebalance/targets'),
  addRebalanceTarget: (data) => fetchApi('/portfolio/rebalance/targets', { method: 'POST', body: JSON.stringify(data) }),
  deleteRebalanceTarget: (id) => fetchApi(`/portfolio/rebalance/targets/${id}`, { method: 'DELETE' }),
  rebalancePreview: (extra_cash = 0) => fetchApi('/portfolio/rebalance/preview', {
    method: 'POST',
    body: JSON.stringify({ extra_cash })
  }),
  
  importPortfolio: async (csvFile) => {
    const formData = new FormData();
    formData.append('file', csvFile);
    const token = localStorage.getItem('auth_token');
    const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
    
    const response = await fetch(`${API_BASE}/portfolio/import`, {
      method: 'POST',
      headers,
      body: formData
    });
    
    if (response.status === 401) {
      localStorage.removeItem('auth_token');
      window.location.href = `/static/login.html?redirect=${encodeURIComponent(window.location.pathname)}`;
      return null;
    }
    
    if (!response.ok) throw new Error('Import failed');
    return response.json();
  },
  
  // Dashboard & Live Markets
  getDashboard: () => fetchApi('/dashboard/'),
  getIndices: () => fetchApi('/dashboard/indices'),
  getHeatmap: () => fetchApi('/dashboard/heatmap'),
  getPerformance: (days = 30) => fetchApi(`/dashboard/performance?days=${days}`),
  
  // Advice & AI On-Demand
  getAdvice: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return fetchApi(`/advice${qs ? '?' + qs : ''}`);
  },
  getLatestAdvice: () => fetchApi('/advice/latest'),
  followAdvice: (id) => fetchApi(`/advice/${id}/follow`, { method: 'POST' }),
  generateAdvice: (force = false) => fetchApi(`/advice/generate${force ? '?force=true' : ''}`, { method: 'POST' }),
  analyzeStockOnDemand: (ticker) => fetchApi(`/advice/stock/${encodeURIComponent(ticker)}`, { method: 'POST' }),
  
  // Settings
  getSettings: () => fetchApi('/settings/'),
  updateSettings: (data) => fetchApi('/settings/', { method: 'PUT', body: JSON.stringify(data) }),
  getAlertRules: () => fetchApi('/settings/alerts'),
  addAlertRule: (data) => fetchApi('/settings/alerts', { method: 'POST', body: JSON.stringify(data) }),
  deleteAlertRule: (id) => fetchApi(`/settings/alerts/${id}`, { method: 'DELETE' }),
  testTelegram: () => fetchApi('/settings/telegram/test', { method: 'POST' })
};
