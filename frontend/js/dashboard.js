import { api } from './api.js';
import { formatCurrency, formatPercent, showLoading, hideLoading, showToast } from './app.js';

let chart = null;
let lineSeries = null;
let resizeObserver = null;
let currentChartDays = 30;
let activeBenchmark = 'none';
const benchSeriesMap = {};
const BENCH_COLORS = { '^GSPC': '#10b981', 'FTSEMIB.MI': '#f59e0b' };

// Cache dei valori precedenti per effetti flash green/red sugli stat
const _prevStatValues = new Map();
const setStatValue = (el, text, numericVal) => {
  if (!el) return;
  if (el.textContent !== text) {
    el.textContent = text;
  }
  if (numericVal !== undefined && !isNaN(numericVal)) {
    const prev = _prevStatValues.get(el.id);
    if (prev !== undefined && numericVal !== prev) {
      el.classList.remove('flash-up', 'flash-down');
      void el.offsetWidth; // reset animazione
      el.classList.add(numericVal > prev ? 'flash-up' : 'flash-down');
    }
    _prevStatValues.set(el.id, numericVal);
  }
};

const initChart = () => {
  const chartContainer = document.getElementById('portfolioChart');
  if (!chartContainer || typeof LightweightCharts === 'undefined') return;

  if (chart) {
    try { chart.remove(); } catch(e){}
  }

  chart = LightweightCharts.createChart(chartContainer, {
    layout: {
      background: { type: 'solid', color: 'transparent' },
      textColor: '#9aa0c2',
      fontFamily: 'Inter, system-ui, sans-serif',
      fontSize: 12
    },
    grid: {
      vertLines: { color: 'rgba(33, 37, 61, 0.5)' },
      horzLines: { color: 'rgba(33, 37, 61, 0.5)' },
    },
    rightPriceScale: {
      borderVisible: false,
      scaleMargins: { top: 0.1, bottom: 0.1 }
    },
    timeScale: {
      borderVisible: false,
      fixLeftEdge: true,
      fixRightEdge: true
    },
    crosshair: {
      vertLine: { color: '#3b82f6', width: 1, style: 3 },
      horzLine: { color: '#3b82f6', width: 1, style: 3 }
    },
    // Volume sub-chart below the price chart
    overlay: true
  });

  // Variabile per tenere traccia del tipo di serie corrente
  let currentSeriesType = 'area';

  // Crea la serie principale (candlestick)
  const priceSeries = chart.addCandleSeries({
    upColor: '#10b981',
    downColor: '#f43f5e',
    borderColor: '#6b7280',
    wickColor: '#6b7280'
  });

  // Serie volume in sovrapposizione (sotto il prezzo)
  const volumeSeries = chart.addHistogramSeries({
    color: '#3b82f6',
    lastValueVisible: false
  });

  // Linea per il prezzo medio di acquisto (breakeven)
  const breakevenLine = chart.createPriceLine({
    color: '#f59e0b',
    lineWidth: 1,
    title: 'Prezzo Medio Acquisto',
    axis: 'price',
    linestyle: 2,
    lastValueVisible: true,
    visible: false
  });

  // Serie benchmark (S&P 500 e FTSE MIB), attivate dai chip di confronto
  for (const [tk, color] of Object.entries(BENCH_COLORS)) {
    benchSeriesMap[tk] = chart.addLineSeries({
      color,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerVisible: false,
      visible: false
    });
  }

  // Funzione per caricare dati volume
  const updateVolumeData = (volumes) => {
    if (volumeSeries && volumes) {
      volumeSeries.setData(volumes);
    }
  };

  // Funzione per impostare il prezzo medio di acquisto (breakeven)
  const setBreakevenPrice = (price) => {
    if (breakevenLine) {
      breakevenLine.setPrice(price);
      breakevenLine.setVisible(price > 0);
    }
  };

  // Event listener per il toggle chart type
  const chartTypeGroup = document.getElementById('chartTypeGroup');
  if (chartTypeGroup) {
    chartTypeGroup.querySelectorAll('.chart-type-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        chartTypeGroup.querySelectorAll('.chart-type-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentSeriesType = btn.dataset.type;
        if (currentSeriesType === 'area') {
          priceSeries.setData([]);
        }
        // Modalità candlestick - i dati saranno popolati dai fetch
      });
    });
    chartTypeGroup.querySelector('.chart-type-btn[data-type="area"]').classList.add('active');
  }

  if (window.ResizeObserver) {
    if (resizeObserver) { try { resizeObserver.disconnect(); } catch(e){} }
    resizeObserver = new ResizeObserver(entries => {
      for (const entry of entries) {
        if (entry.contentRect.width > 0 && chart) {
          chart.applyOptions({
            width: entry.contentRect.width,
            height: chartContainer.clientHeight || 340
          });
        }
      }
    });
    resizeObserver.observe(chartContainer);
  }
};

