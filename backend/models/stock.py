from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float, ForeignKey, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from backend.database import Base

class Stock(Base):
    __tablename__ = "stocks"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=True)
    market = Column(String, nullable=True) # IT, US, EU, etc.
    currency = Column(String, nullable=True) # EUR, USD
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    
    # Relationships
    history = relationship("PriceHistory", back_populates="stock", cascade="all, delete-orphan")
    holdings = relationship("Holding", back_populates="stock", cascade="all, delete-orphan")
    sentiments = relationship("Sentiment", back_populates="stock", cascade="all, delete-orphan")
    advices = relationship("Advice", back_populates="stock", cascade="all, delete-orphan")
    alerts = relationship("AlertRule", back_populates="stock", cascade="all, delete-orphan")
    transactions = relationship("Transaction", back_populates="stock", cascade="all, delete-orphan")
    watchlist_items = relationship("WatchlistItem", back_populates="stock", cascade="all, delete-orphan")


class PriceHistory(Base):
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    open = Column(Float, nullable=True)
    high = Column(Float, nullable=True)
    low = Column(Float, nullable=True)
    close = Column(Float, nullable=True)
    volume = Column(Integer, nullable=True)

    # Relationships
    stock = relationship("Stock", back_populates="history")
    
    __table_args__ = (
        Index('idx_stock_id_timestamp', 'stock_id', 'timestamp'),
    )
