import json
import time
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import func
from typing import Optional
from datetime import datetime, timedelta, timezone

from backend.database import get_db
from backend.models.advice import Advice
from backend.models.stock import Stock
from backend.services.advisor import AdvisorService
from backend.services.market_data import MarketDataService

# Rate limiter in-memory per proteggere le quote API Gemini
_CALL_TIMESTAMPS = defaultdict(list)
RATE_LIMIT_MAX_CALLS = 12  # Max 12 richieste al minuto
RATE_LIMIT_WINDOW_SECONDS = 60

def _check_rate_limit(client_id: str = "global"):
    now = time.time()
    timestamps = _CALL_TIMESTAMPS[client_id]
    _CALL_TIMESTAMPS[client_id] = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW_SECONDS]
    if len(_CALL_TIMESTAMPS[client_id]) >= RATE_LIMIT_MAX_CALLS:
        raise HTTPException(
            status_code=429,
            detail="Troppe richieste di analisi AI inviate in breve tempo. Riprova tra 60 secondi."
        )
    _CALL_TIMESTAMPS[client_id].append(now)

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
    query = select(Advice).options(selectinload(Advice.stock))
    
    if market:
        query = query.where(Advice.market == market.upper())
    if action:
        query = query.where(Advice.action.ilike(f"%{action}%"))
        
    if date:
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
            query = query.where(func.date(Advice.timestamp) == target_date)
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

        stock_ticker = a.stock.ticker if a.stock else None
        stock_name = a.stock.name if a.stock else None

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
    _check_rate_limit(f"stock_{ticker.upper()}")
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
    _check_rate_limit("generate")
    if not force and not MarketDataService.are_any_markets_open():
        raise HTTPException(
            status_code=400,
            detail="Tutti i mercati finanziari sono attualmente chiusi (Milano 09:00-17:30, Wall Street 15:30-22:00 ora italiana). Puoi comunque forzare la generazione manuale."
        )
    advisor = AdvisorService()
    advices = await advisor.generate_advice(db, force=force)
    return {"status": "success", "generated_count": len(advices), "advices": advices}
