from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from datetime import datetime, timedelta, timezone

from backend.database import get_db
from backend.models.holding import Holding
from backend.models.stock import Stock
from backend.models.alert import AlertRule
from backend.models.advice import Advice
from backend.services.market_data import MarketDataService
from backend.routers.portfolio import get_summary

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

@router.get("/")
async def get_dashboard(db: AsyncSession = Depends(get_db)):
    # 1. Portfolio summary
    portfolio_summary = await get_summary(db)
    
    # 2. Recent advices
    advices_result = await db.execute(
        select(Advice).order_by(Advice.timestamp.desc()).limit(5)
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
    
    # 5. Top movers
    top_movers = {"gainers": [], "losers": []}
    
    return {
        "portfolio_summary": portfolio_summary,
        "recent_advices": recent_advices,
        "active_alerts_count": active_alerts_count,
        "market_status": market_status,
        "top_movers": top_movers
    }

@router.get("/performance")
async def get_performance(days: int = Query(30), db: AsyncSession = Depends(get_db)):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    
    performance = []
    for i in range(days):
        day = cutoff + timedelta(days=i)
        performance.append({
            "date": day.strftime("%Y-%m-%d"),
            "value": 10000.0 + (i * 50.0) # Base incrementale per grafico storico
        })
        
    return {"data": performance}
