import { api } from './api.js';
import { formatCurrency, formatPercent, showLoading, hideLoading, showToast } from './app.js';

let chart = null;
let lineSeries = null;
let resizeObserver = null;
let currentChartDays = 30;
let activeBenchmark = 'none'; // 'none' | '^GSPC' | 'FTSEMIB.MI' | 'both'
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
    }
  });

  lineSeries = chart.addAreaSeries({
    topColor: 'rgba(59, 130, 246, 0.4)',
    bottomColor: 'rgba(59, 130, 246, 0.01)',
    lineColor: '#3b82f6',
    lineWidth: 2,
    crosshairMarkerVisible: true,
  });

  // Serie benchmark (S&P 500 e FTSE MIB), attivate dai chip di confronto
  for (const [tk, color] of Object.entries(BENCH_COLORS)) {
    benchSeriesMap[tk] = chart.addLineSeries({
      color,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerVisible: false,
      visible: false,
    });
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

const updateMarketStatus = (statusData = null) => {
  const mibEl = document.getElementById('statusMib');
  const usEl = document.getElementById('statusUs');
  
  const itOpen = statusData?.IT === 'OPEN' || (new Date().getDay() >= 1 && new Date().getDay() <= 5 && new Date().getHours() >= 9 && new Date().getHours() < 18);
  const usOpen = statusData?.US === 'OPEN' || (new Date().getDay() >= 1 && new Date().getDay() <= 5 && new Date().getHours() >= 15 && new Date().getHours() < 22);

  if (mibEl) {
    mibEl.className = `status-dot ${itOpen ? 'open' : 'closed'}`;
    mibEl.title = itOpen ? 'Borsa Italiana: Aperta (09:00 - 17:30)' : 'Borsa Italiana: Chiusa (09:00 - 17:30)';
  }
  if (usEl) {
    usEl.className = `status-dot ${usOpen ? 'open' : 'closed'}`;
    usEl.title = usOpen ? 'Wall Street: Aperta (15:30 - 22:00)' : 'Wall Street: Chiusa (15:30 - 22:00)';
  }
};

const renderHeatmap = (items) => {
  const container = document.getElementById('marketHeatmap');
  if (!container) return;

  if (!items || items.length === 0) {
    container.innerHTML = '<div class="text-muted text-xs py-4 text-center">Nessun titolo attivo per la heatmap</div>';
    return;
  }

  container.innerHTML = items.map(item => {
    const chg = item.change_percent || 0;
    let tileClass = 'tile-neutral';
    if (chg >= 3.0) tileClass = 'tile-gain-high';
    else if (chg >= 1.0) tileClass = 'tile-gain-mid';
    else if (chg > 0.0) tileClass = 'tile-gain-low';
    else if (chg <= -3.0) tileClass = 'tile-loss-high';
    else if (chg <= -1.0) tileClass = 'tile-loss-mid';
    else if (chg < 0.0) tileClass = 'tile-loss-low';

    const isUp = chg >= 0;
    const sign = isUp ? '+' : '';
    const flag = item.market === 'IT' ? '🇮🇹' : '🇺🇸';

    return `
      <div class="heatmap-tile ${tileClass}" onclick="window.openStockModal('${item.ticker}')">
        <div class="flex justify-between items-center mb-1">
          <span class="font-bold text-primary font-mono text-sm">${item.ticker}</span>
          <span class="text-xs">${flag}</span>
        </div>
        <div class="text-xs text-secondary mb-1" style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${item.name || item.ticker}</div>
        <div class="flex justify-between items-end">
          <span class="text-xs font-mono font-bold">${formatCurrency(item.current_price, item.currency)}</span>
          <span class="text-xs font-mono font-bold ${isUp ? 'text-profit' : 'text-loss'}">${sign}${chg.toFixed(2)}%</span>
        </div>
      </div>
    `;
  }).join('');
};

const loadPerformanceChart = async (days = 30) => {
  if (!lineSeries) return;
  const chartEl = document.getElementById('portfolioChart');
  if (chartEl) chartEl.classList.add('skeleton');
  try {
    // 1. Tenta il confronto benchmark (serie normalizzate in crescita %)
    const bench = await api.getBenchmarks(days).catch(() => null);
    if (bench && Array.isArray(bench.portfolio) && bench.portfolio.length > 0) {
      lineSeries.setData(bench.portfolio.map(p => ({ time: p.date, value: p.growth_pct })));
      for (const [tk, series] of Object.entries(benchSeriesMap)) {
        const b = bench.benchmarks ? bench.benchmarks[tk] : null;
        const show = (activeBenchmark === 'both' || activeBenchmark === tk) && b && Array.isArray(b.data) && b.data.length > 0;
        series.applyOptions({ visible: Boolean(show) });
        if (show) {
          series.setData(b.data.map(p => ({ time: p.date, value: p.growth_pct })));
        } else {
          series.setData([]);
        }
      }
      chart.timeScale().fitContent();
      return;
    }

    // 2. Fallback: valore assoluto del portafoglio (nessun benchmark disponibile)
    Object.values(benchSeriesMap).forEach(s => { s.applyOptions({ visible: false }); s.setData([]); });
    const performance = await api.getPerformance(days).catch(() => ({ data: [] }));
    if (performance.data && performance.data.length > 0) {
      const chartData = performance.data.map(d => ({ time: d.date, value: d.value }));
      lineSeries.setData(chartData);
      chart.timeScale().fitContent();
    }
  } catch (e) {
    console.error('Errore storico performance:', e);
  } finally {
    if (chartEl) chartEl.classList.remove('skeleton');
  }
};

const renderRiskMetrics = (m) => {
  const row = document.getElementById('riskMetricsRow');
  if (!row) return;
  if (!m || !m.days_analyzed || m.days_analyzed < 3) {
    row.innerHTML = '<div class="text-muted text-xs py-3">Metriche disponibili con almeno 3 giorni di storico portafoglio.</div>';
    return;
  }
  const dd = m.max_drawdown_pct ?? 0;
  const vol = m.annualized_volatility_pct ?? 0;
  const sharpe = m.sharpe_ratio ?? 0;
  const beta = m.weighted_beta ?? 0;
  const annRet = m.annualized_return_pct ?? 0;

  const sharpeClass = sharpe >= 1 ? 'text-profit' : (sharpe < 0 ? 'text-loss' : 'text-primary');
  row.innerHTML = `
    <div class="risk-card scale-in">
      <div class="risk-label">📉 Max Drawdown</div>
      <div class="risk-value font-mono text-loss">${dd.toFixed(2)}%</div>
      <div class="risk-sub">Picco → minimo (${m.days_analyzed}gg)</div>
    </div>
    <div class="risk-card scale-in">
      <div class="risk-label">🌊 Volatilità Ann.</div>
      <div class="risk-value font-mono text-primary">${vol.toFixed(2)}%</div>
      <div class="risk-sub">Dev. standard × √252</div>
    </div>
    <div class="risk-card scale-in">
      <div class="risk-label">⚖️ Sharpe Ratio</div>
      <div class="risk-value font-mono ${sharpeClass}">${sharpe.toFixed(2)}</div>
      <div class="risk-sub">Rf ${m.risk_free_rate_pct ?? 2}% annuo</div>
    </div>
    <div class="risk-card scale-in">
      <div class="risk-label">🎯 Beta Pesato</div>
      <div class="risk-value font-mono" style="color: var(--purple-color);">${beta.toFixed(2)}</div>
      <div class="risk-sub">Sensibilità al mercato</div>
    </div>
    <div class="risk-card scale-in">
      <div class="risk-label">🚀 Rendimento Ann.</div>
      <div class="risk-value font-mono ${annRet >= 0 ? 'text-profit' : 'text-loss'}">${annRet >= 0 ? '+' : ''}${annRet.toFixed(2)}%</div>
      <div class="risk-sub">CAGR geometrico</div>
    </div>
  `;
};

const loadRiskMetrics = async () => {
  try {
    const metrics = await api.getRiskMetrics(180).catch(() => null);
    renderRiskMetrics(metrics);
  } catch (e) {
    console.error('Errore metriche di rischio:', e);
  }
};

const loadDashboardData = async (isSilentRefresh = false) => {
  try {
    if (!isSilentRefresh) {
      showLoading('dashboardContent');
    }
    
    const [dashData, portfolio, heatmap, advice] = await Promise.all([
      api.getDashboard().catch(() => ({})),
      api.getPortfolio().catch(() => []),
      api.getHeatmap().catch(() => []),
      api.getLatestAdvice().catch(() => [])
    ]);

    const summary = dashData.portfolio_summary || {};

    // 1. Stat Cards (con flash green/red sui valori variati)
    const totalValEl = document.getElementById('statTotalValue');
    setStatValue(totalValEl, formatCurrency(summary.total_value || 0), summary.total_value || 0);
    
    const dailyEl = document.getElementById('statDailyPnL');
    if (dailyEl) {
      const dPnL = summary.daily_pnl || 0;
      const dPct = summary.daily_pnl_percent || 0;
      setStatValue(dailyEl, `${formatCurrency(dPnL)} (${formatPercent(dPct)})`, dPnL);
      dailyEl.classList.toggle('text-profit', dPnL >= 0);
      dailyEl.classList.toggle('text-loss', dPnL < 0);
    }
    
    const totalEl = document.getElementById('statTotalPnL');
    if (totalEl) {
      const tPnL = summary.total_pnl || 0;
      const tPct = summary.total_pnl_percent || 0;
      setStatValue(totalEl, `${formatCurrency(tPnL)} (${formatPercent(tPct)})`, tPnL);
      totalEl.classList.toggle('text-profit', tPnL >= 0);
      totalEl.classList.toggle('text-loss', tPnL < 0);
    }

    // Dividends
    const divEl = document.getElementById('statDividends');
    if (divEl) divEl.textContent = `${formatCurrency(summary.estimated_annual_dividends || 0)}/anno`;
    const divYieldEl = document.getElementById('statDividendYield');
    if (divYieldEl) divYieldEl.textContent = `Yield Stimato: ${(summary.estimated_dividend_yield || 0).toFixed(2)}%`;

    // Top Gainer
    const tgEl = document.getElementById('statTopGainer');
    const tgDescEl = document.getElementById('statTopGainerDesc');
    if (tgEl && summary.top_gainer) {
      tgEl.textContent = `${summary.top_gainer.ticker} (${formatPercent(summary.top_gainer.pnl_percent)})`;
      tgDescEl.textContent = `P&L Netto: ${formatCurrency(summary.top_gainer.pnl_absolute)}`;
    } else if (tgEl) {
      tgEl.textContent = '--';
      tgDescEl.textContent = 'Nessuna posizione in utile';
    }

    // 2. Chart
    await loadPerformanceChart(currentChartDays);

    // 3. Heatmap
    renderHeatmap(heatmap);

    // 4. Holdings Table
    const tbody = document.getElementById('holdingsTableBody');
    if (tbody) {
      if (portfolio.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-6">Nessun titolo nel portafoglio. Clicca su <strong>"Gestisci Portafoglio"</strong> per aggiungere la prima posizione.</td></tr>';
      } else {
        tbody.innerHTML = portfolio.slice(0, 6).map(item => {
          const pnl = item.pnl_absolute ?? 0;
          const pnlPct = item.pnl_percent ?? 0;
          const curPrice = item.current_price ?? item.avg_purchase_price ?? 0;
          const totalVal = item.total_value ?? (item.quantity * curPrice);
          const flag = item.market === 'IT' ? '🇮🇹' : '🇺🇸';

          return `
            <tr>
              <td>
                <div class="flex items-center gap-2">
                  <span>${flag}</span>
                  <div>
                    <a href="#" class="stock-ticker-link font-bold font-mono" data-stock="${item.ticker}">${item.ticker}</a>
                    <div class="text-xs text-secondary">${item.name || item.ticker}</div>
                  </div>
                </div>
              </td>
              <td class="text-right font-mono font-bold">${item.quantity}</td>
              <td class="text-right font-mono font-bold">${formatCurrency(curPrice, item.currency)}</td>
              <td class="text-right font-mono font-bold text-primary">${formatCurrency(totalVal, item.currency)}</td>
              <td class="text-right font-mono font-bold ${pnl >= 0 ? 'text-profit' : 'text-loss'}">
                ${formatCurrency(pnl, item.currency)}
              </td>
              <td class="text-right font-mono font-bold ${pnlPct >= 0 ? 'text-profit' : 'text-loss'}">
                ${formatPercent(pnlPct)}
              </td>
              <td class="text-center">
                <button class="btn btn-ghost btn-sm" onclick="window.openStockModal('${item.ticker}')" title="Apri scheda completa">
                  🔍
                </button>
              </td>
            </tr>
          `;
        }).join('');
      }
    }

    // 5. Recent Advice
    const adviceList = document.getElementById('recentAdviceList');
    if (adviceList) {
      if (advice.length === 0) {
        adviceList.innerHTML = '<div class="text-center text-muted py-6 text-xs">Nessuna analisi recente. Generane una nella sezione Consigli.</div>';
      } else {
        adviceList.innerHTML = advice.slice(0, 2).map(adv => {
          const isIT = adv.market === 'IT';
          const flag = isIT ? '🇮🇹' : '🇺🇸';
          const action = (adv.action || 'HOLD').toUpperCase();
          let badgeClass = 'badge-hold';
          if (action.includes('ACCUMULO') || action.includes('BUY')) badgeClass = 'badge-buy';
          else if (action.includes('PROFITTO') || action.includes('SELL')) badgeClass = 'badge-sell';

          return `
            <div class="card p-3 border border-border-color" style="background-color: rgba(0, 0, 0, 0.2);">
              <div class="flex justify-between items-center mb-1.5">
                <span class="font-bold text-primary flex items-center gap-1.5 text-sm">
                  <span>${flag}</span>
                  <span>${adv.title || (isIT ? 'Borsa Italiana' : 'Wall Street')}</span>
                </span>
                <span class="badge ${badgeClass}">${action}</span>
              </div>
              <p class="text-xs text-secondary leading-relaxed" style="display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;">
                ${adv.overview || adv.strategy || 'Nessuna descrizione disponibile.'}
              </p>
            </div>
          `;
        }).join('');
      }
    }

    updateMarketStatus(dashData.market_status);

  } catch (error) {
    if (!isSilentRefresh) {
      showToast('Errore nel caricamento della dashboard', 'error');
    }
    console.error('Errore dashboard:', error);
  } finally {
    if (!isSilentRefresh) {
      hideLoading('dashboardContent');
    }
  }
};

document.addEventListener('DOMContentLoaded', () => {
  initChart();
  loadDashboardData();
  loadRiskMetrics();
  
  // Timeframe selector
  const tfGroup = document.getElementById('dashboardTimeframeGroup');
  if (tfGroup) {
    tfGroup.querySelectorAll('.timeframe-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        tfGroup.querySelectorAll('.timeframe-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentChartDays = parseInt(btn.dataset.days) || 30;
        loadPerformanceChart(currentChartDays);
      });
    });
  }

  // Benchmark chips (confronto con S&P 500 / FTSE MIB)
  const benchChips = document.getElementById('benchmarkChips');
  if (benchChips) {
    benchChips.querySelectorAll('.bench-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        benchChips.querySelectorAll('.bench-chip').forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        activeBenchmark = chip.dataset.bench || 'none';
        loadPerformanceChart(currentChartDays);
      });
    });
  }

  // Auto-refresh silenzioso ogni 60s
  setInterval(() => {
    if (!document.hidden) {
      loadDashboardData(true);
    }
  }, 60000);
});
