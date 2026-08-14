import logging
import json
import asyncio
from datetime import datetime, timezone
from google import genai
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.models.stock import Stock, PriceHistory
from backend.models.portfolio import Holding
from backend.models.sentiment import Sentiment
from backend.models.settings import UserSettings
from backend.models.advice import Advice
from backend.services.sentiment import SentimentService
from backend.services.market_data import MarketDataService
from backend.config import settings

logger = logging.getLogger(__name__)

class AdvisorService:
    def __init__(self):
        self.client = None
        if settings.GEMINI_API_KEY:
            self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
        else:
            logger.warning("GEMINI_API_KEY non configurata.")
        self.sentiment_service = SentimentService()

    async def generate_advice(self, db_session: AsyncSession, force: bool = False) -> list[dict]:
        # 0. Verifica se i mercati finanziari sono aperti
        if not force and not MarketDataService.are_any_markets_open():
            logger.info("Borse chiuse: generazione analisi saltata (nessun mercato aperto).")
            return []

        # 1. Recupera titoli monitorati
        result = await db_session.execute(select(Stock).where(Stock.is_active == True))
        stocks = result.scalars().all()
        if not stocks:
            logger.warning("Nessun titolo attivo trovato per la generazione dei consigli.")
            return []
        
        # 2. Recupera posizioni attuali in portafoglio
        result = await db_session.execute(select(Holding))
        holdings = result.scalars().all()
        
        # 3. Recupera impostazioni utente
        result = await db_session.execute(select(UserSettings).limit(1))
        user_settings = result.scalars().first()
        
        # 4. Raccogli dati tecnici e notizie per ciascun titolo
        italian_stocks = []
        us_stocks = []

        for s in stocks:
            ph_res = await db_session.execute(
                select(PriceHistory)
                .where(PriceHistory.stock_id == s.id)
                .order_by(PriceHistory.timestamp.desc())
                .limit(2)
            )
            prices = ph_res.scalars().all()
            last_price = prices[0].close if prices else None
            prev_price = prices[1].close if len(prices) > 1 else last_price
            day_change_pct = ((last_price - prev_price) / prev_price * 100) if (last_price and prev_price) else 0.0

            news_items = await self.sentiment_service.get_combined_market_context(s.ticker, s.name or s.ticker)
            top_headlines = [n['title'] for n in news_items[:3]]

            # Trova se posseduto in portafoglio
            holding = next((h for h in holdings if h.stock_id == s.id), None)

            stock_info = {
                "ticker": s.ticker,
                "name": s.name or s.ticker,
                "current_price": round(last_price, 2) if last_price else "N/A",
                "day_change_pct": round(day_change_pct, 2),
                "in_portfolio": holding is not None,
                "quantity_owned": holding.quantity if holding else 0,
                "avg_purchase_price": holding.avg_purchase_price if holding else None,
                "recent_news": top_headlines
            }

            if s.ticker.upper().endswith('.MI') or (s.market and s.market.upper() == 'IT'):
                italian_stocks.append(stock_info)
            else:
                us_stocks.append(stock_info)

        settings_summary = {
            "strategy": user_settings.strategy if user_settings else "mixed",
            "total_budget": user_settings.total_budget if user_settings else 10000.0,
            "target_markets": user_settings.markets.split(",") if (user_settings and user_settings.markets) else ["IT", "US"]
        }

        # 5. Chiama Gemini
        prompt = self._build_macro_prompt(italian_stocks, us_stocks, settings_summary)
        response_json = await asyncio.to_thread(self._call_gemini, prompt)
        
        if not response_json:
            logger.error("Risposta vuota o formato non valido da Gemini.")
            return []

        advices_created = []
        now_utc = datetime.now(timezone.utc)

        # Elabora Blocco Borsa Italiana
        it_data = response_json.get('borsa_italiana')
        if it_data:
            stocks_json_str = json.dumps(it_data.get('stocks_analysis', []), ensure_ascii=False)
            adv_it = Advice(
                market="IT",
                title=it_data.get('title', 'Borsa Italiana (Piazza Affari)'),
                action=it_data.get('action', 'MANTENIMENTO').upper(),
                overview=it_data.get('overview', ''),
                reasoning=it_data.get('strategy', ''),
                stocks_json=stocks_json_str,
                risks=it_data.get('risks', ''),
                confidence=it_data.get('confidence', 'MEDIUM').upper(),
                timeframe=it_data.get('timeframe', 'Medio Termine'),
                timestamp=now_utc
            )
            db_session.add(adv_it)
            advices_created.append({
                "market": "IT",
                "title": adv_it.title,
                "action": adv_it.action,
                "overview": adv_it.overview,
                "strategy": adv_it.reasoning,
                "stocks_analysis": it_data.get('stocks_analysis', []),
                "risks": adv_it.risks,
                "confidence": adv_it.confidence,
                "timeframe": adv_it.timeframe,
                "timestamp": str(now_utc)
            })

        # Elabora Blocco Borsa Americana
        us_data = response_json.get('borsa_americana')
        if us_data:
            stocks_json_str = json.dumps(us_data.get('stocks_analysis', []), ensure_ascii=False)
            adv_us = Advice(
                market="US",
                title=us_data.get('title', 'Borsa Americana (Wall Street / S&P500 & Nasdaq)'),
                action=us_data.get('action', 'MANTENIMENTO').upper(),
                overview=us_data.get('overview', ''),
                reasoning=us_data.get('strategy', ''),
                stocks_json=stocks_json_str,
                risks=us_data.get('risks', ''),
                confidence=us_data.get('confidence', 'MEDIUM').upper(),
                timeframe=us_data.get('timeframe', 'Medio Termine'),
                timestamp=now_utc
            )
            db_session.add(adv_us)
            advices_created.append({
                "market": "US",
                "title": adv_us.title,
                "action": adv_us.action,
                "overview": adv_us.overview,
                "strategy": adv_us.reasoning,
                "stocks_analysis": us_data.get('stocks_analysis', []),
                "risks": adv_us.risks,
                "confidence": adv_us.confidence,
                "timeframe": adv_us.timeframe,
                "timestamp": str(now_utc)
            })

        await db_session.commit()
        return advices_created

    def _build_macro_prompt(self, italian_stocks: list, us_stocks: list, user_settings: dict) -> str:
        return f"""
Sei un Chief Investment Officer e Senior Quantitative Market Strategist.
Non devi limitarti a singoli consigli frammentati per azione, ma devi produrre DUE GRANDI BLOCCHI STRATEGICI GENERALI DI MERCATO:
1. 🇮🇹 BORSA ITALIANA (Piazza Affari / FTSE MIB)
2. 🇺🇸 BORSA AMERICANA (Wall Street / S&P 500 & Nasdaq)

---
DATI MERCATO ITALIANO (Titoli Monitorati & Portafoglio Utente):
{json.dumps(italian_stocks, ensure_ascii=False, indent=2)}

DATI MERCATO AMERICANO (Titoli Monitorati & Portafoglio Utente):
{json.dumps(us_stocks, ensure_ascii=False, indent=2)}

PROFILO UTENTE:
- Strategia: {user_settings.get('strategy', 'mixed')}
- Budget Target: {user_settings.get('total_budget', 10000)} €

---
ISTRUZIONI PER CIASCUN BLOCCO DI BORSA:
1. **Quadro Generale (overview)**: Analisi dello scenario macroeconomico, sentiment generale, politica monetaria (BCE/Fed), trimestrali e trend degli indici.
2. **Strategia Operativa Generale (strategy)**: Piano d'azione aggregato per quel mercato (es. se privilegiare accumulo, prese di profitto, difesa o titoli ciclici/growth/value).
3. **Analisi Titoli (stocks_analysis)**: Per ciascun titolo monitorato/posseduto di quel mercato, fornisci:
   - ticker e nome
   - azione raccomandata (BUY / HOLD / SELL)
   - target price stimato (€ o $)
   - nota operativa sintetica e motivata
4. **Punti di Attenzione & Rischi (risks)**: Catalizzatori e rischi chiave da monitorare nel breve/medio periodo.
5. **Azione di Fondo**: 'ACCUMULO' (BUY), 'MANTENIMENTO' (HOLD), 'PRESA_PROFITTO' (SELL) o 'PRUDENZA'.

Rispondi ESCLUSIVAMENTE in formato JSON con la seguente struttura:
{{
    "market_summary": "Sintesi macro globale brevissima",
    "borsa_italiana": {{
        "title": "Borsa Italiana (Piazza Affari)",
        "market": "IT",
        "action": "ACCUMULO" | "MANTENIMENTO" | "PRESA_PROFITTO" | "PRUDENZA",
        "overview": "Approfondimento esaustivo sullo scenario italiano, FTSE MIB, tassi BCE, settore bancario, energetico e utilities...",
        "strategy": "Strategia complessiva per il mercato italiano in base al profilo utente...",
        "stocks_analysis": [
            {{
                "ticker": "ENEL.MI",
                "name": "Enel S.p.A.",
                "action": "BUY" | "HOLD" | "SELL",
                "target_price": 7.40,
                "note": "Spiegazione sintetica basata su trend e notizie recenti"
            }}
        ],
        "risks": "Fattori di rischio specifici per l'Italia...",
        "confidence": "HIGH" | "MEDIUM" | "LOW",
        "timeframe": "Breve Termine" | "Medio Termine" | "Lungo Termine"
    }},
    "borsa_americana": {{
        "title": "Borsa Americana (Wall Street / S&P 500 & Nasdaq)",
        "market": "US",
        "action": "ACCUMULO" | "MANTENIMENTO" | "PRESA_PROFITTO" | "PRUDENZA",
        "overview": "Approfondimento esaustivo sullo scenario USA, Wall Street, tassi Fed, trimestrali Big Tech e semiconduttori...",
        "strategy": "Strategia complessiva per il mercato americano...",
        "stocks_analysis": [
            {{
                "ticker": "AAPL",
                "name": "Apple Inc.",
                "action": "BUY" | "HOLD" | "SELL",
                "target_price": 245.00,
                "note": "Spiegazione sintetica basata su trend e notizie recenti"
            }}
        ],
        "risks": "Fattori di rischio specifici per gli USA...",
        "confidence": "HIGH" | "MEDIUM" | "LOW",
        "timeframe": "Breve Termine" | "Medio Termine" | "Lungo Termine"
    }}
}}
"""

    def _call_gemini(self, prompt: str) -> dict:
        if not self.client:
            return {}
        try:
            model_name = settings.GEMINI_MODEL or 'gemini-3.7-flash'
            logger.info(f"Chiamata a Google Gemini con modello: {model_name} (Macro Blocchi Borsa)")
            response = self.client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={'response_mime_type': 'application/json'}
            )
            return json.loads(response.text)
        except Exception as e:
            logger.error(f"Errore chiamata Gemini API ({model_name}): {e}")
            return {}
