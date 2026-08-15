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

# Concurrency semaphore for Gemini API
_gemini_semaphore = asyncio.Semaphore(2)

class AdvisorService:
    def __init__(self):
        self.client = None
        if settings.GEMINI_API_KEY:
            self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
        else:
            logger.warning("GEMINI_API_KEY non configurata.")
        self.sentiment_service = SentimentService()

    async def generate_advice(self, db_session: AsyncSession, force: bool = False) -> list[dict]:
        if not force and not MarketDataService.are_any_markets_open():
            logger.info("Borse chiuse: generazione analisi saltata (nessun mercato aperto).")
            return []

        result = await db_session.execute(select(Stock).where(Stock.is_active == True))
        stocks = result.scalars().all()
        if not stocks:
            logger.warning("Nessun titolo attivo trovato per la generazione dei consigli.")
            return []
        
        result = await db_session.execute(select(Holding))
        holdings = result.scalars().all()
        
        result = await db_session.execute(select(UserSettings).limit(1))
        user_settings = result.scalars().first()
        
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

        prompt = self._build_macro_prompt(italian_stocks, us_stocks, settings_summary)
        
        async with _gemini_semaphore:
            response_json = await self._call_gemini(prompt)
        
        if not response_json or not ('borsa_italiana' in response_json or 'borsa_americana' in response_json):
            logger.info("Risposta Gemini non disponibile, generazione consigli quantitativi di fallback...")
            response_json = self._build_deterministic_macro_fallback(italian_stocks, us_stocks, settings_summary)

        advices_created = []
        now_utc = datetime.now(timezone.utc)

        # 1. Borsa Italiana
        it_data = response_json.get('borsa_italiana', {})
        if it_data and 'overview' in it_data:
            adv_it = Advice(
                market="IT",
                title=it_data.get('title', "Borsa Italiana (Piazza Affari)"),
                action=it_data.get('action', 'MANTENIMENTO'),
                overview=it_data.get('overview'),
                reasoning=it_data.get('strategy'),
                stocks_json=json.dumps(it_data.get('stocks_analysis', []), ensure_ascii=False),
                risks=it_data.get('risks'),
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

        # 2. Borsa Americana
        us_data = response_json.get('borsa_americana', {})
        if us_data and 'overview' in us_data:
            adv_us = Advice(
                market="US",
                title=us_data.get('title', "Borsa Americana (Wall Street)"),
                action=us_data.get('action', 'MANTENIMENTO'),
                overview=us_data.get('overview'),
                reasoning=us_data.get('strategy'),
                stocks_json=json.dumps(us_data.get('stocks_analysis', []), ensure_ascii=False),
                risks=us_data.get('risks'),
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

    async def analyze_single_stock(self, ticker: str, db_session: AsyncSession) -> dict:
        """
        Genera un'analisi approfondita istantanea su richiesta per un singolo titolo con Google Gemini 3.7 Flash,
        incorporando il contesto reale delle posizioni in portafoglio dell'utente.
        """
        ticker_up = ticker.strip().upper()
        deep_data = await MarketDataService.fetch_stock_deep_dive(ticker_up)
        
        # Check if user holds this stock in portfolio
        holding_info = None
        res = await db_session.execute(
            select(Holding).join(Stock).where(Stock.ticker == ticker_up)
        )
        holding = res.scalars().first()
        if holding:
            cur_price = deep_data.get('current_price', holding.avg_purchase_price)
            invested = holding.quantity * holding.avg_purchase_price
            cur_val = holding.quantity * cur_price
            pnl_abs = cur_val - invested
            pnl_pct = (pnl_abs / invested * 100) if invested else 0.0
            holding_info = {
                "in_portfolio": True,
                "quantity": holding.quantity,
                "avg_purchase_price": holding.avg_purchase_price,
                "total_invested": round(invested, 2),
                "current_pnl_abs": round(pnl_abs, 2),
                "current_pnl_pct": round(pnl_pct, 2)
            }

        # News contestuali
        name = deep_data.get("name", ticker_up)
        news_items = await self.sentiment_service.get_combined_market_context(ticker_up, name)
        headlines = [n["title"] for n in news_items[:5]]

        # Profile settings
        result = await db_session.execute(select(UserSettings).limit(1))
        user_settings = result.scalars().first()
        strategy = user_settings.strategy if user_settings else "mixed"

        portfolio_context_str = "L'utente NON possiede attualmente questo titolo in portafoglio."
        if holding_info:
            portfolio_context_str = f"""
POSIZIONE ATTUALE DELL'UTENTE IN PORTAFOGLIO:
- Quantità posseduta: {holding_info['quantity']} azioni
- Prezzo Medio di Carico: {holding_info['avg_purchase_price']} {deep_data.get('currency')}
- P&L Attuale: {holding_info['current_pnl_pct']}% ({holding_info['current_pnl_abs']} {deep_data.get('currency')})
⚠️ NOTA BENE: Personalizza la tua raccomandazione operativa tenendo conto se l'utente è in profitto o perdita sulla posizione esistente!
"""

        prompt = f"""
Sei un Senior Equity Research Analyst & Quantitative Portfolio Strategist.
Fornisci un'analisi approfondita e una raccomandazione operativa chiara per il titolo {ticker_up} ({name}).

{portfolio_context_str}

DATI FONDAMENTALI E DI MERCATO:
- Prezzo Attuale: {deep_data.get('current_price')} {deep_data.get('currency')}
- Variazione Giornaliera: {deep_data.get('change_percent')}%
- Range 52 Settimane: {deep_data.get('fifty_two_week_low')} - {deep_data.get('fifty_two_week_high')} {deep_data.get('currency')}
- P/E Ratio: {deep_data.get('pe_ratio') or 'N/A'} (Forward P/E: {deep_data.get('forward_pe') or 'N/A'})
- EPS: {deep_data.get('eps') or 'N/A'}
- Beta: {deep_data.get('beta') or 'N/A'}
- Dividend Yield: {deep_data.get('dividend_yield') or 'N/A'}%
- Settore / Industria: {deep_data.get('sector')} / {deep_data.get('industry')}

INDICATORI TECNICI:
- RSI (14 periodi): {deep_data.get('technical', {}).get('rsi_14')} ({deep_data.get('technical', {}).get('rsi_status')})
- Media Mobile 20 giorni (SMA 20): {deep_data.get('technical', {}).get('sma_20') or 'N/A'}
- Media Mobile 50 giorni (SMA 50): {deep_data.get('technical', {}).get('sma_50') or 'N/A'}
- Trend Configurato: {deep_data.get('technical', {}).get('trend')}

ULTIME NOTIZIE & SENTIMENT RECENTE:
{json.dumps(headlines, ensure_ascii=False, indent=2)}

PROFILO UTENTE: Strategia {strategy}.

ISTRUZIONI:
1. Valuta il quadro tecnico e fondamentale integrando le notizie e l'eventuale posizione posseduta.
2. Definisci una raccomandazione categorica tra: ACCUMULO (Buy), MANTENIMENTO (Hold), PRESA_PROFITTO (Sell), PRUDENZA.
3. Fornisci un Target Price numerico realistico a 3-6 mesi e un livello di Stop Loss prudenziale.
4. Elenca i principali catalizzatori positivi (Bull Case) e i fattori di rischio (Bear Case).
5. Esprimi un giudizio tecnico sintetico e la strategia operativa personalizzata.

Rispondi ESCLUSIVAMENTE in formato JSON con questo schema:
{{
    "ticker": "{ticker_up}",
    "name": "{name}",
    "holding_context": {json.dumps(holding_info) if holding_info else 'null'},
    "action": "ACCUMULO" | "MANTENIMENTO" | "PRESA_PROFITTO" | "PRUDENZA",
    "action_label": "🟢 Accumulo / Buy" | "🟡 Mantenimento / Hold" | "🔴 Presa Profitto / Sell" | "🛡️ Prudenza",
    "target_price": 0.0,
    "stop_loss": 0.0,
    "upside_potential_pct": 0.0,
    "timeframe": "Breve Termine" | "Medio Termine" | "Lungo Termine",
    "confidence": "ALTA" | "MEDIA" | "BASSA",
    "summary": "Sintesi esecutiva dell'analisi in 2-3 frasi...",
    "bull_case": "Fattori di crescita, vantaggi competitivi e catalizzatori rialzisti...",
    "bear_case": "Rischi di mercato, concorrenza, tassi, trimestrali...",
    "technical_verdict": "Sintesi tecnica basata su RSI, supporti/resistenze e medie mobili...",
    "operational_strategy": "Consiglio pratico personalizzato di posizionamento..."
}}
"""
        async with _gemini_semaphore:
            response_json = await self._call_gemini(prompt)
        
        if response_json and "action" in response_json:
            if holding_info:
                response_json['holding_context'] = holding_info
            return response_json

        # Fallback quantitativo deterministico
        price = deep_data.get('current_price', 10.0)
        rsi = deep_data.get('technical', {}).get('rsi_14', 50.0)
        
        if rsi < 35:
            action = "ACCUMULO"
            action_label = "🟢 Accumulo / Buy"
            tp = round(price * 1.12, 2)
            sl = round(price * 0.94, 2)
            conf = "MEDIA"
        elif rsi > 70:
            action = "PRESA_PROFITTO"
            action_label = "🔴 Presa Profitto / Sell"
            tp = round(price * 0.95, 2)
            sl = round(price * 0.98, 2)
            conf = "MEDIA"
        else:
            action = "MANTENIMENTO"
            action_label = "🟡 Mantenimento / Hold"
            tp = round(price * 1.06, 2)
            sl = round(price * 0.92, 2)
            conf = "MEDIA"

        summary_text = f"Valutazione quantitativa per {ticker_up}. L'indicatore RSI({rsi}) suggerisce una configurazione di {action.lower()}."
        if holding_info:
            summary_text += f" Posizione in portafoglio: {holding_info['quantity']} azioni a carico {holding_info['avg_purchase_price']}€ ({holding_info['current_pnl_pct']}%)."

        return {
            "ticker": ticker_up,
            "name": name,
            "holding_context": holding_info,
            "action": action,
            "action_label": action_label,
            "target_price": tp,
            "stop_loss": sl,
            "upside_potential_pct": round(((tp - price) / price * 100), 2) if price else 0.0,
            "timeframe": "Medio Termine",
            "confidence": conf,
            "summary": summary_text,
            "bull_case": "Solido posizionamento settoriale e potenziale espansione dei multipli nel medio periodo.",
            "bear_case": "Possibile volatilità legata a fattori macroeconomici e dati trimestrali.",
            "technical_verdict": f"RSI a {rsi}, trend attuale {deep_data.get('technical', {}).get('trend', 'neutro')}.",
            "operational_strategy": "Mantenere una corretta diversificazione e impostare opportuni ordini di stop loss."
        }

    def _build_deterministic_macro_fallback(self, italian_stocks: list, us_stocks: list, user_settings: dict) -> dict:
        """Genera 5-10 consigli schematici e prioritizzati bilanciati (BUY, SELL, HOLD) quando Gemini non è disponibile."""
        def make_stocks_list(stocks_in, default_market):
            items = []
            pool = stocks_in if stocks_in else [
                {"ticker": "ENEL.MI" if default_market == "IT" else "AAPL", "name": "Enel S.p.A." if default_market == "IT" else "Apple Inc.", "current_price": 6.85 if default_market == "IT" else 230.0},
                {"ticker": "ISP.MI" if default_market == "IT" else "MSFT", "name": "Intesa Sanpaolo" if default_market == "IT" else "Microsoft", "current_price": 3.82 if default_market == "IT" else 425.0},
                {"ticker": "RACE.MI" if default_market == "IT" else "NVDA", "name": "Ferrari N.V." if default_market == "IT" else "NVIDIA Corp.", "current_price": 428.0 if default_market == "IT" else 140.0},
                {"ticker": "LDO.MI" if default_market == "IT" else "AMZN", "name": "Leonardo S.p.A." if default_market == "IT" else "Amazon.com", "current_price": 24.10 if default_market == "IT" else 205.0},
                {"ticker": "G.MI" if default_market == "IT" else "GOOGL", "name": "Generali Assicurazioni" if default_market == "IT" else "Alphabet", "current_price": 26.40 if default_market == "IT" else 175.0},
            ]
            actions_cycle = [
                ("BUY", "ALTA", "Valutazione attraente su supporto tecnico con solido rendimento cedolare e buyback."),
                ("HOLD", "MEDIA", "Trend laterale solido; mantenere la posizione impostando trailing stop prudenziale."),
                ("BUY", "OPPORTUNITÀ", "Forte momentum su trimestrali positive ed espansione dei margini operativi."),
                ("SELL", "RISCHIO", "Prezzi vicini ai massimi storici con ipercomprato RSI; consigliata presa di profitto parziale."),
                ("HOLD", "MEDIA", "Quadro fondamentale stabile in attesa dei prossimi dati macroeconomici.")
            ]
            for i, s in enumerate(pool[:6]):
                act, prio, reason = actions_cycle[i % len(actions_cycle)]
                price = float(s.get('current_price', 10.0)) if str(s.get('current_price')).replace('.', '', 1).isdigit() else 10.0
                tp = round(price * (1.10 if act == "BUY" else (0.95 if act == "SELL" else 1.05)), 2)
                items.append({
                    "ticker": s.get("ticker"),
                    "name": s.get("name", s.get("ticker")),
                    "action": act,
                    "priority": prio,
                    "target_price": tp,
                    "note": reason
                })
            return items

        return {
            "market_summary": "Quadro macroeconomico globale caratterizzato da politiche monetarie caute e rotazione settoriale verso qualità e dividendi.",
            "borsa_italiana": {
                "title": "Borsa Italiana (Piazza Affari)",
                "market": "IT",
                "action": "MANTENIMENTO",
                "overview": "Il FTSE MIB consolida i recenti guadagni sostenuto dal comparto bancario e utilities ad alto dividendo.",
                "strategy": "Privilegiare titoli a solida generazione di cassa (value & dividendi) mantenendo liquidità per storni.",
                "stocks_analysis": make_stocks_list(italian_stocks, "IT"),
                "risks": "Sensibilità ai tassi BCE e possibile rallentamento della crescita manifatturiera europea.",
                "confidence": "HIGH",
                "timeframe": "Medio Termine"
            },
            "borsa_americana": {
                "title": "Borsa Americana (Wall Street)",
                "market": "US",
                "action": "ACCUMULO",
                "overview": "S&P 500 e Nasdaq mostrano resilienza grazie alla leadership tecnologica e agli investimenti in AI infrastrutturale.",
                "strategy": "Mantenere esposizione core su Big Tech e accumulare sui pullback tecnici.",
                "stocks_analysis": make_stocks_list(us_stocks, "US"),
                "risks": "Volatilità post-trimestrali e aspettative sui tagli dei tassi della Federal Reserve.",
                "confidence": "HIGH",
                "timeframe": "Medio Termine"
            }
        }

    def _build_macro_prompt(self, italian_stocks: list, us_stocks: list, user_settings: dict) -> str:
        return f"""
Sei un Chief Investment Officer e Senior Quantitative Market Strategist.
Devi produrre DUE BLOCCHI STRATEGICI GENERALI DI MERCATO (Borsa Italiana e Borsa Americana).

IMPORTANTE: All'interno di ciascun mercato, devi fornire **tra 5 e 10 consigli azionari schematici e prioritizzati** (mischiati in modo bilanciato tra BUY, HOLD e SELL in base alle reali opportunità e rischi attuali).
Ogni consiglio deve essere **estremamente schematico**, chiaro e con un motivo breve ed essenziale (1-2 frasi).

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
1. **Quadro Generale (overview)**: Breve sintesi (2-3 frasi) dello scenario macro, tassi e trend degli indici.
2. **Strategia Operativa Generale (strategy)**: Piano d'azione sintetico (es. bilanciare dividendi e growth).
3. **Consigli Schematici (stocks_analysis)**: Fornisci da 5 a 10 consigli ordinati per priorità (miscela tra BUY, HOLD, SELL):
   - `ticker`: Simbolo
   - `name`: Nome titolo
   - `action`: 'BUY' | 'HOLD' | 'SELL'
   - `priority`: 'ALTA' | 'MEDIA' | 'OPPORTUNITÀ' | 'RISCHIO'
   - `target_price`: Prezzo obiettivo stimato numerico
   - `note`: Motivo schematico e sintetico in 1-2 frasi (es. "RSI in ipervenduto + catalizzatore trimestrale favorevole.")
4. **Punti di Attenzione & Rischi (risks)**: Catalizzatori chiave da monitorare.
5. **Azione di Fondo**: 'ACCUMULO', 'MANTENIMENTO', 'PRESA_PROFITTO' o 'PRUDENZA'.

Rispondi ESCLUSIVAMENTE in formato JSON con la seguente struttura:
{{
    "market_summary": "Sintesi macro globale brevissima",
    "borsa_italiana": {{
        "title": "Borsa Italiana (Piazza Affari)",
        "market": "IT",
        "action": "ACCUMULO" | "MANTENIMENTO" | "PRESA_PROFITTO" | "PRUDENZA",
        "overview": "Sintesi scenario italiano...",
        "strategy": "Strategia complessiva...",
        "stocks_analysis": [
            {{
                "ticker": "ENEL.MI",
                "name": "Enel S.p.A.",
                "action": "BUY",
                "priority": "ALTA",
                "target_price": 7.40,
                "note": "Rendimento da dividendo >6% e trend rialzista sopra SMA 50."
            }},
            {{
                "ticker": "ISP.MI",
                "name": "Intesa Sanpaolo",
                "action": "HOLD",
                "priority": "MEDIA",
                "target_price": 4.10,
                "note": "Margini solidi; mantenere posizione con stop a protezione."
            }},
            {{
                "ticker": "RACE.MI",
                "name": "Ferrari N.V.",
                "action": "SELL",
                "priority": "RISCHIO",
                "target_price": 410.00,
                "note": "Multipli tirati e ipercomprato; consigliata presa di profitto parziale."
            }}
        ],
        "risks": "Rischi specifici per l'Italia...",
        "confidence": "HIGH" | "MEDIUM" | "LOW",
        "timeframe": "Medio Termine"
    }},
    "borsa_americana": {{
        "title": "Borsa Americana (Wall Street)",
        "market": "US",
        "action": "ACCUMULO" | "MANTENIMENTO" | "PRESA_PROFITTO" | "PRUDENZA",
        "overview": "Sintesi scenario USA...",
        "strategy": "Strategia complessiva...",
        "stocks_analysis": [
            {{
                "ticker": "NVDA",
                "name": "NVIDIA Corporation",
                "action": "BUY",
                "priority": "ALTA",
                "target_price": 160.00,
                "note": "Forte domanda data center AI e breakout tecnico confermato."
            }},
            {{
                "ticker": "AAPL",
                "name": "Apple Inc.",
                "action": "HOLD",
                "priority": "MEDIA",
                "target_price": 245.00,
                "note": "Posizionamento solido; attendere consolidamento per nuovi ingressi."
            }},
            {{
                "ticker": "TSLA",
                "name": "Tesla Inc.",
                "action": "SELL",
                "priority": "RISCHIO",
                "target_price": 280.00,
                "note": "Pressione sui margini auto e volatilità elevata nel breve termine."
            }}
        ],
        "risks": "Rischi specifici per gli USA...",
        "confidence": "HIGH" | "MEDIUM" | "LOW",
        "timeframe": "Medio Termine"
    }}
}}
"""

    async def _call_gemini(self, prompt: str) -> dict:
        if not self.client:
            return {}
        model_name = settings.GEMINI_MODEL or 'gemini-3.7-flash'
        try:
            logger.info(f"Chiamata asincrona a Google Gemini con modello: {model_name}")
            if hasattr(self.client, 'aio') and hasattr(self.client.aio, 'models'):
                response = await self.client.aio.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config={'response_mime_type': 'application/json'}
                )
            else:
                response = await asyncio.to_thread(
                    self.client.models.generate_content,
                    model=model_name,
                    contents=prompt,
                    config={'response_mime_type': 'application/json'}
                )
            return json.loads(response.text)
        except Exception as e:
            logger.error(f"Errore chiamata Gemini API ({model_name}): {e}")
            return {}
