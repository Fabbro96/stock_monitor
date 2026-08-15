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

# ---------------------------------------------------------------------------
# HTTP Session riutilizzabile con header browser realistici (anti 403/429)
# ---------------------------------------------------------------------------
BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9,it;q=0.8',
    'Accept-Encoding': 'gzip, deflate',
    'Connection': 'keep-alive',
}

_yf_session = requests.Session()
_yf_session.headers.update(BROWSER_HEADERS)

YF_TIMEOUT = 20  # secondi per singola chiamata yfinance


def _is_rate_limited_error(exc: Exception) -> bool:
    """Rileva errori di rate limiting / blocco (429/403) da Yahoo Finance."""
    msg = str(exc).lower()
    return (
        "too many requests" in msg
        or "rate limit" in msg
        or "429" in msg
        or "403" in msg
        or "unusual activity" in msg
    )


def _safe_float(value, default=None):
    """
    Converte in float proteggendo da NaN/inf (Yahoo sotto rate-limit può
    restituire NaN che romperebbero la serializzazione JSON).
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if np.isnan(f) or np.isinf(f):
        return default
    return f


def _sync_with_retries(fn, attempts: int = 3, base_delay: float = 1.0, respect_circuit: bool = True):
    """
    Esegue una funzione sincrona (yfinance) con retry a backoff esponenziale
    e jitter in caso di rate limiting. DA ESEGUIRE SOLO IN asyncio.to_thread.

    respect_circuit=True: se il circuit breaker è già aperto (rate limit già
    rilevato da una chiamata precedente/concorrente), interrompe subito i retry
    invece di attendere tutti i tentativi -> endpoint sempre reattivi.
    """
    last_exc = None
    for attempt in range(attempts):
        # Fast-fail se il circuit breaker è già aperto da un'altra chiamata
        if respect_circuit and attempt > 0 and _circuit_open():
            logger.debug("Circuit breaker aperto: interruzione retry anticipata.")
            break
        try:
            result = fn()
            _reset_circuit()
            return result
        except Exception as e:
            last_exc = e
            if _is_rate_limited_error(e):
                _trip_circuit()
                if attempt < attempts - 1:
                    delay = base_delay * (2 ** attempt) + np.random.uniform(0, 0.5)
                    logger.warning(
                        f"Rate limit Yahoo Finance (tentativo {attempt + 1}/{attempts}), "
                        f"backoff {delay:.1f}s: {e}"
                    )
                    time.sleep(delay)
                    continue
            break
    raise last_exc if last_exc else RuntimeError("Errore sconosciuto")


async def _to_thread_safe(fn, timeout: float = YF_TIMEOUT):
    """
    Wrapper asincrono per chiamate bloccanti: le isola in un thread dedicato
    con timeout, senza mai bloccare l'event loop di FastAPI.
    """
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("Timeout chiamata esterna (yfinance/web)")
        raise TimeoutError("Chiamata dati di mercato in timeout")

# ---------------------------------------------------------------------------
# Cache in-memory: TTL per dati freschi + cache STALE permanente come
# fallback quando Yahoo/Gemini rispondono 429/403 (resilienza a cascata).
# ---------------------------------------------------------------------------
_PRICE_CACHE: dict[str, tuple[dict, float]] = {}
_INDICES_CACHE: tuple[list[dict], float] = ([], 0.0)
_DEEP_DIVE_CACHE: dict[str, tuple[dict, float]] = {}
_CANDLES_CACHE: dict[str, tuple[list, float]] = {}
_INDEX_HISTORY_CACHE: dict[str, tuple[list, float]] = {}
CACHE_TTL = 60.0        # 1 minuto
DEEP_CACHE_TTL = 180.0  # 3 minuti
CANDLES_CACHE_TTL = 300.0      # 5 minuti
INDEX_HISTORY_CACHE_TTL = 3600.0  # 1 ora (serie giornaliere)

# Cache "last known good" senza scadenza: ultimo valore valido noto per ticker.
_LAST_GOOD_PRICE: dict[str, dict] = {}
_LAST_GOOD_DEEP: dict[str, dict] = {}
_LAST_GOOD_CANDLES: dict[str, list] = {}
_LAST_GOOD_INDICES: list[dict] = []
_LAST_GOOD_INDEX_HISTORY: dict[str, list] = {}

# ---------------------------------------------------------------------------
# Circuit breaker per rate-limiting persistente (429/403): quando Yahoo è in
# blocco continuativo, le chiamate live vengono cortocircuitate e si usa
# subito la cache stale/DB -> UI sempre reattiva.
# ---------------------------------------------------------------------------
_CIRCUIT = {"until": 0.0, "cooldown": 60.0}


def _circuit_open() -> bool:
    return time.time() < _CIRCUIT["until"]


def _trip_circuit():
    _CIRCUIT["until"] = time.time() + _CIRCUIT["cooldown"]
    logger.warning(
        f"Circuit breaker Yahoo aperto per {_CIRCUIT['cooldown']:.0f}s "
        f"(rate limit persistente): attivo fallback cache stale."
    )
    _CIRCUIT["cooldown"] = min(_CIRCUIT["cooldown"] * 2, 600.0)


def _reset_circuit():
    _CIRCUIT["cooldown"] = 60.0

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
        """
        Recupera il prezzo corrente con:
        1. cache fresca (TTL) -> 2. live con retry/backoff -> 3. cache stale
        (ultimo valore valido noto, marcato stale=True) -> 4. dict vuoto.
        """
        now_ts = time.time()
        if ticker in _PRICE_CACHE:
            cached_data, cached_time = _PRICE_CACHE[ticker]
            if now_ts - cached_time < CACHE_TTL:
                return cached_data

        # Circuit breaker: rate limit persistente -> subito cache stale
        if _circuit_open():
            stale = _LAST_GOOD_PRICE.get(ticker)
            if stale:
                stale_copy = dict(stale)
                stale_copy["stale"] = True
                stale_copy["source"] = "cache"
                _PRICE_CACHE[ticker] = (stale_copy, now_ts)
                return stale_copy
            return {}

        def _sync_fetch():
            def _do():
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
                    raise ValueError(f"Nessun dato per {ticker}")

                latest = hist.iloc[-1]
                prev = hist.iloc[-2]['Close'] if len(hist) > 1 else latest['Open']
                price = _safe_float(latest["Close"])
                if price is None:
                    raise ValueError(f"Prezzo NaN per {ticker}")
                prev = _safe_float(prev, default=price)
                change_abs = price - prev
                change_pct = (change_abs / prev * 100) if prev else 0.0

                return {
                    "open": _safe_float(latest["Open"], price),
                    "high": _safe_float(latest["High"], price),
                    "low": _safe_float(latest["Low"], price),
                    "close": price,
                    "volume": int(latest["Volume"]) if _safe_float(latest["Volume"], 0) else 0,
                    "previous_close": prev,
                    "change_abs": round(change_abs, 3),
                    "change_percent": round(change_pct, 2)
                }
            return _sync_with_retries(_do, attempts=3, base_delay=1.0)

        try:
            result = await _to_thread_safe(_sync_fetch)
            if result:
                result["stale"] = False
                result["source"] = "live"
                result["timestamp"] = datetime.now(timezone.utc).isoformat()
                _PRICE_CACHE[ticker] = (result, now_ts)
                _LAST_GOOD_PRICE[ticker] = result
                return result
        except Exception as e:
            logger.warning(f"Errore recupero prezzo live per {ticker}: {e}")

        # FALLBACK: ultimo valore valido noto (cache stale)
        stale = _LAST_GOOD_PRICE.get(ticker)
        if stale:
            stale_copy = dict(stale)
            stale_copy["stale"] = True
            stale_copy["source"] = "cache"
            logger.info(f"Fallback stale-cache per {ticker} (ultimo valore noto)")
            _PRICE_CACHE[ticker] = (stale_copy, now_ts)
            return stale_copy
        return {}

    @staticmethod
    async def fetch_market_indices() -> list[dict]:
        global _INDICES_CACHE, _LAST_GOOD_INDICES
        now_ts = time.time()
        cached_data, cached_time = _INDICES_CACHE
        if cached_data and (now_ts - cached_time < CACHE_TTL):
            return cached_data

        # Circuit breaker: rate limit persistente -> subito cache stale
        if _circuit_open():
            if cached_data:
                return cached_data
            if _LAST_GOOD_INDICES:
                return [{**item, "stale": True} for item in _LAST_GOOD_INDICES]
            return []

        def _sync_fetch_all_indices():
            def _do():
                tickers = [item["ticker"] for item in MarketDataService.GLOBAL_INDICES]
                df = yf.download(
                    tickers, period="2d", interval="1d", group_by="ticker",
                    auto_adjust=True, threads=True, progress=False
                )
                if df is None or getattr(df, "empty", True):
                    raise ValueError("Nessun dato indici da Yahoo")

                results = []
                for item in MarketDataService.GLOBAL_INDICES:
                    tk = item["ticker"]
                    try:
                        ticker_df = df[tk] if len(tickers) > 1 else df
                        if ticker_df.empty:
                            continue
                        latest = ticker_df.iloc[-1]
                        price = _safe_float(latest["Close"])
                        if price is None:
                            continue  # riga NaN sotto rate-limit: skip
                        prev = _safe_float(
                            ticker_df.iloc[-2]['Close'] if len(ticker_df) > 1 else latest['Open'],
                            default=price
                        )
                        change_abs = price - prev
                        change_pct = (change_abs / prev * 100) if prev else 0.0
                        results.append({
                            "ticker": tk,
                            "name": item["name"],
                            "flag": item["flag"],
                            "type": item["type"],
                            "price": round(price, 4 if "EURUSD" in tk else 2),
                            "change_abs": round(change_abs, 4 if "EURUSD" in tk else 2),
                            "change_percent": round(change_pct, 2)
                        })
                    except Exception as e:
                        logger.debug(f"Errore recupero indice {item['ticker']}: {e}")
                if not results:
                    raise ValueError("Dati indici vuoti")
                return results
            return _sync_with_retries(_do, attempts=2, base_delay=1.0)

        try:
            data = await _to_thread_safe(_sync_fetch_all_indices, timeout=YF_TIMEOUT + 15)
            if data:
                _LAST_GOOD_INDICES = data
                _INDICES_CACHE = (data, now_ts)
                return data
        except Exception as e:
            logger.warning(f"Errore recupero indici globali: {e}")

        # FALLBACK 1: cache TTL scaduta ma popolata
        if cached_data:
            return cached_data
        # FALLBACK 2: ultimi indici validi noti (marcandoli come stale)
        if _LAST_GOOD_INDICES:
            stale_results = [{**item, "stale": True} for item in _LAST_GOOD_INDICES]
            _INDICES_CACHE = (stale_results, now_ts)
            logger.info("Fallback stale-cache per indici globali")
            return stale_results
        return []

    @staticmethod
    async def fetch_stock_deep_dive(ticker: str) -> dict:
        now_ts = time.time()
        ticker_up = ticker.strip().upper()
        if ticker_up in _DEEP_DIVE_CACHE:
            cached_data, cached_time = _DEEP_DIVE_CACHE[ticker_up]
            if now_ts - cached_time < DEEP_CACHE_TTL:
                return cached_data

        # Circuit breaker: rate limit persistente -> subito cache stale
        if _circuit_open():
            if ticker_up in _DEEP_DIVE_CACHE:
                return _DEEP_DIVE_CACHE[ticker_up][0]
            stale = _LAST_GOOD_DEEP.get(ticker_up)
            if stale:
                stale_copy = dict(stale)
                stale_copy["stale"] = True
                stale_copy["source"] = "cache"
                return stale_copy
            return {
                "ticker": ticker_up,
                "name": ticker_up,
                "current_price": 0.0,
                "change_abs": 0.0,
                "change_percent": 0.0,
                "currency": "EUR" if ticker_up.endswith('.MI') else "USD",
                "stale": True,
                "source": "unavailable",
                "technical": {"rsi_14": 50, "rsi_status": "N/D", "rsi_badge": "badge-hold", "trend": "N/D"}
            }

        def _sync_deep_dive():
            try:
                stock = yf.Ticker(ticker_up, session=_yf_session)
                info = {}
                try:
                    info = _sync_with_retries(
                        lambda: (stock.info or {}), attempts=2, base_delay=1.0
                    )
                except Exception as e:
                    logger.debug(f"info() non disponibile per {ticker_up}: {e}")
                    info = {}

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
                    current_price = _safe_float(closes.iloc[-1], 0.0)
                    day_high = _safe_float(hist['High'].iloc[-1], current_price)
                    day_low = _safe_float(hist['Low'].iloc[-1], current_price)
                    volume = int(_safe_float(hist['Volume'].iloc[-1], 0) or 0)

                    if len(closes) > 1:
                        prev_close = _safe_float(closes.iloc[-2], current_price)
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
                        v20 = _safe_float(closes.rolling(window=20).mean().iloc[-1])
                        sma20_val = round(v20, 2) if v20 is not None else None
                    
                    if len(closes) >= 50:
                        v50 = _safe_float(closes.rolling(window=50).mean().iloc[-1])
                        sma50_val = round(v50, 2) if v50 is not None else None

                if current_price == 0.0:
                    current_price = _safe_float(info.get('regularMarketPrice') or info.get('currentPrice'), 0.0)

                fifty_two_high = _safe_float(info.get('fiftyTwoWeekHigh'), day_high or current_price)
                fifty_two_low = _safe_float(info.get('fiftyTwoWeekLow'), day_low or current_price)
                
                range_span = fifty_two_high - fifty_two_low
                range_pct = round(((current_price - fifty_two_low) / range_span * 100), 1) if range_span > 0 else 50.0

                def _fr(key, mult=1):
                    """Fundamentali arrotondati con protezione NaN."""
                    v = _safe_float(info.get(key))
                    return round(v * mult, 2) if v is not None else None

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
                    "avg_volume": int(_safe_float(info.get('averageVolume') or info.get('averageVolume10days'), volume) or volume),
                    "market_cap": _safe_float(info.get('marketCap')),
                    "pe_ratio": _fr('trailingPE'),
                    "forward_pe": _fr('forwardPE'),
                    "eps": _fr('trailingEps'),
                    "beta": _fr('beta'),
                    "dividend_yield": _fr('dividendYield', 100) or _fr('trailingAnnualDividendYield', 100),
                    "dividend_rate": _fr('dividendRate'),
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

        data = None
        try:
            data = await _to_thread_safe(_sync_deep_dive, timeout=YF_TIMEOUT + 10)
        except Exception as e:
            logger.warning(f"Errore deep dive live per {ticker_up}: {e}")

        if data and data.get('current_price', 0) > 0:
            data["stale"] = False
            data["source"] = "live"
            _DEEP_DIVE_CACHE[ticker_up] = (data, now_ts)
            _LAST_GOOD_DEEP[ticker_up] = data
            return data

        # FALLBACK 1: cache TTL scaduta ma valida
        if ticker_up in _DEEP_DIVE_CACHE:
            return _DEEP_DIVE_CACHE[ticker_up][0]
        # FALLBACK 2: ultimo deep dive valido noto
        stale = _LAST_GOOD_DEEP.get(ticker_up)
        if stale:
            stale_copy = dict(stale)
            stale_copy["stale"] = True
            stale_copy["source"] = "cache"
            logger.info(f"Fallback stale-cache deep dive per {ticker_up}")
            _DEEP_DIVE_CACHE[ticker_up] = (stale_copy, now_ts)
            return stale_copy
        # FALLBACK 3: skeleton vuoto (non blocca mai il frontend)
        return data or {
            "ticker": ticker_up,
            "name": ticker_up,
            "current_price": 0.0,
            "change_abs": 0.0,
            "change_percent": 0.0,
            "currency": "EUR" if ticker_up.endswith('.MI') else "USD",
            "stale": True,
            "source": "unavailable",
            "technical": {"rsi_14": 50, "rsi_status": "N/D", "rsi_badge": "badge-hold", "trend": "N/D"}
        }

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
        cache_key = f"{ticker_up}:{timeframe.lower()}"

        now_ts = time.time()
        if cache_key in _CANDLES_CACHE:
            cached_data, cached_time = _CANDLES_CACHE[cache_key]
            if now_ts - cached_time < CANDLES_CACHE_TTL:
                return cached_data

        # Circuit breaker: rate limit persistente -> subito cache stale
        if _circuit_open():
            if cache_key in _CANDLES_CACHE:
                return _CANDLES_CACHE[cache_key][0]
            stale = _LAST_GOOD_CANDLES.get(cache_key)
            return stale if stale else []

        def _sync_candles():
            def _do():
                stock = yf.Ticker(ticker_up, session=_yf_session)
                hist = stock.history(period=period, interval=interval)
                if hist.empty:
                    raise ValueError(f"Nessuna candela per {ticker_up}")

                results = []
                for index, row in hist.iterrows():
                    if interval in ["5m", "15m", "30m", "60m", "1h"]:
                        time_val = int(index.timestamp())
                    else:
                        time_val = index.strftime("%Y-%m-%d")

                    close_v = _safe_float(row["Close"])
                    if close_v is None:
                        continue  # candela NaN sotto rate-limit

                    results.append({
                        "time": time_val,
                        "open": _safe_float(row["Open"], close_v),
                        "high": _safe_float(row["High"], close_v),
                        "low": _safe_float(row["Low"], close_v),
                        "close": round(close_v, 2),
                        "value": round(close_v, 2),
                        "volume": int(_safe_float(row.get("Volume", 0), 0) or 0)
                    })
                if not results:
                    raise ValueError(f"Nessuna candela valida per {ticker_up}")
                return results
            return _sync_with_retries(_do, attempts=2, base_delay=1.0)

        try:
            results = await _to_thread_safe(_sync_candles, timeout=YF_TIMEOUT + 10)
            if results:
                _CANDLES_CACHE[cache_key] = (results, now_ts)
                _LAST_GOOD_CANDLES[cache_key] = results
                return results
        except Exception as e:
            logger.warning(f"Errore candele live per {ticker_up} ({period}/{interval}): {e}")

        # FALLBACK: ultime candele valide note
        stale = _LAST_GOOD_CANDLES.get(cache_key)
        if stale:
            logger.info(f"Fallback stale-cache candele per {ticker_up}")
            _CANDLES_CACHE[cache_key] = (stale, now_ts)
            return stale
        return []

    @staticmethod
    async def fetch_index_history(ticker: str, period: str = "1y") -> list[dict]:
        """
        Serie storica giornaliera di un indice (es. ^GSPC, FTSEMIB.MI)
        per il confronto benchmark. Ritorna [{time, close}] con cache 1h
        e fallback stale in caso di rate limiting.
        """
        now_ts = time.time()
        cache_key = f"{ticker}:{period}"
        if cache_key in _INDEX_HISTORY_CACHE:
            cached_data, cached_time = _INDEX_HISTORY_CACHE[cache_key]
            if now_ts - cached_time < INDEX_HISTORY_CACHE_TTL:
                return cached_data

        # Circuit breaker: rate limit persistente -> subito cache stale
        if _circuit_open():
            if cache_key in _INDEX_HISTORY_CACHE:
                return _INDEX_HISTORY_CACHE[cache_key][0]
            stale = _LAST_GOOD_INDEX_HISTORY.get(cache_key)
            return stale if stale else []

        def _sync_index_history():
            def _do():
                t = yf.Ticker(ticker, session=_yf_session)
                hist = t.history(period=period, interval="1d")
                if hist.empty:
                    raise ValueError(f"Nessuno storico per {ticker}")
                results = []
                for index, row in hist.iterrows():
                    close_v = _safe_float(row["Close"])
                    if close_v is None:
                        continue
                    results.append({
                        "time": index.strftime("%Y-%m-%d"),
                        "close": round(close_v, 2)
                    })
                if not results:
                    raise ValueError(f"Nessuno storico valido per {ticker}")
                return results
            return _sync_with_retries(_do, attempts=2, base_delay=1.0)

        try:
            results = await _to_thread_safe(_sync_index_history, timeout=YF_TIMEOUT + 10)
            if results:
                _INDEX_HISTORY_CACHE[cache_key] = (results, now_ts)
                _LAST_GOOD_INDEX_HISTORY[cache_key] = results
                return results
        except Exception as e:
            logger.warning(f"Errore storico indice {ticker}: {e}")

        stale = _LAST_GOOD_INDEX_HISTORY.get(cache_key)
        if stale:
            logger.info(f"Fallback stale-cache storico indice {ticker}")
            _INDEX_HISTORY_CACHE[cache_key] = (stale, now_ts)
            return stale
        return []

    @staticmethod
    async def resolve_stock_info(ticker: str) -> dict:
        """
        Risoluzione asincrona non bloccante di nome/mercato di un ticker
        (sostituisce le chiamate sincrone yf.Ticker().info dentro gli endpoint).
        Non solleva mai eccezioni: ritorna {} in caso di errore.
        """
        ticker_up = ticker.strip().upper()

        if _circuit_open():
            return {}

        def _sync():
            def _do():
                stock = yf.Ticker(ticker_up, session=_yf_session)
                info = stock.info or {}
                name = info.get("shortName") or info.get("longName")
                if not name:
                    raise ValueError("Nome non disponibile")
                return {
                    "ticker": ticker_up,
                    "name": name,
                    "market": "IT" if ticker_up.endswith(".MI") else (
                        "EU" if any(ticker_up.endswith(s) for s in ['.DE', '.AS', '.PA']) else "US"
                    ),
                    "currency": info.get('currency', 'EUR' if ticker_up.endswith('.MI') else 'USD')
                }
            return _sync_with_retries(_do, attempts=2, base_delay=1.0)

        try:
            return await _to_thread_safe(_sync)
        except Exception as e:
            logger.debug(f"resolve_stock_info fallita per {ticker_up}: {e}")
            return {}

    @staticmethod
    async def calculate_portfolio_history(holdings: list[dict], days: int = 30) -> list[dict]:
        if not holdings:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            return [{"date": (cutoff + timedelta(days=i)).strftime("%Y-%m-%d"), "value": 0.0} for i in range(days)]

        period = f"{days}d" if days <= 60 else ("3mo" if days <= 90 else ("1y" if days <= 365 else "5y"))

        def _flat_fallback(reason: str = "") -> list[dict]:
            current_total = sum(
                h.get("current_price", h.get("avg_purchase_price", 0)) * h["quantity"]
                for h in holdings
            )
            cutoff_fb = datetime.now(timezone.utc) - timedelta(days=days)
            return [{
                "date": (cutoff_fb + timedelta(days=i)).strftime("%Y-%m-%d"),
                "value": round(current_total, 2)
            } for i in range(days)]

        # Circuit breaker: rate limit persistente -> subito fallback flat
        if _circuit_open():
            logger.info("Circuit breaker attivo: storico portafoglio in fallback flat.")
            return _flat_fallback()

        def _sync_portfolio_history():
            def _do():
                tickers = [h["ticker"] for h in holdings]
                qty_map = {h["ticker"]: h["quantity"] for h in holdings}

                df = yf.download(tickers, period=period, interval="1d", progress=False, group_by='ticker', auto_adjust=True, session=_yf_session)

                if df is None or df.empty:
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

            try:
                return _sync_with_retries(_do, attempts=2, base_delay=1.0)
            except Exception as e:
                # FALLBACK: serie piatta sul controvalore attuale (nessun dato inventato)
                logger.warning(f"Storico portafoglio non disponibile, uso fallback flat: {e}")
                return _flat_fallback()

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
            # I dati 'stale' (fallback cache) NON vanno persistiti come nuovo storico
            if isinstance(price_data, dict) and price_data.get('close') and not price_data.get('stale'):
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

        try:
            await db_session.commit()
        except Exception as e:
            await db_session.rollback()
            logger.error(f"Errore commit prezzi (rollback eseguito): {e}")
            return []
        return saved_prices

    @staticmethod
    async def search_ticker(query: str) -> list:
        def _sync_search():
            def _do():
                stock = yf.Ticker(query, session=_yf_session)
                info = stock.info or {}
                name = info.get("shortName") or info.get("longName")
                if not name:
                    raise ValueError("Ticker non trovato")
                return [{
                    "ticker": query.upper(),
                    "name": name,
                    "market": "IT" if query.upper().endswith(".MI") else "US"
                }]
            try:
                return _sync_with_retries(_do, attempts=2, base_delay=1.0)
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
