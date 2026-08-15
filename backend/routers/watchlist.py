import asyncio
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

from backend.database import get_db
from backend.models.watchlist import WatchlistItem
from backend.models.stock import Stock
from backend.models.portfolio import Holding
from backend.services.market_data import MarketDataService

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])

class WatchlistAddRequest(BaseModel):
    ticker: str
    notes: Optional[str] = None
    alert_above: Optional[float] = None
    alert_below: Optional[float] = None

class WatchlistAlertUpdateRequest(BaseModel):
    alert_above: Optional[float] = None
    alert_below: Optional[float] = None

@router.get("/")
async def get_watchlist(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(WatchlistItem).join(Stock))
    items = result.scalars().all()

    if not items:
        return []

    stock_ids = [item.stock_id for item in items]
    stocks_result = await db.execute(select(Stock).where(Stock.id.in_(stock_ids)))
    stocks_map = {s.id: s for s in stocks_result.scalars().all()}

    holdings_result = await db.execute(select(Holding.stock_id).where(Holding.stock_id.in_(stock_ids)))
    in_portfolio_ids = {row[0] for row in holdings_result.all()}

    deep_tasks = [
        MarketDataService.fetch_stock_deep_dive(stocks_map[item.stock_id].ticker)
        for item in items if item.stock_id in stocks_map
    ]
    deep_results = await asyncio.gather(*deep_tasks, return_exceptions=True)

    watchlist = []
    deep_iter = iter(deep_results)
    for item in items:
        stock = stocks_map.get(item.stock_id)
        if not stock:
            continue

        deep = next(deep_iter)
        if not isinstance(deep, dict):
            deep = {}

        cur_price = deep.get("current_price", 0.0)
        is_triggered = False
        if item.alert_above and cur_price >= item.alert_above:
            is_triggered = True
        elif item.alert_below and cur_price <= item.alert_below:
            is_triggered = True

        watchlist.append({
            "id": item.id,
            "stock_id": stock.id,
            "ticker": stock.ticker,
            "name": stock.name or deep.get("name", stock.ticker),
            "market": stock.market or deep.get("market", "US"),
            "currency": stock.currency or deep.get("currency", "USD"),
            "current_price": cur_price,
            "change_abs": deep.get("change_abs", 0.0),
            "change_percent": deep.get("change_percent", 0.0),
            "day_high": deep.get("day_high", 0.0),
            "day_low": deep.get("day_low", 0.0),
            "fifty_two_week_high": deep.get("fifty_two_week_high", 0.0),
            "fifty_two_week_low": deep.get("fifty_two_week_low", 0.0),
            "fifty_two_week_pct": deep.get("fifty_two_week_pct", 50.0),
            "pe_ratio": deep.get("pe_ratio"),
            "dividend_yield": deep.get("dividend_yield"),
            "rsi": deep.get("technical", {}).get("rsi_14", 50.0),
            "rsi_status": deep.get("technical", {}).get("rsi_status", "Neutro"),
            "rsi_badge": deep.get("technical", {}).get("rsi_badge", "badge-hold"),
            "notes": item.notes or "",
            "alert_above": item.alert_above,
            "alert_below": item.alert_below,
            "alert_triggered": is_triggered,
            "is_in_portfolio": stock.id in in_portfolio_ids,
            "added_at": str(item.added_at) if item.added_at else None
        })

    return watchlist

@router.post("/")
async def add_to_watchlist(data: WatchlistAddRequest, db: AsyncSession = Depends(get_db)):
    ticker = data.ticker.strip().upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker non valido.")

    result = await db.execute(select(Stock).where(Stock.ticker == ticker))
    stock = result.scalars().first()
    if not stock:
        market = "IT" if ticker.endswith(".MI") else "US"
        deep = await MarketDataService.fetch_stock_deep_dive(ticker)
        name = deep.get("name") or ticker
        market = deep.get("market") or market
        stock = Stock(ticker=ticker, name=name, market=market, currency="USD" if market == "US" else "EUR")
        db.add(stock)
        await db.commit()
        await db.refresh(stock)

    w_res = await db.execute(select(WatchlistItem).where(WatchlistItem.stock_id == stock.id))
    existing = w_res.scalars().first()
    if existing:
        if data.notes:
            existing.notes = data.notes
        if data.alert_above is not None:
            existing.alert_above = data.alert_above
        if data.alert_below is not None:
            existing.alert_below = data.alert_below
        await db.commit()
        return {"status": "exists", "message": f"{ticker} è già nella Watchlist (aggiornato)", "id": existing.id}

    item = WatchlistItem(
        stock_id=stock.id,
        notes=data.notes,
        alert_above=data.alert_above,
        alert_below=data.alert_below
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return {"status": "success", "message": f"{ticker} aggiunto alla Watchlist", "id": item.id}

@router.put("/{item_id}/alert")
async def update_watchlist_alert(item_id: int, data: WatchlistAlertUpdateRequest, db: AsyncSession = Depends(get_db)):
    item = await db.get(WatchlistItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Elemento Watchlist non trovato.")
    
    item.alert_above = data.alert_above
    item.alert_below = data.alert_below
    await db.commit()
    return {"status": "success", "message": "Alert aggiornato con successo"}

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
