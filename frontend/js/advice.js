import { api } from './api.js';
import { formatCurrency, formatDateTime, showLoading, hideLoading, showToast } from './app.js';

let currentPage = 1;
let currentFilters = { action: '', q: '' };

const renderAdviceCard = (advice) => {
  const isFollowed = advice.followed ? 'checked' : '';
  const actionLabel = advice.action === 'BUY' ? '🟢 COMPRARE (BUY)' : (advice.action === 'SELL' ? '🔴 VENDERE (SELL)' : '🟡 MANTENERE (HOLD)');
  const badgeClass = advice.action === 'BUY' ? 'badge-buy' : (advice.action === 'SELL' ? 'badge-sell' : 'badge-hold');
  
  return `
    <div class="card" id="advice-${advice.id}">
        <div class="flex justify-between items-start mb-3">
            <div>
                <div class="flex items-center gap-3 mb-1">
                    <h3 class="text-lg font-bold">${advice.ticker}</h3>
                    <span class="badge ${badgeClass}">${actionLabel}</span>
                </div>
                <div class="text-xs text-muted">Generato: ${formatDateTime(advice.timestamp || advice.createdAt)}</div>
            </div>
            <div class="flex items-center gap-2 text-sm bg-[rgba(255,255,255,0.03)] px-3 py-1 rounded border border-border-color">
                <input type="checkbox" id="cb-${advice.id}" ${isFollowed} onchange="window.toggleFollow(${advice.id})">
                <label for="cb-${advice.id}" class="mb-0 cursor-pointer text-xs font-semibold">Segnato come Seguito</label>
            </div>
        </div>
        
        <p class="mb-4 text-primary leading-relaxed" style="font-size: 0.95rem;">${advice.reasoning}</p>
        
        <div class="flex gap-4 text-sm bg-[var(--bg-color)] p-3 rounded border border-border-color mt-3 flex-wrap">
            ${advice.targetPrice ? `
            <div>
                <span class="text-muted block text-xs flex items-center gap-1">
                    Target Price
                    <span class="has-tooltip"><i class="info-badge">i</i><span class="tooltip-box">Prezzo obiettivo stimato dall'IA</span></span>
                </span>
                <span class="font-bold text-primary">${formatCurrency(advice.targetPrice)}</span>
            </div>` : ''}
            
            ${advice.confidence ? `
            <div>
                <span class="text-muted block text-xs flex items-center gap-1">
                    Confidenza
                    <span class="has-tooltip"><i class="info-badge">i</i><span class="tooltip-box">Grado di attendibilità dell'analisi</span></span>
                </span>
                <span class="font-bold">${advice.confidence}</span>
            </div>` : ''}
            
            ${advice.timeframe ? `
            <div>
                <span class="text-muted block text-xs flex items-center gap-1">
                    Orizzonte
                    <span class="has-tooltip"><i class="info-badge">i</i><span class="tooltip-box">Orizzonte temporale raccomandato</span></span>
                </span>
                <span class="font-bold">${advice.timeframe}</span>
            </div>` : ''}
        </div>
    </div>
  `;
};


const loadAdvice = async (page = 1, append = false) => {
  try {
    if (!append) showLoading('adviceContent');
    
    const params = { page, limit: 10, ...currentFilters };
    const response = await api.getAdvice(params).catch(() => []);
    
    // Assume response is array or { data: [], summary: "..." }
    const adviceList = Array.isArray(response) ? response : (response.data || []);
    const summary = response.summary || '';

    if (page === 1 && summary) {
      document.getElementById('marketSummaryText').textContent = summary;
    } else if (page === 1) {
      document.getElementById('marketSummaryText').textContent = "Nessun riassunto di mercato disponibile.";
    }

    const listEl = document.getElementById('adviceList');
    if (!append) listEl.innerHTML = '';

    if (adviceList.length === 0 && !append) {
      listEl.innerHTML = '<div class="text-center text-muted py-8">Nessun consiglio trovato. Genera un\'analisi o cambia i filtri.</div>';
      document.getElementById('btnLoadMore').style.display = 'none';
      return;
    }

    listEl.innerHTML += adviceList.map(renderAdviceCard).join('');
    
    // Basic pagination logic
    document.getElementById('btnLoadMore').style.display = adviceList.length === 10 ? 'block' : 'none';

  } catch (error) {
    showToast('Errore nel caricamento dei consigli', 'error');
  } finally {
    hideLoading('adviceContent');
  }
};

window.toggleFollow = async (id) => {
  try {
    await api.followAdvice(id);
    showToast('Stato aggiornato', 'success');
  } catch(e) {
    showToast('Errore aggiornamento', 'error');
    // Revert checkbox
    const cb = document.getElementById(`cb-${id}`);
    if(cb) cb.checked = !cb.checked;
  }
};

document.addEventListener('DOMContentLoaded', () => {
  loadAdvice(1);

  document.getElementById('btnLoadMore').addEventListener('click', () => {
    currentPage++;
    loadAdvice(currentPage, true);
  });

  let debounceTimer;
  const applyFilters = () => {
    currentPage = 1;
    currentFilters.action = document.getElementById('filterAction').value;
    currentFilters.q = document.getElementById('filterSearch').value;
    loadAdvice(1);
  };

  document.getElementById('filterAction').addEventListener('change', applyFilters);
  document.getElementById('filterSearch').addEventListener('input', () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(applyFilters, 500);
  });

  document.getElementById('btnGenerate').addEventListener('click', async () => {
    try {
      showLoading('adviceContent');
      await api.generateAdvice();
      showToast('Analisi generata con successo!', 'success');
      loadAdvice(1);
    } catch(e) {
      showToast('Errore durante la generazione dell\'analisi', 'error');
    } finally {
      hideLoading('adviceContent');
    }
  });
});
