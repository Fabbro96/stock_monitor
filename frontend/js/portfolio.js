import { api } from './api.js';
import { formatCurrency, formatPercent, showLoading, hideLoading, showToast } from './app.js';

let portfolioData = [];
let summaryData = {};
let modifiedHoldings = new Map();
let currentAllocView = localStorage.getItem('portfolio_alloc_view') || 'stock'; // 'stock' or 'market'

const colors = ['#3b82f6', '#10b981', '#f43f5e', '#f59e0b', '#8b5cf6', '#06b6d4', '#ec4899', '#6366f1', '#14b8a6'];

const renderSkeletons = () => {
  const tbody = document.getElementById('portfolioTableBody');
  if (tbody) {
    tbody.innerHTML = `
      <tr><td colspan="9"><div class="skeleton skeleton-row"></div></td></tr>
      <tr><td colspan="9"><div class="skeleton skeleton-row"></div></td></tr>
      <tr><td colspan="9"><div class="skeleton skeleton-row"></div></td></tr>
    `;
  }
};

const drawPieChart = (data) => {
  const canvas = document.getElementById('allocationChart');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  
  const dpr = window.devicePixelRatio || 1;
  const displayWidth = 190;
  const displayHeight = 190;

  if (canvas.width !== displayWidth * dpr || canvas.height !== displayHeight * dpr) {
    canvas.width = displayWidth * dpr;
    canvas.height = displayHeight * dpr;
    canvas.style.width = `${displayWidth}px`;
    canvas.style.height = `${displayHeight}px`;
  }
  
  ctx.save();
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, displayWidth, displayHeight);
  
  const centerX = displayWidth / 2;
  const centerY = displayHeight / 2;
  const outerRadius = Math.min(centerX, centerY) - 8;
  const innerRadius = outerRadius * 0.58;

  if (!data || data.length === 0) {
    ctx.fillStyle = 'rgba(122, 162, 247, 0.15)';
    ctx.beginPath();
    ctx.arc(centerX, centerY, outerRadius, 0, 2 * Math.PI);
    ctx.fill();
    ctx.restore();
    const legend = document.getElementById('allocationLegend');
    if (legend) legend.innerHTML = '<div class="text-muted text-center text-xs">Nessun dato</div>';
    return;
  }

  const total = data.reduce((sum, item) => sum + (item.value || 0), 0);
  if (total <= 0) {
    ctx.restore();
    return;
  }

  let startAngle = -0.5 * Math.PI;

  data.forEach((item, i) => {
    const sliceAngle = (item.value / total) * 2 * Math.PI;
    ctx.fillStyle = colors[i % colors.length];
    ctx.beginPath();
    ctx.arc(centerX, centerY, outerRadius, startAngle, startAngle + sliceAngle);
    ctx.arc(centerX, centerY, innerRadius, startAngle + sliceAngle, startAngle, true);
    ctx.closePath();
    ctx.fill();
    startAngle += sliceAngle;
  });

  ctx.restore();

  const legend = document.getElementById('allocationLegend');
  if (legend) {
    legend.innerHTML = data.slice(0, 7).map((item, i) => `
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-2">
          <div style="width:10px;height:10px;background-color:${colors[i % colors.length]};border-radius:3px;"></div>
          <span class="font-bold text-primary font-mono">${item.label}</span>
        </div>
        <span class="font-mono text-secondary">${formatPercent((item.value / total) * 100)}</span>
      </div>
    `).join('');
  }
};

const updateAllocationChart = () => {
  if (currentAllocView === 'market') {
    const allocMap = summaryData.market_allocation || {};
    const marketLabels = { 'IT': '🇮🇹 Italia', 'US': '🇺🇸 USA', 'EU': '🇪🇺 Europa' };
    const data = Object.entries(allocMap).map(([k, v]) => ({
      label: marketLabels[k] || k,
      value: v
    })).filter(d => d.value > 0);
    drawPieChart(data);
  } else {
    const data = portfolioData.map(item => ({
      label: item.ticker,
      value: item.total_value || ((item.current_price || item.avg_purchase_price) * item.quantity)
    })).filter(d => d.value > 0);
    drawPieChart(data);
  }
};

