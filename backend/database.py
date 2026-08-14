import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy import text
from backend.config import settings

DATABASE_URL = f"sqlite+aiosqlite:///{settings.DB_PATH}"

# Configurazione ottimizzata engine con timeout e concurrency WAL per SQLite
engine = create_async_engine(
    DATABASE_URL,
    echo=(settings.LOG_LEVEL == "DEBUG"),
    connect_args={"timeout": 20},
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
        # Ottimizzazioni performance SQLite per NAS
        await conn.execute(text("PRAGMA journal_mode=WAL;"))
        await conn.execute(text("PRAGMA synchronous=NORMAL;"))
        await conn.execute(text("PRAGMA busy_timeout=5000;"))
        await conn.execute(text("PRAGMA foreign_keys=ON;"))
        
        await conn.run_sync(Base.metadata.create_all)
        
        # Migrazione sicura per colonna is_admin
        try:
            await conn.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT 0"))
        except Exception:
            pass # Colonna già presente

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
