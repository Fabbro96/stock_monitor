import logging
import html
import httpx
from backend.config import settings

logger = logging.getLogger(__name__)

class TelegramService:
    def __init__(self):
        self.bot_token = settings.TELEGRAM_BOT_TOKEN
        self.chat_id = settings.TELEGRAM_CHAT_ID

    async def send_message(self, text: str):
        if not self.bot_token or not self.chat_id or self.bot_token.startswith("your_"):
            logger.debug("Telegram credentials not configured. Skipping message.")
            return

        api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(api_url, json=payload)
                response.raise_for_status()
                logger.info("Messaggio Telegram inviato con successo.")
        except Exception as e:
            logger.error(f"Errore invio messaggio Telegram: {e}")

    async def send_advice_report(self, advices: list, market_summary: str = ""):
        text = f"📊 <b>Stock Monitor - Report Strategico Borse</b>\n\n"
        if market_summary:
            text += f"<i>{html.escape(market_summary)}</i>\n\n"
        
        for adv in advices:
            is_it = adv.get('market') == 'IT'
            flag = "🇮🇹" if is_it else "🇺🇸"
            title = adv.get('title') or ("Borsa Italiana" if is_it else "Borsa Americana")
            action = adv.get('action', 'MANTENIMENTO').upper()
            
            action_emoji = "🟢" if "BUY" in action or "ACCUMULO" in action else ("🔴" if "SELL" in action or "PROFITTO" in action else "🟡")
            
            text += f"{flag} <b>{html.escape(title)}</b>\n"
            text += f"Azione di Fondo: {action_emoji} <code>{html.escape(action)}</code>\n"
            
            if adv.get('overview'):
                text += f"🌐 <i>{html.escape(adv['overview'][:200])}...</i>\n"
            if adv.get('strategy'):
                text += f"🎯 <b>Strategia:</b> {html.escape(adv['strategy'][:250])}\n"
            
            stocks = adv.get('stocks_analysis', [])
            if stocks:
                text += "📈 <b>Focus Titoli:</b>\n"
                for s in stocks[:4]:
                    s_act = s.get('action', 'HOLD')
                    s_emoji = "🟢" if s_act == "BUY" else ("🔴" if s_act == "SELL" else "⚪")
                    tp = f" | Target: <b>{s['target_price']}</b>" if s.get('target_price') else ""
                    text += f"  • {s_emoji} <b>{html.escape(s.get('ticker',''))}</b> (<code>{s_act}</code>{tp})\n"
            
            text += "\n"
            
        await self.send_message(text)


    async def send_alert(self, stock_name: str, ticker: str, change_percent: float, current_price: float):
        emoji = "🚀" if change_percent > 0 else "📉"
        direction = "in RIALZO" if change_percent > 0 else "in RIBASSO"
        text = (
            f"🚨 <b>ALLERTA PREZZO {direction}</b> 🚨\n\n"
            f"{emoji} <b>{html.escape(stock_name)}</b> (<code>{html.escape(ticker)}</code>)\n"
            f"Variazione: <b>{change_percent:+.2f}%</b>\n"
            f"Prezzo Attuale: <b>{current_price:.2f} €</b>"
        )
        await self.send_message(text)

    async def send_test_message(self):
        await self.send_message("🤖 <b>Stock Monitor</b>: Messaggio di test completato con successo! ✅")
