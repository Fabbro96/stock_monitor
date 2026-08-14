from sqlalchemy import Column, Integer, Float, String, Text, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from backend.database import Base

class Advice(Base):
    __tablename__ = "advices"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    market = Column(String, default="ALL") # 'IT' (Borsa Italiana), 'US' (Borsa Americana), 'EU'
    title = Column(String, nullable=True) # Es. "Report Strategico Borsa Italiana"
    action = Column(String, nullable=False, default="HOLD") # 'ACCUMULO' / 'BUY', 'MANTENIMENTO' / 'HOLD', 'ALLEGGERIMENTO' / 'SELL'
    overview = Column(Text, nullable=True) # Quadro generale / scenario macro di borsa
    reasoning = Column(Text, nullable=False) # Strategia operativa complessiva
    stocks_json = Column(Text, nullable=True) # Dettagli JSON dei singoli titoli analizzati
    risks = Column(Text, nullable=True) # Punti di attenzione e catalizzatori di rischio
    confidence = Column(String, nullable=False, default="MEDIUM") # 'LOW', 'MEDIUM', 'HIGH'
    timeframe = Column(String, nullable=True, default="Medio Termine")
    target_price = Column(Float, nullable=True)
    suggested_quantity = Column(Integer, nullable=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=True)
    followed = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    stock = relationship("Stock", back_populates="advices", lazy="selectin")
