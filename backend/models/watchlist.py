from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import Integer, String, Float, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.database import Base

class WatchlistItem(Base):
    __tablename__ = "watchlist_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    stock_id: Mapped[int] = mapped_column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    alert_above: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    alert_below: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    alert_triggered: Mapped[bool] = mapped_column(Boolean, default=False)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationship
    stock = relationship("Stock", lazy="joined")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "stock_id": self.stock_id,
            "ticker": self.stock.ticker if self.stock else None,
            "name": self.stock.name if self.stock else None,
            "market": self.stock.market if self.stock else None,
            "notes": self.notes,
            "alert_above": self.alert_above,
            "alert_below": self.alert_below,
            "alert_triggered": self.alert_triggered,
            "added_at": self.added_at.isoformat() if self.added_at else None
        }
