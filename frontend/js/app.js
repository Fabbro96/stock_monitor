import { api } from './api.js';

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

    const renderItems = (items) => items.map(idx => {
      const isUp = idx.change_percent >= 0;
      const changeClass = isUp ? 'up' : 'down';
      const sign = isUp ? '+' : '';
      return `
        <div class="ticker-item" onclick="window.openStockModal && window.openStockModal('${idx.ticker}')">
          <span>${idx.flag || '📊'}</span>
          <span class="ticker-name">${idx.name}</span>
          <span class="ticker-price">${idx.price}</span>
          <span class="ticker-change ${changeClass}">${sign}${idx.change_percent}%</span>
        </div>
      `;
    }).join('');

    // Duplicate track content for seamless infinite marquee loop
    tapeContainer.innerHTML = `
      <div class="ticker-tape-track">
        ${renderItems(indices)}
        ${renderItems(indices)}
      </div>
    `;
  } catch (e) {
    console.debug('Ticker marquee error:', e);
  }
};

// Global Stock Deep Dive Modal
let modalChart = null;
let modalAreaSeries = null;
let currentModalTicker = null;
let currentModalTimeframe = '1m';

const injectStockModalHTML = () => {
  if (document.getElementById('stockDeepDiveModal')) return;

  const modalEl = document.createElement('div');
  modalEl.className = 'modal-overlay';
  modalEl.id = 'stockDeepDiveModal';
  modalEl.innerHTML = `
    <div class="modal-content stock-modal-large">
      <div class="modal-header">
        <div class="flex items-center gap-3">
          <span style="font-size: 1.5rem;" id="smFlag">📈</span>
          <div>
            <div class="flex items-center gap-2">
              <h2 class="modal-title" id="smTicker" style="margin:0;">--</h2>
              <span class="badge" id="smMarketBadge">--</span>
            </div>
            <div class="text-xs text-secondary" id="smName">--</div>
          </div>
        </div>
        <div class="flex items-center gap-3">
          <div class="text-right">
            <div class="text-xl font-bold font-mono" id="smPrice">-- €</div>
            <div class="text-xs font-mono font-bold" id="smChange">--</div>
          </div>
          <button class="modal-close" id="closeStockModal">×</button>
        </div>
      </div>

      <!-- Modal Tabs -->
      <div class="modal-tabs">
        <button class="modal-tab-btn active" data-tab="tab-chart">📈 Grafico & Dati</button>
        <button class="modal-tab-btn" data-tab="tab-technicals">⚡ Indicatori Tecnici</button>
        <button class="modal-tab-btn" data-tab="tab-fundamentals">📊 Fondamentali</button>
        <button class="modal-tab-btn" data-tab="tab-ai">🤖 Analisi AI Gemini</button>
      </div>

      <!-- Tab Content: Chart -->
      <div id="tab-chart" class="modal-tab-panel">
        <div class="flex justify-between items-center mb-3 flex-wrap gap-2">
          <div class="timeframe-group" id="modalTimeframeGroup">
            <button class="timeframe-btn" data-tf="1d">1G</button>
            <button class="timeframe-btn" data-tf="1w">1S</button>
            <button class="timeframe-btn active" data-tf="1m">1M</button>
            <button class="timeframe-btn" data-tf="6m">6M</button>
            <button class="timeframe-btn" data-tf="1y">1A</button>
            <button class="timeframe-btn" data-tf="5y">5A</button>
          </div>
          <div class="flex gap-2">
            <button class="btn btn-ghost btn-sm" id="btnModalAddWatchlist">⭐ Salva in Watchlist</button>
            <button class="btn btn-primary btn-sm" id="btnModalAddHolding">➕ Aggiungi al Portafoglio</button>
          </div>
        </div>
        <div id="stockModalChart" style="width: 100%; height: 320px; border-radius: 8px; overflow: hidden; background: rgba(0,0,0,0.2);"></div>
      </div>

      <!-- Tab Content: Technicals -->
      <div id="tab-technicals" class="modal-tab-panel" style="display: none;">
        <div class="grid gap-3 mb-4" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));">
          <div class="card p-3" style="background: rgba(0,0,0,0.2);">
            <div class="text-xs text-muted mb-1">RSI (14 Periodi)</div>
            <div class="text-2xl font-bold font-mono" id="smRsiVal">--</div>
            <span class="badge mt-2" id="smRsiBadge">Neutro</span>
          </div>
          <div class="card p-3" style="background: rgba(0,0,0,0.2);">
            <div class="text-xs text-muted mb-1">Media Mobile 20 (SMA 20)</div>
            <div class="text-xl font-bold font-mono" id="smSma20">--</div>
            <div class="text-xs text-secondary mt-1">Trend breve termine</div>
          </div>
          <div class="card p-3" style="background: rgba(0,0,0,0.2);">
            <div class="text-xs text-muted mb-1">Media Mobile 50 (SMA 50)</div>
            <div class="text-xl font-bold font-mono" id="smSma50">--</div>
            <div class="text-xs text-secondary mt-1">Trend medio termine</div>
          </div>
          <div class="card p-3" style="background: rgba(0,0,0,0.2);">
            <div class="text-xs text-muted mb-1">Configurazione Trend</div>
            <div class="text-lg font-bold text-primary mt-1" id="smTrend">--</div>
          </div>
        </div>

        <div class="card p-4" style="background: rgba(0,0,0,0.2);">
          <div class="text-xs font-bold text-muted uppercase mb-2">Range 52 Settimane</div>
          <div class="range-bar-container">
            <div class="range-bar-track" style="height: 8px;">
              <div class="range-bar-fill"></div>
              <div class="range-bar-pin" id="sm52Pin" style="left: 50%;"></div>
            </div>
            <div class="range-bar-labels mt-1">
              <span>Min: <strong id="sm52Low">--</strong></span>
              <span id="sm52Pos">Posizione: 50%</span>
              <span>Max: <strong id="sm52High">--</strong></span>
            </div>
          </div>
        </div>
      </div>

      <!-- Tab Content: Fundamentals -->
      <div id="tab-fundamentals" class="modal-tab-panel" style="display: none;">
        <div class="grid gap-3 mb-4" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));">
          <div class="card p-3" style="background: rgba(0,0,0,0.2);">
            <div class="text-xs text-muted">Capitalizzazione</div>
            <div class="text-lg font-bold font-mono mt-1" id="smMarketCap">--</div>
          </div>
          <div class="card p-3" style="background: rgba(0,0,0,0.2);">
            <div class="text-xs text-muted">P/E Ratio (Trailing)</div>
            <div class="text-lg font-bold font-mono mt-1" id="smPe">--</div>
          </div>
          <div class="card p-3" style="background: rgba(0,0,0,0.2);">
            <div class="text-xs text-muted">EPS (Utile per Azione)</div>
            <div class="text-lg font-bold font-mono mt-1" id="smEps">--</div>
          </div>
          <div class="card p-3" style="background: rgba(0,0,0,0.2);">
            <div class="text-xs text-muted">Beta (Volatilità)</div>
            <div class="text-lg font-bold font-mono mt-1" id="smBeta">--</div>
          </div>
          <div class="card p-3" style="background: rgba(0,0,0,0.2);">
            <div class="text-xs text-muted">Dividend Yield</div>
            <div class="text-lg font-bold font-mono text-profit mt-1" id="smDivYield">--%</div>
          </div>
          <div class="card p-3" style="background: rgba(0,0,0,0.2);">
            <div class="text-xs text-muted">Volume Medio</div>
            <div class="text-lg font-bold font-mono mt-1" id="smVolume">--</div>
          </div>
        </div>
        <div class="card p-3 text-xs text-secondary leading-relaxed" id="smSummary" style="background: rgba(0,0,0,0.2); max-height: 120px; overflow-y: auto;">
          Nessuna descrizione disponibile per questa società.
        </div>
      </div>

      <!-- Tab Content: AI Analysis -->
      <div id="tab-ai" class="modal-tab-panel" style="display: none;">
        <div class="flex justify-between items-center mb-3">
          <div class="text-sm font-bold text-primary flex items-center gap-1.5">
            <span>🧠 Analisi Istantanea Gemini 3.7 Flash</span>
          </div>
          <button class="btn btn-primary btn-sm" id="btnRunStockAi">⚡ Elabora Analisi Ora</button>
        </div>
        <div id="stockAiResultContainer" class="card p-4" style="background: rgba(0,0,0,0.25); border-left: 3px solid var(--primary-color);">
          <div class="text-center text-muted py-6 text-sm">
            Clicca <strong>"Elabora Analisi Ora"</strong> per interrogare l'IA su fondamentali, indicatori tecnici e catalizzatori di mercato.
          </div>
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(modalEl);

  // Event Listeners for Stock Modal
  document.getElementById('closeStockModal').addEventListener('click', () => {
    modalEl.classList.remove('active');
  });

  modalEl.querySelectorAll('.modal-tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      modalEl.querySelectorAll('.modal-tab-btn').forEach(b => b.classList.remove('active'));
      modalEl.querySelectorAll('.modal-tab-panel').forEach(p => p.style.display = 'none');
      btn.classList.add('active');
      const targetPanel = document.getElementById(btn.dataset.tab);
      if (targetPanel) targetPanel.style.display = 'block';
    });
  });

  modalEl.querySelectorAll('.timeframe-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      modalEl.querySelectorAll('.timeframe-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentModalTimeframe = btn.dataset.tf;
      loadModalChart(currentModalTicker, currentModalTimeframe);
    });
  });

  document.getElementById('btnRunStockAi').addEventListener('click', runModalStockAi);

  document.getElementById('btnModalAddWatchlist').addEventListener('click', async () => {
    if (!currentModalTicker) return;
    try {
      const res = await api.addToWatchlist({ ticker: currentModalTicker });
      showToast(res.message || `${currentModalTicker} aggiunto alla Watchlist!`, 'success');
    } catch (e) {
      showToast(e.message || 'Errore salvataggio in Watchlist', 'error');
    }
  });

  document.getElementById('btnModalAddHolding').addEventListener('click', () => {
    modalEl.classList.remove('active');
    if (window.location.pathname.includes('portfolio.html')) {
      const tickerInput = document.getElementById('tickerInput');
      if (tickerInput) {
        tickerInput.value = currentModalTicker;
        document.getElementById('holdingModal')?.classList.add('active');
      }
    } else {
      window.location.href = `/static/portfolio.html?add=${encodeURIComponent(currentModalTicker)}`;
    }
  });
};

const initModalChart = () => {
  const container = document.getElementById('stockModalChart');
  if (!container || typeof LightweightCharts === 'undefined') return;

  if (modalChart) {
    try { modalChart.remove(); } catch(e){}
  }

  modalChart = LightweightCharts.createChart(container, {
    layout: {
      background: { type: 'solid', color: 'transparent' },
      textColor: '#9aa0c2',
      fontFamily: 'Inter, system-ui, sans-serif',
      fontSize: 11
    },
    grid: {
      vertLines: { color: 'rgba(33, 37, 61, 0.5)' },
      horzLines: { color: 'rgba(33, 37, 61, 0.5)' },
    },
    rightPriceScale: { borderVisible: false },
    timeScale: { borderVisible: false }
  });

  modalAreaSeries = modalChart.addAreaSeries({
    topColor: 'rgba(59, 130, 246, 0.4)',
    bottomColor: 'rgba(59, 130, 246, 0.01)',
    lineColor: '#3b82f6',
    lineWidth: 2,
  });
};

const loadModalChart = async (ticker, timeframe = '1m') => {
  if (!modalAreaSeries || !ticker) return;
  try {
    const candles = await api.getStockCandles(ticker, timeframe);
    if (candles && candles.length > 0) {
      modalAreaSeries.setData(candles.map(c => ({ time: c.time, value: c.close })));
      modalChart.timeScale().fitContent();
    }
  } catch (e) {
    console.error('Errore caricamento candele modale:', e);
  }
};

const runModalStockAi = async () => {
  if (!currentModalTicker) return;
  const container = document.getElementById('stockAiResultContainer');
  const btn = document.getElementById('btnRunStockAi');
  
  btn.disabled = true;
  btn.textContent = 'Analisi in corso...';
  container.innerHTML = '<div class="flex justify-center items-center py-8"><div class="spinner"></div></div>';

  try {
    const result = await api.analyzeStockOnDemand(currentModalTicker);
    const actionBadgeClass = result.action === 'ACCUMULO' || result.action === 'BUY' ? 'badge-buy' : (result.action === 'PRESA_PROFITTO' || result.action === 'SELL' ? 'badge-sell' : 'badge-hold');

    container.innerHTML = `
      <div>
        <div class="flex justify-between items-center mb-3">
          <span class="badge ${actionBadgeClass}" style="font-size: 0.85rem; padding: 4px 10px;">${result.action_label || result.action}</span>
          <div class="text-xs text-muted">Confidenza: <strong class="text-primary">${result.confidence || 'MEDIA'}</strong> • Orizzonte: <strong class="text-primary">${result.timeframe || 'Medio Termine'}</strong></div>
        </div>

        <div class="grid gap-3 mb-3" style="display: grid; grid-template-columns: 1fr 1fr;">
          <div class="p-2 rounded border border-border-color" style="background: rgba(0,0,0,0.2);">
            <div class="text-xs text-muted">🎯 Target Price Stimato</div>
            <div class="text-lg font-bold text-primary font-mono">${formatCurrency(result.target_price)} <span class="text-xs text-profit">(+${result.upside_potential_pct || 0}%)</span></div>
          </div>
          <div class="p-2 rounded border border-border-color" style="background: rgba(0,0,0,0.2);">
            <div class="text-xs text-muted">🛡️ Stop Loss Consigliato</div>
            <div class="text-lg font-bold text-danger font-mono">${result.stop_loss ? formatCurrency(result.stop_loss) : '--'}</div>
          </div>
        </div>

        <p class="text-sm text-primary leading-relaxed mb-3">${result.summary || ''}</p>

        <div class="grid gap-2 text-xs mb-3" style="display: grid; grid-template-columns: 1fr 1fr;">
          <div class="p-2.5 rounded" style="background: rgba(16, 185, 129, 0.08); border-left: 2px solid var(--success-color);">
            <strong class="text-profit block mb-1">🟢 Bull Case & Punti di Forza</strong>
            <span class="text-secondary leading-normal">${result.bull_case || '--'}</span>
          </div>
          <div class="p-2.5 rounded" style="background: rgba(244, 63, 94, 0.08); border-left: 2px solid var(--danger-color);">
            <strong class="text-loss block mb-1">🔴 Bear Case & Rischi Chiave</strong>
            <span class="text-secondary leading-normal">${result.bear_case || '--'}</span>
          </div>
        </div>

        <div class="p-2.5 rounded border border-border-color" style="background: rgba(0,0,0,0.3);">
          <strong class="text-xs text-primary block mb-1">💡 Strategia Operativa Suggerita</strong>
          <span class="text-xs text-secondary leading-normal">${result.operational_strategy || '--'}</span>
        </div>
      </div>
    `;
  } catch (e) {
    container.innerHTML = `<div class="alert-error text-center py-4 text-xs">Impossibile completare l'analisi per ${currentModalTicker}: ${e.message}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = '⚡ Rielabora Analisi';
  }
};

