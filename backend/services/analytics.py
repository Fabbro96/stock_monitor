import logging
import asyncio
import math
import time
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.stock import PriceHistory
from backend.services.market_data import MarketDataService
from backend.services.portfolio_service import build_portfolio_rows

logger = logging.getLogger(__name__)

TRADING_DAYS_PER_YEAR = 252

# Cache risultati costosi: (result, timestamp)
_RISK_CACHE: dict[str, tuple[dict, float]] = {}
_RISK_CACHE_TTL = 300.0  # 5 minuti


# ---------------------------------------------------------------------------
# Serie storica giornaliera del valore del portafoglio
# ---------------------------------------------------------------------------
async def build_portfolio_daily_series(db: AsyncSession, days: int = 180) -> list[dict]:
    """
    Costruisce la serie giornaliera del valore del portafoglio:
    1. chiusura giornaliera da PriceHistory (dati raccolti dallo scheduler)
    2. backfill con candele giornaliere Yahoo (fetch_stock_candles '1y')
    3. forward-fill dei giorni mancanti; prezzo corrente per l'ultimo giorno
    Ritorna [{"date": "YYYY-MM-DD", "value": float}] ordinato per data.
    """
    portfolio = await build_portfolio_rows(db)
    if not portfolio:
        return []

    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=days)

    # 1. Chiusure giornaliere aggregate da PriceHistory (aggregazione in Python
    #    per massima robustezza con i tipi data di SQLite/aiosqlite)
    stock_ids = [h["stock_id"] for h in portfolio]
    cutoff = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
    result = await db.execute(
        select(PriceHistory.stock_id, PriceHistory.timestamp, PriceHistory.close)
        .where(PriceHistory.stock_id.in_(stock_ids), PriceHistory.timestamp >= cutoff)
        .order_by(PriceHistory.timestamp)
    )
    rows = result.all()

    daily_close: dict[int, dict[str, float]] = {sid: {} for sid in stock_ids}
    for sid, ts, close in rows:
        if not close:
            continue
        ts_date = ts.date() if hasattr(ts, "date") else ts
        day_str = str(ts_date)[:10]
        # Le righe sono ordinate per timestamp: l'ultima chiusura del giorno vince
        daily_close[sid][day_str] = float(close)

    # 2. Backfill con candele giornaliere Yahoo per i ticker con storico insufficiente
    backfill_tasks = {}
    for h in portfolio:
        if len(daily_close.get(h["stock_id"], {})) < 5:
            backfill_tasks[h["stock_id"]] = MarketDataService.fetch_stock_candles(h["ticker"], "1y")
    if backfill_tasks:
        results = await asyncio.gather(*backfill_tasks.values(), return_exceptions=True)
        for sid, res in zip(backfill_tasks.keys(), results):
            if isinstance(res, list) and res:
                merged = daily_close.setdefault(sid, {})
                for c in res:
                    if isinstance(c.get("time"), str) and c.get("close"):
                        merged.setdefault(c["time"], float(c["close"]))

    # 3. Costruzione serie giornaliera con forward-fill
    series = []
    last_known: dict[int, float] = {}
    for h in portfolio:
        price = h.get("current_price") or h.get("avg_purchase_price")
        if price:
            last_known[h["stock_id"]] = float(price)

    current = start_date
    while current <= today:
        day_str = current.strftime("%Y-%m-%d")
        total = 0.0
        has_data = False
        for h in portfolio:
            closes = daily_close.get(h["stock_id"], {})
            if day_str in closes:
                last_known[h["stock_id"]] = closes[day_str]
            price = last_known.get(h["stock_id"])
            if price:
                total += float(h["quantity"]) * price
                has_data = True
        if has_data:
            series.append({"date": day_str, "value": round(total, 2)})
        current += timedelta(days=1)

    return series


def normalize_growth(series: list[dict]) -> list[dict]:
    """Normalizza una serie di valori in crescita percentuale dal primo punto."""
    if not series:
        return []
    base = series[0]["value"]
    if not base:
        base = next((p["value"] for p in series if p["value"]), 0.0)
    if not base:
        return [{"date": p["date"], "growth_pct": 0.0} for p in series]
    return [
        {"date": p["date"], "growth_pct": round((p["value"] / base - 1.0) * 100.0, 3)}
        for p in series
    ]