const updateSaveBar = () => {
  const saveBar = document.getElementById('saveBar');
  const countEl = document.getElementById('pendingChangesCount');
  if (!saveBar) return;
  
  const count = modifiedHoldings.size;
  if (count > 0) {
    saveBar.style.display = 'flex';
    countEl.textContent = `Hai ${count} posizion${count === 1 ? 'e modificata' : 'i modificate'}. Clicca "Salva Modifiche" per applicarle.`;
  } else {
    saveBar.style.display = 'none';
  }
};

const triggerSeedDemo = async () => {
  try {
    showLoading('portfolioContent');
    const res = await api.seedDemo();
    showToast(res.message || 'Demo inizializzata con successo!', 'success');
    loadPortfolio();
  } catch (e) {
    showToast(e.message || 'Errore nel caricamento della demo', 'error');
  } finally {
    hideLoading('portfolioContent');
  }
};

const renderTable = () => {
  const tbody = document.getElementById('portfolioTableBody');
  if (!tbody) return;
  
  if (portfolioData.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="9" class="text-center text-muted py-8">
          Nessun titolo in portafoglio. 
          <div class="mt-3 flex justify-center gap-2">
            <button class="btn btn-primary" onclick="window.openAddHoldingModal()">➕ Aggiungi Holding</button>
            <button class="btn btn-ghost" id="btnEmptySeedDemo">🚀 Inizializza Demo</button>
          </div>
        </td>
      </tr>
    `;
    tbody.querySelector('#btnEmptySeedDemo')?.addEventListener('click', triggerSeedDemo);
    return;
  }

  tbody.innerHTML = portfolioData.map(item => {
    const isModified = modifiedHoldings.has(item.id);
    const mod = modifiedHoldings.get(item.id);
    
    const displayQty = mod ? mod.newQty : item.quantity;
    const displayPrice = mod ? mod.newPrice : item.avg_purchase_price;
    const currentPrice = item.current_price || displayPrice;
    
    const totalValue = displayQty * currentPrice;
    const invested = displayQty * displayPrice;
    const pnlAbs = totalValue - invested;
    const pnlPct = invested > 0 ? (pnlAbs / invested) * 100 : 0;
    const flag = item.market === 'IT' ? '🇮🇹' : '🇺🇸';

    return `
      <tr class="${isModified ? 'row-modified' : ''}" data-id="${item.id}">
        <td>
          <div class="flex items-center gap-2">
            <span>${flag}</span>
            <div>
              <a href="#" class="stock-ticker-link font-bold font-mono" data-stock="${item.ticker}">${item.ticker}</a>
              ${item.notes ? `<div class="text-[11px] text-muted" title="${item.notes}">📝 ${item.notes.substring(0, 20)}</div>` : ''}
            </div>
          </div>
        </td>
        <td class="text-secondary">${item.name || item.ticker}</td>
        
        <!-- Editable Quantity -->
        <td class="text-right">
          <div class="modern-stepper">
            <button type="button" class="stepper-btn dec" data-step="1" title="Diminuisci (−1, Shift: −10)" aria-label="Diminuisci quantità per ${item.ticker}">−</button>
            <input 
              type="number" 
              class="inline-input input-qty font-mono" 
              data-id="${item.id}" 
              value="${displayQty}" 
              step="1" 
              min="0"
              aria-label="Quantità per ${item.ticker}"
            >
            <button type="button" class="stepper-btn inc" data-step="1" title="Aumenta (+1, Shift: +10)" aria-label="Aumenta quantità per ${item.ticker}">+</button>
          </div>
        </td>

        <!-- Editable Purchase Price -->
        <td class="text-right">
          <div class="modern-stepper">
            <button type="button" class="stepper-btn dec" data-step="0.5" title="Diminuisci (−0.50, Shift: −5.00)" aria-label="Diminuisci prezzo di carico per ${item.ticker}">−</button>
            <input 
              type="number" 
              class="inline-input input-price font-mono" 
              data-id="${item.id}" 
              value="${displayPrice}" 
              step="any" 
              min="0"
              aria-label="Prezzo medio carico per ${item.ticker}"
            >
            <button type="button" class="stepper-btn inc" data-step="0.5" title="Aumenta (+0.50, Shift: +5.00)" aria-label="Aumenta prezzo di carico per ${item.ticker}">+</button>
          </div>
        </td>

        <td class="text-right font-mono font-bold">${formatCurrency(currentPrice, item.currency)}</td>
        <td class="text-right font-mono font-bold text-primary" id="val-${item.id}">${formatCurrency(totalValue, item.currency)}</td>
        <td class="text-right font-mono font-bold ${pnlAbs >= 0 ? 'text-profit' : 'text-loss'}" id="pnlabs-${item.id}">
          ${formatCurrency(pnlAbs, item.currency)}
        </td>
        <td class="text-right font-mono font-bold ${pnlPct >= 0 ? 'text-profit' : 'text-loss'}" id="pnlpct-${item.id}">
          ${formatPercent(pnlPct)}
        </td>
        <td class="text-center">
          <div class="flex justify-center gap-1">
            <button class="btn btn-ghost btn-sm text-loss" title="Elimina" aria-label="Elimina posizione ${item.ticker}" onclick="window.deleteHolding(${item.id})">🗑️</button>
          </div>
        </td>
      </tr>
    `;
  }).join('');

  tbody.querySelectorAll('.input-qty, .input-price').forEach(input => {
    input.addEventListener('input', handleInlineEdit);
  });
};

const handleInlineEdit = (e) => {
  const input = e.target;
  const holdingId = parseInt(input.dataset.id);
  const row = input.closest('tr');
  
  const item = portfolioData.find(h => h.id === holdingId);
  if (!item) return;

  const qtyInput = row.querySelector('.input-qty');
  const priceInput = row.querySelector('.input-price');

  const newQty = parseFloat(qtyInput.value) || 0;
  const newPrice = parseFloat(priceInput.value) || 0;

  const isChanged = (newQty !== item.quantity) || (Math.abs(newPrice - item.avg_purchase_price) > 0.0001);

  if (isChanged) {
    modifiedHoldings.set(holdingId, {
      id: holdingId,
      ticker: item.ticker,
      originalQty: item.quantity,
      newQty: newQty,
      originalPrice: item.avg_purchase_price,
      newPrice: newPrice,
      notes: item.notes
    });
    row.classList.add('row-modified');
  } else {
    modifiedHoldings.delete(holdingId);
    row.classList.remove('row-modified');
  }

  const currentPrice = item.current_price || newPrice;
  const totalValue = newQty * currentPrice;
  const invested = newQty * newPrice;
  const pnlAbs = totalValue - invested;
  const pnlPct = invested > 0 ? (pnlAbs / invested) * 100 : 0;

  const valEl = document.getElementById(`val-${holdingId}`);
  const pnlAbsEl = document.getElementById(`pnlabs-${holdingId}`);
  const pnlPctEl = document.getElementById(`pnlpct-${holdingId}`);

  if (valEl) valEl.textContent = formatCurrency(totalValue, item.currency);
  if (pnlAbsEl) {
    pnlAbsEl.textContent = formatCurrency(pnlAbs, item.currency);
    pnlAbsEl.className = `text-right font-mono font-bold ${pnlAbs >= 0 ? 'text-profit' : 'text-loss'}`;
  }
  if (pnlPctEl) {
    pnlPctEl.textContent = formatPercent(pnlPct);
    pnlPctEl.className = `text-right font-mono font-bold ${pnlPct >= 0 ? 'text-profit' : 'text-loss'}`;
  }

  updateSaveBar();
};

export const loadPortfolio = async () => {
  try {
    renderSkeletons();
    summaryData = await api.getPortfolioSummary().catch(() => ({}));
    portfolioData = await api.getPortfolio().catch(() => []);

    document.getElementById('totalValue').textContent = formatCurrency(summaryData.total_value || 0);
    document.getElementById('totalInvested').textContent = formatCurrency(summaryData.total_invested || 0);
    document.getElementById('totalCount').textContent = summaryData.holdings_count || portfolioData.length;
    
    const pnlEl = document.getElementById('totalPnL');
    const totPnL = summaryData.total_pnl || 0;
    const totPct = summaryData.total_pnl_percent || 0;
    pnlEl.textContent = `${formatCurrency(totPnL)} (${formatPercent(totPct)})`;
    pnlEl.className = `text-2xl font-bold font-mono mt-1 ${totPnL >= 0 ? 'text-profit' : 'text-loss'}`;

    const divEl = document.getElementById('totalDividends');
    if (divEl) {
      divEl.textContent = `${formatCurrency(summaryData.estimated_annual_dividends || 0)}/anno (${(summaryData.estimated_dividend_yield || 0).toFixed(2)}%)`;
    }

    modifiedHoldings.clear();
    updateSaveBar();
    renderTable();
    updateAllocationChart();
    loadRealizedPnL();
    loadTransactions();

  } catch (error) {
    showToast('Errore nel caricamento del portafoglio', 'error');
  }
};

export const loadRealizedPnL = async () => {
  try {
    const res = await api.getRealizedPnL();
    const el = document.getElementById('totalRealizedPnL');
    if (el && res) {
      const net = res.net_realized_profit || 0;
      el.textContent = `${net >= 0 ? '+' : ''}${formatCurrency(net)}`;
      el.className = `text-2xl font-bold font-mono mt-1 ${net >= 0 ? 'text-profit' : 'text-loss'}`;
    }
  } catch (e) {
    console.error('Error loading realized PnL:', e);
  }
};

let currentTxFilter = 'ALL';

export const loadTransactions = async (type = currentTxFilter) => {
  currentTxFilter = type;
  const tbody = document.getElementById('transactionsTableBody');
  if (!tbody) return;

  try {
    const params = type !== 'ALL' ? { type } : {};
    const txs = await api.getTransactions(params);

    if (!txs || txs.length === 0) {
      tbody.innerHTML = `
        <tr>
          <td colspan="10" class="text-center text-muted py-6">
            Nessuna transazione registrata ${type !== 'ALL' ? `con filtro <strong>${type}</strong>` : ''}.
            <div class="mt-2">
              <button class="btn btn-primary btn-sm" onclick="window.openTxModal()">➕ Registra la prima esecuzione</button>
            </div>
          </td>
        </tr>
      `;
      return;
    }

    tbody.innerHTML = txs.map(tx => {
      const isBuy = tx.type === 'BUY';
      const isSell = tx.type === 'SELL';
      const isDiv = tx.type === 'DIVIDEND';

      const typeBadge = isBuy 
        ? '<span class="badge badge-buy">🟢 BUY</span>'
        : (isSell ? '<span class="badge badge-sell">🔴 SELL</span>' : '<span class="badge badge-hold">💰 DIVIDENDO</span>');

      const dateStr = tx.transaction_date ? tx.transaction_date.substring(0, 10) : '--';
      
      let pnlHtml = '--';
      if (isSell && tx.realized_pnl !== null) {
        const pnl = tx.realized_pnl;
        pnlHtml = `<span class="${pnl >= 0 ? 'text-profit' : 'text-loss'} font-bold font-mono">${pnl >= 0 ? '+' : ''}${formatCurrency(pnl, tx.currency)}</span>`;
      } else if (isDiv && tx.realized_pnl !== null) {
        pnlHtml = `<span class="text-profit font-bold font-mono">+${formatCurrency(tx.realized_pnl, tx.currency)}</span>`;
      }

      return `
        <tr>
          <td class="font-mono text-xs text-muted">${dateStr}</td>
          <td>${typeBadge}</td>
          <td>
            <a href="#" class="stock-ticker-link font-bold font-mono" data-stock="${tx.ticker}">${tx.ticker}</a>
          </td>
          <td class="text-secondary text-xs">${tx.name || tx.ticker}</td>
          <td class="text-right font-mono">${isDiv ? '--' : tx.quantity}</td>
          <td class="text-right font-mono">${formatCurrency(tx.price, tx.currency)}</td>
          <td class="text-right font-mono text-muted text-xs">${tx.fee > 0 ? formatCurrency(tx.fee, 'EUR') : '0 €'}</td>
          <td class="text-right font-mono">${pnlHtml}</td>
          <td class="text-xs text-muted" title="${tx.notes || ''}">${tx.notes ? tx.notes.substring(0, 25) : '--'}</td>
          <td class="text-center">
            <button class="btn btn-ghost btn-sm text-loss" title="Elimina transazione" aria-label="Elimina transazione #${tx.id}" onclick="window.deleteTransaction(${tx.id})">🗑️</button>
          </td>
        </tr>
      `;
    }).join('');
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="10" class="text-center text-loss py-4">Errore nel caricamento del Trade Ledger</td></tr>`;
  }
};