export const openStockModal = async (ticker) => {
  if (!ticker) return;
  injectStockModalHTML();
  
  currentModalTicker = ticker.trim().toUpperCase();
  const modal = document.getElementById('stockDeepDiveModal');
  modal.classList.add('active');

  // Reset modal tab to first
  modal.querySelectorAll('.modal-tab-btn').forEach((b, idx) => {
    if (idx === 0) b.classList.add('active');
    else b.classList.remove('active');
  });
  modal.querySelectorAll('.modal-tab-panel').forEach((p, idx) => {
    p.style.display = idx === 0 ? 'block' : 'none';
  });

  // Reset header data
  document.getElementById('smTicker').textContent = currentModalTicker;
  document.getElementById('smName').textContent = 'Caricamento dati...';
  document.getElementById('smPrice').textContent = '--';
  document.getElementById('smChange').textContent = '--';

  initModalChart();
  loadModalChart(currentModalTicker, currentModalTimeframe);

  try {
    const data = await api.getStockDetails(currentModalTicker);
    document.getElementById('smName').textContent = data.name || currentModalTicker;
    document.getElementById('smPrice').textContent = formatCurrency(data.current_price, data.currency);
    
    const changeEl = document.getElementById('smChange');
    const isUp = data.change_percent >= 0;
    changeEl.textContent = `${isUp ? '+' : ''}${data.change_abs} (${formatPercent(data.change_percent)})`;
    changeEl.className = `text-xs font-mono font-bold ${isUp ? 'text-profit' : 'text-loss'}`;

    const marketBadge = document.getElementById('smMarketBadge');
    marketBadge.textContent = data.market || 'US';
    marketBadge.className = `badge ${data.market === 'IT' ? 'badge-buy' : 'badge-cyan'}`;

    document.getElementById('smFlag').textContent = data.market === 'IT' ? '🇮🇹' : '🇺🇸';

    // Technicals
    const tech = data.technical || {};
    document.getElementById('smRsiVal').textContent = tech.rsi_14 || '--';
    const rsiBadge = document.getElementById('smRsiBadge');
    rsiBadge.textContent = tech.rsi_status || 'Neutro';
    rsiBadge.className = `badge ${tech.rsi_badge || 'badge-hold'}`;

    document.getElementById('smSma20').textContent = tech.sma_20 ? formatCurrency(tech.sma_20, data.currency) : '--';
    document.getElementById('smSma50').textContent = tech.sma_50 ? formatCurrency(tech.sma_50, data.currency) : '--';
    document.getElementById('smTrend').textContent = tech.trend || 'Neutro';

    // 52W Range
    document.getElementById('sm52Low').textContent = formatCurrency(data.fifty_two_week_low, data.currency);
    document.getElementById('sm52High').textContent = formatCurrency(data.fifty_two_week_high, data.currency);
    const pin = document.getElementById('sm52Pin');
    const pct = Math.max(0, Math.min(100, data.fifty_two_week_pct || 50));
    pin.style.left = `${pct}%`;
    document.getElementById('sm52Pos').textContent = `Posizione: ${pct}%`;

    // Fundamentals
    document.getElementById('smMarketCap').textContent = formatCompactNumber(data.market_cap);
    document.getElementById('smPe').textContent = data.pe_ratio || '--';
    document.getElementById('smEps').textContent = data.eps ? formatCurrency(data.eps, data.currency) : '--';
    document.getElementById('smBeta').textContent = data.beta || '--';
    document.getElementById('smDivYield').textContent = data.dividend_yield ? `${data.dividend_yield}%` : '--%';
    document.getElementById('smVolume').textContent = formatCompactNumber(data.avg_volume || data.volume);
    document.getElementById('smSummary').textContent = data.summary || 'Nessuna descrizione disponibile.';

  } catch (e) {
    console.error('Errore recupero dettagli titolo:', e);
  }
};

