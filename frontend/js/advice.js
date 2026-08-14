import { api } from './api.js';
import { formatCurrency, formatDateTime, showLoading, hideLoading, showToast } from './app.js';

let currentPage = 1;
let currentFilters = { market: '', action: '', q: '' };

const renderAdviceCard = (advice) => {
  const isFollowed = advice.followed ? 'checked' : '';
  const isIT = advice.market === 'IT';
  const flag = isIT ? '🇮🇹' : '🇺🇸';
  const marketBadgeColor = isIT ? 'rgba(16, 185, 129, 0.10)' : 'rgba(59, 130, 246, 0.10)';
  const marketBorderColor = isIT ? 'rgba(16, 185, 129, 0.5)' : 'rgba(59, 130, 246, 0.5)';

  let actionBadge = 'badge-hold';
  let actionText = '🟡 MANTENIMENTO';
  const act = (advice.action || '').toUpperCase();
  if (act.includes('ACCUMULO') || act.includes('BUY')) {
    actionBadge = 'badge-buy';
    actionText = '🟢 ACCUMULO / BUY';
  } else if (act.includes('PROFITTO') || act.includes('SELL') || act.includes('ALLEGGERIMENTO')) {
    actionBadge = 'badge-sell';
    actionText = '🔴 PRESA PROFITTO / SELL';
  } else if (act.includes('PRUDENZA')) {
    actionBadge = 'badge-hold';
    actionText = '🛡️ PRUDENZA';
  }

  // Stocks analysis table
  const stocks = Array.isArray(advice.stocks_analysis) ? advice.stocks_analysis : [];
  let stocksHtml = '';
  if (stocks.length > 0) {
    stocksHtml = `
      <div class="mt-4 pt-4 border-t border-border-color">
        <h4 class="text-sm font-bold mb-3 text-primary flex items-center gap-2">
          <span>📊 Focus & Raccomandazioni sui Titoli ${flag}</span>
        </h4>
        <div class="table-container border border-border-color rounded">
          <table>
            <thead>
              <tr>
                <th style="width: 180px;">Ticker & Titolo</th>
                <th class="text-center" style="width: 120px;">Azione</th>
                <th class="text-right" style="width: 140px;">Target Price</th>
                <th>Razionale & Note Operative</th>
              </tr>
            </thead>
            <tbody>
              ${stocks.map(s => {
                const sAct = (s.action || 'HOLD').toUpperCase();
                const sBadge = sAct === 'BUY' ? 'badge-buy' : (sAct === 'SELL' ? 'badge-sell' : 'badge-hold');
                const sLabel = sAct === 'BUY' ? '🟢 BUY' : (sAct === 'SELL' ? '🔴 SELL' : '🟡 HOLD');
                return `
                  <tr>
                    <td>
                      <div class="font-bold text-primary">${s.ticker}</div>
                      <div class="text-xs text-muted">${s.name || ''}</div>
                    </td>
                    <td class="text-center">
                      <span class="badge ${sBadge}">${sLabel}</span>
                    </td>
                    <td class="text-right font-mono font-bold text-primary">
                      ${s.target_price ? formatCurrency(s.target_price) : '--'}
                    </td>
                    <td class="text-sm text-secondary leading-relaxed">
                      ${s.note || s.reasoning || '--'}
                    </td>
                  </tr>
                `;
              }).join('')}
            </tbody>
          </table>
        </div>
      </div>
    `;
  }

  return `
    <div class="card mb-4" id="advice-${advice.id}" style="border-top: 3px solid ${marketBorderColor}; background: linear-gradient(180deg, ${marketBadgeColor} 0%, var(--surface-color) 45px);">
      <!-- Top header -->
      <div class="flex justify-between items-start mb-4 flex-wrap gap-2">
        <div>
          <div class="flex items-center gap-3 mb-1">
            <span style="font-size: 1.6rem;">${flag}</span>
            <h3 class="text-xl font-bold">${advice.title || (isIT ? 'Borsa Italiana (Piazza Affari)' : 'Borsa Americana (Wall Street)')}</h3>
            <span class="badge ${actionBadge}">${actionText}</span>
          </div>
          <div class="text-xs text-muted">Analisi strategica: ${formatDateTime(advice.timestamp)}</div>
        </div>
        <div class="flex items-center gap-2 text-sm bg-[rgba(255,255,255,0.03)] px-3 py-1.5 rounded border border-border-color">
          <input type="checkbox" id="cb-${advice.id}" ${isFollowed} onchange="window.toggleFollow(${advice.id})">
          <label for="cb-${advice.id}" class="mb-0 cursor-pointer text-xs font-semibold">Segnato come Letto</label>
        </div>
      </div>

      <!-- Overview Section -->
      ${advice.overview ? `
      <div class="mb-4">
        <h4 class="text-xs font-bold text-muted uppercase tracking-wider mb-1">🌐 Quadro & Scenario Generale</h4>
        <p class="text-primary leading-relaxed" style="font-size: 0.95rem;">${advice.overview}</p>
      </div>` : ''}

      <!-- Strategy Section -->
      ${advice.strategy ? `
      <div class="mb-4 bg-[var(--bg-color)] p-4 rounded border border-border-color">
        <h4 class="text-xs font-bold text-muted uppercase tracking-wider mb-1">🎯 Strategia Operativa & Piano d'Azione</h4>
        <p class="text-primary leading-relaxed" style="font-size: 0.95rem;">${advice.strategy}</p>
      </div>` : ''}

      <!-- Stocks Breakdown -->
      ${stocksHtml}

      <!-- Risks Section -->
      ${advice.risks ? `
      <div class="mt-4 p-3 rounded" style="background: rgba(239, 68, 68, 0.08); border-left: 3px solid var(--danger-color);">
        <h4 class="text-xs font-bold mb-1 text-danger flex items-center gap-1">
          <span>⚠️ Punti di Attenzione & Rischi Chiave</span>
        </h4>
        <p class="text-xs text-secondary leading-relaxed mb-0">${advice.risks}</p>
      </div>` : ''}

      <!-- Footer Info -->
      <div class="flex gap-4 text-xs text-muted mt-4 pt-3 border-t border-border-color flex-wrap items-center justify-between">
        <div class="flex gap-4">
          ${advice.confidence ? `<div>Confidenza IA: <strong class="text-primary">${advice.confidence}</strong></div>` : ''}
          ${advice.timeframe ? `<div>Orizzonte: <strong class="text-primary">${advice.timeframe}</strong></div>` : ''}
        </div>
        <div class="text-xs text-muted">Gemini 3.7 Flash</div>
      </div>
    </div>
  `;
};

