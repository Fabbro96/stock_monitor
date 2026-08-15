import logging
import html
import httpx
from backend.config import settings
from backend.database import session_scope

logger = logging.getLogger(__name__)


def _is_key_configured(val: str | None) -> bool:
    return bool(val) and not str(val).strip().lower().startswith("your_")


class TelegramService:
    def __init__(self):
        self.bot_token = settings.TELEGRAM_BOT_TOKEN
        self.chat_id = settings.TELEGRAM_CHAT_ID

    async def send_message(self, text: str):
        if not _is_key_configured(self.bot_token) or not self.chat_id:
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


# ===========================================================================
# BOT TELEGRAM INTERATTIVO BIDIREZIONALE (polling)
# Comandi: /start /help /value /radar /advice [TICKER]
# Ogni handler apre la PROPRIA sessione DB isolata (session_scope).
# ===========================================================================
class InteractiveTelegramBot:
    """
    Gestisce il polling dei comandi in ingresso. Viene avviato/fermato nel
    lifespan di FastAPI solo se TELEGRAM_BOT_TOKEN è configurato.
    """

    def __init__(self):
        self.application = None

    def _authorized(self, update) -> bool:
        """Solo la chat configurata può usare il bot (multi-tenant safe)."""
        try:
            chat_id = str(update.effective_chat.id)
            return not settings.TELEGRAM_CHAT_ID or chat_id == str(settings.TELEGRAM_CHAT_ID)
        except Exception:
            return False

    async def _cmd_start(self, update, context):
        await update.message.reply_text(
            "🤖 <b>Stock Monitor Bot</b>\n\n"
            "Comandi disponibili:\n"
            "/value ➔ Valore portafoglio, P&L giornaliero e top movers\n"
            "/radar ➔ Watchlist con prezzi live e segnali RSI\n"
            "/advice &lt;TICKER&gt; ➔ Analisi AI Gemini on-demand (es. /advice AAPL)",
            parse_mode="HTML"
        )

    async def _cmd_value(self, update, context):
        if not self._authorized(update):
            return
        await update.message.reply_text("⏳ Calcolo valore portafoglio...")
        try:
            from backend.services.portfolio_service import build_portfolio_rows, build_portfolio_summary
            async with session_scope() as session:
                summary = await build_portfolio_summary(session)
                portfolio = await build_portfolio_rows(session)

            if not portfolio:
                await update.message.reply_text("📭 Il portafoglio è vuoto. Aggiungi posizioni dalla web app.")
                return

            pnl = summary["total_pnl"]
            pnl_emoji = "📈" if pnl >= 0 else "📉"
            text = (
                f"💼 <b>REPORT PORTAFOGLIO</b>\n"
                f"{'─' * 24}\n"
                f"💰 Valore Totale: <b>{summary['total_value']:,.2f} €</b>\n"
                f"🏦 Investito: {summary['total_invested']:,.2f} €\n"
                f"{pnl_emoji} P&L Totale: <b>{pnl:+,.2f} € ({summary['total_pnl_percent']:+.2f}%)</b>\n"
                f"📊 Posizioni: {summary['holdings_count']}\n"
            )

            # Top movers (per P&L %)
            movers = sorted(portfolio, key=lambda h: h["pnl_percent"], reverse=True)
            if movers:
                text += f"\n🏆 <b>Top Movers</b>\n"
                for h in movers[:3]:
                    emoji = "🟢" if h["pnl_percent"] >= 0 else "🔴"
                    text += f"{emoji} <code>{h['ticker']}</code> {h['pnl_percent']:+.2f}% ({h['pnl_absolute']:+,.2f} €)\n"
                if len(movers) > 3:
                    text += f"\n🐢 <b>Peggiori</b>\n"
                    for h in movers[-2:]:
                        if h["pnl_percent"] < 0:
                            text += f"🔴 <code>{h['ticker']}</code> {h['pnl_percent']:+.2f}%\n"

            await update.message.reply_text(text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Errore /value: {e}")
            await update.message.reply_text(f"⚠️ Errore nel calcolo del portafoglio: {e}")

    async def _cmd_radar(self, update, context):
        if not self._authorized(update):
            return
        await update.message.reply_text("⏳ Interrogazione radar watchlist...")
        try:
            from sqlalchemy.future import select
            from backend.models.watchlist import WatchlistItem
            from backend.models.stock import Stock
            from backend.services.market_data import MarketDataService

            async with session_scope() as session:
                result = await session.execute(select(WatchlistItem).join(Stock))
                items = result.scalars().all()
                rows = []
                for item in items:
                    stock = await session.get(Stock, item.stock_id)
                    if stock:
                        rows.append(stock)

            if not rows:
                await update.message.reply_text("📭 La watchlist è vuota.")
                return

            text = f"📡 <b>RADAR WATCHLIST</b> ({len(rows)} titoli)\n{'─' * 24}\n"
            for stock in rows[:15]:
                deep = await MarketDataService.fetch_stock_deep_dive(stock.ticker)
                price = deep.get("current_price", 0.0)
                chg = deep.get("change_percent", 0.0)
                tech = deep.get("technical", {})
                rsi = tech.get("rsi_14", 50.0)
                rsi_flag = "🔥" if (rsi or 50) >= 70 else ("🧊" if (rsi or 50) <= 30 else "⚪")
                emoji = "🟢" if chg >= 0 else "🔴"
                stale_tag = " ⏸" if deep.get("stale") else ""
                text += (
                    f"{emoji} <code>{stock.ticker}</code> {price:,.2f} ({chg:+.2f}%){stale_tag}\n"
                    f"   RSI: {rsi_flag} {rsi}\n"
                )

            await update.message.reply_text(text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Errore /radar: {e}")
            await update.message.reply_text(f"⚠️ Errore radar: {e}")

    async def _cmd_help(self, update, context):
        await self._cmd_start(update, context)

    async def _cmd_advice(self, update, context):
        if not self._authorized(update):
            return
        args = context.args or []
        if not args:
            await update.message.reply_text("ℹ️ Uso: /advice <TICKER>\nEs. <code>/advice AAPL</code>", parse_mode="HTML")
            return
        ticker = args[0].strip().upper()

        await update.message.reply_text(f"🧠 Analisi AI di <code>{ticker}</code> in corso (Gemini)... attendi ~15s", parse_mode="HTML")
        try:
            from backend.services.advisor import AdvisorService
            from backend.database import async_session_maker

            advisor = AdvisorService()
            # Sessione dedicata e isolata per l'analisi on-demand
            async with async_session_maker() as session:
                analysis = await advisor.analyze_single_stock(ticker, session)

            if not analysis:
                await update.message.reply_text(f"⚠️ Nessuna analisi disponibile per {ticker}.")
                return

            action = analysis.get("action", "N/D")
            action_label = analysis.get("action_label", "")
            tp = analysis.get("target_price")
            sl = analysis.get("stop_loss")
            upside = analysis.get("upside_potential_pct")
            confidence = analysis.get("confidence", "N/D")
            timeframe = analysis.get("timeframe", "N/D")
            summary = analysis.get("summary", "")
            bull = analysis.get("bull_case", "")
            bear = analysis.get("bear_case", "")

            text = (
                f"🧠 <b>ANALISI AI — {html.escape(analysis.get('name', ticker))}</b> (<code>{ticker}</code>)\n"
                f"{'─' * 24}\n"
                f"🎯 Raccomandazione: <b>{html.escape(str(action_label or action))}</b>\n"
                f"💹 Target Price: <b>{tp}</b>\n"
                f"🛑 Stop Loss: <b>{sl}</b>\n"
                f"📈 Upside: <b>{upside}%</b>\n"
                f"🕒 Timeframe: {timeframe} | Confidenza: {confidence}\n\n"
                f"📝 <i>{html.escape(str(summary))}</i>\n"
            )
            if bull:
                text += f"\n🟢 <b>Bull Case:</b> {html.escape(str(bull)[:300])}\n"
            if bear:
                text += f"🔴 <b>Bear Case:</b> {html.escape(str(bear)[:300])}\n"
            text += "\n<i>⚠️ Non è consulenza finanziaria.</i>"

            await update.message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)
        except Exception as e:
            logger.error(f"Errore /advice: {e}")
            await update.message.reply_text(f"⚠️ Errore durante l'analisi di {ticker}: {e}")

    # --- Lifecycle -------------------------------------------------------
    async def start(self):
        """Avvia il polling. Chiamato nel lifespan di FastAPI."""
        if not _is_key_configured(settings.TELEGRAM_BOT_TOKEN):
            logger.info("Telegram bot token non configurato: bot interattivo disattivato.")
            return False
        if not settings.TELEGRAM_BOT_ENABLED:
            logger.info("TELEGRAM_BOT_ENABLED=False: bot interattivo disattivato.")
            return False
        try:
            from telegram.ext import Application, CommandHandler
            self.application = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
            self.application.add_handler(CommandHandler(["start", "help"], self._cmd_start))
            self.application.add_handler(CommandHandler("value", self._cmd_value))
            self.application.add_handler(CommandHandler("radar", self._cmd_radar))
            self.application.add_handler(CommandHandler("advice", self._cmd_advice))
            await self.application.initialize()
            await self.application.start()
            await self.application.updater.start_polling(drop_pending_updates=True)
            logger.info("Bot Telegram interattivo avviato (polling).")
            return True
        except Exception as e:
            logger.error(f"Impossibile avviare il bot Telegram interattivo: {e}")
            self.application = None
            return False

    async def stop(self):
        """Arresto graceful. Chiamato nello shutdown del lifespan."""
        if not self.application:
            return
        try:
            await self.application.updater.stop()
        except Exception:
            pass
        try:
            await self.application.stop()
            await self.application.shutdown()
        except Exception as e:
            logger.debug(f"Errore shutdown bot Telegram: {e}")
        finally:
            self.application = None
            logger.info("Bot Telegram interattivo arrestato.")
