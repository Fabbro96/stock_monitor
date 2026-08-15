import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    """
    Impostazioni dell'applicazione caricate da variabili d'ambiente o dal file .env
    """
    TELEGRAM_BOT_TOKEN: str | None = None
    TELEGRAM_CHAT_ID: str | None = None
    TELEGRAM_BOT_ENABLED: bool = True
    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-3.7-flash"
    REDDIT_CLIENT_ID: str | None = None
    REDDIT_CLIENT_SECRET: str | None = None
    REDDIT_USER_AGENT: str = "stock_monitor/1.0"
    DB_PATH: str = "data/stock_monitor.db"
    ALERT_CHECK_INTERVAL_MINUTES: int = 15
    LOG_LEVEL: str = "INFO"

    # Analytics & Risk Metrics
    RISK_FREE_RATE: float = 0.02  # Tasso risk-free annuo per Sharpe Ratio

    # Sicurezza & Autenticazione
    SECRET_KEY: str = "stock-monitor-super-secret-key-change-in-env-2026"
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin123"
    ACCESS_TOKEN_EXPIRE_DAYS: int = 7

    model_config = {
        "env_file": ".env"
    }

settings = Settings()

