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
    date: Optional[str] = None,
    days: int = Query(7),
    skip: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db)
):
    query = select(Advice)
    
    if market:
        query = query.where(Advice.market == market.upper())
    if action:
        query = query.where(Advice.action.ilike(f"%{action}%"))
        
    if date:
        try:
            day_start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            day_end = day_start + timedelta(days=1)
            query = query.where(Advice.timestamp >= day_start).where(Advice.timestamp < day_end)
        except ValueError:
            pass
    else:
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
            "followed": bool(a.followed),
            "timestamp": str(a.timestamp) if a.timestamp else str(a.created_at)
        })
        
    return output

@router.get("/latest")
async def get_latest(db: AsyncSession = Depends(get_db)):
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    result = await db.execute(
        select(Advice)
        .where(Advice.timestamp >= cutoff)
        .order_by(Advice.timestamp.desc())
        .limit(4)
    )
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
            "followed": bool(a.followed),
            "timestamp": str(a.timestamp) if a.timestamp else str(a.created_at)
        })
    return output

@router.post("/stock/{ticker}")
async def analyze_stock_on_demand(ticker: str, db: AsyncSession = Depends(get_db)):
    """
    Richiede un'analisi istantanea approfondita a Google Gemini 3.7 Flash per un singolo titolo.
    """
    advisor = AdvisorService()
    analysis = await advisor.analyze_single_stock(ticker, db)
    return analysis

@router.post("/{advice_id}/toggle-follow")
@router.post("/{advice_id}/follow")
async def toggle_follow_advice(advice_id: int, db: AsyncSession = Depends(get_db)):
    advice = await db.get(Advice, advice_id)
    if not advice:
        raise HTTPException(status_code=404, detail="Analisi non trovata")
        
    advice.followed = not bool(advice.followed)
    await db.commit()
    return {"status": "success", "followed": advice.followed}

@router.post("/generate")
async def generate_advice(force: bool = Query(False), db: AsyncSession = Depends(get_db)):
    if not force and not MarketDataService.are_any_markets_open():
        raise HTTPException(
            status_code=400,
            detail="Tutti i mercati finanziari sono attualmente chiusi (Milano 09:00-17:30, Wall Street 15:30-22:00 ora italiana). Puoi comunque forzare la generazione manuale."
        )
    advisor = AdvisorService()
    advices = await advisor.generate_advice(db, force=force)
    return {"status": "success", "generated_count": len(advices), "advices": advices}
