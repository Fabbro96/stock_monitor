import io
import csv
from datetime import datetime, date, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Response, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
import yfinance as yf

from backend.database import get_db
from backend.models.portfolio import Holding
from backend.models.stock import Stock, PriceHistory
from backend.utils.helpers import calculate_pnl

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

class HoldingCreate(BaseModel):
    ticker: Optional[str] = None
    stock_id: Optional[int] = None
    quantity: float
    avg_purchase_price: float
    purchase_date: Optional[date] = None
    notes: Optional[str] = None

class HoldingUpdate(BaseModel):
    quantity: Optional[float] = None
    avg_purchase_price: Optional[float] = None
    purchase_date: Optional[date] = None
    notes: Optional[str] = None

class HoldingBatchItem(BaseModel):
    id: int
    quantity: float
    avg_purchase_price: float
    notes: Optional[str] = None

class BatchUpdateRequest(BaseModel):
    holdings: List[HoldingBatchItem]

@router.get("/")
async def get_portfolio(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Holding).join(Stock).where(Stock.is_active == True))
    holdings = result.scalars().all()
    
    portfolio = []
    for h in holdings:
        stock = await db.get(Stock, h.stock_id)
        if not stock:
            continue
            
        # Get latest price from PriceHistory or fallback to MarketDataService
        price_result = await db.execute(
            select(PriceHistory)
            .where(PriceHistory.stock_id == h.stock_id)
            .order_by(PriceHistory.timestamp.desc())
            .limit(1)
        )
        latest_price = price_result.scalars().first()
        current_price = latest_price.close if latest_price and latest_price.close else None
        
        if not current_price:
            price_data = await MarketDataService.fetch_current_price(stock.ticker)
            current_price = price_data.get("close", h.avg_purchase_price)

        pnl = calculate_pnl(current_price, h.avg_purchase_price, h.quantity)
        
        portfolio.append({
            "id": h.id,
            "stock_id": h.stock_id,
            "ticker": stock.ticker,
            "name": stock.name or stock.ticker,
            "market": stock.market or ("IT" if stock.ticker.endswith(".MI") else "US"),
            "currency": stock.currency or ("EUR" if stock.ticker.endswith(".MI") else "USD"),
            "quantity": h.quantity,
            "avg_purchase_price": h.avg_purchase_price,
            "current_price": current_price,
            "total_value": round(h.quantity * current_price, 2),
            "total_invested": round(h.quantity * h.avg_purchase_price, 2),
            "pnl_absolute": pnl["pnl_absolute"],
            "pnl_percent": pnl["pnl_percent"],
            "purchase_date": str(h.purchase_date) if h.purchase_date else None,
            "notes": h.notes or ""
        })
        
    return portfolio

