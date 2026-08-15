import { api } from './api.js';
import { formatCurrency, formatPercent, showLoading, hideLoading, showToast, getChartThemeColors } from './app.js';

let chart = null;
let lineSeries = null;
let candleSeries = null;
let volumeSeries = null;
let resizeObserver = null;
let currentChartDays = parseInt(localStorage.getItem('dashboard_timeframe')) || 30;
let currentChartType = 'area';
let benchSeriesMap = {};
let activeBenchmark = 'none';
let performanceRawData = [];

const renderSkeletons = () => {
  const statGrid = document.querySelector('.stat-grid');
  if (statGrid) {
    const valEls = statGrid.querySelectorAll('.stat-value');
    valEls.forEach(el => {
      el.innerHTML = '<div class="skeleton skeleton-value"></div>';
    });
  }

  const tbody = document.getElementById('holdingsTableBody');
  if (tbody) {
    tbody.innerHTML = `
      <tr><td colspan="7"><div class="skeleton skeleton-row"></div></td></tr>
      <tr><td colspan="7"><div class="skeleton skeleton-row"></div></td></tr>
      <tr><td colspan="7"><div class="skeleton skeleton-row"></div></td></tr>
    `;
  }

  const heatmap = document.getElementById('marketHeatmap');
  if (heatmap) {
    heatmap.innerHTML = `
      <div class="skeleton skeleton-card"></div>
      <div class="skeleton skeleton-card"></div>
      <div class="skeleton skeleton-card"></div>
      <div class="skeleton skeleton-card"></div>
    `;
  }
};