# ---------------------------------------------------------------------------
# Metriche di rischio quantitative
# ---------------------------------------------------------------------------
async def compute_risk_metrics(db: AsyncSession, days: int = 180) -> dict:
    """
    Calcola le metriche di rischio/performance del portafoglio:
    Max Drawdown, Volatilità annualizzata, Sharpe Ratio, Beta pesato,
    Rendimento annualizzato.
    """
    cache_key = f"risk:{days}"
    now_ts = time.time()
    cached = _RISK_CACHE.get(cache_key)
    if cached and now_ts - cached[1] < _RISK_CACHE_TTL:
        return cached[0]

    series = await build_portfolio_daily_series(db, days=days)
    values = [p["value"] for p in series if p["value"] > 0]

    metrics = {
        "days_analyzed": len(values),
        "series_start": series[0]["date"] if series else None,
        "series_end": series[-1]["date"] if series else None,
        "max_drawdown_pct": 0.0,
        "annualized_volatility_pct": 0.0,
        "sharpe_ratio": 0.0,
        "annualized_return_pct": 0.0,
        "weighted_beta": 0.0,
        "risk_free_rate_pct": round(settings.RISK_FREE_RATE * 100, 2),
        "current_value": values[-1] if values else 0.0,
    }

    if len(values) >= 3:
        arr = np.asarray(values, dtype=float)

        # --- Max Drawdown (peak-to-trough) ---
        running_max = np.maximum.accumulate(arr)
        drawdowns = (arr - running_max) / running_max
        metrics["max_drawdown_pct"] = round(float(drawdowns.min()) * 100.0, 2)

        # --- Rendimenti giornalieri -> volatilità annualizzata ---
        daily_returns = np.diff(arr) / arr[:-1]
        ann_vol = float(np.std(daily_returns, ddof=1) * math.sqrt(TRADING_DAYS_PER_YEAR))
        metrics["annualized_volatility_pct"] = round(ann_vol * 100.0, 2)

        # --- Rendimento annualizzato (geometrico) ---
        n_days = max(len(arr) - 1, 1)
        total_return = arr[-1] / arr[0]
        years = n_days / TRADING_DAYS_PER_YEAR
        if total_return > 0 and years > 0:
            ann_return = total_return ** (1.0 / years) - 1.0
        else:
            ann_return = 0.0
        metrics["annualized_return_pct"] = round(ann_return * 100.0, 2)

        # --- Sharpe Ratio stimato ---
        if ann_vol > 1e-9:
            metrics["sharpe_ratio"] = round((ann_return - settings.RISK_FREE_RATE) / ann_vol, 2)

    # --- Beta pesato (pesi = controvalore attuale) ---
    portfolio = await build_portfolio_rows(db)
    total_value = sum(h["total_value"] for h in portfolio)
    if portfolio and total_value > 0:
        deep_tasks = [MarketDataService.fetch_stock_deep_dive(h["ticker"]) for h in portfolio]
        deep_results = await asyncio.gather(*deep_tasks, return_exceptions=True)
        weighted_beta = 0.0
        betas = {}
        for h, deep in zip(portfolio, deep_results):
            beta = deep.get("beta") if isinstance(deep, dict) else None
            try:
                beta = float(beta)
            except (TypeError, ValueError):
                beta = None
            if beta is None or not (0.0 <= beta <= 5.0):
                beta = 1.0  # default prudenziale
            betas[h["ticker"]] = round(beta, 2)
            weighted_beta += (h["total_value"] / total_value) * beta
        metrics["weighted_beta"] = round(weighted_beta, 2)
        metrics["betas"] = betas

    _RISK_CACHE[cache_key] = (metrics, now_ts)
    return metrics


# ---------------------------------------------------------------------------
# Confronto Benchmark (S&P 500 / FTSE MIB)
# ---------------------------------------------------------------------------
BENCHMARKS = {
    "^GSPC": {"name": "S&P 500", "flag": "🇺🇸"},
    "FTSEMIB.MI": {"name": "FTSE MIB", "flag": "🇮🇹"},
}


def _period_for_days(days: int) -> str:
    if days <= 31:
        return "3mo"
    if days <= 120:
        return "6mo"
    if days <= 380:
        return "1y"
    return "5y"


async def compute_benchmark_comparison(db: AsyncSession, days: int = 90, benchmark_tickers: list[str] | None = None) -> dict:
    """
    Confronta la curva di crescita % del portafoglio con uno o più indici di mercato.
    """
    if benchmark_tickers is None:
        benchmark_tickers = list(BENCHMARKS.keys())

    series = await build_portfolio_daily_series(db, days=days)
    portfolio_growth = normalize_growth(series)

    start_date = series[0]["date"] if series else None
    end_date = series[-1]["date"] if series else None

    period = _period_for_days(max(days, 90))
    tasks = [MarketDataService.fetch_index_history(t, period) for t in benchmark_tickers]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    benchmarks_out = {}
    for ticker, res in zip(benchmark_tickers, results):
        meta = BENCHMARKS.get(ticker, {"name": ticker, "flag": "📊"})
        if isinstance(res, list) and res:
            filtered = [
                p for p in res
                if (start_date is None or p["time"] >= start_date)
                and (end_date is None or p["time"] <= end_date)
            ]
            if not filtered:
                filtered = res[-days:] if len(res) > days else res
            benchmarks_out[ticker] = {
                "name": meta["name"],
                "flag": meta["flag"],
                "data": normalize_growth([{"date": p["time"], "value": p["close"]} for p in filtered])
            }
        else:
            benchmarks_out[ticker] = {"name": meta["name"], "flag": meta["flag"], "data": []}

    return {
        "start_date": start_date,
        "end_date": end_date,
        "portfolio": portfolio_growth,
        "benchmarks": benchmarks_out
    }


