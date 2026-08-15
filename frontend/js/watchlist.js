import { api } from './api.js';
import { formatCurrency, formatPercent, showLoading, hideLoading, showToast } from './app.js';

let watchlistData = [];

const renderWatchlistTable = (items) => {
  const tbody = document.getElementById('watchlistTableBody');
  if (!tbody) return;

  if (items.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="8" class="text-center text-muted py-8">
          Nessun titolo presente nel radar. Clicca <strong>"➕ Aggiungi Titolo al Radar"</strong> per iniziare a monitorare azioni ed ETF.
        </td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = items.map(item => {
    const isUp = item.change_percent >= 0;
    const changeClass = isUp ? 'text-profit' : 'text-loss';
    const sign = isUp ? '+' : '';
    const pct52 = Math.max(0, Math.min(100, item.fifty_two_week_pct || 50));
    const marketFlag = item.market === 'IT' ? '🇮🇹' : '🇺🇸';

    return `
      <tr>
        <td>
          <div class="flex items-center gap-2">
            <span>${marketFlag}</span>
            <div>
              <a href="#" class="stock-ticker-link font-bold font-mono" data-stock="${item.ticker}">${item.ticker}</a>
              <div class="text-xs text-secondary">${item.name || item.ticker}</div>
              ${item.notes ? `<div class="text-[11px] text-muted mt-0.5">📝 ${item.notes}</div>` : ''}
            </div>
          </div>
        </td>
        <td class="text-right font-mono font-bold">${formatCurrency(item.current_price, item.currency)}</td>
        <td class="text-right font-mono font-bold ${changeClass}">
          ${sign}${formatCurrency(item.change_abs, item.currency)} (${sign}${formatPercent(item.change_percent)})
        </td>
        <td>
          <div class="range-bar-container">
            <div class="range-bar-track">
              <div class="range-bar-fill"></div>
              <div class="range-bar-pin" style="left: ${pct52}%;"></div>
            </div>
            <div class="range-bar-labels">
              <span>${formatCurrency(item.fifty_two_week_low, item.currency)}</span>
              <span>${formatCurrency(item.fifty_two_week_high, item.currency)}</span>
            </div>
          </div>
        </td>
        <td class="text-center font-mono">
          <span class="badge ${item.rsi_badge || 'badge-hold'}">${item.rsi || '--'}</span>
        </td>
        <td class="text-right font-mono text-secondary">${item.pe_ratio ? item.pe_ratio.toFixed(1) : '--'}</td>
        <td class="text-right font-mono ${item.dividend_yield ? 'text-profit' : 'text-secondary'}">
          ${item.dividend_yield ? `${item.dividend_yield.toFixed(2)}%` : '--'}
        </td>
        <td class="text-center">
          <div class="flex justify-center gap-1.5">
            <button class="btn btn-ghost btn-sm" title="Analizza con AI Gemini" onclick="window.openStockModalWithTab('${item.ticker}', 'tab-ai')">
              🤖
            </button>
            <button class="btn btn-ghost btn-sm" title="Porta nel Portafoglio" onclick="window.location.href='/static/portfolio.html?add=${encodeURIComponent(item.ticker)}'">
              💼
            </button>
            <button class="btn btn-ghost btn-sm text-loss" title="Rimuovi dal radar" onclick="window.removeWatchlistItem(${item.id}, '${item.ticker}')">
              🗑️
            </button>
          </div>
        </td>
      </tr>
    `;
  }).join('');
};

export const loadWatchlist = async () => {
  try {
    showLoading('watchlistContent');
    watchlistData = await api.getWatchlist().catch(() => []);

    // Stats
    const total = watchlistData.length;
    const gainers = watchlistData.filter(i => i.change_percent > 0).length;
    const losers = watchlistData.filter(i => i.change_percent < 0).length;
    const oversold = watchlistData.filter(i => i.rsi && i.rsi < 35).length;

    document.getElementById('statWatchlistCount').textContent = total;
    document.getElementById('statWatchlistGainers').textContent = gainers;
    document.getElementById('statWatchlistLosers').textContent = losers;
    document.getElementById('statWatchlistOversold').textContent = oversold;

    renderWatchlistTable(watchlistData);

  } catch (error) {
    showToast('Errore nel caricamento della Watchlist', 'error');
    console.error(error);
  } finally {
    hideLoading('watchlistContent');
  }
};

window.removeWatchlistItem = async (id, ticker) => {
  if (confirm(`Rimuovere ${ticker} dalla Watchlist?`)) {
    try {
      await api.removeFromWatchlist(id);
      showToast(`${ticker} rimosso dalla Watchlist`, 'info');
      loadWatchlist();
    } catch (e) {
      showToast('Errore durante la rimozione', 'error');
    }
  }
};

window.openStockModalWithTab = (ticker, tabName) => {
  window.openStockModal(ticker);
  setTimeout(() => {
    const tabBtn = document.querySelector(`.modal-tab-btn[data-tab="${tabName}"]`);
    if (tabBtn) tabBtn.click();
  }, 100);
};

// Modal & Autocomplete setup
const addModal = document.getElementById('addWatchlistModal');
const openAddModal = () => {
  document.getElementById('addWatchlistForm').reset();
  addModal.classList.add('active');
};
const closeAddModal = () => addModal.classList.remove('active');

document.addEventListener('DOMContentLoaded', () => {
  loadWatchlist();

  document.getElementById('btnOpenAddWatchlist').addEventListener('click', openAddModal);
  document.getElementById('closeAddWatchlistModal').addEventListener('click', closeAddModal);
  document.getElementById('cancelAddWatchlist').addEventListener('click', closeAddModal);

  // Search filter
  document.getElementById('watchlistSearch').addEventListener('input', (e) => {
    const q = e.target.value.trim().toUpperCase();
    if (!q) {
      renderWatchlistTable(watchlistData);
    } else {
      const filtered = watchlistData.filter(item => 
        item.ticker.toUpperCase().includes(q) || (item.name && item.name.toUpperCase().includes(q))
      );
      renderWatchlistTable(filtered);
    }
  });

  // Add form submit
  document.getElementById('addWatchlistForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const ticker = document.getElementById('wlTickerInput').value.trim().toUpperCase();
    const notes = document.getElementById('wlNotesInput').value.trim() || null;

    if (!ticker) return;

    try {
      const res = await api.addToWatchlist({ ticker, notes });
      showToast(res.message || `${ticker} aggiunto al Radar!`, 'success');
      closeAddModal();
      loadWatchlist();
    } catch (err) {
      showToast(err.message || 'Errore durante l\'aggiunta in Watchlist', 'error');
    }
  });

  // Autocomplete
  let timeout = null;
  const tickerInput = document.getElementById('wlTickerInput');
  const resultsDiv = document.getElementById('wlAutocompleteResults');

  tickerInput.addEventListener('input', (e) => {
    clearTimeout(timeout);
    const q = e.target.value.trim();
    if (q.length < 2) {
      resultsDiv.style.display = 'none';
      return;
    }
    timeout = setTimeout(async () => {
      try {
        const results = await api.searchStocks(q);
        if (results && results.length > 0) {
          resultsDiv.innerHTML = results.map(r => `
            <div style="padding: 10px 14px; cursor: pointer; border-bottom: 1px solid var(--border-color); transition: var(--transition);" 
                 onmouseover="this.style.backgroundColor='var(--surface-hover)'" 
                 onmouseout="this.style.backgroundColor='transparent'"
                 onclick="document.getElementById('wlTickerInput').value='${r.ticker}';document.getElementById('wlAutocompleteResults').style.display='none';">
              <strong class="text-primary">${r.ticker}</strong> — <span class="text-secondary">${r.name}</span>
            </div>
          `).join('');
          resultsDiv.style.display = 'block';
        } else {
          resultsDiv.style.display = 'none';
        }
      } catch (e) {
        resultsDiv.style.display = 'none';
      }
    }, 250);
  });
});
