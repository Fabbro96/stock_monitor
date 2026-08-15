import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy import text
from backend.config import settings

DATABASE_URL = f"sqlite+aiosqlite:///{settings.DB_PATH}"

# Configurazione ottimizzata engine con timeout esteso e concurrency WAL per SQLite
engine = create_async_engine(
    DATABASE_URL,
    echo=(settings.LOG_LEVEL == "DEBUG"),
    connect_args={"timeout": 30},
    pool_pre_ping=True
)

async_session_maker = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

Base = declarative_base()

async def init_db() -> None:
    """
    Crea tutte le tabelle nel database e abilita modalità WAL ad alta concorrenza.
    """
    async with engine.begin() as conn:
        # Ottimizzazioni performance SQLite per NAS & SSD
        await conn.execute(text("PRAGMA journal_mode=WAL;"))
        await conn.execute(text("PRAGMA synchronous=NORMAL;"))
        await conn.execute(text("PRAGMA busy_timeout=20000;"))
        await conn.execute(text("PRAGMA foreign_keys=ON;"))
        
        await conn.run_sync(Base.metadata.create_all)
        
        # Migrazione sicura per colonna is_admin
        try:
            await conn.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT 0"))
        except Exception:
            pass

        # Migrazioni sicure per colonne advices
        for col_sql in [
            "ALTER TABLE advices ADD COLUMN market VARCHAR DEFAULT 'ALL'",
            "ALTER TABLE advices ADD COLUMN title VARCHAR",
            "ALTER TABLE advices ADD COLUMN overview TEXT",
            "ALTER TABLE advices ADD COLUMN stocks_json TEXT",
            "ALTER TABLE advices ADD COLUMN risks TEXT"
        ]:
            try:
                await conn.execute(text(col_sql))
            except Exception:
                pass

        # Migrazioni sicure per colonne watchlist alerts
        for wl_col in [
            "ALTER TABLE watchlist_items ADD COLUMN alert_above FLOAT",
            "ALTER TABLE watchlist_items ADD COLUMN alert_below FLOAT",
            "ALTER TABLE watchlist_items ADD COLUMN alert_triggered BOOLEAN DEFAULT 0"
        ]:
            try:
                await conn.execute(text(wl_col))
            except Exception:
                pass


from contextlib import asynccontextmanager

@asynccontextmanager
async def session_scope():
    """
    Context manager asincrono per sessioni DB isolate (es. per scheduler, telegram bot e background tasks).
    """
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

async def get_db():
    """
    Dependency FastAPI per sessione DB asincrona sicura
    """
    async with async_session_maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

