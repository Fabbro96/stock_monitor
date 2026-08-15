import logging
import asyncio
import time
from datetime import datetime, timedelta, timezone
try:
    from zoneinfo import ZoneInfo
except ImportError:
    import pytz
    ZoneInfo = lambda tz_name: pytz.timezone(tz_name)
import requests
import yfinance as yf
import pandas as pd
import numpy as np

from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.models.stock import Stock, PriceHistory

logger = logging.getLogger(__name__)

# Custom Session with headers to prevent Yahoo rate-limiting/403
_yf_session = requests.Session()
_yf_session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9,it;q=0.8',
})

# In-memory caches with TTL
_PRICE_CACHE: dict[str, tuple[dict, float]] = {}
_INDICES_CACHE: tuple[list[dict], float] = ([], 0.0)
_DEEP_DIVE_CACHE: dict[str, tuple[dict, float]] = {}
CACHE_TTL = 60.0 # 1 minute
DEEP_CACHE_TTL = 180.0 # 3 minutes

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

    GLOBAL_INDICES = [
        {"ticker": "FTSEMIB.MI", "name": "FTSE MIB", "flag": "🇮🇹", "type": "index"},
        {"ticker": "^GSPC", "name": "S&P 500", "flag": "🇺🇸", "type": "index"},
        {"ticker": "^IXIC", "name": "NASDAQ", "flag": "🇺🇸", "type": "index"},
        {"ticker": "^GDAXI", "name": "DAX 40", "flag": "🇩🇪", "type": "index"},
        {"ticker": "EURUSD=X", "name": "EUR/USD", "flag": "💱", "type": "forex"},
        {"ticker": "BTC-USD", "name": "Bitcoin", "flag": "🪙", "type": "crypto"},
        {"ticker": "GC=F", "name": "Oro", "flag": "🥇", "type": "commodity"},
        {"ticker": "CL=F", "name": "Petrolio WTI", "flag": "🛢️", "type": "commodity"}
    ]

    @staticmethod
    def is_market_open(market: str) -> bool:
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
        
        if now.weekday() > 4:
            return False
            
        open_time = datetime.strptime(hours['open'], '%H:%M').time()
        close_time = datetime.strptime(hours['close'], '%H:%M').time()
        
        return open_time <= now.time() <= close_time

    @staticmethod
    def are_any_markets_open() -> bool:
        return any(MarketDataService.is_market_open(m) for m in ['IT', 'EU', 'US'])

    @staticmethod
    def is_ticker_market_open(ticker: str) -> bool:
        t = (ticker or '').upper()
        if t.endswith('.MI'):
            return MarketDataService.is_market_open('IT')
        elif any(t.endswith(s) for s in ['.DE', '.AS', '.PA', '.MC', '.BR', '.VI', '.LS']):
            return MarketDataService.is_market_open('EU')
        else:
            return MarketDataService.is_market_open('US')

    @staticmethod
    async def fetch_current_price(ticker: str) -> dict:
        now_ts = time.time()
        if ticker in _PRICE_CACHE:
            cached_data, cached_time = _PRICE_CACHE[ticker]
            if now_ts - cached_time < CACHE_TTL:
                return cached_data

        def _sync_fetch():
            try:
                stock = yf.Ticker(ticker, session=_yf_session)
                hist = stock.history(period="2d")
                if hist.empty:
                    info = getattr(stock, 'fast_info', None)
                    if info and hasattr(info, 'last_price') and info.last_price is not None:
                        price = float(info.last_price)
                        prev = float(getattr(info, 'previous_close', price) or price)
                        change_abs = price - prev
                        change_pct = (change_abs / prev * 100) if prev else 0.0
                        return {
                            "open": float(getattr(info, 'open', price) or price),
                            "high": float(getattr(info, 'day_high', price) or price),
                            "low": float(getattr(info, 'day_low', price) or price),
                            "close": price,
                            "volume": int(getattr(info, 'last_volume', 0) or 0),
                            "previous_close": prev,
                            "change_abs": round(change_abs, 3),
                            "change_percent": round(change_pct, 2)
                        }
                    return {}
                
                latest = hist.iloc[-1]
                prev = hist.iloc[-2]['Close'] if len(hist) > 1 else latest['Open']
                price = float(latest["Close"])
                change_abs = price - prev
                change_pct = (change_abs / prev * 100) if prev else 0.0

                return {
                    "open": float(latest["Open"]),
                    "high": float(latest["High"]),
                    "low": float(latest["Low"]),
                    "close": price,
                    "volume": int(latest["Volume"]),
                    "previous_close": float(prev),
                    "change_abs": round(change_abs, 3),
                    "change_percent": round(change_pct, 2)
                }
            except Exception as e:
                logger.debug(f"Errore recupero prezzo per {ticker}: {e}")
                return {}

        result = await asyncio.to_thread(_sync_fetch)
        if result:
            _PRICE_CACHE[ticker] = (result, now_ts)
        return result

    @staticmethod
    async def fetch_market_indices() -> list[dict]:
        global _INDICES_CACHE
        now_ts = time.time()
        cached_data, cached_time = _INDICES_CACHE
        if cached_data and (now_ts - cached_time < CACHE_TTL):
            return cached_data

        def _sync_fetch_all_indices():
            results = []
            for item in MarketDataService.GLOBAL_INDICES:
                try:
                    ticker = item["ticker"]
                    t = yf.Ticker(ticker, session=_yf_session)
                    hist = t.history(period="2d")
                    if not hist.empty:
                        latest = hist.iloc[-1]
                        prev = hist.iloc[-2]['Close'] if len(hist) > 1 else latest['Open']
                        price = float(latest["Close"])
                        change_abs = price - prev
                        change_pct = (change_abs / prev * 100) if prev else 0.0
                        results.append({
                            "ticker": ticker,
                            "name": item["name"],
                            "flag": item["flag"],
                            "type": item["type"],
                            "price": round(price, 4 if "EURUSD" in ticker else 2),
                            "change_abs": round(change_abs, 4 if "EURUSD" in ticker else 2),
                            "change_percent": round(change_pct, 2)
                        })
                except Exception as e:
                    logger.debug(f"Errore recupero indice {item['ticker']}: {e}")
            return results

        data = await asyncio.to_thread(_sync_fetch_all_indices)
        if data:
            _INDICES_CACHE = (data, now_ts)
        return data or cached_data

    @staticmethod
    async def fetch_stock_deep_dive(ticker: str) -> dict:
        now_ts = time.time()
        ticker_up = ticker.strip().upper()
        if ticker_up in _DEEP_DIVE_CACHE:
            cached_data, cached_time = _DEEP_DIVE_CACHE[ticker_up]
            if now_ts - cached_time < DEEP_CACHE_TTL:
                return cached_data

        def _sync_deep_dive():
            try:
                stock = yf.Ticker(ticker_up, session=_yf_session)
                info = {}
                try:
                    info = stock.info or {}
                except Exception:
                    pass

                hist = pd.DataFrame()
                try:
                    hist = stock.history(period="6mo")
                    if hist.empty:
                        hist = stock.history(period="1mo")
                except Exception:
                    pass

                current_price = 0.0
                change_abs = 0.0
                change_percent = 0.0
                prev_close = 0.0
                
                rsi_val = 50.0
                sma20_val = None
                sma50_val = None
                day_high = 0.0
                day_low = 0.0
                volume = 0

                if not hist.empty:
                    closes = hist['Close']
                    current_price = float(closes.iloc[-1])
                    day_high = float(hist['High'].iloc[-1])
                    day_low = float(hist['Low'].iloc[-1])
                    volume = int(hist['Volume'].iloc[-1])

                    if len(closes) > 1:
                        prev_close = float(closes.iloc[-2])
                        change_abs = current_price - prev_close
                        change_percent = (change_abs / prev_close * 100) if prev_close else 0.0
                    else:
                        prev_close = current_price

                    if len(closes) >= 15:
                        delta = closes.diff()
                        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
                        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
                        rs = gain / loss.replace(0, np.nan)
                        rsi_series = 100 - (100 / (1 + rs))
                        last_rsi = rsi_series.iloc[-1]
                        if not np.isnan(last_rsi):
                            rsi_val = round(float(last_rsi), 1)

                    if len(closes) >= 20:
                        sma20_val = round(float(closes.rolling(window=20).mean().iloc[-1]), 2)
                    
                    if len(closes) >= 50:
                        sma50_val = round(float(closes.rolling(window=50).mean().iloc[-1]), 2)

                if current_price == 0.0:
                    current_price = float(info.get('regularMarketPrice') or info.get('currentPrice') or 0.0)

                fifty_two_high = float(info.get('fiftyTwoWeekHigh') or day_high or current_price)
                fifty_two_low = float(info.get('fiftyTwoWeekLow') or day_low or current_price)
                
                range_span = fifty_two_high - fifty_two_low
                range_pct = round(((current_price - fifty_two_low) / range_span * 100), 1) if range_span > 0 else 50.0

                if rsi_val >= 70:
                    rsi_status = "Ipercomprato (Overbought)"
                    rsi_badge = "badge-sell"
                elif rsi_val <= 30:
                    rsi_status = "Ipervenduto (Oversold)"
                    rsi_badge = "badge-buy"
                else:
                    rsi_status = "Neutro (Neutral)"
                    rsi_badge = "badge-hold"

                trend = "Neutro"
                if sma20_val and current_price > sma20_val:
                    trend = "Rialzista (Bullish)" if (not sma50_val or sma20_val > sma50_val) else "Recupero"
                elif sma20_val and current_price < sma20_val:
                    trend = "Ribassista (Bearish)" if (not sma50_val or sma20_val < sma50_val) else "Correzione"

                return {
                    "ticker": ticker_up,
                    "name": info.get('shortName') or info.get('longName') or ticker_up,
                    "market": "IT" if ticker_up.endswith('.MI') else ("EU" if any(ticker_up.endswith(s) for s in ['.DE', '.AS', '.PA']) else "US"),
                    "currency": info.get('currency', 'EUR' if ticker_up.endswith('.MI') else 'USD'),
                    "current_price": round(current_price, 2),
                    "previous_close": round(prev_close, 2),
                    "change_abs": round(change_abs, 2),
                    "change_percent": round(change_percent, 2),
                    "day_high": round(day_high, 2),
                    "day_low": round(day_low, 2),
                    "volume": volume,
                    "avg_volume": int(info.get('averageVolume') or info.get('averageVolume10days') or volume),
                    "market_cap": info.get('marketCap'),
                    "pe_ratio": round(float(info.get('trailingPE')), 2) if info.get('trailingPE') else None,
                    "forward_pe": round(float(info.get('forwardPE')), 2) if info.get('forwardPE') else None,
                    "eps": round(float(info.get('trailingEps')), 2) if info.get('trailingEps') else None,
                    "beta": round(float(info.get('beta')), 2) if info.get('beta') else None,
                    "dividend_yield": round(float(info.get('dividendYield') * 100), 2) if info.get('dividendYield') else (round(float(info.get('trailingAnnualDividendYield') * 100), 2) if info.get('trailingAnnualDividendYield') else None),
                    "dividend_rate": round(float(info.get('dividendRate')), 2) if info.get('dividendRate') else None,
                    "fifty_two_week_high": fifty_two_high,
                    "fifty_two_week_low": fifty_two_low,
                    "fifty_two_week_pct": range_pct,
                    "sector": info.get('sector', 'N/A'),
                    "industry": info.get('industry', 'N/A'),
                    "summary": info.get('longBusinessSummary') or info.get('description') or '',
                    "technical": {
                        "rsi_14": rsi_val,
                        "rsi_status": rsi_status,
                        "rsi_badge": rsi_badge,
                        "sma_20": sma20_val,
                        "sma_50": sma50_val,
                        "trend": trend
                    }
                }
            except Exception as e:
                logger.debug(f"Errore deep dive per {ticker_up}: {e}")
                return {
                    "ticker": ticker_up,
                    "name": ticker_up,
                    "current_price": 0.0,
                    "change_abs": 0.0,
                    "change_percent": 0.0,
                    "currency": "EUR" if ticker_up.endswith('.MI') else "USD",
                    "technical": {"rsi_14": 50, "rsi_status": "N/D", "rsi_badge": "badge-hold", "trend": "N/D"}
                }

        data = await asyncio.to_thread(_sync_deep_dive)
        if data and data.get('current_price', 0) > 0:
            _DEEP_DIVE_CACHE[ticker_up] = (data, now_ts)
        return data

    @staticmethod
    async def fetch_stock_candles(ticker: str, timeframe: str = "1mo") -> list[dict]:
        ticker_up = ticker.strip().upper()
        tf_mapping = {
            "1d": ("1d", "5m"),
            "1w": ("5d", "15m"),
            "1m": ("1mo", "1d"),
            "6m": ("6mo", "1d"),
            "1y": ("1y", "1d"),
            "5y": ("5y", "1wk")
        }
        period, interval = tf_mapping.get(timeframe.lower(), ("1mo", "1d"))

        def _sync_candles():
            try:
                stock = yf.Ticker(ticker_up, session=_yf_session)
                hist = stock.history(period=period, interval=interval)
                if hist.empty:
                    return []
                
                results = []
                for index, row in hist.iterrows():
                    if interval in ["5m", "15m", "30m", "60m", "1h"]:
                        time_val = int(index.timestamp())
                    else:
                        time_val = index.strftime("%Y-%m-%d")
                    
                    results.append({
                        "time": time_val,
                        "open": round(float(row["Open"]), 2),
                        "high": round(float(row["High"]), 2),
                        "low": round(float(row["Low"]), 2),
                        "close": round(float(row["Close"]), 2),
                        "value": round(float(row["Close"]), 2),
                        "volume": int(row.get("Volume", 0))
                    })
                return results
            except Exception as e:
                logger.debug(f"Errore candele per {ticker_up} ({period}/{interval}): {e}")
                return []

        return await asyncio.to_thread(_sync_candles)

    @staticmethod
    async def calculate_portfolio_history(holdings: list[dict], days: int = 30) -> list[dict]:
        if not holdings:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            return [{"date": (cutoff + timedelta(days=i)).strftime("%Y-%m-%d"), "value": 0.0} for i in range(days)]

        period = f"{days}d" if days <= 60 else ("3mo" if days <= 90 else ("1y" if days <= 365 else "5y"))

        def _sync_portfolio_history():
            try:
                tickers = [h["ticker"] for h in holdings]
                qty_map = {h["ticker"]: h["quantity"] for h in holdings}
                
                df = yf.download(tickers, period=period, interval="1d", progress=False, group_by='ticker', auto_adjust=True, session=_yf_session)
                
                if df.empty:
                    raise ValueError("Empty historical data")

                history_points = []
                if len(tickers) == 1:
                    t = tickers[0]
                    closes = df['Close'] if 'Close' in df else df
                    for dt_idx, close_val in closes.items():
                        if pd.isna(close_val):
                            continue
                        val = float(close_val) * qty_map[t]
                        history_points.append({
                            "date": dt_idx.strftime("%Y-%m-%d"),
                            "value": round(val, 2)
                        })
                else:
                    dates = df.index
                    for dt_idx in dates:
                        daily_total = 0.0
                        has_val = False
                        for t in tickers:
                            try:
                                close_val = df[t]['Close'].loc[dt_idx]
                                if not pd.isna(close_val):
                                    daily_total += float(close_val) * qty_map[t]
                                    has_val = True
                            except Exception:
                                pass
                        if has_val and daily_total > 0:
                            history_points.append({
                                "date": dt_idx.strftime("%Y-%m-%d"),
                                "value": round(daily_total, 2)
                            })

                if history_points:
                    return history_points[-days:] if len(history_points) > days else history_points
                raise ValueError("No points generated")
            except Exception as e:
                current_total = sum(h.get("current_price", h.get("avg_purchase_price", 0)) * h["quantity"] for h in holdings)
                cutoff = datetime.now(timezone.utc) - timedelta(days=days)
                return [{
                    "date": (cutoff + timedelta(days=i)).strftime("%Y-%m-%d"),
                    "value": round(current_total * (1 + (i - days) * 0.002), 2)
                } for i in range(days)]

        return await asyncio.to_thread(_sync_portfolio_history)

    @staticmethod
    async def fetch_all_prices(db_session: AsyncSession) -> list:
        result = await db_session.execute(select(Stock).where(Stock.is_active == True))
        stocks = result.scalars().all()
        if not stocks:
            return []

        tasks = [MarketDataService.fetch_current_price(stock.ticker) for stock in stocks]
        prices = await asyncio.gather(*tasks, return_exceptions=True)

        saved_prices = []
        now_utc = datetime.now(timezone.utc)

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
                stock = yf.Ticker(query, session=_yf_session)
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
