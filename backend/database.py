import os
import logging
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base

from backend.config import settings

logger = logging.getLogger(__name__)

# Garantisce che la directory del database esista prima di aprire SQLite
_db_dir = os.path.dirname(os.path.abspath(settings.DB_PATH))
if _db_dir:
    os.makedirs(_db_dir, exist_ok=True)

DATABASE_URL = f"sqlite+aiosqlite:///{settings.DB_PATH}"

# Engine asincrono con pool ridotto: SQLite beneficia di pochi writer serializzati.
# WAL + busy_timeout gestiscono la concorrenza senza deadlock.
engine = create_async_engine(
    DATABASE_URL,
    echo=(settings.LOG_LEVEL == "DEBUG"),
    connect_args={"timeout": 30},
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)


def _set_sqlite_pragmas(dbapi_connection, connection_record):
    """
    I PRAGMA SQLite (tranne journal_mode) sono PER-CONNESSIONE: vanno applicati
    su ogni connessione del pool, altrimenti si ottengono errori
    'OperationalError: database is locked' sotto concorrenza.
    """
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA busy_timeout=10000;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.execute("PRAGMA temp_store=MEMORY;")
        cursor.execute("PRAGMA cache_size=-20000;")  # ~20MB
        cursor.close()
    except Exception as e:  # pragma: no cover
        logger.warning(f"Impossibile applicare PRAGMA SQLite: {e}")


# Listener sull'engine sincrono sottostante: ogni nuova connessione del pool
# (API, scheduler APScheduler, bot Telegram) riceve i PRAGMA corretti.
event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)

async_session_maker = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

Base = declarative_base()


@asynccontextmanager
async def session_scope():
    """
    Context manager asincrono per sessioni isolate e auto-gestite.

    Ogni task/coroutine/job DEVE aprire la propria sessione tramite questo
    helper (o async_session_maker) per garantire zero condivisione di sessioni
    tra task concorrenti (asyncio.gather, APScheduler, bot).
    Commit automatico all'uscita, rollback garantito su eccezione e
    chiusura sempre eseguita (cleanup cursori/connessioni).
    """
    session = async_session_maker()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def init_db() -> None:
    """
    Crea tutte le tabelle nel database e abilita modalità WAL ad alta concorrenza.
    """
    async with engine.begin() as conn:
        # I PRAGMA qui sono ridondanti rispetto al listener ma utili per la
        # primissima connessione di bootstrap (create_all).
        await conn.execute(text("PRAGMA journal_mode=WAL;"))
        await conn.execute(text("PRAGMA synchronous=NORMAL;"))
        await conn.execute(text("PRAGMA busy_timeout=10000;"))
        await conn.execute(text("PRAGMA foreign_keys=ON;"))

        await conn.run_sync(Base.metadata.create_all)

        # Migrazione sicura per colonna is_admin
        try:
            await conn.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT 0"))
        except Exception:
            pass  # Colonna già presente

        # Migrazioni sicure per colonne advices suddivise per mercato
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


async def get_db():
    """
    Dependency FastAPI per sessione DB asincrona sicura e isolata per-request.
    """
    async with async_session_maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
