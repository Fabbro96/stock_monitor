from sqlalchemy import Column, Integer, Float, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from backend.database import Base

class TargetAllocation(Base):
    """
    Allocazione target definita dall'utente per il motore di ribilanciamento.

    scope_type:
      - 'MARKET'  -> scope_value = 'IT' | 'US' | 'EU' (tutti i titoli di quel mercato)
      - 'TICKERS' -> scope_value = 'AAPL,MSFT,ENEL.MI' (lista esplicita di ticker)
      - 'CASH'    -> scope_value = '' (liquidità / non investito)
    """
    __tablename__ = "target_allocations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)                # Es. "US Tech", "IT Dividend", "ETF/Cash"
    target_percent = Column(Float, nullable=False, default=0.0)
    scope_type = Column(String, nullable=False, default="MARKET")  # MARKET | TICKERS | CASH
    scope_value = Column(String, nullable=True, default="")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
