import io
import csv
from datetime import datetime, date, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Response, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from backend.database import get_db
from backend.models.portfolio import Holding
from backend.models.stock import Stock, PriceHistory
from backend.models.target_allocation import TargetAllocation
from backend.services.market_data import MarketDataService
from backend.services.portfolio_service import build_portfolio_rows, build_portfolio_summary
from backend.services.analytics import (
    compute_risk_metrics,
    compute_benchmark_comparison,
    compute_rebalance_plan,
    BENCHMARKS,
)

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
    """Lista posizioni con prezzi live (fallback DB/cache mai bloccante)."""
    return await build_portfolio_rows(db)

@router.get("/summary")
async def get_summary(db: AsyncSession = Depends(get_db)):
    return await build_portfolio_summary(db)

@router.get("/risk-metrics")
async def get_risk_metrics(days: int = Query(180, ge=30, le=3650), db: AsyncSession = Depends(get_db)):
    """
    Metriche quantitative di rischio/performance del portafoglio:
    Max Drawdown, Volatilità annualizzata, Sharpe Ratio, Beta pesato.
    """
    return await compute_risk_metrics(db, days=days)

@router.get("/benchmarks")
async def get_benchmark_comparison(
    days: int = Query(90, ge=7, le=1825),
    tickers: Optional[str] = Query(None, description="Comma-separated: ^GSPC,FTSEMIB.MI"),
    db: AsyncSession = Depends(get_db)
):
    """
    Crescita % storica del portafoglio confrontata con gli indici di mercato
    (default: S&P 500 ^GSPC e FTSE MIB FTSEMIB.MI).
    """
    benchmark_list = None
    if tickers:
        benchmark_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
        for t in benchmark_list:
            BENCHMARKS.setdefault(t, {"name": t, "flag": "📊"})
    return await compute_benchmark_comparison(db, days=days, benchmark_tickers=benchmark_list)

# ---------------------------------------------------------------------------
# Rebalancer: Target Allocation CRUD + Preview ordini
# ---------------------------------------------------------------------------
class TargetAllocationCreate(BaseModel):
    name: str
    target_percent: float
    scope_type: str = "MARKET"   # MARKET | TICKERS | CASH
    scope_value: Optional[str] = ""

class RebalancePreviewRequest(BaseModel):
    extra_cash: float = 0.0

@router.get("/rebalance/targets")
async def list_targets(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(TargetAllocation).order_by(TargetAllocation.id))
    targets = result.scalars().all()
    return [
        {
            "id": t.id,
            "name": t.name,
            "target_percent": t.target_percent,
            "scope_type": t.scope_type,
            "scope_value": t.scope_value or "",
        }
        for t in targets
    ]

@router.post("/rebalance/targets")
async def create_target(data: TargetAllocationCreate, db: AsyncSession = Depends(get_db)):
    if not (0.0 <= data.target_percent <= 100.0):
        raise HTTPException(status_code=400, detail="target_percent deve essere tra 0 e 100.")
    scope_type = (data.scope_type or "MARKET").upper()
    if scope_type not in ("MARKET", "TICKERS", "CASH"):
        raise HTTPException(status_code=400, detail="scope_type deve essere MARKET, TICKERS o CASH.")
    if scope_type == "MARKET" and not data.scope_value:
        raise HTTPException(status_code=400, detail="Per scope MARKET indica scope_value (IT, US, EU).")

    target = TargetAllocation(
        name=data.name.strip(),
        target_percent=data.target_percent,
        scope_type=scope_type,
        scope_value=(data.scope_value or "").strip().upper(),
    )
    db.add(target)
    try:
        await db.commit()
        await db.refresh(target)
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": target.id, "name": target.name, "target_percent": target.target_percent,
            "scope_type": target.scope_type, "scope_value": target.scope_value or ""}

@router.delete("/rebalance/targets/{target_id}")
async def delete_target(target_id: int, db: AsyncSession = Depends(get_db)):
    target = await db.get(TargetAllocation, target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Allocazione target non trovata.")
    await db.delete(target)
    await db.commit()
    return {"status": "success"}

@router.post("/rebalance/preview")
async def rebalance_preview(data: RebalancePreviewRequest, db: AsyncSession = Depends(get_db)):
    """
    Calcola il piano di ribilanciamento: delta per bucket e ordini buy/sell
    (quantità stimate) necessari per raggiungere le allocazioni target.
    """
    result = await db.execute(select(TargetAllocation).order_by(TargetAllocation.id))
    targets = result.scalars().all()
    if not targets:
        raise HTTPException(status_code=400, detail="Nessuna allocazione target configurata.")

    target_dicts = [
        {"id": t.id, "name": t.name, "target_percent": t.target_percent,
         "scope_type": t.scope_type, "scope_value": t.scope_value or ""}
        for t in targets
    ]
    portfolio = await build_portfolio_rows(db)
    plan = compute_rebalance_plan(portfolio, target_dicts, extra_cash=data.extra_cash)
    plan["portfolio_empty"] = len(portfolio) == 0
    return plan


@router.post("/holdings")
async def add_holding(holding_data: HoldingCreate, db: AsyncSession = Depends(get_db)):
    stock_id = holding_data.stock_id

    # If stock_id not provided but ticker is, find or create Stock
    if not stock_id and holding_data.ticker:
        ticker = holding_data.ticker.strip().upper()
        result = await db.execute(select(Stock).where(Stock.ticker == ticker))
        stock = result.scalars().first()
        if not stock:
            # Auto-create stock (risoluzione nome asincrona e non bloccante)
            market = "IT" if ticker.endswith(".MI") else "US"
            info = await MarketDataService.resolve_stock_info(ticker)
            name = info.get("name") or ticker
            if info.get("market"):
                market = info["market"]
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
async def export_portfolio(format: str = Query("csv", pattern="^(csv|json)$"), db: AsyncSession = Depends(get_db)):
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
