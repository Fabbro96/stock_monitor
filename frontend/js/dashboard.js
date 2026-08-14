import { api } from './api.js';
import { formatCurrency, formatPercent, showLoading, hideLoading, showToast } from './app.js';

let chart = null;
let lineSeries = null;
let resizeObserver = null;

const initChart = () => {
  const chartContainer = document.getElementById('portfolioChart');
  if (!chartContainer || typeof LightweightCharts === 'undefined') return;
  
  chart = LightweightCharts.createChart(chartContainer, {
    layout: {
      background: { type: 'solid', color: 'transparent' },
      textColor: '#9496b0',
      fontFamily: 'Inter, system-ui, sans-serif',
      fontSize: 12
    },
    grid: {
      vertLines: { color: 'rgba(35, 37, 59, 0.5)' },
      horzLines: { color: 'rgba(35, 37, 59, 0.5)' },
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
    }
  });

  lineSeries = chart.addAreaSeries({
    topColor: 'rgba(59, 130, 246, 0.35)',
    bottomColor: 'rgba(59, 130, 246, 0.01)',
    lineColor: '#3b82f6',
    lineWidth: 2,
    crosshairMarkerVisible: true,
  });

  // Responsive Resize
  if (window.ResizeObserver) {
    resizeObserver = new ResizeObserver(entries => {
      for (const entry of entries) {
        if (entry.contentRect.width > 0) {
          chart.applyOptions({
            width: entry.contentRect.width,
            height: chartContainer.clientHeight || 350
          });
        }
      }
    });
    resizeObserver.observe(chartContainer);
  }
};

const updateMarketStatus = () => {
  const isBusinessDay = new Date().getDay() >= 1 && new Date().getDay() <= 5;
  const hour = new Date().getHours();
  
  const mibEl = document.getElementById('statusMib');
  const usEl = document.getElementById('statusUs');
  
  // Market hours (approximate Rome & NY time)
  const mibOpen = isBusinessDay && hour >= 9 && hour < 18;
  const usOpen = isBusinessDay && hour >= 15 && hour < 22;

  if (mibEl) {
    mibEl.className = `status-dot ${mibOpen ? 'open' : 'closed'}`;
    mibEl.title = mibOpen ? 'Borsa Italiana: Aperta' : 'Borsa Italiana: Chiusa';
  }
  if (usEl) {
    usEl.className = `status-dot ${usOpen ? 'open' : 'closed'}`;
    usEl.title = usOpen ? 'Mercati USA: Aperti' : 'Mercati USA: Chiusi';
  }
};