@router.get("/summary")
async def get_summary(db: AsyncSession = Depends(get_db)):
    portfolio = await get_portfolio(db)
    
    total_invested = sum(h["quantity"] * h["avg_purchase_price"] for h in portfolio)
    total_value = sum(h["quantity"] * h["current_price"] for h in portfolio)
    total_pnl = total_value - total_invested
    total_pnl_percent = (total_pnl / total_invested * 100) if total_invested > 0 else 0.0

    # Top Gainer & Top Loser
    sorted_by_pnl = sorted(portfolio, key=lambda x: x["pnl_percent"], reverse=True)
    top_gainer = sorted_by_pnl[0] if sorted_by_pnl and sorted_by_pnl[0]["pnl_percent"] > 0 else None
    top_loser = sorted_by_pnl[-1] if sorted_by_pnl and sorted_by_pnl[-1]["pnl_percent"] < 0 else None

    # Market Allocation Breakdown
    market_allocation = {"IT": 0.0, "US": 0.0, "EU": 0.0}
    for h in portfolio:
        m = (h.get("market") or "US").upper()
        if m in market_allocation:
            market_allocation[m] += h["total_value"]
        else:
            market_allocation["US"] += h["total_value"]

    # Dividend estimation (approx based on deep dive cache or default yield)
    estimated_annual_dividends = 0.0
    for h in portfolio:
        deep = await MarketDataService.fetch_stock_deep_dive(h["ticker"])
        dy = deep.get("dividend_yield")
        if dy and dy > 0:
            estimated_annual_dividends += (h["total_value"] * (dy / 100.0))
        elif deep.get("dividend_rate"):
            estimated_annual_dividends += (h["quantity"] * deep["dividend_rate"])

    estimated_dividend_yield = (estimated_annual_dividends / total_value * 100) if total_value > 0 else 0.0

    return {
        "total_value": round(total_value, 2),
        "total_invested": round(total_invested, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_percent": round(total_pnl_percent, 2),
        "holdings_count": len(portfolio),
        "top_gainer": top_gainer,
        "top_loser": top_loser,
        "market_allocation": {k: round(v, 2) for k, v in market_allocation.items()},
        "estimated_annual_dividends": round(estimated_annual_dividends, 2),
        "estimated_dividend_yield": round(estimated_dividend_yield, 2)
    }


@router.post("/holdings")
async def add_holding(holding_data: HoldingCreate, db: AsyncSession = Depends(get_db)):
    stock_id = holding_data.stock_id

    # If stock_id not provided but ticker is, find or create Stock
    if not stock_id and holding_data.ticker:
        ticker = holding_data.ticker.strip().upper()
        result = await db.execute(select(Stock).where(Stock.ticker == ticker))
        stock = result.scalars().first()
        if not stock:
            # Auto-create stock
            name = ticker
            market = "IT" if ticker.endswith(".MI") else "US"
            try:
                info = yf.Ticker(ticker).info
                name = info.get("shortName", ticker)
            except Exception:
                pass
            stock = Stock(ticker=ticker, name=name, market=market, currency="USD" if market == "US" else "EUR")
            db.add(stock)
            await db.commit()
            await db.refresh(stock)
        stock_id = stock.id

    if not stock_id:
        raise HTTPException(status_code=400, detail="Specificare stock_id o ticker valido.")

    # Check if a holding for this stock already exists -> if so, update weighted average
    existing_result = await db.execute(select(Holding).where(Holding.stock_id == stock_id))
    existing_holding = existing_result.scalars().first()
    if existing_holding:
        total_qty = existing_holding.quantity + holding_data.quantity
        if total_qty > 0:
            new_avg = (
                (existing_holding.quantity * existing_holding.avg_purchase_price) +
                (holding_data.quantity * holding_data.avg_purchase_price)
            ) / total_qty
            existing_holding.quantity = total_qty
            existing_holding.avg_purchase_price = round(new_avg, 4)
            if holding_data.notes:
                existing_holding.notes = f"{existing_holding.notes or ''}; {holding_data.notes}".strip("; ")
            await db.commit()
            await db.refresh(existing_holding)
            return existing_holding

    new_holding = Holding(
        stock_id=stock_id,
        quantity=holding_data.quantity,
        avg_purchase_price=holding_data.avg_purchase_price,
        purchase_date=holding_data.purchase_date or date.today(),
        notes=holding_data.notes
    )
    db.add(new_holding)
    await db.commit()
    await db.refresh(new_holding)
    return new_holding

@router.put("/holdings/{holding_id}")
async def update_holding(holding_id: int, holding_update: HoldingUpdate, db: AsyncSession = Depends(get_db)):
    holding = await db.get(Holding, holding_id)
    if not holding:
        raise HTTPException(status_code=404, detail="Holding non trovata")
        
    update_data = holding_update.dict(exclude_unset=True)
    for key, value in update_data.items():
        if value is not None:
            setattr(holding, key, value)
        
    await db.commit()
    await db.refresh(holding)
    return holding

@router.put("/batch")
async def batch_update_holdings(batch_data: BatchUpdateRequest, db: AsyncSession = Depends(get_db)):
    """
    Aggiorna più posizioni contemporaneamente con una singola transazione sicura.
    """
    updated_count = 0
    for item in batch_data.holdings:
        holding = await db.get(Holding, item.id)
        if holding:
            holding.quantity = item.quantity
            holding.avg_purchase_price = item.avg_purchase_price
            if item.notes is not None:
                holding.notes = item.notes
            updated_count += 1

    await db.commit()
    return {"status": "success", "updated_count": updated_count}

@router.delete("/holdings/{holding_id}")
async def remove_holding(holding_id: int, db: AsyncSession = Depends(get_db)):
    holding = await db.get(Holding, holding_id)
    if not holding:
        raise HTTPException(status_code=404, detail="Holding non trovata")
        
    await db.delete(holding)
    await db.commit()
    return {"status": "success", "message": "Holding rimossa"}

@router.get("/export")
async def export_portfolio(format: str = Query("csv", regex="^(csv|json)$"), db: AsyncSession = Depends(get_db)):
    """
    Esporta il portafoglio corrente in formato CSV o JSON.
    """
    portfolio = await get_portfolio(db)
    
    if format == "json":
        return portfolio

    # Format CSV
    output = io.StringIO()
    writer = csv.writer(output, delimiter=",")
    writer.writerow(["ticker", "name", "quantity", "avg_purchase_price", "current_price", "pnl_absolute", "pnl_percent", "purchase_date", "notes"])
    
    for item in portfolio:
        writer.writerow([
            item["ticker"],
            item["name"],
            item["quantity"],
            item["avg_purchase_price"],
            item["current_price"],
            item["pnl_absolute"],
            item["pnl_percent"],
            item.get("purchase_date", ""),
            item.get("notes", "")
        ])
        
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    csv_content = output.getvalue()
    
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=portafoglio_{today_str}.csv"}
    )

