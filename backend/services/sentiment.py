import logging
import asyncio
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
import httpx
import yfinance as yf
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.models.stock import Stock
from backend.models.sentiment import Sentiment
from backend.config import settings

logger = logging.getLogger(__name__)

class SentimentService:
    """
    Servizio di Sentiment e Notizie multi-fonte.
    Non richiede obbligatoriamente le API di Reddit:
    utilizza Yahoo Finance News, Google News RSS e Reddit pubblico (zero-auth)
    per raccogliere notizie e trend in tempo reale.
    """

    def __init__(self):
        self.reddit = None
        # PRAW opzionale: inizializzato solo se l'utente ha configurato le chiavi nel .env
        if settings.REDDIT_CLIENT_ID and settings.REDDIT_CLIENT_SECRET and not settings.REDDIT_CLIENT_ID.startswith("your_"):
            try:
                import praw
                self.reddit = praw.Reddit(
                    client_id=settings.REDDIT_CLIENT_ID,
                    client_secret=settings.REDDIT_CLIENT_SECRET,
                    user_agent=settings.REDDIT_USER_AGENT or "stock_monitor/1.0"
                )
                logger.info("Reddit PRAW API inizializzata con successo")
            except Exception as e:
                logger.warning(f"Impossibile inizializzare Reddit PRAW: {e}")

    async def fetch_yahoo_news(self, ticker: str) -> list[dict]:
        """Recupera le ultime notizie finanziarie da Yahoo Finance (gratuito, zero auth)."""
        def _get_news():
            try:
                stock = yf.Ticker(ticker)
                raw_news = stock.news or []
                news_list = []
                for item in raw_news[:5]:
                    title = item.get("title") or (item.get("content", {}).get("title") if isinstance(item.get("content"), dict) else "")
                    publisher = item.get("publisher") or (item.get("content", {}).get("provider", {}).get("displayName") if isinstance(item.get("content"), dict) else "")
                    if title:
                        news_list.append({
                            "title": title,
                            "source": f"Yahoo ({publisher})" if publisher else "Yahoo Finance",
                            "type": "news"
                        })
                return news_list
            except Exception as e:
                logger.debug(f"Errore recupero Yahoo News per {ticker}: {e}")
                return []

        return await asyncio.to_thread(_get_news)

    async def fetch_google_news_rss(self, ticker: str, stock_name: str) -> list[dict]:
        """Recupera le ultime notizie da Google News RSS (gratuito, zero auth)."""
        clean_ticker = ticker.split(".")[0]
        query = f"{clean_ticker} {stock_name} stock".replace(" ", "+")
        url = f"https://news.google.com/rss/search?q={query}&hl=it&gl=IT&ceid=IT:it"
        
        try:
            async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return []
                
                root = ET.fromstring(resp.text)
                items = root.findall(".//item")
                results = []
                for item in items[:4]:
                    title_elem = item.find("title")
                    source_elem = item.find("source")
                    if title_elem is not None and title_elem.text:
                        source_name = source_elem.text if source_elem is not None else "Google News"
                        results.append({
                            "title": title_elem.text,
                            "source": f"News ({source_name})",
                            "type": "news"
                        })
                return results
        except Exception as e:
            logger.debug(f"Errore recupero Google News per {ticker}: {e}")
            return []

    async def fetch_public_reddit_discussions(self, ticker: str, stock_name: str) -> list[dict]:
        """Recupera discussioni da Reddit tramite endpoint JSON pubblico (zero chiavi API)."""
        clean_ticker = ticker.split(".")[0]
        url = f"https://www.reddit.com/r/stocks+investing+wallstreetbets/search.json?q={clean_ticker}&sort=new&limit=5&restrict_sr=1"
        try:
            async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": "Mozilla/5.0 (StockMonitor/1.0)"}) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return []
                data = resp.json()
                children = data.get("data", {}).get("children", [])
                results = []
                for child in children:
                    post = child.get("data", {})
                    title = post.get("title")
                    sub = post.get("subreddit")
                    score = post.get("score", 0)
                    if title:
                        results.append({
                            "title": title,
                            "source": f"Reddit r/{sub} (upvotes: {score})",
                            "type": "social",
                            "score": score
                        })
                return results
        except Exception as e:
            logger.debug(f"Errore recupero Reddit pubblico per {ticker}: {e}")
            return []

    async def get_combined_market_context(self, ticker: str, stock_name: str) -> list[dict]:
        """Aggrega notizie e discussioni da tutte le fonti disponibili in parallelo."""
        tasks = [
            self.fetch_yahoo_news(ticker),
            self.fetch_google_news_rss(ticker, stock_name),
            self.fetch_public_reddit_discussions(ticker, stock_name)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        combined = []
        for res in results:
            if isinstance(res, list):
                combined.extend(res)
        return combined

    async def analyze_all_stocks(self, db_session: AsyncSession):
        """Job periodico per raccogliere news e aggiornare il sentiment per tutti i titoli attivi."""
        result = await db_session.execute(select(Stock).where(Stock.is_active == True))
        stocks = result.scalars().all()
        
        for stock in stocks:
            try:
                context_items = await self.get_combined_market_context(stock.ticker, stock.name or stock.ticker)
                
                # Calcolo sentiment basico su polarità parole chiave (il giudizio avanzato lo farà Gemini)
                positive_keywords = ['record', 'crescita', 'buy', 'upgrade', 'profit', 'rialzo', 'utile', 'dividendo', 'gain', 'bullish']
                negative_keywords = ['crollo', 'perdita', 'downgrade', 'sell', 'calo', 'crisi', 'inflazione', 'bearish', 'warning']
                
                pos_count = 0
                neg_count = 0
                titles_sample = []
                
                for item in context_items:
                    text_lower = item['title'].lower()
                    titles_sample.append(item['title'])
                    if any(k in text_lower for k in positive_keywords):
                        pos_count += 1
                    if any(k in text_lower for k in negative_keywords):
                        neg_count += 1
                
                total = max(pos_count + neg_count, 1)
                score = (pos_count - neg_count) / total
                score = round(max(min(score, 1.0), -1.0), 2)
                
                summary = f"Trovate {len(context_items)} notizie/discussioni su Yahoo, Google e Reddit"
                if titles_sample:
                    summary += f": {titles_sample[0]}"
                
                sentiment = Sentiment(
                    stock_id=stock.id,
                    timestamp=datetime.now(timezone.utc),
                    score=score,
                    source='multi-source (yahoo/google/reddit)',
                    summary=summary[:250]
                )
                db_session.add(sentiment)
            except Exception as e:
                logger.error(f"Errore durante l'analisi del sentiment per {stock.ticker}: {e}")
                
        await db_session.commit()