window.deleteTransaction = async (id) => {
  if (!confirm(`Sei sicuro di voler eliminare la transazione #${id}?`)) return;
  try {
    await api.deleteTransaction(id);
    showToast('Transazione rimossa dal registro', 'info');
    loadTransactions();
    loadRealizedPnL();
  } catch (e) {
    showToast(e.message || 'Errore durante la cancellazione', 'error');
  }
};

window.deleteHolding = async (id) => {
  const item = portfolioData.find(h => h.id === id);
  const ticker = item ? item.ticker : 'questa holding';
  
  try {
    await api.deleteHolding(id);
    showToast(`${ticker} eliminata con successo`, 'info', 'Annulla', async () => {
      if (item) {
        await api.addHolding({
          ticker: item.ticker,
          quantity: item.quantity,
          avg_purchase_price: item.avg_purchase_price,
          notes: item.notes
        });
        loadPortfolio();
      }
    });
    loadPortfolio();
  } catch (e) {
    showToast('Errore durante l\'eliminazione', 'error');
  }
};

// Modals
const holdingModal = document.getElementById('holdingModal');
const confirmSaveModal = document.getElementById('confirmSaveModal');
const importModal = document.getElementById('importModal');
const txModal = document.getElementById('txModal');

const openAddModal = (defaultTicker = '') => {
  document.getElementById('holdingForm').reset();
  document.getElementById('holdingId').value = '';
  document.getElementById('modalTitle').textContent = 'Aggiungi Titolo al Portafoglio';
  if (defaultTicker) {
    document.getElementById('tickerInput').value = defaultTicker;
  }
  holdingModal.classList.add('active');
};
window.openAddHoldingModal = openAddModal;

