from sqlalchemy import Column, Integer, Float, String, Text, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from backend.database import Base

class Advice(Base):
    __tablename__ = "advices"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    action = Column(String, nullable=False) # 'BUY', 'SELL', 'HOLD'
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False)
    reasoning = Column(Text, nullable=False)
    confidence = Column(String, nullable=False) # 'LOW', 'MEDIUM', 'HIGH'
    target_price = Column(Float, nullable=True)
    suggested_quantity = Column(Integer, nullable=True)
    timeframe = Column(String, nullable=True)
    followed = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    stock = relationship("Stock", back_populates="advices")