const initChart = () => {
  const chartContainer = document.getElementById('portfolioChart');
  if (!chartContainer || typeof LightweightCharts === 'undefined') return;
  
  if (chart) {
    try { chart.remove(); } catch(e){}
  }

  const themeColors = getChartThemeColors();

  chart = LightweightCharts.createChart(chartContainer, {
    layout: {
      background: { type: 'solid', color: 'transparent' },
      textColor: themeColors.textColor,
      fontFamily: 'Inter, system-ui, sans-serif',
      fontSize: 12
    },
    grid: {
      vertLines: { color: themeColors.gridColor },
      horzLines: { color: themeColors.gridColor },
    },
    rightPriceScale: {
      borderVisible: false,
      scaleMargins: { top: 0.1, bottom: 0.25 }
    },
    timeScale: {
      borderVisible: false,
      fixLeftEdge: true,
      fixRightEdge: true
    },
    crosshair: {
      vertLine: { color: themeColors.lineColor, width: 1, style: 3 },
      horzLine: { color: themeColors.lineColor, width: 1, style: 3 }
    }
  });

  lineSeries = chart.addAreaSeries({
    topColor: themeColors.topColor,
    bottomColor: themeColors.bottomColor,
    lineColor: themeColors.lineColor,
    lineWidth: 2,
    crosshairMarkerVisible: true,
  });

  candleSeries = chart.addCandlestickSeries({
    upColor: themeColors.upColor,
    downColor: themeColors.downColor,
    borderUpColor: themeColors.upColor,
    borderDownColor: themeColors.downColor,
    wickUpColor: themeColors.upColor,
    wickDownColor: themeColors.downColor,
    visible: false
  });

  volumeSeries = chart.addHistogramSeries({
    color: themeColors.volumeColor,
    priceFormat: { type: 'volume' },
    priceScaleId: '',
    scaleMargins: { top: 0.82, bottom: 0 }
  });

  // Benchmark series
  benchSeriesMap['^GSPC'] = chart.addLineSeries({
    color: '#10b981',
    lineWidth: 2,
    priceLineVisible: false,
    lastValueVisible: true,
    crosshairMarkerVisible: false,
    visible: false
  });

  benchSeriesMap['FTSEMIB.MI'] = chart.addLineSeries({
    color: '#f59e0b',
    lineWidth: 2,
    priceLineVisible: false,
    lastValueVisible: true,
    crosshairMarkerVisible: false,
    visible: false
  });

  if (window.ResizeObserver) {
    if (resizeObserver) {
      try { resizeObserver.disconnect(); } catch(e){}
    }
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

const updateChartTheme = () => {
  if (!chart) return;
  const themeColors = getChartThemeColors();
  chart.applyOptions({
    layout: { textColor: themeColors.textColor },
    grid: {
      vertLines: { color: themeColors.gridColor },
      horzLines: { color: themeColors.gridColor }
    },
    crosshair: {
      vertLine: { color: themeColors.lineColor },
      horzLine: { color: themeColors.lineColor }
    }
  });
  lineSeries?.applyOptions({
    topColor: themeColors.topColor,
    bottomColor: themeColors.bottomColor,
    lineColor: themeColors.lineColor
  });
  candleSeries?.applyOptions({
    upColor: themeColors.upColor,
    downColor: themeColors.downColor,
    borderUpColor: themeColors.upColor,
    borderDownColor: themeColors.downColor,
    wickUpColor: themeColors.upColor,
    wickDownColor: themeColors.downColor
  });
  volumeSeries?.applyOptions({ color: themeColors.volumeColor });
};

const applyChartData = () => {
  if (!chart || performanceRawData.length === 0) return;

  if (currentChartType === 'candles') {
    lineSeries.applyOptions({ visible: false });
    candleSeries.applyOptions({ visible: true });
    candleSeries.setData(performanceRawData.map(d => ({
      time: d.date,
      open: d.open || (d.value * 0.995),
      high: d.high || (d.value * 1.008),
      low: d.low || (d.value * 0.992),
      close: d.value
    })));
  } else {
    candleSeries.applyOptions({ visible: false });
    lineSeries.applyOptions({ visible: true });
    lineSeries.setData(performanceRawData.map(d => ({ time: d.date, value: d.value })));
  }

  volumeSeries.setData(performanceRawData.map((d, i) => ({
    time: d.date,
    value: d.volume || (d.value * 50),
    color: i > 0 && d.value >= performanceRawData[i-1].value ? 'rgba(158, 206, 106, 0.35)' : 'rgba(247, 118, 142, 0.35)'
  })));

  chart.timeScale().fitContent();
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
    container.innerHTML = `
      <div class="text-muted text-xs py-6 text-center" style="grid-column: 1 / -1;">
        Nessun titolo attivo per la heatmap. 
        <button class="btn btn-primary btn-sm mt-2" id="btnHeatmapSeedDemo">🚀 Inizializza Dati Demo</button>
      </div>
    `;
    container.querySelector('#btnHeatmapSeedDemo')?.addEventListener('click', triggerSeedDemo);
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

const triggerSeedDemo = async () => {
  try {
    showLoading('dashboardContent');
    const res = await api.seedDemo();
    showToast(res.message || 'Demo caricata con successo!', 'success');
    loadDashboardData();
  } catch (e) {
    showToast(e.message || 'Errore nel caricamento della demo', 'error');
  } finally {
    hideLoading('dashboardContent');
  }
};

const loadPerformanceChart = async (days = 30) => {
  if (!chart) return;
  try {
    const performance = await api.getPerformance(days).catch(() => ({ data: [] }));
    if (performance.data && performance.data.length > 0) {
      performanceRawData = performance.data;
      applyChartData();
    }
  } catch (e) {
    console.error('Errore storico performance:', e);
  }
};

const loadRiskMetrics = async () => {
  const container = document.getElementById('riskMetricsRow');
  if (!container) return;

  try {
    const metrics = await api.getRiskMetrics(180).catch(() => ({}));
    if (!metrics || Object.keys(metrics).length === 0) {
      container.innerHTML = '<div class="text-muted text-xs py-2 text-center" style="grid-column: 1 / -1;">Metriche calcolate dopo l\'inserimento di posizioni storiche.</div>';
      return;
    }

    container.innerHTML = `
      <div class="stat-card p-3" style="background: var(--surface-hover);">
        <div class="text-xs text-muted">Max Drawdown</div>
        <div class="text-lg font-bold font-mono text-loss mt-1">${formatPercent(metrics.max_drawdown || 0)}</div>
        <div class="text-[11px] text-muted mt-0.5">Picco-minimo</div>
      </div>
      <div class="stat-card p-3" style="background: var(--surface-hover);">
        <div class="text-xs text-muted">Volatilità Annua</div>
        <div class="text-lg font-bold font-mono text-primary mt-1">${(metrics.annualized_volatility || 0).toFixed(1)}%</div>
        <div class="text-[11px] text-muted mt-0.5">Deviazione std</div>
      </div>
      <div class="stat-card p-3" style="background: var(--surface-hover);">
        <div class="text-xs text-muted">Sharpe Ratio</div>
        <div class="text-lg font-bold font-mono ${(metrics.sharpe_ratio || 0) >= 1 ? 'text-profit' : 'text-primary'} mt-1">${(metrics.sharpe_ratio || 0).toFixed(2)}</div>
        <div class="text-[11px] text-muted mt-0.5">Rendimento / Rischio</div>
      </div>
      <div class="stat-card p-3" style="background: var(--surface-hover);">
        <div class="text-xs text-muted">Beta Pesato</div>
        <div class="text-lg font-bold font-mono text-primary mt-1">${(metrics.weighted_beta || 1.0).toFixed(2)}</div>
        <div class="text-[11px] text-muted mt-0.5">Sensibilità mercato</div>
      </div>
    `;
  } catch (e) {
    console.error('Errore metriche rischio:', e);
  }
};

const clearSkeletons = () => {
  const statTotal = document.getElementById('statTotalValue');
  if (statTotal && statTotal.querySelector('.skeleton')) statTotal.textContent = '0,00 €';
  
  const dailyEl = document.getElementById('statDailyPnL');
  if (dailyEl && dailyEl.querySelector('.skeleton')) dailyEl.textContent = '0,00 € (+0.00%)';
  
  const totalEl = document.getElementById('statTotalPnL');
  if (totalEl && totalEl.querySelector('.skeleton')) totalEl.textContent = '0,00 € (+0.00%)';
  
  const divEl = document.getElementById('statDividends');
  if (divEl && divEl.querySelector('.skeleton')) divEl.textContent = '0,00 €/anno';
  
  const tgEl = document.getElementById('statTopGainer');
  if (tgEl && tgEl.querySelector('.skeleton')) tgEl.textContent = '--';

  const riskRow = document.getElementById('riskMetricsRow');
  if (riskRow && riskRow.querySelector('.skeleton')) {
    riskRow.innerHTML = '<div class="text-muted text-xs py-2 text-center" style="grid-column: 1 / -1;">Metriche calcolate dopo l\'inserimento di posizioni nel portafoglio.</div>';
  }

  const recentAdv = document.getElementById('recentAdviceList');
  if (recentAdv && recentAdv.textContent.includes('Caricamento')) {
    recentAdv.innerHTML = '<div class="text-center text-muted py-4 text-xs">Nessuna analisi recente.</div>';
  }

  const heatmap = document.getElementById('marketHeatmap');
  if (heatmap && (heatmap.querySelector('.skeleton') || heatmap.textContent.includes('Caricamento'))) {
    renderHeatmap([]);
  }

  const tbody = document.getElementById('holdingsTableBody');
  if (tbody && (tbody.querySelector('.skeleton') || tbody.textContent.includes('Caricamento'))) {
    tbody.innerHTML = `
      <tr>
        <td colspan="7" class="text-center text-muted py-6">
          Nessun titolo nel portafoglio.
          <div class="mt-2 flex justify-center gap-2">
            <a href="/static/portfolio.html" class="btn btn-primary btn-sm">➕ Aggiungi Holding</a>
            <button class="btn btn-ghost btn-sm" id="btnTableSeedDemoFallback">🚀 Prova Demo</button>
          </div>
        </td>
      </tr>
    `;
    tbody.querySelector('#btnTableSeedDemoFallback')?.addEventListener('click', triggerSeedDemo);
  }
};

const loadDashboardData = async (isSilentRefresh = false) => {
  try {
    if (!isSilentRefresh) {
      renderSkeletons();
    }
    
    const [dashData, portfolioRes, heatmapRes, adviceRes] = await Promise.all([
      api.getDashboard().catch(() => ({})),
      api.getPortfolio().catch(() => []),
      api.getHeatmap().catch(() => []),
      api.getLatestAdvice().catch(() => [])
    ]);

    const summary = (dashData && dashData.portfolio_summary) ? dashData.portfolio_summary : {};
    const portfolio = Array.isArray(portfolioRes) ? portfolioRes : [];
    const heatmap = Array.isArray(heatmapRes) ? heatmapRes : [];
    const advice = Array.isArray(adviceRes) ? adviceRes : [];

    // 1. Stat Cards
    const totalValEl = document.getElementById('statTotalValue');
    if (totalValEl) totalValEl.textContent = formatCurrency(summary.total_value || 0);
    
    const dailyEl = document.getElementById('statDailyPnL');
    if (dailyEl) {
      const dPnL = summary.daily_pnl || 0;
      const dPct = summary.daily_pnl_percent || 0;
      dailyEl.textContent = `${formatCurrency(dPnL)} (${formatPercent(dPct)})`;
      dailyEl.className = `stat-value font-mono ${dPnL >= 0 ? 'text-profit' : 'text-loss'}`;
    }
    
    const totalEl = document.getElementById('statTotalPnL');
    if (totalEl) {
      const tPnL = summary.total_pnl || 0;
      const tPct = summary.total_pnl_percent || 0;
      totalEl.textContent = `${formatCurrency(tPnL)} (${formatPercent(tPct)})`;
      totalEl.className = `stat-value font-mono ${tPnL >= 0 ? 'text-profit' : 'text-loss'}`;
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
      if (tgDescEl) tgDescEl.textContent = `P&L Netto: ${formatCurrency(summary.top_gainer.pnl_absolute)}`;
    } else if (tgEl) {
      tgEl.textContent = '--';
      if (tgDescEl) tgDescEl.textContent = 'Nessuna posizione in utile';
    }

    // 2. Chart & Risk
    await loadPerformanceChart(currentChartDays).catch(err => console.debug('Chart error:', err));
    loadRiskMetrics().catch(err => console.debug('Risk error:', err));

    // 3. Heatmap
    renderHeatmap(heatmap);

    // 4. Holdings Table
    const tbody = document.getElementById('holdingsTableBody');
    if (tbody) {
      if (portfolio.length === 0) {
        tbody.innerHTML = `
          <tr>
            <td colspan="7" class="text-center text-muted py-6">
              Nessun titolo nel portafoglio. 
              <div class="mt-2 flex justify-center gap-2">
                <a href="/static/portfolio.html" class="btn btn-primary btn-sm">➕ Aggiungi Holding</a>
                <button class="btn btn-ghost btn-sm" id="btnTableSeedDemo">🚀 Prova Demo</button>
              </div>
            </td>
          </tr>
        `;
        tbody.querySelector('#btnTableSeedDemo')?.addEventListener('click', triggerSeedDemo);
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
            <div class="card p-3 border border-border-color" style="background-color: var(--surface-hover);">
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

    if (dashData && dashData.market_status) {
      updateMarketStatus(dashData.market_status);
    } else {
      updateMarketStatus();
    }

  } catch (error) {
    clearSkeletons();
    if (!isSilentRefresh) {
      showToast('Errore nel caricamento della dashboard', 'error');
    }
    console.error('Errore dashboard:', error);
  }
};

const initDashboard = () => {
  initChart();
  loadDashboardData();
  
  // Listen for theme change
  window.addEventListener('themeChanged', () => {
    updateChartTheme();
  });

  // Timeframe selector with persistence
  const tfGroup = document.getElementById('dashboardTimeframeGroup');
  if (tfGroup) {
    tfGroup.querySelectorAll('.timeframe-btn').forEach(btn => {
      const d = parseInt(btn.dataset.days);
      if (d === currentChartDays) {
        tfGroup.querySelectorAll('.timeframe-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
      }

      btn.addEventListener('click', () => {
        tfGroup.querySelectorAll('.timeframe-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentChartDays = parseInt(btn.dataset.days) || 30;
        localStorage.setItem('dashboard_timeframe', currentChartDays);
        loadPerformanceChart(currentChartDays);
      });
    });
  }

  // Chart type switcher
  const chartTypeGroup = document.getElementById('chartTypeGroup');
  if (chartTypeGroup) {
    chartTypeGroup.querySelectorAll('.chart-type-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        chartTypeGroup.querySelectorAll('.chart-type-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentChartType = btn.dataset.type;
        applyChartData();
      });
    });
  }

  // Benchmark switcher
  const benchChips = document.getElementById('benchmarkChips');
  if (benchChips) {
    benchChips.querySelectorAll('.bench-chip').forEach(chip => {
      chip.addEventListener('click', async () => {
        benchChips.querySelectorAll('.bench-chip').forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        activeBenchmark = chip.dataset.bench;

        if (activeBenchmark === 'none') {
          benchSeriesMap['^GSPC']?.applyOptions({ visible: false });
          benchSeriesMap['FTSEMIB.MI']?.applyOptions({ visible: false });
        } else {
          try {
            const benchData = await api.getBenchmarks(currentChartDays);
            if (activeBenchmark === '^GSPC' || activeBenchmark === 'both') {
              const spData = benchData?.benchmarks?.['^GSPC'] || [];
              benchSeriesMap['^GSPC']?.applyOptions({ visible: true });
              benchSeriesMap['^GSPC']?.setData(spData.map(p => ({ time: p.date, value: p.value })));
            } else {
              benchSeriesMap['^GSPC']?.applyOptions({ visible: false });
            }

            if (activeBenchmark === 'FTSEMIB.MI' || activeBenchmark === 'both') {
              const mibData = benchData?.benchmarks?.['FTSEMIB.MI'] || [];
              benchSeriesMap['FTSEMIB.MI']?.applyOptions({ visible: true });
              benchSeriesMap['FTSEMIB.MI']?.setData(mibData.map(p => ({ time: p.date, value: p.value })));
            } else {
              benchSeriesMap['FTSEMIB.MI']?.applyOptions({ visible: false });
            }
          } catch (e) {
            console.error('Errore benchmark:', e);
          }
        }
      });
    });
  }

  // Auto-refresh ogni 60s
  setInterval(() => {
    if (!document.hidden) {
      loadDashboardData(true);
    }
  }, 60000);
};

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initDashboard);
} else {
  initDashboard();
}
