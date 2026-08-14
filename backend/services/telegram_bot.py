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

    async def send_advice_report(self, advices: list, market_summary: str):
        text = f"📊 <b>Report Giornaliero Stock Monitor</b>\n\n"
        if market_summary:
            text += f"<i>{html.escape(market_summary)}</i>\n\n"
        text += "<b>Consigli IA (Gemini 3.7 Flash):</b>\n\n"
        
        for adv in advices:
            action = adv.get('action', 'HOLD').upper()
            emoji = "🟢" if action == "BUY" else "🔴" if action == "SELL" else "⚪"
            ticker = html.escape(adv.get('ticker', ''))
            reasoning = html.escape(adv.get('reasoning', ''))
            timeframe = html.escape(adv.get('timeframe', ''))
            target_price = adv.get('target_price')
            
            text += f"{emoji} <b>{ticker}</b> ➔ <code>{action}</code>\n"
            if target_price:
                text += f"🎯 Target: <b>{target_price:.2f} €</b> | Timeframe: <i>{timeframe}</i>\n"
            text += f"💡 <i>{reasoning}</i>\n\n"
            
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
