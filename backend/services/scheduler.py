import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from backend.database import async_session_maker
from backend.services.market_data import MarketDataService
from backend.services.sentiment import SentimentService
from backend.services.advisor import AdvisorService
from backend.services.alerting import AlertingService
from backend.config import settings

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

async def collect_prices_job():
    logger.info("Avvio job periodico: aggiornamento prezzi di mercato")
    try:
        async with async_session_maker() as session:
            saved = await MarketDataService.fetch_all_prices(session)
            logger.info(f"Aggiornati con successo i prezzi per {len(saved)} titoli.")
    except Exception as e:
        logger.error(f"Errore durante collect_prices_job: {e}")

async def check_alerts_job():
    try:
        async with async_session_maker() as session:
            alert_service = AlertingService()
            await alert_service.check_alerts(session)
    except Exception as e:
        logger.error(f"Errore durante check_alerts_job: {e}")

async def analyze_sentiment_job():
    logger.info("Avvio job periodico: raccolta notizie e sentiment multi-fonte")
    try:
        async with async_session_maker() as session:
            sentiment_service = SentimentService()
            await sentiment_service.analyze_all_stocks(session)
            logger.info("Aggiornamento notizie e sentiment completato.")
    except Exception as e:
        logger.error(f"Errore durante analyze_sentiment_job: {e}")

async def generate_advice_job():
    if not MarketDataService.are_any_markets_open():
        logger.info("Borse chiuse: job periodico generazione consigli saltato.")
        return

    logger.info("Avvio job periodico: generazione 5 consigli AI con Gemini 3.7 Flash")
    try:
        async with async_session_maker() as session:
            advisor_service = AdvisorService()
            advices = await advisor_service.generate_advice(session)
            logger.info(f"Generati con successo {len(advices)} nuovi consigli finanziari.")
    except Exception as e:
        logger.error(f"Errore durante generate_advice_job: {e}")


async def cleanup_old_data_job():
    logger.info("Avvio job pulizia: eliminazione analisi più vecchie di 7 giorni")
    try:
        async with async_session_maker() as session:
            from backend.models.advice import Advice
            from sqlalchemy import delete
            from datetime import datetime, timezone, timedelta
            
            cutoff = datetime.now(timezone.utc) - timedelta(days=7)
            await session.execute(delete(Advice).where(Advice.timestamp < cutoff))
            await session.commit()
            logger.info("Pulizia analisi storiche (più vecchie di 7 giorni) completata.")
    except Exception as e:
        logger.error(f"Errore durante cleanup_old_data_job: {e}")


def init_scheduler(app):
    # Esegui ogni ora durante l'orario di borsa
    scheduler.add_job(
        collect_prices_job,
        'cron',
        minute=0,
        id='collect_prices_job',
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300
    )
    # Controllo alert
    scheduler.add_job(
        check_alerts_job,
        'interval',
        minutes=settings.ALERT_CHECK_INTERVAL_MINUTES,
        id='check_alerts_job',
        replace_existing=True,
        max_instances=1,
        coalesce=True
    )
    # Raccogli news due volte al giorno (12:00 e 20:00)
    scheduler.add_job(
        analyze_sentiment_job,
        'cron',
        hour='12,20',
        minute=0,
        id='analyze_sentiment_job',
        replace_existing=True,
        max_instances=1,
        coalesce=True
    )
    # Generazione consigli 2 volte al giorno (09:00 e 18:00)
    scheduler.add_job(
        generate_advice_job,
        'cron',
        hour='9,18',
        minute=0,
        id='generate_advice_job',
        replace_existing=True,
        max_instances=1,
        coalesce=True
    )
    # Pulizia automatica analisi vecchie (> 7 giorni) ogni notte alle 03:00
    scheduler.add_job(
        cleanup_old_data_job,
        'cron',
        hour=3,
        minute=0,
        id='cleanup_old_data_job',
        replace_existing=True,
        max_instances=1,
        coalesce=True
    )
    
    scheduler.start()

    logger.info("Scheduler APScheduler avviato con successo.")


def shutdown_scheduler():
    if scheduler.running:
        # wait=False: non bloccare lo shutdown del lifespan se un job è in corso
        scheduler.shutdown(wait=False)
        logger.info("Scheduler terminato correttamente.")
