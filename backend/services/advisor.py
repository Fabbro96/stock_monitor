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
            logger.info("Borse chiuse: generazione consigli IA saltata (nessun mercato aperto).")
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
        
        # 4. Raccogli dati tecnici e notizie per ciascun titolo in parallelo
        stocks_rich_data = []
        for s in stocks:
            # Ultimi 2 prezzi storici per calcolare variazione recente
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

            # Ultime notizie e sentiment
            news_items = await self.sentiment_service.get_combined_market_context(s.ticker, s.name or s.ticker)
            top_headlines = [n['title'] for n in news_items[:3]]

            stocks_rich_data.append({
                "ticker": s.ticker,
                "name": s.name,
                "market": s.market,
                "current_price": round(last_price, 2) if last_price else "N/A",
                "day_change_pct": round(day_change_pct, 2),
                "recent_news": top_headlines
            })

        # Costruisci portafoglio aggregato
        portfolio_summary = []
        for h in holdings:
            st = await db_session.get(Stock, h.stock_id)
            portfolio_summary.append({
                "ticker": st.ticker if st else "N/A",
                "name": st.name if st else "N/A",
                "quantity": h.quantity,
                "avg_purchase_price": h.avg_purchase_price
            })

        settings_summary = {
            "strategy": user_settings.strategy if user_settings else "mixed",
            "total_budget": user_settings.total_budget if user_settings else 10000.0,
            "target_markets": user_settings.markets.split(",") if (user_settings and user_settings.markets) else ["IT", "US", "EU"]
        }

        # 5. Chiama Gemini
        prompt = self._build_prompt(stocks_rich_data, portfolio_summary, settings_summary)
        response_json = await asyncio.to_thread(self._call_gemini, prompt)
        
        if not response_json or 'advices' not in response_json:
            logger.error("Risposta vuota o formato non valido da Gemini.")
            return []
            
        advices_created = []
        for adv in response_json['advices']:
            stock = next((s for s in stocks if s.ticker.upper() == adv.get('ticker', '').upper()), None)
            if not stock:
                continue
                
            advice = Advice(
                stock_id=stock.id,
                action=adv.get('action', 'HOLD').upper(),
                reasoning=adv.get('reasoning', ''),
                confidence=adv.get('confidence', 'MEDIUM').upper(),
                target_price=adv.get('target_price'),
                suggested_quantity=adv.get('suggested_quantity'),
                timeframe=adv.get('timeframe', 'Medio Termine')
            )
            db_session.add(advice)
            advices_created.append({
                "id": None, # Will be committed
                "ticker": stock.ticker,
                "name": stock.name,
                "action": advice.action,
                "reasoning": advice.reasoning,
                "confidence": advice.confidence,
                "targetPrice": advice.target_price,
                "suggestedQuantity": advice.suggested_quantity,
                "timeframe": advice.timeframe,
                "followed": False,
                "timestamp": str(datetime.now(timezone.utc))
            })
            
        await db_session.commit()
        return advices_created

    def _build_prompt(self, stocks_data: list, portfolio: list, user_settings: dict) -> str:
        return f"""
Sei un analista quantitativo e consulente finanziario senior.
Analizza il portafoglio dell'utente, i titoli monitorati e il contesto di mercato recente con le relative notizie.

Dati Titoli Monitorati con Notizie e Variazioni:
{json.dumps(stocks_data, ensure_ascii=False, indent=2)}

Portafoglio Attuale Utente:
{json.dumps(portfolio, ensure_ascii=False, indent=2)}


Profilo & Strategia Utente:
- Orizzonte d'investimento: {user_settings.get('strategy', 'mixed')} (short term, long term, o mixed)
- Capitale / Budget target: {user_settings.get('total_budget', 10000)} €
- Mercati d'interesse: {user_settings.get('target_markets', ['IT', 'US'])}

Istruzioni:
1. Fornisci ESATTAMENTE 5 consigli finanziari concreti e motivati sui titoli monitorati.
2. Considera il prezzo di carico dell'utente (se già possiede il titolo) per valutare prese di profitto (SELL), accumuli (BUY), o stop loss/mantenimento (HOLD).
3. Integra le notizie recenti e i trend per spiegare il razionale (reasoning) in italiano chiaro e professionale.
4. Rispetta rigorosamente il formato JSON specificato qui sotto.

Rispondi ESCLUSIVAMENTE in JSON valido con questa struttura:
{{
    "market_summary": "Sintesi chiara dello scenario macro e dell'andamento odierno dei mercati d'interesse",
    "advices": [
        {{
            "ticker": "LDO.MI",
            "action": "BUY" | "SELL" | "HOLD",
            "reasoning": "Spiegazione approfondita basata su notizie, valutazioni e prezzo d'acquisto",
            "confidence": "HIGH" | "MEDIUM" | "LOW",
            "target_price": 24.50,
            "suggested_quantity": 25,
            "timeframe": "Breve Termine" | "Lungo Termine"
        }}
    ]
}}
"""

    def _call_gemini(self, prompt: str) -> dict:
        if not self.client:
            return {}
        try:
            model_name = settings.GEMINI_MODEL or 'gemini-3.7-flash'
            logger.info(f"Chiamata a Google Gemini con modello: {model_name}")
            response = self.client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={'response_mime_type': 'application/json'}
            )
            return json.loads(response.text)
        except Exception as e:
            logger.error(f"Errore chiamata Gemini API ({model_name}): {e}")
            return {}