const loadDashboardData = async (isSilentRefresh = false) => {
  try {
    if (!isSilentRefresh) {
      showLoading('dashboardContent');
    }
    
    // Fetch parallel per massime performance
    const [summary, performance, portfolio, advice] = await Promise.all([
      api.getPortfolioSummary().catch(() => ({ totalValue: 0, dailyPnL: 0, dailyPnLPercent: 0, totalPnL: 0, totalPnLPercent: 0, count: 0 })),
      api.getPerformance(30).catch(() => ({ data: [] })),
      api.getPortfolio().catch(() => []),
      api.getLatestAdvice().catch(() => [])
    ]);

    // 1. Stat Cards
    const totalValEl = document.getElementById('statTotalValue');
    if (totalValEl) totalValEl.textContent = formatCurrency(summary.totalValue || 0);
    
    const dailyEl = document.getElementById('statDailyPnL');
    if (dailyEl) {
      const dPnL = summary.dailyPnL || 0;
      const dPct = summary.dailyPnLPercent || 0;
      dailyEl.textContent = `${formatCurrency(dPnL)} (${formatPercent(dPct)})`;
      dailyEl.className = `stat-value font-mono ${dPnL >= 0 ? 'text-profit' : 'text-loss'}`;
    }
    
    const totalEl = document.getElementById('statTotalPnL');
    if (totalEl) {
      const tPnL = summary.totalPnL || 0;
      const tPct = summary.totalPnLPercent || 0;
      totalEl.textContent = `${formatCurrency(tPnL)} (${formatPercent(tPct)})`;
      totalEl.className = `stat-value font-mono ${tPnL >= 0 ? 'text-profit' : 'text-loss'}`;
    }
    
    const countEl = document.getElementById('statStockCount');
    if (countEl) countEl.textContent = summary.count || portfolio.length || 0;

    // 2. Performance Chart
    if (lineSeries && performance.data && performance.data.length > 0) {
      const chartData = performance.data.map(d => ({ time: d.date, value: d.value }));
      lineSeries.setData(chartData);
      chart.timeScale().fitContent();
    }

    // 3. Holdings Table
    const tbody = document.getElementById('holdingsTableBody');
    if (tbody) {
      if (portfolio.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-4">Nessun titolo nel portafoglio. Inizia aggiungendone uno nella sezione Portafoglio.</td></tr>';
      } else {
        tbody.innerHTML = portfolio.slice(0, 6).map(item => {
          const pnl = item.pnl_absolute ?? item.totalPnL ?? 0;
          const pnlPct = item.pnl_percent ?? item.totalPnLPercent ?? 0;
          const curPrice = item.current_price ?? item.currentPrice ?? item.avg_purchase_price ?? 0;
          return `
            <tr>
              <td><span class="font-bold text-primary">${item.ticker}</span></td>
              <td>${item.name || item.ticker}</td>
              <td class="text-right font-mono font-bold">${formatCurrency(curPrice)}</td>
              <td class="text-right font-mono ${pnlPct >= 0 ? 'text-profit' : 'text-loss'}">
                ${formatPercent(pnlPct)}
              </td>
              <td class="text-right font-mono font-bold ${pnl >= 0 ? 'text-profit' : 'text-loss'}">
                ${formatCurrency(pnl)}
              </td>
            </tr>
          `;
        }).join('');
      }
    }

    // 4. Advice List
    const adviceList = document.getElementById('recentAdviceList');
    if (adviceList) {
      if (advice.length === 0) {
        adviceList.innerHTML = '<div class="text-center text-muted py-4 text-sm">Nessuna analisi recente. Generane una nella sezione Consigli.</div>';
      } else {
        adviceList.innerHTML = advice.slice(0, 2).map(adv => {
          const isIT = adv.market === 'IT';
          const flag = isIT ? '🇮🇹' : '🇺🇸';
          const action = (adv.action || 'HOLD').toUpperCase();
          let badgeClass = 'badge-hold';
          if (action.includes('ACCUMULO') || action.includes('BUY')) badgeClass = 'badge-buy';
          else if (action.includes('PROFITTO') || action.includes('SELL') || action.includes('ALLEGGERIMENTO')) badgeClass = 'badge-sell';

          return `
            <div class="card p-3 border border-border-color" style="background-color: rgba(0, 0, 0, 0.2);">
              <div class="flex justify-between items-center mb-1">
                <span class="font-bold text-primary flex items-center gap-1.5 text-sm">
                  <span>${flag}</span>
                  <span>${adv.title || (isIT ? 'Borsa Italiana' : 'Borsa Americana')}</span>
                </span>
                <span class="badge ${badgeClass}">${action}</span>
              </div>
              <p class="text-xs text-secondary" style="line-height: 1.4; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;">
                ${adv.overview || adv.strategy || adv.reasoning || 'Nessuna descrizione disponibile.'}
              </p>
            </div>
          `;
        }).join('');
      }
    }


  } catch (error) {
    if (!isSilentRefresh) {
      showToast('Errore nel caricamento della dashboard', 'error');
    }
    console.error('Errore dashboard:', error);
  } finally {
    if (!isSilentRefresh) {
      hideLoading('dashboardContent');
    }
    updateMarketStatus();
  }
};

document.addEventListener('DOMContentLoaded', () => {
  initChart();
  loadDashboardData();
  
  // Auto-refresh silenzioso ogni 60 secondi
  setInterval(() => {
    if (!document.hidden) {
      loadDashboardData(true);
    }
  }, 60000);
});
