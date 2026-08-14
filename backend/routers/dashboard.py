from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from datetime import datetime, timedelta, timezone

from backend.database import get_db
from backend.routers.portfolio import get_summary
from backend.models.advice import Advice
from backend.models.settings import AlertRule
from backend.models.stock import Stock
from backend.services.market_data import MarketDataService

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
    
    # 4. Market status
    market_status = {
        market: "OPEN" if MarketDataService.is_market_open(market) else "CLOSED"
        for market in ['IT', 'EU', 'US']
    }
    market_status["ANY_OPEN"] = "OPEN" if MarketDataService.are_any_markets_open() else "CLOSED"

    
    # 5. Top movers - Simplified, in real life we'd calculate from today's price histories
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
    # Mock data for performance chart since calculating real historical portfolio value
    # requires complex joins between price history and holding history
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    
    performance = []
    for i in range(days):
        day = cutoff + timedelta(days=i)
        performance.append({
            "date": day.strftime('%Y-%m-%d'),
            "total_value": 10000 + (i * 100) # Dummy data
        })
        
    return performance
