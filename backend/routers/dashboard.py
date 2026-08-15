import asyncio
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from datetime import datetime, timedelta, timezone

from backend.database import get_db
from backend.models.portfolio import Holding
from backend.models.stock import Stock
from backend.models.watchlist import WatchlistItem
from backend.models.settings import AlertRule
from backend.models.advice import Advice

from backend.services.market_data import MarketDataService
from backend.services.portfolio_service import build_portfolio_summary, build_portfolio_rows
from backend.services.analytics import build_portfolio_daily_series

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

@router.get("/")
async def get_dashboard(db: AsyncSession = Depends(get_db)):
    # 1. Portfolio summary
    portfolio_summary = await build_portfolio_summary(db)
    
    # 2. Recent advices
    advices_result = await db.execute(
        select(Advice).order_by(Advice.timestamp.desc()).limit(4)
    )
    recent_advices = advices_result.scalars().all()
    
    # 3. Active alerts count
    alerts_result = await db.execute(
        select(AlertRule).where(AlertRule.is_active == True)
    )
    active_alerts_count = len(alerts_result.scalars().all())
    
    # 4. Market status strutturato con orari italiani
    it_open = MarketDataService.is_market_open('IT')
    us_open = MarketDataService.is_market_open('US')
    eu_open = MarketDataService.is_market_open('EU')
    any_open = it_open or us_open or eu_open

    market_status = {
        "IT": "OPEN" if it_open else "CLOSED",
        "US": "OPEN" if us_open else "CLOSED",
        "EU": "OPEN" if eu_open else "CLOSED",
        "ANY_OPEN": "OPEN" if any_open else "CLOSED",
        "details": {
            "IT": {
                "name": "Borsa Italiana (Milano)",
                "flag": "🇮🇹",
                "status": "OPEN" if it_open else "CLOSED",
                "hours": "09:00 - 17:30"
            },
            "US": {
                "name": "Wall Street (New York)",
                "flag": "🇺🇸",
                "status": "OPEN" if us_open else "CLOSED",
                "hours": "15:30 - 22:00"
            }
        }
    }
    
    return {
        "portfolio_summary": portfolio_summary,
        "recent_advices": recent_advices,
        "active_alerts_count": active_alerts_count,
        "market_status": market_status
    }

@router.get("/indices")
async def get_indices():
    """
    Ritorna le quotazioni in tempo reale degli indici e commodity globali per la barra scorrevole.
    """
    indices = await MarketDataService.fetch_market_indices()
    return indices

@router.get("/heatmap")
async def get_market_heatmap(db: AsyncSession = Depends(get_db)):
    """
    Ritorna la panoramica di tutti i titoli monitorati e in portafoglio con
    variazione % odierna per la Heatmap. Fetch prezzi in parallelo (gather),
    ogni chiamata è thread-isolata con retry e fallback stale.
    """
    result = await db.execute(select(Stock).where(Stock.is_active == True))
    stocks = result.scalars().all()
    if not stocks:
        return []

    price_tasks = [MarketDataService.fetch_current_price(stock.ticker) for stock in stocks]
    prices = await asyncio.gather(*price_tasks, return_exceptions=True)

    heatmap_items = []
    for stock, price_data in zip(stocks, prices):
        if not isinstance(price_data, dict) or not price_data:
            continue
        heatmap_items.append({
            "ticker": stock.ticker,
            "name": stock.name or stock.ticker,
            "market": stock.market or ("IT" if stock.ticker.endswith(".MI") else "US"),
            "currency": stock.currency or ("EUR" if stock.ticker.endswith(".MI") else "USD"),
            "current_price": price_data.get("close", 0.0),
            "change_percent": price_data.get("change_percent", 0.0),
            "change_abs": price_data.get("change_abs", 0.0),
            "day_high": price_data.get("high", 0.0),
            "day_low": price_data.get("low", 0.0),
            "volume": price_data.get("volume", 0),
            "stale": bool(price_data.get("stale", False))
        })

    heatmap_items.sort(key=lambda x: abs(x["change_percent"]), reverse=True)
    return heatmap_items

@router.get("/performance")
async def get_performance(days: int = Query(30), db: AsyncSession = Depends(get_db)):
    """
    Ritorna l'andamento calcolato del valore complessivo del portafoglio giorno
    per giorno (PriceHistory + backfill Yahoo). 'source' indica se la serie è
    reale ('real') oppure un fallback piatto ('fallback').
    """
    portfolio = await build_portfolio_rows(db)
    series = await build_portfolio_daily_series(db, days=days)
    if series:
        return {"data": series, "source": "real", "points": len(series)}

    # Portfolio vuoto: fallback tramite MarketDataService (serie piatta/zero)
    performance = await MarketDataService.calculate_portfolio_history(portfolio, days=days)
    return {"data": performance, "source": "fallback", "points": len(performance)}
