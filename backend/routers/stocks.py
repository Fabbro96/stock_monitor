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
    # Auto-detect if missing
    name = stock.name
    market = stock.market
    
    if not name or not market:
        try:
            info = yf.Ticker(stock.ticker).info
            if not name:
                name = info.get('shortName', stock.ticker)
            if not market:
                market = 'US' # Simplified fallback
        except Exception:
            pass

    new_stock = Stock(
        ticker=stock.ticker.upper(),
        name=name,
        market=market,
        currency="USD" if market == "US" else "EUR",
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

@router.get("/search")
async def search_ticker(q: str):
    results = await MarketDataService.search_ticker(q)
    return results