# ---------------------------------------------------------------------------
# Motore di Ribilanciamento Smart
# ---------------------------------------------------------------------------
def _market_of(holding_row: dict) -> str:
    m = (holding_row.get("market") or "US").upper()
    if m.startswith("EU"):
        return "EU"
    return m


def compute_rebalance_plan(portfolio: list[dict], targets: list[dict], extra_cash: float = 0.0) -> dict:
    """
    Motore di ribilanciamento: date le allocazioni target e le posizioni correnti,
    calcola per ciascun bucket il delta e genera gli ordini (buy/sell) necessari,
    distribuiti pro-quota sui titoli del bucket.

    targets: [{"id", "name", "target_percent", "scope_type" (MARKET|TICKERS|CASH), "scope_value"}]
    """
    total_value = sum(h["total_value"] for h in portfolio) + max(extra_cash, 0.0)

    allocations = []
    orders = []

    for target in targets:
        scope_type = (target.get("scope_type") or "MARKET").upper()
        scope_value = (target.get("scope_value") or "").strip().upper()
        target_pct = float(target.get("target_percent") or 0.0)
        target_value = total_value * target_pct / 100.0

        # Identifica i titoli nel bucket
        constituents = []
        if scope_type == "MARKET":
            constituents = [h for h in portfolio if _market_of(h) == scope_value]
        elif scope_type == "TICKERS":
            ticker_set = {t.strip().upper() for t in scope_value.split(",") if t.strip()}
            constituents = [h for h in portfolio if h["ticker"].upper() in ticker_set]
        # CASH -> nessun titolo costituente

        current_value = max(extra_cash, 0.0) if scope_type == "CASH" else sum(h["total_value"] for h in constituents)
        delta = target_value - current_value
        current_pct = (current_value / total_value * 100.0) if total_value > 0 else 0.0

        allocations.append({
            "id": target.get("id"),
            "name": target.get("name"),
            "scope_type": scope_type,
            "scope_value": scope_value,
            "target_percent": target_pct,
            "current_percent": round(current_pct, 2),
            "target_value": round(target_value, 2),
            "current_value": round(current_value, 2),
            "delta": round(delta, 2),
            "drift_pct": round(target_pct - current_pct, 2),
        })

        # Genera ordini distribuiti pro-quota sul bucket
        if scope_type != "CASH" and abs(delta) >= 1.0 and constituents:
            bucket_total = sum(h["total_value"] for h in constituents)
            for h in constituents:
                price = h.get("current_price") or 0.0
                if price <= 0:
                    continue
                weight = (h["total_value"] / bucket_total) if bucket_total > 0 else (1.0 / len(constituents))
                leg_value = delta * weight
                qty = leg_value / price
                if abs(qty) < 0.0001:
                    continue
                # Non vendere mai più di quanto posseduto
                if qty < 0:
                    qty = max(qty, -float(h["quantity"]))
                    leg_value = qty * price
                orders.append({
                    "ticker": h["ticker"],
                    "name": h["name"],
                    "allocation_name": target.get("name"),
                    "side": "BUY" if qty > 0 else "SELL",
                    "quantity": round(abs(qty), 4),
                    "estimated_price": round(price, 2),
                    "estimated_value": round(abs(leg_value), 2),
                    "currency": h.get("currency", "EUR"),
                })

    # Ordini: prima i BUY più grandi, poi i SELL
    orders.sort(key=lambda o: (o["side"] != "BUY", -o["estimated_value"]))

    covered_targets = sum(a["target_percent"] for a in allocations)

    return {
        "total_value": round(total_value, 2),
        "extra_cash": round(max(extra_cash, 0.0), 2),
        "targets_sum_percent": round(covered_targets, 2),
        "allocations": allocations,
        "orders": orders,
        "orders_count": len(orders),
        "total_buy_value": round(sum(o["estimated_value"] for o in orders if o["side"] == "BUY"), 2),
        "total_sell_value": round(sum(o["estimated_value"] for o in orders if o["side"] == "SELL"), 2),
    }
