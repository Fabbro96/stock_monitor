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
    logger.info("Avvio job periodico: generazione 5 consigli AI con Gemini 3.7 Flash")
    try:
        async with async_session_maker() as session:
            advisor_service = AdvisorService()
            advices = await advisor_service.generate_advice(session)
            logger.info(f"Generati con successo {len(advices)} nuovi consigli finanziari.")
    except Exception as e:
        logger.error(f"Errore durante generate_advice_job: {e}")

async def cleanup_old_data_job():
    logger.info("Avvio job pulizia dati storici obsoleti")
    # Pulizia log / storici più vecchi di 365 giorni se necessario
    pass

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
    
    scheduler.start()
    logger.info("Scheduler APScheduler avviato con successo.")

def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler terminato correttamente.")
