from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import Optional
from datetime import datetime, timedelta, timezone

from backend.database import get_db
from backend.models.advice import Advice
from backend.models.stock import Stock
from backend.services.advisor import AdvisorService

router = APIRouter(prefix="/api/advice", tags=["advice"])

@router.get("/")
async def list_advices(
    action: Optional[str] = None,
    ticker: Optional[str] = None,
    days: int = Query(30),
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db)
):
    query = select(Advice).join(Stock)
    
    if action:
        query = query.where(Advice.action == action.upper())
    if ticker:
        query = query.where(Stock.ticker.ilike(f"%{ticker}%"))
        
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    query = query.where(Advice.timestamp >= cutoff)
    query = query.order_by(Advice.timestamp.desc()).offset(skip).limit(limit)
    
    result = await db.execute(query)
    advices = result.scalars().all()
    
    output = []
    for a in advices:
        stock = await db.get(Stock, a.stock_id)
        output.append({
            "id": a.id,
            "stock_id": a.stock_id,
            "ticker": stock.ticker if stock else "N/A",
            "name": stock.name if stock else "N/A",
            "action": a.action,
            "reasoning": a.reasoning,
            "confidence": a.confidence,
            "targetPrice": a.target_price,
            "suggestedQuantity": a.suggested_quantity,
            "timeframe": a.timeframe,
            "followed": a.followed,
            "timestamp": str(a.timestamp) if a.timestamp else str(a.created_at)
        })
        
    return output

@router.get("/latest")
async def get_latest(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Advice).order_by(Advice.timestamp.desc()).limit(5))
    advices = result.scalars().all()
    
    output = []
    for a in advices:
        stock = await db.get(Stock, a.stock_id)
        output.append({
            "id": a.id,
            "stock_id": a.stock_id,
            "ticker": stock.ticker if stock else "N/A",
            "name": stock.name if stock else "N/A",
            "action": a.action,
            "reasoning": a.reasoning,
            "confidence": a.confidence,
            "targetPrice": a.target_price,
            "suggestedQuantity": a.suggested_quantity,
            "timeframe": a.timeframe,
            "followed": a.followed,
            "timestamp": str(a.timestamp) if a.timestamp else str(a.created_at)
        })
    return output

@router.post("/{advice_id}/follow")
async def follow_advice(advice_id: int, db: AsyncSession = Depends(get_db)):
    advice = await db.get(Advice, advice_id)
    if not advice:
        raise HTTPException(status_code=404, detail="Consiglio non trovato")
        
    advice.followed = not advice.followed
    await db.commit()
    return {"status": "success", "followed": advice.followed}

@router.post("/generate")
async def generate_advice(db: AsyncSession = Depends(get_db)):
    advisor = AdvisorService()
    advices = await advisor.generate_advice(db)
    return {"status": "success", "generated_count": len(advices), "advices": advices}
