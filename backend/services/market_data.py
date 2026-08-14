import logging
import asyncio
import time
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except ImportError:
    import pytz
    ZoneInfo = lambda tz_name: pytz.timezone(tz_name)
import yfinance as yf


from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.models.stock import Stock, PriceHistory

logger = logging.getLogger(__name__)

# Cache in memoria per i prezzi correnti (TTL: 60 secondi)
_PRICE_CACHE: dict[str, tuple[dict, float]] = {}
CACHE_TTL = 60.0

class MarketDataService:
    MARKET_SUFFIXES = {
        'IT': '.MI',
        'US': '',
        'EU_NL': '.AS',
        'EU_DE': '.DE',
        'EU_FR': '.PA'
    }

    MARKET_HOURS = {
        'IT': {'open': '09:00', 'close': '17:30', 'tz': 'Europe/Rome'},
        'EU': {'open': '09:00', 'close': '17:30', 'tz': 'Europe/Paris'},
        'US': {'open': '09:30', 'close': '16:00', 'tz': 'America/New_York'}
    }

    @staticmethod
    def is_market_open(market: str) -> bool:
        """
        Verifica se uno specifico mercato (IT, EU, US) è aperto oggi e in questo momento.
        """
        market_key = (market or 'US').upper()
        if market_key.startswith('EU'):
            hours = MarketDataService.MARKET_HOURS['EU']
        elif market_key == 'IT' or market_key.endswith('.MI'):
            hours = MarketDataService.MARKET_HOURS['IT']
        elif market_key in MarketDataService.MARKET_HOURS:
            hours = MarketDataService.MARKET_HOURS[market_key]
        else:
            hours = MarketDataService.MARKET_HOURS['US']
        
        tz = ZoneInfo(hours['tz'])
        now = datetime.now(tz)

        
        # Sabato (5) o Domenica (6) -> Chiuso
        if now.weekday() > 4:
            return False
            
        open_time = datetime.strptime(hours['open'], '%H:%M').time()
        close_time = datetime.strptime(hours['close'], '%H:%M').time()
        
        return open_time <= now.time() <= close_time

    @staticmethod
    def are_any_markets_open() -> bool:
        """
        Verifica se almeno uno dei mercati finanziari supportati (IT, EU, US) è attualmente aperto.
        """
        return any(MarketDataService.is_market_open(m) for m in ['IT', 'EU', 'US'])

    @staticmethod
    def is_ticker_market_open(ticker: str) -> bool:
        """
        Determina il mercato dal suffisso del ticker e verifica se è aperto.
        """
        t = (ticker or '').upper()
        if t.endswith('.MI'):
            return MarketDataService.is_market_open('IT')
        elif any(t.endswith(s) for s in ['.DE', '.AS', '.PA', '.MC', '.BR', '.VI', '.LS']):
            return MarketDataService.is_market_open('EU')
        else:
            return MarketDataService.is_market_open('US')


    @staticmethod
    async def fetch_current_price(ticker: str) -> dict:
        """
        Recupera il prezzo corrente con cache in memoria e chiamata non bloccante via thread pool.
        """
        now_ts = time.time()
        if ticker in _PRICE_CACHE:
            cached_data, cached_time = _PRICE_CACHE[ticker]
            if now_ts - cached_time < CACHE_TTL:
                return cached_data

        def _sync_fetch():
            try:
                stock = yf.Ticker(ticker)
                hist = stock.history(period="1d")
                if hist.empty:
                    # Prova fallback con fast_info
                    info = getattr(stock, 'fast_info', None)
                    if info and hasattr(info, 'last_price') and info.last_price is not None:
                        return {
                            "open": float(getattr(info, 'open', info.last_price)),
                            "high": float(getattr(info, 'day_high', info.last_price)),
                            "low": float(getattr(info, 'day_low', info.last_price)),
                            "close": float(info.last_price),
                            "volume": int(getattr(info, 'last_volume', 0) or 0)
                        }
                    return {}
                
                latest = hist.iloc[-1]
                return {
                    "open": float(latest["Open"]),
                    "high": float(latest["High"]),
                    "low": float(latest["Low"]),
                    "close": float(latest["Close"]),
                    "volume": int(latest["Volume"])
                }
            except Exception as e:
                logger.error(f"Errore recupero prezzo per {ticker}: {e}")
                return {}

        result = await asyncio.to_thread(_sync_fetch)
        if result:
            _PRICE_CACHE[ticker] = (result, now_ts)
        return result

    @staticmethod
    async def fetch_all_prices(db_session: AsyncSession) -> list:
        """
        Raccoglie i prezzi di tutti i titoli attivi in parallelo con concurrency controllata.
        """
        result = await db_session.execute(select(Stock).where(Stock.is_active == True))
        stocks = result.scalars().all()
        if not stocks:
            return []

        # Fetch in parallelo
        tasks = [MarketDataService.fetch_current_price(stock.ticker) for stock in stocks]
        prices = await asyncio.gather(*tasks, return_exceptions=True)

        saved_prices = []
        now_utc = datetime.now(pytz.utc)

        for stock, price_data in zip(stocks, prices):
            if isinstance(price_data, dict) and price_data.get('close'):
                history_entry = PriceHistory(
                    stock_id=stock.id,
                    timestamp=now_utc,
                    open=price_data.get('open', price_data['close']),
                    high=price_data.get('high', price_data['close']),
                    low=price_data.get('low', price_data['close']),
                    close=price_data['close'],
                    volume=price_data.get('volume', 0)
                )
                db_session.add(history_entry)
                saved_prices.append(history_entry)

        await db_session.commit()
        return saved_prices

    @staticmethod
    async def search_ticker(query: str) -> list:
        def _sync_search():
            try:
                stock = yf.Ticker(query)
                info = stock.info or {}
                name = info.get("shortName") or info.get("longName")
                if name:
                    return [{
                        "ticker": query.upper(),
                        "name": name,
                        "market": "IT" if query.upper().endswith(".MI") else "US"
                    }]
                return []
            except Exception:
                return []

        return await asyncio.to_thread(_sync_search)

    @staticmethod
    async def get_price_change(stock_id: int, db_session: AsyncSession) -> dict:
        result = await db_session.execute(
            select(PriceHistory)
            .where(PriceHistory.stock_id == stock_id)
            .order_by(PriceHistory.timestamp.desc())
            .limit(2)
        )
        histories = result.scalars().all()
        
        if len(histories) < 2:
            return {"change_percent": 0.0, "change_abs": 0.0}
            
        current = histories[0].close
        previous = histories[1].close
        
        if previous == 0:
            return {"change_percent": 0.0, "change_abs": 0.0}
            
        change_abs = round(current - previous, 3)
        change_percent = round((change_abs / previous) * 100, 2)
        
        return {
            "change_abs": change_abs,
            "change_percent": change_percent
        }
