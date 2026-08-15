import logging
import asyncio
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.stock import Stock, PriceHistory
from backend.models.portfolio import Holding
from backend.services.market_data import MarketDataService
from backend.utils.helpers import calculate_pnl

logger = logging.getLogger(__name__)


async def get_latest_price(db: AsyncSession, stock_id: int, ticker: str, fallback_price: float | None = None) -> dict:
    """
    Recupera l'ultimo prezzo noto per un titolo con strategia resiliente:
    1. ultimo record PriceHistory nel DB
    2. live MarketDataService (con retry e stale-cache interna)
    3. prezzo di fallback fornito (es. avg_purchase_price)

    Ritorna {"price", "stale", "previous_close"}.
    """
    price_result = await db.execute(
        select(PriceHistory)
        .where(PriceHistory.stock_id == stock_id)
        .order_by(PriceHistory.timestamp.desc())
        .limit(2)
    )
    latest_rows = price_result.scalars().all()
    if latest_rows and latest_rows[0].close:
        prev_close = latest_rows[1].close if len(latest_rows) > 1 else None
        return {
            "price": float(latest_rows[0].close),
            "stale": False,
            "previous_close": float(prev_close) if prev_close else None,
        }

    price_data = await MarketDataService.fetch_current_price(ticker)
    if price_data and price_data.get("close"):
        return {
            "price": float(price_data["close"]),
            "stale": bool(price_data.get("stale", False)),
            "previous_close": float(price_data["previous_close"]) if price_data.get("previous_close") else None,
        }

    if fallback_price:
        return {"price": float(fallback_price), "stale": True, "previous_close": None}
    return {"price": None, "stale": True, "previous_close": None}


async def build_portfolio_rows(db: AsyncSession) -> list[dict]:
    """
    Costruisce le righe complete del portafoglio (holdings + prezzi live/DB).
    Condiviso da router REST e bot Telegram. Nessuna dipendenza implicita:
    ogni servizio usato è importato esplicitamente (fix NameError storico).
    """
    result = await db.execute(
        select(Holding).join(Stock).where(Stock.is_active == True)
    )
    holdings = result.scalars().all()

    portfolio = []
    for h in holdings:
        stock = await db.get(Stock, h.stock_id)
        if not stock:
            continue

        price_info = await get_latest_price(
            db, h.stock_id, stock.ticker, fallback_price=h.avg_purchase_price
        )
        current_price = price_info["price"] if price_info["price"] is not None else h.avg_purchase_price
        previous_close = price_info.get("previous_close")

        pnl = calculate_pnl(current_price, h.avg_purchase_price, h.quantity)

        daily_pnl = None
        if previous_close:
            daily_pnl = round((current_price - previous_close) * h.quantity, 2)

        portfolio.append({
            "id": h.id,
            "stock_id": h.stock_id,
            "ticker": stock.ticker,
            "name": stock.name or stock.ticker,
            "market": stock.market or ("IT" if stock.ticker.endswith(".MI") else "US"),
            "currency": stock.currency or ("EUR" if stock.ticker.endswith(".MI") else "USD"),
            "quantity": h.quantity,
            "avg_purchase_price": h.avg_purchase_price,
            "current_price": current_price,
            "previous_close": previous_close,
            "price_stale": price_info.get("stale", False),
            "total_value": round(h.quantity * current_price, 2),
            "total_invested": round(h.quantity * h.avg_purchase_price, 2),
            "pnl_absolute": pnl["pnl_absolute"],
            "pnl_percent": pnl["pnl_percent"],
            "daily_pnl": daily_pnl,
            "purchase_date": str(h.purchase_date) if h.purchase_date else None,
            "notes": h.notes or ""
        })

    return portfolio


async def build_portfolio_summary(db: AsyncSession) -> dict:
    """Riepilogo aggregato del portafoglio con allocazione e stima dividendi."""
    portfolio = await build_portfolio_rows(db)

    total_invested = sum(h["quantity"] * h["avg_purchase_price"] for h in portfolio)
    total_value = sum(h["quantity"] * h["current_price"] for h in portfolio)
    total_pnl = total_value - total_invested
    total_pnl_percent = (total_pnl / total_invested * 100) if total_invested > 0 else 0.0

    # P&L Giornaliero: somma dei delta (prezzo attuale - chiusura precedente) x qty
    daily_pnl = 0.0
    prev_value_base = 0.0
    has_daily = False
    for h in portfolio:
        if h.get("previous_close"):
            daily_pnl += (h["current_price"] - h["previous_close"]) * h["quantity"]
            prev_value_base += h["previous_close"] * h["quantity"]
            has_daily = True
    daily_pnl_percent = (daily_pnl / prev_value_base * 100) if prev_value_base > 0 else 0.0

    # Top Gainer & Top Loser
    sorted_by_pnl = sorted(portfolio, key=lambda x: x["pnl_percent"], reverse=True)
    top_gainer = sorted_by_pnl[0] if sorted_by_pnl and sorted_by_pnl[0]["pnl_percent"] > 0 else None
    top_loser = sorted_by_pnl[-1] if sorted_by_pnl and sorted_by_pnl[-1]["pnl_percent"] < 0 else None

    # Market Allocation Breakdown
    market_allocation = {"IT": 0.0, "US": 0.0, "EU": 0.0}
    for h in portfolio:
        m = (h.get("market") or "US").upper()
        if m in market_allocation:
            market_allocation[m] += h["total_value"]
        else:
            market_allocation["US"] += h["total_value"]

    # Stima dividendi: deep-dive in parallelo (ogni chiamata è già thread-safe)
    estimated_annual_dividends = 0.0
    if portfolio:
        deep_tasks = [MarketDataService.fetch_stock_deep_dive(h["ticker"]) for h in portfolio]
        deep_results = await asyncio.gather(*deep_tasks, return_exceptions=True)
        for h, deep in zip(portfolio, deep_results):
            if not isinstance(deep, dict):
                continue
            dy = deep.get("dividend_yield")
            if dy and dy > 0:
                estimated_annual_dividends += (h["total_value"] * (dy / 100.0))
            elif deep.get("dividend_rate"):
                estimated_annual_dividends += (h["quantity"] * deep["dividend_rate"])

    estimated_dividend_yield = (estimated_annual_dividends / total_value * 100) if total_value > 0 else 0.0

    return {
        "total_value": round(total_value, 2),
        "total_invested": round(total_invested, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_percent": round(total_pnl_percent, 2),
        "daily_pnl": round(daily_pnl, 2) if has_daily else 0.0,
        "daily_pnl_percent": round(daily_pnl_percent, 2) if has_daily else 0.0,
        "holdings_count": len(portfolio),
        "top_gainer": top_gainer,
        "top_loser": top_loser,
        "market_allocation": {k: round(v, 2) for k, v in market_allocation.items()},
        "estimated_annual_dividends": round(estimated_annual_dividends, 2),
        "estimated_dividend_yield": round(estimated_dividend_yield, 2)
    }
