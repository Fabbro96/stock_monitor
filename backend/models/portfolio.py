from sqlalchemy import Column, Integer, Float, String, Date, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from backend.database import Base

class Holding(Base):
    __tablename__ = "holdings"

    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False)
    quantity = Column(Float, nullable=False)  # Float per supportare frazioni (ETF, rebalancer)
    avg_purchase_price = Column(Float, nullable=False)
    purchase_date = Column(Date, nullable=True)
    notes = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    stock = relationship("Stock", back_populates="holdings")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False)
    type = Column(String, nullable=False)  # BUY, SELL, DIVIDEND
    quantity = Column(Float, nullable=False, default=0.0)
    price = Column(Float, nullable=False, default=0.0)
    fee = Column(Float, nullable=False, default=0.0)
    realized_pnl = Column(Float, nullable=True)  # Calcolato su vendita: (price - avg_buy_price) * qty - fee
    currency = Column(String, nullable=True, default="EUR")
    transaction_date = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    notes = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    stock = relationship("Stock", back_populates="transactions")
