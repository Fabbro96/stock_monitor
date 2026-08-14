from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List
from pydantic import BaseModel

from backend.database import get_db
from backend.models.settings import UserSettings, AlertRule
from backend.models.stock import Stock
from backend.services.telegram_bot import TelegramService

router = APIRouter(prefix="/api/settings", tags=["settings"])

class UserSettingsUpdate(BaseModel):
    strategy: str
    markets: str
    advice_times: str
    advice_frequency: int
    total_budget: float

class AlertRuleCreate(BaseModel):
    stock_id: int
    threshold_percent: float
    direction: str

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
    
    if not settings:
        settings = UserSettings(**update_data.dict())
        db.add(settings)
    else:
        for key, value in update_data.dict().items():
            setattr(settings, key, value)
            
    await db.commit()
    await db.refresh(settings)
    return settings

@router.get("/alerts")
async def list_alerts(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AlertRule).join(Stock))
    alerts = result.scalars().all()
    # To return with stock info correctly we'd map it, but for simplicity returning objects
    return alerts

@router.post("/alerts")
async def create_alert(rule: AlertRuleCreate, db: AsyncSession = Depends(get_db)):
    new_rule = AlertRule(**rule.dict())
    db.add(new_rule)
    await db.commit()
    await db.refresh(new_rule)
    return new_rule

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
