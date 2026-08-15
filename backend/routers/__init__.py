from .stocks import router as stocks_router
from .portfolio import router as portfolio_router
from .dashboard import router as dashboard_router
from .advice import router as advice_router
from .settings import router as settings_router
from .auth import router as auth_router
from .watchlist import router as watchlist_router

__all__ = [
    "stocks_router",
    "portfolio_router",
    "dashboard_router",
    "advice_router",
    "settings_router",
    "auth_router",
    "watchlist_router"
]