window.openStockModal = openStockModal;

const initSidebar = () => {
  const currentPath = window.location.pathname;
  const links = document.querySelectorAll('.nav-link');
  
  links.forEach(link => {
    const href = link.getAttribute('href');
    if (currentPath.endsWith(href) || (currentPath === '/' && href === '/static/index.html')) {
      link.classList.add('active');
    } else {
      link.classList.remove('active');
    }
  });

  const toggle = document.querySelector('.mobile-toggle');
  const sidebar = document.querySelector('.sidebar');
  if (toggle && sidebar) {
    toggle.addEventListener('click', () => {
      sidebar.classList.toggle('open');
    });
  }

  // Add User Footer to Sidebar
  if (sidebar && !sidebar.querySelector('.sidebar-footer')) {
    const username = localStorage.getItem('auth_username') || 'Utente';
    const footer = document.createElement('div');
    footer.className = 'sidebar-footer';
    footer.style.cssText = 'padding: 16px 20px; border-top: 1px solid var(--border-color); display: flex; align-items: center; justify-content: space-between; font-size: 0.85rem;';
    footer.innerHTML = `
      <div style="display: flex; align-items: center; gap: 8px; overflow: hidden;">
        <span style="font-size: 1.1rem;">👤</span>
        <span style="font-weight: 600; color: var(--text-primary); text-overflow: ellipsis; overflow: hidden; white-space: nowrap;">${username}</span>
      </div>
      <button id="btnLogout" title="Disconnetti" style="background: none; border: none; color: var(--text-muted); cursor: pointer; font-size: 1.1rem; padding: 4px 6px; border-radius: 4px; transition: var(--transition);" onmouseover="this.style.color='var(--danger-color)'" onmouseout="this.style.color='var(--text-muted)'">
        🚪
      </button>
    `;
    sidebar.appendChild(footer);

    const btnLogout = footer.querySelector('#btnLogout');
    if (btnLogout) {
      btnLogout.addEventListener('click', async () => {
        if (confirm('Sei sicuro di voler effettuare il logout?')) {
          await api.logout();
        }
      });
    }
  }
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