const loadAdvice = async (page = 1, append = false) => {
  try {
    if (!append) showLoading('adviceContent');
    
    const params = { page, limit: 10, ...currentFilters };
    const response = await api.getAdvice(params).catch(() => []);
    
    const adviceList = Array.isArray(response) ? response : (response.data || []);
    const summary = response.summary || '';

    if (page === 1 && summary) {
      document.getElementById('marketSummaryText').textContent = summary;
    } else if (page === 1) {
      document.getElementById('marketSummaryText').textContent = "Scenario suddiviso per Borsa Italiana (Piazza Affari) e Borsa Americana (Wall Street).";
    }

    const listEl = document.getElementById('adviceList');
    if (!append) listEl.innerHTML = '';

    // Filter locally if search query is entered
    let filtered = adviceList;
    if (currentFilters.q) {
      const query = currentFilters.q.toUpperCase();
      filtered = filtered.filter(adv => {
        const titleMatch = (adv.title || '').toUpperCase().includes(query);
        const overviewMatch = (adv.overview || '').toUpperCase().includes(query);
        const stocksMatch = (adv.stocks_analysis || []).some(s => 
          (s.ticker || '').toUpperCase().includes(query) || (s.name || '').toUpperCase().includes(query)
        );
        return titleMatch || overviewMatch || stocksMatch;
      });
    }

    if (filtered.length === 0 && !append) {
      listEl.innerHTML = '<div class="text-center text-muted py-8">Nessuna analisi di mercato trovata per i filtri selezionati. Clicca "Genera Analisi Ora" per elaborare i nuovi blocchi di borsa.</div>';
      document.getElementById('btnLoadMore').style.display = 'none';
      return;
    }

    listEl.innerHTML += filtered.map(renderAdviceCard).join('');
    
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
    const cb = document.getElementById(`cb-${id}`);
    if(cb) cb.checked = !cb.checked;
  }
};

const checkMarketStatus = async () => {
  try {
    const data = await api.getDashboard();
    const marketStatus = data.market_status || {};
    const anyOpen = marketStatus.ANY_OPEN === 'OPEN';
    const dot = document.getElementById('marketDot');
    const text = document.getElementById('marketStatusText');
    const btn = document.getElementById('btnGenerate');

    if (dot && text) {
      if (anyOpen) {
        dot.className = 'status-dot open';
        text.textContent = '🟢 Borse Aperte';
        if (btn) {
          btn.title = 'Genera nuova analisi macro per Borsa Italiana e Americana';
        }
      } else {
        dot.className = 'status-dot closed';
        text.textContent = '🔴 Borse Chiuse';
        if (btn) {
          btn.title = 'I mercati sono chiusi. L\'IA genera consigli solo a borse aperte.';
        }
      }
    }
  } catch(e) {
    console.error('Errore recupero stato mercati', e);
  }
};

document.addEventListener('DOMContentLoaded', () => {
  loadAdvice(1);
  checkMarketStatus();

  document.getElementById('btnLoadMore').addEventListener('click', () => {
    currentPage++;
    loadAdvice(currentPage, true);
  });

  let debounceTimer;
  const applyFilters = () => {
    currentPage = 1;
    currentFilters.market = document.getElementById('filterMarket').value;
    currentFilters.action = document.getElementById('filterAction').value;
    currentFilters.q = document.getElementById('filterSearch').value;
    loadAdvice(1);
  };

  document.getElementById('filterMarket').addEventListener('change', applyFilters);
  document.getElementById('filterAction').addEventListener('change', applyFilters);
  document.getElementById('filterSearch').addEventListener('input', () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(applyFilters, 500);
  });

  document.getElementById('btnGenerate').addEventListener('click', async () => {
    try {
      showLoading('adviceContent');
      await api.generateAdvice();
      showToast('Analisi per Borsa Italiana e Americana generata con successo!', 'success');
      loadAdvice(1);
    } catch(e) {
      const errorMsg = e.message || 'I mercati finanziari sono attualmente chiusi. L\'IA non genera consigli a borsa chiusa.';
      showToast(errorMsg, 'error');
    } finally {
      hideLoading('adviceContent');
    }
  });
});