window.openTxModal = (ticker = '') => {
  if (!txModal) return;
  document.getElementById('txForm')?.reset();
  if (ticker) {
    const input = document.getElementById('txTickerInput');
    if (input) input.value = ticker;
  }
  const dateInput = document.getElementById('txDateInput');
  if (dateInput) {
    dateInput.value = new Date().toISOString().substring(0, 10);
  }
  txModal.classList.add('active');
};

const closeHoldingModal = () => holdingModal?.classList.remove('active');
const closeConfirmModal = () => confirmSaveModal?.classList.remove('active');
const closeImportModal = () => importModal?.classList.remove('active');
const closeTxModal = () => txModal?.classList.remove('active');

const initPortfolio = () => {
  loadPortfolio();

  // Listen for theme changes to redraw canvas chart
  window.addEventListener('themeChanged', () => {
    updateAllocationChart();
  });

  // Check URL query params for ?add=TICKER
  const params = new URLSearchParams(window.location.search);
  const addTicker = params.get('add');
  if (addTicker) {
    openAddModal(addTicker.toUpperCase());
  }

  // Allocation toggle with persistence
  const allocGroup = document.getElementById('allocTypeGroup');
  if (allocGroup) {
    allocGroup.querySelectorAll('.timeframe-btn').forEach(btn => {
      if (btn.dataset.type === currentAllocView) {
        allocGroup.querySelectorAll('.timeframe-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
      }

      btn.addEventListener('click', () => {
        allocGroup.querySelectorAll('.timeframe-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentAllocView = btn.dataset.type;
        localStorage.setItem('portfolio_alloc_view', currentAllocView);
        updateAllocationChart();
      });
    });
  }

  document.getElementById('btnAddHolding')?.addEventListener('click', () => openAddModal());
  document.getElementById('closeModal')?.addEventListener('click', closeHoldingModal);
  document.getElementById('cancelModal')?.addEventListener('click', closeHoldingModal);

  document.getElementById('closeConfirmModal')?.addEventListener('click', closeConfirmModal);
  document.getElementById('cancelConfirmModal')?.addEventListener('click', closeConfirmModal);

  document.getElementById('closeImportModal')?.addEventListener('click', closeImportModal);
  document.getElementById('cancelImportModal')?.addEventListener('click', closeImportModal);

  // Cancel Pending Changes
  document.getElementById('btnCancelChanges')?.addEventListener('click', () => {
    if (confirm('Vuoi annullare tutte le modifiche non salvate?')) {
      modifiedHoldings.clear();
      updateSaveBar();
      renderTable();
      showToast('Modifiche annullate', 'info');
    }
  });

  // Open Confirm Save Modal
  document.getElementById('btnSaveChanges')?.addEventListener('click', () => {
    if (modifiedHoldings.size === 0) return;

    const listEl = document.getElementById('confirmChangesList');
    listEl.innerHTML = Array.from(modifiedHoldings.values()).map(m => `
      <div class="change-item">
        <div>
          <span class="font-bold text-primary font-mono">${m.ticker}</span>
        </div>
        <div class="text-right font-mono text-xs">
          <div>Q.tà: <span class="text-muted line-through">${m.originalQty}</span> ➔ <strong class="text-profit">${m.newQty}</strong></div>
          <div>Prz: <span class="text-muted line-through">${formatCurrency(m.originalPrice)}</span> ➔ <strong class="text-profit">${formatCurrency(m.newPrice)}</strong></div>
        </div>
      </div>
    `).join('');

    confirmSaveModal.classList.add('active');
  });

  // Execute Batch Save
  document.getElementById('btnExecuteSave')?.addEventListener('click', async () => {
    const btn = document.getElementById('btnExecuteSave');
    btn.disabled = true;
    btn.textContent = 'Salvataggio in corso...';

    const updates = Array.from(modifiedHoldings.values()).map(m => ({
      id: m.id,
      quantity: m.newQty,
      avg_purchase_price: m.newPrice,
      notes: m.notes
    }));

    try {
      const res = await api.batchUpdateHoldings(updates);
      showToast(`Salvate ${res.updated_count || updates.length} posizioni con successo!`, 'success');
      closeConfirmModal();
      loadPortfolio();
    } catch (err) {
      showToast(err.message || 'Errore durante il salvataggio delle modifiche', 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = '✅ Sì, Conferma e Salva';
    }
  });

  // Add Single Holding
  document.getElementById('holdingForm')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const data = {
      ticker: document.getElementById('tickerInput').value.trim().toUpperCase(),
      quantity: parseFloat(document.getElementById('qtyInput').value),
      avg_purchase_price: parseFloat(document.getElementById('priceInput').value),
      purchase_date: document.getElementById('dateInput').value || null,
      notes: document.getElementById('notesInput').value || null
    };

    try {
      await api.addHolding(data);
      showToast(`${data.ticker} salvata nel portafoglio!`, 'success');
      closeHoldingModal();
      loadPortfolio();
    } catch (err) {
      showToast(err.message || 'Errore durante il salvataggio', 'error');
    }
  });

  // Autocomplete
  let timeout = null;
  const tickerInput = document.getElementById('tickerInput');
  const resultsDiv = document.getElementById('autocompleteResults');

  if (tickerInput && resultsDiv) {
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
                   onclick="document.getElementById('tickerInput').value='${r.ticker}';document.getElementById('autocompleteResults').style.display='none';">
                <strong class="text-primary font-mono">${r.ticker}</strong> — <span class="text-secondary">${r.name}</span>
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
  }

  // Export CSV
  document.getElementById('btnExport')?.addEventListener('click', async () => {
    try {
      const token = localStorage.getItem('auth_token');
      const res = await fetch('/api/portfolio/export?format=csv', {
        headers: token ? { 'Authorization': `Bearer ${token}` } : {}
      });
      if (!res.ok) throw new Error('Errore durante l\'esportazione');

      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      const today = new Date().toISOString().split('T')[0];
      a.download = `portafoglio_${today}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);

      showToast('Portafoglio esportato in CSV!', 'success');
    } catch (err) {
      showToast(err.message || 'Errore durante l\'esportazione CSV', 'error');
    }
  });

  // Import CSV
  document.getElementById('btnImport')?.addEventListener('click', () => {
    document.getElementById('csvFileInput').value = '';
    importModal.classList.add('active');
  });

  document.getElementById('btnSubmitImport')?.addEventListener('click', async () => {
    const fileInput = document.getElementById('csvFileInput');
    if (fileInput.files.length === 0) {
      showToast('Seleziona un file CSV da caricare', 'error');
      return;
    }

    const btn = document.getElementById('btnSubmitImport');
    btn.disabled = true;
    btn.textContent = 'Importazione in corso...';

    try {
      const result = await api.importPortfolio(fileInput.files[0]);
      closeImportModal();
      showToast(`Importate: ${result.imported || 0} nuove, Aggiornate: ${result.updated || 0}`, 'success');
      loadPortfolio();
    } catch (err) {
      showToast(err.message || 'Errore durante l\'importazione del file CSV', 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Carica e Importa';
    }
  });

  // Trade Ledger Modal & Filter Handlers
  document.getElementById('btnOpenTxModal')?.addEventListener('click', () => window.openTxModal());
  document.getElementById('closeTxModal')?.addEventListener('click', closeTxModal);
  document.getElementById('cancelTxModal')?.addEventListener('click', closeTxModal);

  document.querySelectorAll('.tx-filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tx-filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      loadTransactions(btn.dataset.type);
    });
  });

  // Tx Autocomplete
  let txTimeout = null;
  const txTickerInput = document.getElementById('txTickerInput');
  const txResultsDiv = document.getElementById('txAutocompleteResults');

  if (txTickerInput && txResultsDiv) {
    txTickerInput.addEventListener('input', (e) => {
      clearTimeout(txTimeout);
      const q = e.target.value.trim();
      if (q.length < 2) {
        txResultsDiv.style.display = 'none';
        return;
      }
      txTimeout = setTimeout(async () => {
        try {
          const results = await api.searchStocks(q);
          if (results && results.length > 0) {
            txResultsDiv.innerHTML = results.map(r => `
              <div style="padding: 10px 14px; cursor: pointer; border-bottom: 1px solid var(--border-color); transition: var(--transition);" 
                   onmouseover="this.style.backgroundColor='var(--surface-hover)'" 
                   onmouseout="this.style.backgroundColor='transparent'"
                   onclick="document.getElementById('txTickerInput').value='${r.ticker}';document.getElementById('txAutocompleteResults').style.display='none';">
                <strong class="text-primary font-mono">${r.ticker}</strong> — <span class="text-secondary">${r.name}</span>
              </div>
            `).join('');
            txResultsDiv.style.display = 'block';
          } else {
            txResultsDiv.style.display = 'none';
          }
        } catch (e) {
          txResultsDiv.style.display = 'none';
        }
      }, 250);
    });
  }

  // Submit Transaction Form
  document.getElementById('txForm')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const ticker = document.getElementById('txTickerInput').value.trim().toUpperCase();
    const type = document.getElementById('txTypeSelect').value;
    const quantity = parseFloat(document.getElementById('txQtyInput').value) || 0;
    const price = parseFloat(document.getElementById('txPriceInput').value) || 0;
    const fee = parseFloat(document.getElementById('txFeeInput').value) || 0;
    const dateVal = document.getElementById('txDateInput').value;
    const notes = document.getElementById('txNotesInput').value.trim();

    if (!ticker) {
      showToast('Inserisci un ticker valido', 'error');
      return;
    }

    const data = {
      ticker,
      type,
      quantity,
      price,
      fee,
      transaction_date: dateVal ? new Date(dateVal).toISOString() : null,
      notes
    };

    const submitBtn = document.getElementById('btnSubmitTx');
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = 'Registrazione...';
    }

    try {
      await api.createTransaction(data);
      showToast(`Transazione ${type} per ${ticker} registrata con successo!`, 'success');
      closeTxModal();
      loadPortfolio();
    } catch (err) {
      showToast(err.message || 'Errore durante la registrazione della transazione', 'error');
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Registra Transazione';
      }
    }
  });
};

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initPortfolio);
} else {
  initPortfolio();
}
