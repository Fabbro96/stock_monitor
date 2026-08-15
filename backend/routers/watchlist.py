from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import yfinance as yf

from backend.database import get_db
from backend.models.watchlist import WatchlistItem
from backend.models.stock import Stock
from backend.models.portfolio import Holding
from backend.services.market_data import MarketDataService

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])

class WatchlistAddRequest(BaseModel):
    ticker: str
    notes: Optional[str] = None

@router.get("/")
async def get_watchlist(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(WatchlistItem).join(Stock))
    items = result.scalars().all()

    watchlist = []
    for item in items:
        stock = await db.get(Stock, item.stock_id)
        if not stock:
            continue

        deep_data = await MarketDataService.fetch_stock_deep_dive(stock.ticker)

        # Check if already in portfolio
        h_res = await db.execute(select(Holding).where(Holding.stock_id == stock.id))
        is_in_portfolio = h_res.scalars().first() is not None

        watchlist.append({
            "id": item.id,
            "stock_id": stock.id,
            "ticker": stock.ticker,
            "name": stock.name or deep_data.get("name", stock.ticker),
            "market": stock.market or deep_data.get("market", "US"),
            "currency": stock.currency or deep_data.get("currency", "USD"),
            "current_price": deep_data.get("current_price", 0.0),
            "change_abs": deep_data.get("change_abs", 0.0),
            "change_percent": deep_data.get("change_percent", 0.0),
            "day_high": deep_data.get("day_high", 0.0),
            "day_low": deep_data.get("day_low", 0.0),
            "fifty_two_week_high": deep_data.get("fifty_two_week_high", 0.0),
            "fifty_two_week_low": deep_data.get("fifty_two_week_low", 0.0),
            "fifty_two_week_pct": deep_data.get("fifty_two_week_pct", 50.0),
            "pe_ratio": deep_data.get("pe_ratio"),
            "dividend_yield": deep_data.get("dividend_yield"),
            "rsi": deep_data.get("technical", {}).get("rsi_14", 50.0),
            "rsi_status": deep_data.get("technical", {}).get("rsi_status", "Neutro"),
            "rsi_badge": deep_data.get("technical", {}).get("rsi_badge", "badge-hold"),
            "notes": item.notes or "",
            "is_in_portfolio": is_in_portfolio,
            "added_at": str(item.added_at) if item.added_at else None
        })

    return watchlist

@router.post("/")
async def add_to_watchlist(data: WatchlistAddRequest, db: AsyncSession = Depends(get_db)):
    ticker = data.ticker.strip().upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker non valido.")

    # Find or create Stock
    result = await db.execute(select(Stock).where(Stock.ticker == ticker))
    stock = result.scalars().first()
    if not stock:
        market = "IT" if ticker.endswith(".MI") else "US"
        name = ticker
        try:
            info = yf.Ticker(ticker).info
            name = info.get("shortName", ticker)
        except Exception:
            pass
        stock = Stock(ticker=ticker, name=name, market=market, currency="USD" if market == "US" else "EUR")
        db.add(stock)
        await db.commit()
        await db.refresh(stock)

    # Check if already in watchlist
    w_res = await db.execute(select(WatchlistItem).where(WatchlistItem.stock_id == stock.id))
    existing = w_res.scalars().first()
    if existing:
        return {"status": "exists", "message": f"{ticker} è già nella Watchlist", "id": existing.id}

    item = WatchlistItem(stock_id=stock.id, notes=data.notes)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return {"status": "success", "message": f"{ticker} aggiunto alla Watchlist", "id": item.id}

@router.delete("/{item_id}")
async def remove_from_watchlist(item_id: int, db: AsyncSession = Depends(get_db)):
    item = await db.get(WatchlistItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Elemento Watchlist non trovato.")

    await db.delete(item)
    await db.commit()
    return {"status": "success", "message": "Rimosso dalla Watchlist"}

@router.delete("/ticker/{ticker}")
async def remove_by_ticker(ticker: str, db: AsyncSession = Depends(get_db)):
    ticker_up = ticker.strip().upper()
    result = await db.execute(select(Stock).where(Stock.ticker == ticker_up))
    stock = result.scalars().first()
    if not stock:
        raise HTTPException(status_code=404, detail="Titolo non trovato.")

    w_res = await db.execute(select(WatchlistItem).where(WatchlistItem.stock_id == stock.id))
    item = w_res.scalars().first()
    if item:
        await db.delete(item)
        await db.commit()

    return {"status": "success", "message": f"{ticker_up} rimosso dalla Watchlist"}
