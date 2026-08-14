import json
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import Optional
from datetime import datetime, timedelta, timezone

from backend.database import get_db
from backend.models.advice import Advice
from backend.models.stock import Stock
from backend.services.advisor import AdvisorService
from backend.services.market_data import MarketDataService

router = APIRouter(prefix="/api/advice", tags=["advice"])

@router.get("/")
async def list_advices(
    market: Optional[str] = None,
    action: Optional[str] = None,
    days: int = Query(30),
    skip: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db)
):
    query = select(Advice)
    
    if market:
        query = query.where(Advice.market == market.upper())
    if action:
        query = query.where(Advice.action.ilike(f"%{action}%"))
        
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    query = query.where(Advice.timestamp >= cutoff)
    query = query.order_by(Advice.timestamp.desc()).offset(skip).limit(limit)
    
    result = await db.execute(query)
    advices = result.scalars().all()
    
    output = []
    for a in advices:
        try:
            stocks_analysis = json.loads(a.stocks_json) if a.stocks_json else []
        except Exception:
            stocks_analysis = []

        stock_ticker = None
        stock_name = None
        if a.stock_id:
            st = await db.get(Stock, a.stock_id)
            if st:
                stock_ticker = st.ticker
                stock_name = st.name

        output.append({
            "id": a.id,
            "market": a.market or "ALL",
            "title": a.title or ("Borsa Italiana (Piazza Affari)" if a.market == "IT" else ("Borsa Americana (Wall Street)" if a.market == "US" else "Analisi di Mercato")),
            "action": a.action,
            "overview": a.overview,
            "strategy": a.reasoning,
            "stocks_analysis": stocks_analysis,
            "risks": a.risks,
            "confidence": a.confidence,
            "timeframe": a.timeframe,
            "targetPrice": a.target_price,
            "suggestedQuantity": a.suggested_quantity,
            "ticker": stock_ticker,
            "name": stock_name,
            "followed": a.followed,
            "timestamp": str(a.timestamp) if a.timestamp else str(a.created_at)
        })
        
    return output

@router.get("/latest")
async def get_latest(db: AsyncSession = Depends(get_db)):
    # Ritorna gli ultimi blocchi per Borsa Italiana e Borsa Americana
    result = await db.execute(select(Advice).order_by(Advice.timestamp.desc()).limit(4))
    advices = result.scalars().all()
    
    output = []
    for a in advices:
        try:
            stocks_analysis = json.loads(a.stocks_json) if a.stocks_json else []
        except Exception:
            stocks_analysis = []

        output.append({
            "id": a.id,
            "market": a.market or "ALL",
            "title": a.title or ("Borsa Italiana (Piazza Affari)" if a.market == "IT" else ("Borsa Americana (Wall Street)" if a.market == "US" else "Analisi di Mercato")),
            "action": a.action,
            "overview": a.overview,
            "strategy": a.reasoning,
            "stocks_analysis": stocks_analysis,
            "risks": a.risks,
            "confidence": a.confidence,
            "timeframe": a.timeframe,
            "targetPrice": a.target_price,
            "suggestedQuantity": a.suggested_quantity,
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
async def generate_advice(force: bool = Query(False), db: AsyncSession = Depends(get_db)):
    if not force and not MarketDataService.are_any_markets_open():
        raise HTTPException(
            status_code=400,
            detail="I mercati finanziari sono attualmente chiusi (weekend o fuori orario di negoziazione). L'IA non genera consigli a borsa chiusa."
        )
    advisor = AdvisorService()
    advices = await advisor.generate_advice(db, force=force)
    return {"status": "success", "generated_count": len(advices), "advices": advices}
