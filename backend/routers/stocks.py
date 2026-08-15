from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime, timedelta
import yfinance as yf

from backend.database import get_db
from backend.models.stock import Stock, PriceHistory
from backend.services.market_data import MarketDataService

router = APIRouter(prefix="/api/stocks", tags=["stocks"])

class StockCreate(BaseModel):
    ticker: str
    name: Optional[str] = None
    market: Optional[str] = None

class StockResponse(BaseModel):
    id: int
    ticker: str
    name: Optional[str]
    market: Optional[str]
    currency: Optional[str]
    is_active: bool

    class Config:
        from_attributes = True

@router.get("/", response_model=List[StockResponse])
async def list_stocks(
    market: Optional[str] = None, 
    is_active: Optional[bool] = None,
    db: AsyncSession = Depends(get_db)
):
    query = select(Stock)
    if market:
        query = query.where(Stock.market == market)
    if is_active is not None:
        query = query.where(Stock.is_active == is_active)
        
    result = await db.execute(query)
    return result.scalars().all()

@router.post("/", response_model=StockResponse)
async def add_stock(stock: StockCreate, db: AsyncSession = Depends(get_db)):
    name = stock.name
    market = stock.market
    ticker = stock.ticker.upper().strip()
    
    if not name or not market:
        try:
            info = yf.Ticker(ticker).info
            if not name:
                name = info.get('shortName', ticker)
            if not market:
                market = 'IT' if ticker.endswith('.MI') else 'US'
        except Exception:
            pass

    new_stock = Stock(
        ticker=ticker,
        name=name or ticker,
        market=market or ("IT" if ticker.endswith(".MI") else "US"),
        currency="EUR" if (market == "IT" or ticker.endswith(".MI")) else "USD",
        is_active=True
    )
    
    db.add(new_stock)
    try:
        await db.commit()
        await db.refresh(new_stock)
        return new_stock
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/{stock_id}")
async def remove_stock(stock_id: int, db: AsyncSession = Depends(get_db)):
    stock = await db.get(Stock, stock_id)
    if not stock:
        raise HTTPException(status_code=404, detail="Stock not found")
        
    stock.is_active = False
    await db.commit()
    return {"status": "success"}

@router.get("/search")
async def search_ticker(q: str):
    results = await MarketDataService.search_ticker(q)
    return results

@router.get("/{ticker}/details")
async def get_stock_deep_dive(ticker: str):
    """
    Ritorna la scheda completa del titolo con fondamentali (PE, EPS, Beta, 52W range) e indicatori tecnici (RSI, Medie Mobili).
    """
    data = await MarketDataService.fetch_stock_deep_dive(ticker)
    return data

@router.get("/{ticker}/candles")
async def get_stock_candles(ticker: str, timeframe: str = Query("1m", regex="^(1d|1w|1m|6m|1y|5y)$")):
    """
    Ritorna serie di candele/prezzi per i grafici TradingView (timeframe: 1d, 1w, 1m, 6m, 1y, 5y).
    """
    candles = await MarketDataService.fetch_stock_candles(ticker, timeframe)
    return candles

@router.get("/{stock_id}/history")
async def get_history(
    stock_id: int, 
    days: int = Query(7), 
    db: AsyncSession = Depends(get_db)
):
    cutoff = datetime.now() - timedelta(days=days)
    result = await db.execute(
        select(PriceHistory)
        .where(PriceHistory.stock_id == stock_id, PriceHistory.timestamp >= cutoff)
        .order_by(PriceHistory.timestamp)
    )
    history = result.scalars().all()
    return history
