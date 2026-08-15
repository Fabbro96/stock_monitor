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