// Global Marquee Ticker
export const initTickerMarquee = async () => {
  const mainContent = document.querySelector('.main-content');
  if (!mainContent) return;

  let tapeContainer = document.querySelector('.ticker-tape-container');
  if (!tapeContainer) {
    tapeContainer = document.createElement('div');
    tapeContainer.className = 'ticker-tape-container';
    tapeContainer.id = 'globalTickerTape';
    mainContent.insertBefore(tapeContainer, mainContent.firstChild);
  }

  try {
    const indices = await api.getIndices().catch(() => []);
    if (!indices || indices.length === 0) return;

    // Cache prezzi precedenti per flash green/red sul marquee
    let prevPrices = new Map();

    // Prezzi indici principali
    const indexMap = {
      'FTSEMIB.MI': { ticker: 'FTSEMIB.MI', name: 'FTSE MIB' },
      '^GSPC': { ticker: '^GSPC', name: 'S&P 500' }
    };

    // Aggiungi i tickers degli indici al ticker tape
    const tickerData = [];
    for (const [key, info] of Object.entries(indexMap)) {
      const idx = indices.find(i => i.ticker === key);
      if (idx) {
        tickerData.push({
          ticker: idx.ticker,
          name: idx.name,
          price: idx.close,
          change: idx.change_percent || 0
        });
      }
    }

    // Render iniziale
    const renderTicker = () => {
      tapeContainer.innerHTML = '';
      tickerData.sort((a, b) => Math.abs(b.change) - Math.abs(a.change));
      for (const item of tickerData) {
        const el = document.createElement('div');
        el.className = 'ticker-item';
        el.style.cssText = 'display: flex; align-items: center; padding: 4px 8px; white-space: nowrap; color: var(--text-primary);';
        const changeClass = item.change > 0 ? 'pulse-green' : 'pulse-red';
        el.innerHTML = `<span class="ticker-symbol ${changeClass}">${item.ticker}</span><span class="ticker-name" style="margin-left: 8px;">${item.name}</span><span class="ticker-price" style="margin-left: auto;">${formatCurrency(item.price, 'USD')}</span><span class="ticker-change ${changeClass}">${item.change >= 0 ? '+' : ''}${item.change.toFixed(2)}%</span>`;
        pulseClass = item.change > 0 ? 'pulse-green' : 'pulse-red';
        el.classList.add(changeClass);
        tapeContainer.appendChild(el);
      }
    };

    renderTicker();

    // Aggiornamento periodico dei prezzi
    const priceInterval = setInterval(async () => {
      try {
        const newIndices = await api.getIndices().catch(() => []);
        if (!newIndices || newIndices.length === 0) return;

        let hasUpdate = false;
        for (const item of tickerData) {
          const idx = newIndices.find(i => i.ticker === item.ticker);
          if (idx && idx.close !== item.price) {
            const prev = item.price;
            item.price = idx.close;
            item.change = idx.change_percent || 0;
            hasUpdate = true;
          }
        }

        if (hasUpdate) {
          renderTicker();
        }
      } catch (e) {
        console.error('Errore aggiornamento ticker:', e);
      }
    }, 60000);

    // Pulizia al logout/navigazione via
    const cleanup = () => {
      clearInterval(priceInterval);
    };
    window.addEventListener('beforeunload', cleanup);
    return () => cleanup();
  } catch (e) {
    console.error('Errore initTickerMarquee:', e);
  }
};

