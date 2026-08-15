import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, FileResponse
from sqlalchemy.future import select
import uvicorn

from backend.config import settings
from backend.database import init_db, async_session_maker
from backend.services.scheduler import init_scheduler, shutdown_scheduler
from backend.models.settings import UserSettings
from backend.models.user import User
from backend.services.auth import get_current_user, hash_password
from backend.services.telegram_bot import InteractiveTelegramBot
from backend.routers import (
    stocks_router,
    portfolio_router,
    dashboard_router,
    advice_router,
    settings_router,
    auth_router,
    watchlist_router
)

# Configura il logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Istanza singleton del bot interattivo (gestita dal lifespan)
telegram_bot = InteractiveTelegramBot()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting up Stock Monitor...")
    
    # Ensure data directory exists (deriva dalla path del DB per robustezza)
    os.makedirs("data", exist_ok=True)
    db_dir = os.path.dirname(os.path.abspath(settings.DB_PATH))
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    
    # Initialize DB
    await init_db()
    
    # Create default settings and initial admin user if none exist
    async with async_session_maker() as session:
        # UserSettings
        result = await session.execute(select(UserSettings).limit(1))
        if not result.scalars().first():
            logger.info("Creating default UserSettings")
            session.add(UserSettings())
            await session.commit()
            
        # Admin User
        user_result = await session.execute(select(User).where(User.username == settings.ADMIN_USERNAME))
        admin = user_result.scalars().first()
        if not admin:
            admin_user = settings.ADMIN_USERNAME
            admin_pass = settings.ADMIN_PASSWORD
            logger.info(f"Creating default admin user: '{admin_user}'")
            hashed = hash_password(admin_pass)
            session.add(User(username=admin_user, hashed_password=hashed, is_admin=True))
            await session.commit()
            logger.info(f"Admin user '{admin_user}' created successfully.")
        elif not admin.is_admin:
            admin.is_admin = True
            await session.commit()
            logger.info(f"Admin status updated for '{admin.username}'")

    
    # Initialize Scheduler
    init_scheduler(app)

    # Avvia bot Telegram interattivo bidirezionale (se configurato)
    await telegram_bot.start()
    
    yield
    
    # Shutdown
    logger.info("Shutting down Stock Monitor...")
    await telegram_bot.stop()
    shutdown_scheduler()

app = FastAPI(title="Stock Monitor", version="2.0.0", lifespan=lifespan)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Allow all for local use
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health", tags=["system"])
async def health():
    """
    Health check pubblico (non protetto) per Docker/Kubernetes.
    Verifica anche la raggiungibilità del database.
    """
    db_ok = True
    try:
        async with async_session_maker() as session:
            from sqlalchemy import text
            await session.execute(text("SELECT 1"))
    except Exception as e:
        logger.error(f"Health check DB fallito: {e}")
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "database": "ok" if db_ok else "error",
        "telegram_bot_active": telegram_bot.application is not None,
        "version": app.version,
    }

# Public Auth router
app.include_router(auth_router)

# Protected API routers (require valid login)
app.include_router(stocks_router, dependencies=[Depends(get_current_user)])
app.include_router(portfolio_router, dependencies=[Depends(get_current_user)])
app.include_router(dashboard_router, dependencies=[Depends(get_current_user)])
app.include_router(advice_router, dependencies=[Depends(get_current_user)])
app.include_router(settings_router, dependencies=[Depends(get_current_user)])
app.include_router(watchlist_router, dependencies=[Depends(get_current_user)])



# Check if frontend exists to mount static files
frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.exists(frontend_dir):
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")
else:
    logger.warning(f"Frontend directory not found at {frontend_dir}. Static files won't be served.")

@app.get("/")
async def root():
    return RedirectResponse(url="/static/index.html")

@app.get("/static/{full_path:path}")
async def catch_all_static(full_path: str):
    # For SPA support
    file_path = os.path.join(frontend_dir, full_path)
    if os.path.exists(file_path):
        return FileResponse(file_path)
    # If not found, serve index.html (SPA fallback)
    index_path = os.path.join(frontend_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"error": "Frontend not built"}

if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