@router.post("/import")
async def import_holdings(file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    """
    Importa posizioni azionarie da un file CSV. Supporta virgola e punto e virgola come separatori.
    """
    content = await file.read()
    text = content.decode("utf-8-sig", errors="ignore")
    
    # Auto-detect separator
    sample = text[:1024]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    
    imported = 0
    updated = 0
    errors = []
    
    for row_idx, row in enumerate(reader, start=1):
        # Normalize header keys to lowercase
        norm_row = {k.strip().lower(): v.strip() for k, v in row.items() if k}
        
        # Ticker resolution
        ticker = norm_row.get("ticker") or norm_row.get("simbolo") or norm_row.get("azione") or norm_row.get("titolo")
        if not ticker:
            continue
        ticker = ticker.upper()
        
        # Quantity resolution
        qty_str = norm_row.get("quantity") or norm_row.get("quantita") or norm_row.get("quantità") or norm_row.get("qty") or "0"
        qty_str = qty_str.replace(",", ".")
        try:
            quantity = float(qty_str)
        except ValueError:
            errors.append(f"Riga {row_idx}: Quantità non valida '{qty_str}' per {ticker}")
            continue
            
        if quantity <= 0:
            continue
            
        # Price resolution
        price_str = norm_row.get("avg_purchase_price") or norm_row.get("avg_price") or norm_row.get("prezzo") or norm_row.get("prezzo_acquisto") or norm_row.get("prezzo_medio") or "0"
        price_str = price_str.replace(",", ".").replace("€", "").replace("$", "").strip()
        try:
            avg_price = float(price_str)
        except ValueError:
            errors.append(f"Riga {row_idx}: Prezzo non valido '{price_str}' per {ticker}")
            continue

        # Notes resolution
        notes = norm_row.get("notes") or norm_row.get("note") or None
        
        # Purchase date resolution
        date_str = norm_row.get("purchase_date") or norm_row.get("data") or norm_row.get("data_acquisto")
        purchase_date = None
        if date_str:
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
                try:
                    purchase_date = datetime.strptime(date_str, fmt).date()
                    break
                except ValueError:
                    pass

        # Find or create Stock
        result = await db.execute(select(Stock).where(Stock.ticker == ticker))
        stock = result.scalars().first()
        if not stock:
            name = norm_row.get("name") or norm_row.get("nome") or ticker
            market = "IT" if ticker.endswith(".MI") else "US"
            stock = Stock(ticker=ticker, name=name, market=market, currency="USD" if market == "US" else "EUR")
            db.add(stock)
            await db.commit()
            await db.refresh(stock)
            
        # Find if holding exists
        h_result = await db.execute(select(Holding).where(Holding.stock_id == stock.id))
        holding = h_result.scalars().first()
        
        if holding:
            holding.quantity = quantity
            holding.avg_purchase_price = avg_price
            if notes:
                holding.notes = notes
            if purchase_date:
                holding.purchase_date = purchase_date
            updated += 1
        else:
            h = Holding(
                stock_id=stock.id,
                quantity=quantity,
                avg_purchase_price=avg_price,
                purchase_date=purchase_date or date.today(),
                notes=notes
            )
            db.add(h)
            imported += 1
            
    await db.commit()
    return {
        "status": "success",
        "imported": imported,
        "updated": updated,
        "errors": errors
    }
