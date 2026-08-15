import logging
import asyncio
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
    Usa selectinload per eliminare query N+1 ed esegue conversione valuta FX.
    """
    result = await db.execute(
        select(Holding)
        .join(Stock)
        .where(Stock.is_active == True)
        .options(selectinload(Holding.stock))
    )
    holdings = result.scalars().all()
    usd_to_eur = await MarketDataService.get_fx_rate("USD", "EUR")

    portfolio = []
    for h in holdings:
        stock = h.stock
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

        currency = stock.currency or ("EUR" if stock.ticker.endswith(".MI") else "USD")
        market = stock.market or ("IT" if stock.ticker.endswith(".MI") else "US")
        fx_rate = usd_to_eur if currency == "USD" else 1.0

        portfolio.append({
            "id": h.id,
            "stock_id": h.stock_id,
            "ticker": stock.ticker,
            "name": stock.name or stock.ticker,
            "market": market,
            "currency": currency,
            "quantity": h.quantity,
            "avg_purchase_price": h.avg_purchase_price,
            "current_price": current_price,
            "previous_close": previous_close,
            "price_stale": price_info.get("stale", False),
            "total_value": round(h.quantity * current_price, 2),
            "total_invested": round(h.quantity * h.avg_purchase_price, 2),
            "total_value_eur": round(h.quantity * current_price * fx_rate, 2),
            "total_invested_eur": round(h.quantity * h.avg_purchase_price * fx_rate, 2),
            "fx_rate_to_eur": fx_rate,
            "pnl_absolute": pnl["pnl_absolute"],
            "pnl_percent": pnl["pnl_percent"],
            "daily_pnl": daily_pnl,
            "purchase_date": str(h.purchase_date) if h.purchase_date else None,
            "notes": h.notes or ""
        })

    return portfolio


async def build_portfolio_summary(db: AsyncSession) -> dict:
    """Riepilogo aggregato del portafoglio in valuta base EUR con allocazione e stima dividendi."""
    portfolio = await build_portfolio_rows(db)
    usd_to_eur = await MarketDataService.get_fx_rate("USD", "EUR")

    total_invested = sum(h.get("total_invested_eur", h["total_invested"]) for h in portfolio)
    total_value = sum(h.get("total_value_eur", h["total_value"]) for h in portfolio)
    total_pnl = total_value - total_invested
    total_pnl_percent = (total_pnl / total_invested * 100) if total_invested > 0 else 0.0

    # P&L Giornaliero in EUR
    daily_pnl = 0.0
    prev_value_base = 0.0
    has_daily = False
    for h in portfolio:
        if h.get("previous_close"):
            fx = h.get("fx_rate_to_eur", 1.0)
            delta = (h["current_price"] - h["previous_close"]) * h["quantity"] * fx
            daily_pnl += delta
            prev_value_base += h["previous_close"] * h["quantity"] * fx
            has_daily = True
    daily_pnl_percent = (daily_pnl / prev_value_base * 100) if prev_value_base > 0 else 0.0

    # Top Gainer & Top Loser
    sorted_by_pnl = sorted(portfolio, key=lambda x: x["pnl_percent"], reverse=True)
    top_gainer = sorted_by_pnl[0] if sorted_by_pnl and sorted_by_pnl[0]["pnl_percent"] > 0 else None
    top_loser = sorted_by_pnl[-1] if sorted_by_pnl and sorted_by_pnl[-1]["pnl_percent"] < 0 else None

    # Market Allocation Breakdown in EUR
    market_allocation = {"IT": 0.0, "US": 0.0, "EU": 0.0}
    for h in portfolio:
        m = (h.get("market") or "US").upper()
        eur_val = h.get("total_value_eur", h["total_value"])
        if m in market_allocation:
            market_allocation[m] += eur_val
        else:
            market_allocation["US"] += eur_val

    # Stima dividendi annui in EUR
    estimated_annual_dividends = 0.0
    for h in portfolio:
        dy = MarketDataService.get_stock_dividend_yield(h["ticker"])
        if dy > 0:
            eur_val = h.get("total_value_eur", h["total_value"])
            estimated_annual_dividends += (eur_val * (dy / 100.0))

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
        "estimated_dividend_yield": round(estimated_dividend_yield, 2),
        "fx_usd_eur": round(usd_to_eur, 4)
    }
