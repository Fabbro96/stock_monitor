import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.models.settings import AlertRule
from backend.models.stock import Stock
from backend.services.market_data import MarketDataService
from backend.services.telegram_bot import TelegramService

logger = logging.getLogger(__name__)

class AlertingService:
    _last_alert_times = {} # dict mapping stock_id -> datetime

    async def check_alerts(self, db_session: AsyncSession):
        result = await db_session.execute(
            select(AlertRule).join(Stock).where(AlertRule.is_active == True, Stock.is_active == True)
        )
        rules = result.scalars().all()
        
        for rule in rules:
            stock = await db_session.get(Stock, rule.stock_id)
            if not stock:
                continue
                
            # Check rate limit (1 hour)
            last_alert = self._last_alert_times.get(stock.id)
            if last_alert and (datetime.now(timezone.utc) - last_alert) < timedelta(hours=1):
                continue
                
            price_data = await MarketDataService.get_price_change(stock.id, db_session)
            change_percent = price_data.get('change_percent', 0.0)
            
            trigger = False
            if rule.direction == 'UP' and change_percent >= rule.threshold_percent:
                trigger = True
            elif rule.direction == 'DOWN' and change_percent <= -rule.threshold_percent:
                trigger = True
            elif rule.direction == 'BOTH' and abs(change_percent) >= rule.threshold_percent:
                trigger = True
                
            if trigger:
                await self.trigger_alert(stock, rule, change_percent, price_data.get('change_abs', 0.0), db_session)

    async def trigger_alert(self, stock, rule, change_percent, current_price, db_session):
        logger.info(f"Triggering alert for {stock.ticker}: {change_percent}%")
        self._last_alert_times[stock.id] = datetime.now(timezone.utc)
        
        telegram = TelegramService()
        await telegram.send_alert(stock.name or stock.ticker, stock.ticker, change_percent, current_price)