export const formatCurrency = (val, currency = 'EUR') => {
  if (val === null || val === undefined || isNaN(val)) return '-';
  const curr = currency === 'USD' ? 'USD' : 'EUR';
  return new Intl.NumberFormat('it-IT', {
    style: 'currency',
    currency: curr,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  }).format(val);
};

export const formatPercent = (val) => {
  if (val === null || val === undefined || isNaN(val)) return '-';
  const sign = val > 0 ? '+' : '';
  return `${sign}${val.toFixed(2)}%`;
};

export const formatCompactNumber = (val) => {
  if (!val || isNaN(val)) return '-';
  return new Intl.NumberFormat('it-IT', {
    notation: 'compact',
    maximumFractionDigits: 2
  }).format(val);
};

export const formatDate = (dateString) => {
  if (!dateString) return '-';
  return new Intl.DateTimeFormat('it-IT', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric'
  }).format(new Date(dateString));
};

export const formatDateTime = (dateString) => {
  if (!dateString) return '-';
  return new Intl.DateTimeFormat('it-IT', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  }).format(new Date(dateString));
};

export const showToast = (message, type = 'info') => {
  let container = document.querySelector('.toast-container');
  if (!container) {
    container = document.createElement('div');
    container.className = 'toast-container';
    document.body.appendChild(container);
  }

  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `<span>${message}</span>`;
  container.appendChild(toast);
  setTimeout(() => toast.classList.add('show'), 10);
  setTimeout(() => {
    toast.classList.remove('show');
    setTimeout(() => toast.remove(), 300);
  }, 3500);
};

export const showLoading = (elementId = null) => {
  if (elementId) {
    const el = document.getElementById(elementId);
    if (el) {
      let overlay = el.querySelector('.loader-overlay');
      if (!overlay) {
        overlay = document.createElement('div');
        overlay.className = 'loader-overlay';
        overlay.innerHTML = '<div class="spinner"></div>';
        el.style.position = 'relative';
        el.appendChild(overlay);
      }
      overlay.classList.add('active');
    }
  }
};

export const hideLoading = (elementId = null) => {
  if (elementId) {
    const el = document.getElementById(elementId);
    if (el) {
      const overlay = el.querySelector('.loader-overlay');
      if (overlay) overlay.classList.remove('active');
    }
  }
};

// Attiva gli effetti pulse sui prezzi che cambiano
export const flashPriceOnUpdate = (elementId, numericVal) => {
  const el = document.getElementById(elementId);
  if (!el) return;
  el.classList.add(numericVal > 0 ? 'pulse-green' : 'pulse-red');
  setTimeout(() => el.classList.remove(numericVal > 0 ? 'pulse-green' : 'pulse-red'), 400);
};

const checkAuth = async () => {
  if (window.location.pathname.includes('login.html')) return;

  try {
    const me = await api.getMe();
    if (me && me.username) {
      localStorage.setItem('auth_username', me.username);
    }
  } catch (e) {
    // Redirect handled by api.js
  }
};

const loadGoogleFont = () => {
  const link = document.createElement('link');
  link.href = 'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Roboto+Mono:wght@400;600;700&display=swap';
  link.rel = 'stylesheet';
  document.head.appendChild(link);
};

document.addEventListener('DOMContentLoaded', () => {
  loadGoogleFont();
  initSidebar();
  checkAuth();
  initTickerMarquee();
  injectStockModalHTML();

  // Attach global click listener for stock tickers
  document.addEventListener('click', (e) => {
    const target = e.target.closest('[data-stock], .stock-ticker-link');
    if (target) {
      const ticker = target.dataset.stock || target.textContent.trim();
      if (ticker) {
        e.preventDefault();
        openStockModal(ticker);
      }
    }
  });
});
