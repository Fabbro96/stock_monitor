from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from typing import List, Optional, Union
from pydantic import BaseModel

from backend.database import get_db
from backend.models.settings import UserSettings, AlertRule
from backend.models.stock import Stock
from backend.services.market_data import MarketDataService
from backend.services.telegram_bot import TelegramService

router = APIRouter(prefix="/api/settings", tags=["settings"])

class UserSettingsUpdate(BaseModel):
    """
    Accetta sia il formato legacy (total_budget/advice_times/advice_frequency/markets str)
    sia il formato del frontend (budget/reportTimes/reportFreq/markets list).
    """
    strategy: Optional[str] = None
    markets: Optional[Union[str, List[str]]] = None
    total_budget: Optional[float] = None
    budget: Optional[float] = None
    advice_frequency: Optional[int] = None
    reportFreq: Optional[int] = None
    advice_times: Optional[Union[str, List[str]]] = None
    reportTimes: Optional[Union[str, List[str]]] = None

    def normalized(self) -> dict:
        out = {}
        if self.strategy is not None:
            out["strategy"] = self.strategy
        if self.markets is not None:
            out["markets"] = ",".join(self.markets) if isinstance(self.markets, list) else self.markets
        budget = self.total_budget if self.total_budget is not None else self.budget
        if budget is not None:
            out["total_budget"] = budget
        freq = self.advice_frequency if self.advice_frequency is not None else self.reportFreq
        if freq is not None:
            out["advice_frequency"] = freq
        times = self.advice_times if self.advice_times is not None else self.reportTimes
        if times is not None:
            out["advice_times"] = ",".join(times) if isinstance(times, list) else times
        return out

class AlertRuleCreate(BaseModel):
    """
    Accetta sia stock_id+threshold_percent (legacy) sia ticker+threshold (frontend).
    """
    stock_id: Optional[int] = None
    ticker: Optional[str] = None
    threshold_percent: Optional[float] = None
    threshold: Optional[float] = None
    direction: str = "BOTH"
    active: Optional[bool] = True

from backend.config import settings as app_settings

def is_valid_api_key(val: str | None) -> bool:
    if not val:
        return False
    v = val.strip().lower()
    if not v or v.startswith("your_") or "placeholder" in v or v.endswith("_here") or "token_here" in v or "key_here" in v:
        return False
    return True

@router.get("/")
async def get_settings(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(UserSettings).limit(1))
    user_settings = result.scalars().first()
    
    if not user_settings:
        user_settings = UserSettings()
        db.add(user_settings)
        await db.commit()
        await db.refresh(user_settings)
        
    return {
        "id": user_settings.id,
        "strategy": user_settings.strategy,
        "markets": user_settings.markets.split(",") if user_settings.markets else ["IT", "US", "EU"],
        "budget": user_settings.total_budget,
        "reportFreq": user_settings.advice_frequency,
        "reportTimes": user_settings.advice_times.split(",") if user_settings.advice_times else ["09:00", "18:00"],
        "apiStatus": {
            "telegram": bool(is_valid_api_key(app_settings.TELEGRAM_BOT_TOKEN) and is_valid_api_key(app_settings.TELEGRAM_CHAT_ID)),
            "gemini": is_valid_api_key(app_settings.GEMINI_API_KEY),
            "gemini_model": app_settings.GEMINI_MODEL,
            "reddit": bool(is_valid_api_key(app_settings.REDDIT_CLIENT_ID) and is_valid_api_key(app_settings.REDDIT_CLIENT_SECRET))
        }
    }



@router.put("/")
async def update_settings(update_data: UserSettingsUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(UserSettings).limit(1))
    settings = result.scalars().first()

    normalized = update_data.normalized()
    if not normalized:
        raise HTTPException(status_code=400, detail="Nessun campo valido da aggiornare.")

    if not settings:
        settings = UserSettings(**normalized)
        db.add(settings)
    else:
        for key, value in normalized.items():
            setattr(settings, key, value)

    await db.commit()
    await db.refresh(settings)
    return {
        "id": settings.id,
        "strategy": settings.strategy,
        "markets": settings.markets.split(",") if settings.markets else [],
        "budget": settings.total_budget,
        "reportFreq": settings.advice_frequency,
        "reportTimes": settings.advice_times.split(",") if settings.advice_times else []
    }

@router.get("/alerts")
async def list_alerts(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AlertRule).join(Stock).options(selectinload(AlertRule.stock)))
    alerts = result.scalars().all()
    output = []
    for rule in alerts:
        stock = rule.stock
        output.append({
            "id": rule.id,
            "stock_id": rule.stock_id,
            "ticker": stock.ticker if stock else "?",
            "name": (stock.name if stock else "") or "",
            "direction": rule.direction,
            "threshold": rule.threshold_percent,
            "threshold_percent": rule.threshold_percent,
            "active": bool(rule.is_active),
        })
    return output

@router.post("/alerts")
async def create_alert(rule: AlertRuleCreate, db: AsyncSession = Depends(get_db)):
    threshold = rule.threshold_percent if rule.threshold_percent is not None else rule.threshold
    if threshold is None:
        raise HTTPException(status_code=400, detail="Specifica threshold_percent o threshold.")
    if threshold <= 0:
        raise HTTPException(status_code=400, detail="La soglia deve essere positiva.")

    direction = (rule.direction or "BOTH").upper()
    if direction not in ("UP", "DOWN", "BOTH"):
        raise HTTPException(status_code=400, detail="direction deve essere UP, DOWN o BOTH.")

    stock_id = rule.stock_id
    if not stock_id:
        if not rule.ticker:
            raise HTTPException(status_code=400, detail="Specifica stock_id oppure ticker.")
        ticker = rule.ticker.strip().upper()
        result = await db.execute(select(Stock).where(Stock.ticker == ticker))
        stock = result.scalars().first()
        if not stock:
            market = "IT" if ticker.endswith(".MI") else "US"
            info = await MarketDataService.resolve_stock_info(ticker)
            name = info.get("name") or ticker
            if info.get("market"):
                market = info["market"]
            stock = Stock(ticker=ticker, name=name, market=market,
                          currency="USD" if market == "US" else "EUR")
            db.add(stock)
            await db.commit()
            await db.refresh(stock)
        stock_id = stock.id

    new_rule = AlertRule(
        stock_id=stock_id,
        threshold_percent=threshold,
        direction=direction,
        is_active=rule.active if rule.active is not None else True,
    )
    db.add(new_rule)
    await db.commit()
    await db.refresh(new_rule)

    stock = await db.get(Stock, new_rule.stock_id)
    return {
        "id": new_rule.id,
        "stock_id": new_rule.stock_id,
        "ticker": stock.ticker if stock else "?",
        "direction": new_rule.direction,
        "threshold": new_rule.threshold_percent,
        "active": bool(new_rule.is_active),
    }

@router.delete("/alerts/{rule_id}")
async def delete_alert(rule_id: int, db: AsyncSession = Depends(get_db)):
    rule = await db.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Alert rule not found")
        
    await db.delete(rule)
    await db.commit()
    return {"status": "success"}

@router.post("/telegram/test")
async def test_telegram():
    service = TelegramService()
    await service.send_test_message()
    return {"status": "success"}
