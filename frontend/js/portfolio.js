import { api } from './api.js';
import { formatCurrency, formatPercent, showLoading, hideLoading, showToast } from './app.js';

let portfolioData = [];
let summaryData = {};
let modifiedHoldings = new Map();
let currentAllocView = 'stock'; // 'stock' or 'market'

const colors = ['#3b82f6', '#10b981', '#f43f5e', '#f59e0b', '#8b5cf6', '#06b6d4', '#ec4899', '#6366f1', '#14b8a6'];

const drawPieChart = (data) => {
  const canvas = document.getElementById('allocationChart');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  
  if (!data || data.length === 0) {
    ctx.fillStyle = '#21253d';
    ctx.beginPath();
    ctx.arc(canvas.width/2, canvas.height/2, Math.min(canvas.width/2, canvas.height/2) - 10, 0, 2 * Math.PI);
    ctx.fill();
    const legend = document.getElementById('allocationLegend');
    if (legend) legend.innerHTML = '<div class="text-muted text-center text-xs">Nessun dato</div>';
    return;
  }

  const total = data.reduce((sum, item) => sum + (item.value || 0), 0);
  if (total <= 0) return;

  let startAngle = -0.5 * Math.PI;

  data.forEach((item, i) => {
    const sliceAngle = (item.value / total) * 2 * Math.PI;
    ctx.fillStyle = colors[i % colors.length];
    ctx.beginPath();
    ctx.moveTo(canvas.width/2, canvas.height/2);
    ctx.arc(canvas.width/2, canvas.height/2, Math.min(canvas.width/2, canvas.height/2) - 10, startAngle, startAngle + sliceAngle);
    ctx.fill();
    startAngle += sliceAngle;
  });

  const legend = document.getElementById('allocationLegend');
  if (legend) {
    legend.innerHTML = data.slice(0, 7).map((item, i) => `
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-2">
          <div style="width:10px;height:10px;background-color:${colors[i % colors.length]};border-radius:2px;"></div>
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
    }));
    data.sort((a, b) => b.value - a.value);
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

const renderTable = () => {
  const tbody = document.getElementById('portfolioTableBody');
  if (!tbody) return;
  
  if (portfolioData.length === 0) {
    tbody.innerHTML = '<tr><td colspan="9" class="text-center text-muted py-8">Nessun titolo in portafoglio. Clicca <strong>"➕ Aggiungi Holding"</strong> o <strong>"📤 Importa CSV"</strong> per iniziare.</td></tr>';
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
          <input 
            type="number" 
            class="inline-input input-qty" 
            data-id="${item.id}" 
            value="${displayQty}" 
            step="any" 
            min="0"
          >
        </td>

        <!-- Editable Purchase Price -->
        <td class="text-right">
          <input 
            type="number" 
            class="inline-input input-price" 
            data-id="${item.id}" 
            value="${displayPrice}" 
            step="any" 
            min="0"
          >
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
            <button class="btn btn-ghost btn-sm text-loss" title="Elimina" onclick="window.deleteHolding(${item.id})">🗑️</button>
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
    showLoading('portfolioContent');
    summaryData = await api.getPortfolioSummary().catch(() => ({}));
    portfolioData = await api.getPortfolio().catch(() => []);
    // Dispatch event for UI effects (pulse green/red on price updates)
    const priceEvent = new CustomEvent("portfolio:price:updated");
    window.dispatchEvent(priceEvent)
    document.getElementById('totalValue').textContent = formatCurrency(summaryData.total_value || 0);
    document.getElementById('totalInvested').textContent = formatCurrency(summaryData.total_invested || 0);
    document.getElementById('totalCount').textContent = summaryData.holdings_count || portfolioData.length;
    
    const pnlEl = document.getElementById('totalPnL');
    const totPnL = summaryData.total_pnl || 0;
    const totPct = summaryData.total_pnl_percent || 0;
    pnlEl.textContent = `${formatCurrency(totPnL)} (${formatPercent(totPct)})`;
    pnlEl.className = `text-2xl font-bold font-mono ${totPnL >= 0 ? 'text-profit' : 'text-loss'}`;

    const divEl = document.getElementById('totalDividends');
    if (divEl) {
      divEl.textContent = `${formatCurrency(summaryData.estimated_annual_dividends || 0)}/anno (${(summaryData.estimated_dividend_yield || 0).toFixed(2)}%)`;
    }

    modifiedHoldings.clear();
    updateSaveBar();
    renderTable();
    updateAllocationChart();

  } catch (error) {
    showToast('Errore nel caricamento del portafoglio', 'error');
  } finally {
    hideLoading('portfolioContent');
  }
};

window.deleteHolding = async (id) => {
  const item = portfolioData.find(h => h.id === id);
  const ticker = item ? item.ticker : 'questa holding';
  
  if (confirm(`Sei sicuro di voler eliminare ${ticker} dal portafoglio?`)) {
    try {
      await api.deleteHolding(id);
      showToast(`${ticker} eliminata con successo`, 'success');
      loadPortfolio();
    } catch (e) {
      showToast('Errore durante l\'eliminazione', 'error');
    }
  }
};

// Modals
const holdingModal = document.getElementById('holdingModal');
const confirmSaveModal = document.getElementById('confirmSaveModal');
const importModal = document.getElementById('importModal');

const openAddModal = (defaultTicker = '') => {
  document.getElementById('holdingForm').reset();
  document.getElementById('holdingId').value = '';
  document.getElementById('modalTitle').textContent = 'Aggiungi Titolo al Portafoglio';
  if (defaultTicker) {
    document.getElementById('tickerInput').value = defaultTicker;
  }
  holdingModal.classList.add('active');
};

const closeHoldingModal = () => holdingModal.classList.remove('active');
const closeConfirmModal = () => confirmSaveModal.classList.remove('active');
const closeImportModal = () => importModal.classList.remove('active');

document.addEventListener('DOMContentLoaded', () => {
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

  // Allocation toggle
  const allocGroup = document.getElementById('allocTypeGroup');
  if (allocGroup) {
    allocGroup.querySelectorAll('.timeframe-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        allocGroup.querySelectorAll('.timeframe-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentAllocView = btn.dataset.type;
        updateAllocationChart();
      });
    });
  }

  document.getElementById('btnAddHolding').addEventListener('click', () => openAddModal());
  document.getElementById('closeModal').addEventListener('click', closeHoldingModal);
  document.getElementById('cancelModal').addEventListener('click', closeHoldingModal);

  document.getElementById('closeConfirmModal').addEventListener('click', closeConfirmModal);
  document.getElementById('cancelConfirmModal').addEventListener('click', closeConfirmModal);

  document.getElementById('closeImportModal').addEventListener('click', closeImportModal);
  document.getElementById('cancelImportModal').addEventListener('click', closeImportModal);

  // Cancel Pending Changes
  document.getElementById('btnCancelChanges').addEventListener('click', () => {
    if (confirm('Vuoi annullare tutte le modifiche non salvate?')) {
      modifiedHoldings.clear();
      updateSaveBar();
      renderTable();
      showToast('Modifiche annullate', 'info');
    }
  });

  // Open Confirm Save Modal
  document.getElementById('btnSaveChanges').addEventListener('click', () => {
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
  document.getElementById('btnExecuteSave').addEventListener('click', async () => {
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
  document.getElementById('holdingForm').addEventListener('submit', async (e) => {
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

  // Export CSV
  document.getElementById('btnExport').addEventListener('click', async () => {
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
  document.getElementById('btnImport').addEventListener('click', () => {
    document.getElementById('csvFileInput').value = '';
    importModal.classList.add('active');
  });

  document.getElementById('btnSubmitImport').addEventListener('click', async () => {
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
});

// ===========================================================================
// ⚖️ SMART PORTFOLIO REBALANCER
// ===========================================================================
const SCOPE_LABELS = { MARKET: '🌍 Mercato', TICKERS: '🏷 Ticker', CASH: '💵 Cash' };

const loadRebalanceTargets = async () => {
  const tbody = document.getElementById('targetsTableBody');
  if (!tbody) return;
  try {
    const targets = await api.getRebalanceTargets();
    if (!targets || targets.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" class="text-center text-muted py-3 text-xs">Nessuna allocazione target definita.</td></tr>';
      return;
    }
    const sumPct = targets.reduce((a, t) => a + (t.target_percent || 0), 0);
    tbody.innerHTML = targets.map(t => `
      <tr>
        <td><span class="font-bold text-primary">${t.name}</span></td>
        <td class="text-center text-xs text-secondary">${SCOPE_LABELS[t.scope_type] || t.scope_type}${t.scope_value ? `: ${t.scope_value}` : ''}</td>
        <td class="text-right font-mono font-bold">${Number(t.target_percent).toFixed(1)}%</td>
        <td class="text-center">
          <button class="btn btn-ghost btn-sm text-loss" title="Elimina" onclick="window.deleteRebalanceTarget(${t.id})">🗑️</button>
        </td>
      </tr>
    `).join('') + `
      <tr>
        <td colspan="2" class="text-right text-xs font-bold ${Math.abs(sumPct - 100) < 0.01 ? 'text-profit' : 'text-warning'}">Somma target:</td>
        <td class="text-right font-mono font-bold ${Math.abs(sumPct - 100) < 0.01 ? 'text-profit' : 'text-warning'}">${sumPct.toFixed(1)}%</td>
        <td></td>
      </tr>
    `;
  } catch (e) {
    console.error('Errore caricamento target:', e);
  }
};

window.deleteRebalanceTarget = async (id) => {
  if (!confirm('Eliminare questa allocazione target?')) return;
  try {
    await api.deleteRebalanceTarget(id);
    showToast('Allocazione eliminata', 'success');
    loadRebalanceTargets();
  } catch (e) {
    showToast(e.message || 'Errore eliminazione target', 'error');
  }
};

const renderRebalancePlan = (plan) => {
  const container = document.getElementById('rebalanceResult');
  if (!container || !plan) return;

  if (plan.portfolio_empty) {
    container.innerHTML = '<div class="alert-error text-center py-4 text-xs">Il portafoglio è vuoto: aggiungi posizioni per calcolare il ribilanciamento.</div>';
    return;
  }

  const allocRows = (plan.allocations || []).map(a => {
    const drift = a.drift_pct || 0;
    const driftClass = Math.abs(drift) < 1 ? 'text-secondary' : (drift > 0 ? 'text-profit' : 'text-loss');
    return `
      <tr>
        <td><span class="font-bold">${a.name}</span></td>
        <td class="text-right font-mono">${Number(a.current_percent).toFixed(1)}% → <strong>${Number(a.target_percent).toFixed(1)}%</strong></td>
        <td class="text-right font-mono ${driftClass}">${drift > 0 ? '+' : ''}${drift.toFixed(1)}%</td>
        <td class="text-right font-mono ${a.delta >= 0 ? 'text-profit' : 'text-loss'}">${a.delta >= 0 ? '+' : ''}${formatCurrency(a.delta)}</td>
      </tr>
    `;
  }).join('');

  const orderRows = (plan.orders || []).map(o => `
    <tr>
      <td><span class="badge ${o.side === 'BUY' ? 'badge-buy' : 'badge-sell'}">${o.side === 'BUY' ? '🟢 BUY' : '🔴 SELL'}</span></td>
      <td><span class="font-bold font-mono">${o.ticker}</span><div class="text-[11px] text-muted">${o.allocation_name}</div></td>
      <td class="text-right font-mono font-bold">${o.quantity}</td>
      <td class="text-right font-mono">${formatCurrency(o.estimated_price, o.currency)}</td>
      <td class="text-right font-mono font-bold ${o.side === 'BUY' ? 'order-side-buy' : 'order-side-sell'}">${formatCurrency(o.estimated_value, o.currency)}</td>
    </tr>
  `).join('');

  container.innerHTML = `
    <div class="mb-3">
      <div class="flex justify-between text-xs text-secondary mb-2 flex-wrap gap-2">
        <span>💰 Valore considerato: <strong class="text-primary font-mono">${formatCurrency(plan.total_value)}</strong></span>
        ${plan.extra_cash > 0 ? `<span>💵 Liquidità extra: <strong class="font-mono">${formatCurrency(plan.extra_cash)}</strong></span>` : ''}
        <span>🎯 Target coperti: <strong class="font-mono">${plan.targets_sum_percent}%</strong></span>
      </div>
      <div class="table-container">
        <table>
          <thead><tr><th>Bucket</th><th class="text-right">Attuale → Target</th><th class="text-right">Drift</th><th class="text-right">Delta €</th></tr></thead>
          <tbody>${allocRows}</tbody>
        </table>
      </div>
    </div>
    ${plan.orders_count > 0 ? `
      <div class="text-xs font-bold text-primary mb-2">🧾 Ordini suggeriti (${plan.orders_count}) — Buy ${formatCurrency(plan.total_buy_value)} | Sell ${formatCurrency(plan.total_sell_value)}</div>
      <div class="table-container">
        <table>
          <thead><tr><th>Side</th><th>Titolo</th><th class="text-right">Quantità</th><th class="text-right">Prezzo Stimato</th><th class="text-right">Controvalore</th></tr></thead>
          <tbody>${orderRows}</tbody>
        </table>
      </div>
      <div class="text-[11px] text-muted mt-2">⚠️ Ordini indicativi calcolati sui prezzi correnti; non costituiscono consulenza finanziaria.</div>
    ` : '<div class="text-center text-muted text-xs py-3">✅ Portafoglio già allineato ai target: nessun ordine necessario.</div>'}
  `;
};

document.addEventListener('DOMContentLoaded', () => {
  const btnAddTarget = document.getElementById('btnAddTarget');
  const btnPreview = document.getElementById('btnRebalancePreview');
  if (!btnAddTarget || !btnPreview) return;

  loadRebalanceTargets();

  const scopeTypeSel = document.getElementById('targetScopeType');
  const scopeValueInput = document.getElementById('targetScopeValue');
  scopeTypeSel.addEventListener('change', () => {
    scopeValueInput.disabled = scopeTypeSel.value === 'CASH';
    if (scopeTypeSel.value === 'CASH') scopeValueInput.value = '';
  });

  btnAddTarget.addEventListener('click', async () => {
    const name = document.getElementById('targetName').value.trim();
    const pct = parseFloat(document.getElementById('targetPct').value);
    const scope_type = scopeTypeSel.value;
    const scope_value = scopeValueInput.value.trim();

    if (!name || isNaN(pct) || pct <= 0 || pct > 100) {
      showToast('Inserisci nome e percentuale target valida (0-100)', 'error');
      return;
    }
    try {
      await api.addRebalanceTarget({ name, target_percent: pct, scope_type, scope_value });
      showToast(`Allocazione "${name}" aggiunta`, 'success');
      document.getElementById('targetName').value = '';
      document.getElementById('targetPct').value = '';
      scopeValueInput.value = '';
      loadRebalanceTargets();
    } catch (e) {
      showToast(e.message || 'Errore salvataggio allocazione', 'error');
    }
  });

  btnPreview.addEventListener('click', async () => {
    const extraCash = parseFloat(document.getElementById('rebalanceCashInput').value) || 0;
    const container = document.getElementById('rebalanceResult');
    btnPreview.disabled = true;
    btnPreview.textContent = 'Calcolo in corso...';
    if (container) container.classList.add('skeleton');
    try {
      const plan = await api.rebalancePreview(extraCash);
      renderRebalancePlan(plan);
    } catch (e) {
      if (container) container.innerHTML = `<div class="alert-error text-center py-4 text-xs">${e.message || 'Errore nel calcolo del piano'}</div>`;
    } finally {
      btnPreview.disabled = false;
      btnPreview.textContent = 'Calcola Ordini ➔';
      if (container) container.classList.remove('skeleton');
    }
  });
});

  // Add breakeven price column and pulse effects
  const priceHeaders = tbody.querySelectorAll("th");
  const priceColIndex = Array.from(priceHeaders).findIndex(h => h.textContent.includes("Prezzo"));
  if (priceColIndex >= 0) {
    const breakevenHeader = document.createElement("th");
    breakevenHeader.style.cssText = "width: 120px;";
    breakevenHeader.textContent = "Prezzo Medio";
    priceHeaders[priceColIndex + 1].parentNode.insertBefore(breakevenHeader, priceHeaders[priceColIndex + 1].nextSibling);
  }
  tbody.querySelectorAll("tr").forEach((row, i) => {
    const priceCels = row.querySelectorAll("td");
    if (priceCels && portfolioData[i]) {
      const breakevenCell = document.createElement("td");
      breakevenCell.className = "breakeven-price text-right font-mono";
      breakevenCell.style.cssText = "width: 120px; color: #f59e0b; font-size: 0.85rem";
      breakevenCell.textContent = formatCurrency(portfolioData[i].avg_purchase_price, portfolioData[i].currency || "EUR");
      const priceCell = priceCels[6];
      if (priceCell) {
        priceCell.parentNode.insertBefore(breakevenCell, priceCell.nextSibling);
      }
    }
  });

  // Pulse effects on price updates
  const pulsePortfolioPrices = () => {
    tbody.querySelectorAll(".price-cell").forEach(cell => {
      const prevVal = cell.dataset.prevPrice;
      if (prevVal === undefined) {
        cell.dataset.prevPrice = cell.textContent;
      }
      const numericVal = parseFloat(cell.textContent.replace(/[^0-9.-]/g, "")) || 0;
      if (cell.dataset.pulsing) return;
      if (numericVal > 0 && numericVal !== parseFloat(cell.dataset.prevPrice)) {
        cell.classList.add(numericVal > parseFloat(cell.dataset.prevPrice) ? "pulse-green" : "pulse-red");
        cell.dataset.prevPrice = cell.textContent;
        setTimeout(() => cell.classList.remove(numericVal > parseFloat(cell.dataset.prevPrice) ? "pulse-green" : "pulse-red"), 400);
      }
    });
  };

  window.addEventListener("portfolio:price:updated", pulsePortfolioPrices);
