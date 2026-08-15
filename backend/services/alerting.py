import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from backend.models.settings import AlertRule
from backend.models.stock import Stock, PriceHistory
from backend.models.watchlist import WatchlistItem
from backend.models.portfolio import Holding
from backend.services.market_data import MarketDataService
from backend.services.telegram_bot import TelegramService

logger = logging.getLogger(__name__)

class AlertingService:
    _last_alert_times = {}  # dict mapping cache_key -> datetime

    async def check_alerts(self, db_session: AsyncSession):
        """
        Esegue il monitoraggio completo dei mercati:
        1. Regole Alert su variazione % giornaliera (AlertRule)
        2. Soglie di prezzo assolute su Watchlist (alert_above / alert_below)
        3. Livelli di Stop-Loss (<= -8%) e Take-Profit (>= +15%) su Holding possedute
        """
        telegram = TelegramService()
        now = datetime.now(timezone.utc)

        # 1. AlertRule (Variazione % giornaliera)
        rules_res = await db_session.execute(
            select(AlertRule).join(Stock).where(AlertRule.is_active == True, Stock.is_active == True).options(selectinload(AlertRule.stock))
        )
        rules = rules_res.scalars().all()
        for rule in rules:
            stock = rule.stock
            if not stock:
                continue

            cache_key = f"rule_{rule.id}"
            last_alert = self._last_alert_times.get(cache_key)
            if last_alert and (now - last_alert) < timedelta(hours=1):
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
                current_price = await self._get_current_price(stock, db_session)
                self._last_alert_times[cache_key] = now
                logger.info(f"Triggered AlertRule #{rule.id} per {stock.ticker}: {change_percent:+.2f}%")
                await telegram.send_alert(stock.name or stock.ticker, stock.ticker, change_percent, current_price)

        # 2. WatchlistItem (Soglie prezzo assolute)
        wl_res = await db_session.execute(
            select(WatchlistItem).join(Stock).where(Stock.is_active == True).options(selectinload(WatchlistItem.stock))
        )
        for item in wl_res.scalars().all():
            stock = item.stock
            if not stock:
                continue

            if not item.alert_above and not item.alert_below:
                continue

            cache_key = f"wl_{item.id}"
            last_alert = self._last_alert_times.get(cache_key)
            if last_alert and (now - last_alert) < timedelta(hours=2):
                continue

            current_price = await self._get_current_price(stock, db_session)
            if current_price <= 0:
                continue

            if item.alert_above and current_price >= item.alert_above:
                self._last_alert_times[cache_key] = now
                logger.info(f"Watchlist alert above superato per {stock.ticker}: {current_price} >= {item.alert_above}")
                await telegram.send_alert(stock.name or stock.ticker, stock.ticker, 0.0, current_price)
            elif item.alert_below and current_price <= item.alert_below:
                self._last_alert_times[cache_key] = now
                logger.info(f"Watchlist alert below raggiunto per {stock.ticker}: {current_price} <= {item.alert_below}")
                await telegram.send_alert(stock.name or stock.ticker, stock.ticker, 0.0, current_price)

        # 3. Holding Stop-Loss / Take-Profit Monitor
        holdings_res = await db_session.execute(
            select(Holding).join(Stock).where(Stock.is_active == True).options(selectinload(Holding.stock))
        )
        for h in holdings_res.scalars().all():
            stock = h.stock
            if not stock or h.avg_purchase_price <= 0:
                continue

            cache_key = f"sl_tp_h_{h.id}"
            last_alert = self._last_alert_times.get(cache_key)
            if last_alert and (now - last_alert) < timedelta(hours=4):
                continue

            current_price = await self._get_current_price(stock, db_session)
            if current_price <= 0:
                continue

            pnl_pct = ((current_price - h.avg_purchase_price) / h.avg_purchase_price) * 100.0

            # Stop-Loss Alert a <= -8%
            if pnl_pct <= -8.0:
                self._last_alert_times[cache_key] = now
                logger.info(f"Stop-Loss triggered su {stock.ticker}: P&L {pnl_pct:.2f}%")
                await telegram.send_stop_loss_alert(
                    stock.name or stock.ticker, stock.ticker, pnl_pct, current_price, h.avg_purchase_price
                )
            # Take-Profit Alert a >= +15%
            elif pnl_pct >= 15.0:
                self._last_alert_times[cache_key] = now
                logger.info(f"Take-Profit raggiunto su {stock.ticker}: P&L +{pnl_pct:.2f}%")
                await telegram.send_take_profit_alert(
                    stock.name or stock.ticker, stock.ticker, pnl_pct, current_price, h.avg_purchase_price
                )

    async def _get_current_price(self, stock: Stock, db_session: AsyncSession) -> float:
        """Ultimo prezzo noto: live con fallback DB (non solleva eccezioni)."""
        try:
            price_data = await MarketDataService.fetch_current_price(stock.ticker)
            if price_data and price_data.get("close"):
                return float(price_data["close"])
        except Exception:
            pass
        try:
            res = await db_session.execute(
                select(PriceHistory)
                .where(PriceHistory.stock_id == stock.id)
                .order_by(PriceHistory.timestamp.desc())
                .limit(1)
            )
            last = res.scalars().first()
            if last and last.close:
                return float(last.close)
        except Exception:
            pass
        return 0.0
