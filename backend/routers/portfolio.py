import io
import csv
from datetime import datetime, date, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Response, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from backend.database import get_db
from backend.models.portfolio import Holding, Transaction
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

@router.post("/seed-demo")
async def seed_demo_data(db: AsyncSession = Depends(get_db)):
    """
    Inizializza posizioni demo bilanciate (Italia + USA) per un'esperienza immediata.
    """
    from backend.models.watchlist import WatchlistItem

    demo_holdings = [
        {"ticker": "ENEL.MI", "name": "Enel S.p.A.", "market": "IT", "currency": "EUR", "qty": 400, "price": 6.20, "notes": "Dividendo Core"},
        {"ticker": "ISP.MI", "name": "Intesa Sanpaolo", "market": "IT", "currency": "EUR", "qty": 800, "price": 3.15, "notes": "Settore Bancario"},
        {"ticker": "RACE.MI", "name": "Ferrari N.V.", "market": "IT", "currency": "EUR", "qty": 8, "price": 380.0, "notes": "Luxury Growth"},
        {"ticker": "AAPL", "name": "Apple Inc.", "market": "US", "currency": "USD", "qty": 15, "price": 195.0, "notes": "Big Tech"},
        {"ticker": "NVDA", "name": "NVIDIA Corporation", "market": "US", "currency": "USD", "qty": 20, "price": 118.0, "notes": "AI Leader"},
        {"ticker": "MSFT", "name": "Microsoft Corporation", "market": "US", "currency": "USD", "qty": 8, "price": 390.0, "notes": "Cloud & AI Enterprise"},
    ]

    demo_watchlist = [
        {"ticker": "LDO.MI", "name": "Leonardo S.p.A.", "market": "IT", "currency": "EUR", "alert_above": 26.0, "alert_below": 22.0, "notes": "Target breakout"},
        {"ticker": "G.MI", "name": "Assicurazioni Generali", "market": "IT", "currency": "EUR", "alert_above": 28.0, "alert_below": 24.5, "notes": "High yield"},
        {"ticker": "AMZN", "name": "Amazon.com Inc.", "market": "US", "currency": "USD", "alert_above": 220.0, "alert_below": 185.0, "notes": "AWS Cloud margin expansion"},
        {"ticker": "GOOGL", "name": "Alphabet Inc.", "market": "US", "currency": "USD", "alert_above": 190.0, "alert_below": 165.0, "notes": "Search AI & Waymo"},
    ]

    created_holdings = 0
    for item in demo_holdings:
        # Check stock
        res = await db.execute(select(Stock).where(Stock.ticker == item["ticker"]))
        stock = res.scalars().first()
        if not stock:
            stock = Stock(ticker=item["ticker"], name=item["name"], market=item["market"], currency=item["currency"])
            db.add(stock)
            await db.commit()
            await db.refresh(stock)

        # Check holding
        h_res = await db.execute(select(Holding).where(Holding.stock_id == stock.id))
        if not h_res.scalars().first():
            h = Holding(
                stock_id=stock.id,
                quantity=item["qty"],
                avg_purchase_price=item["price"],
                purchase_date=date.today(),
                notes=item["notes"]
            )
            db.add(h)
            created_holdings += 1

    created_watchlist = 0
    for item in demo_watchlist:
        res = await db.execute(select(Stock).where(Stock.ticker == item["ticker"]))
        stock = res.scalars().first()
        if not stock:
            stock = Stock(ticker=item["ticker"], name=item["name"], market=item["market"], currency=item["currency"])
            db.add(stock)
            await db.commit()
            await db.refresh(stock)

        w_res = await db.execute(select(WatchlistItem).where(WatchlistItem.stock_id == stock.id))
        if not w_res.scalars().first():
            w = WatchlistItem(
                stock_id=stock.id,
                notes=item["notes"],
                alert_above=item.get("alert_above"),
                alert_below=item.get("alert_below")
            )
            db.add(w)
            created_watchlist += 1

    await db.commit()
    return {
        "status": "success",
        "message": f"Demo popolata con successo ({created_holdings} holding, {created_watchlist} watchlist).",
        "created_holdings": created_holdings,
        "created_watchlist": created_watchlist
    }


# ---------------------------------------------------------------------------
# Trade Ledger (Registro Transazioni, Storico Compravendite & P&L Realizzato)
# ---------------------------------------------------------------------------
class TransactionCreate(BaseModel):
    ticker: str
    type: str  # BUY, SELL, DIVIDEND
    quantity: float = 0.0
    price: float = 0.0
    fee: float = 0.0
    transaction_date: Optional[datetime] = None
    notes: Optional[str] = ""


@router.get("/transactions")
async def list_transactions(
    type: Optional[str] = None,
    ticker: Optional[str] = None,
    limit: int = 100,
    skip: int = 0,
    db: AsyncSession = Depends(get_db)
):
    """Restituisce lo storico completo delle transazioni registrate nel Trade Ledger."""
    query = select(Transaction).join(Stock).options(selectinload(Transaction.stock))
    if type:
        query = query.where(Transaction.type == type.upper())
    if ticker:
        query = query.where(Stock.ticker == ticker.strip().upper())
    query = query.order_by(Transaction.transaction_date.desc()).offset(skip).limit(limit)

    result = await db.execute(query)
    txs = result.scalars().all()
    output = []
    for tx in txs:
        output.append({
            "id": tx.id,
            "stock_id": tx.stock_id,
            "ticker": tx.stock.ticker if tx.stock else "?",
            "name": tx.stock.name if tx.stock else "",
            "market": tx.stock.market if tx.stock else "US",
            "type": tx.type,
            "quantity": tx.quantity,
            "price": tx.price,
            "fee": tx.fee,
            "realized_pnl": tx.realized_pnl,
            "currency": tx.currency or (tx.stock.currency if tx.stock else "EUR"),
            "transaction_date": str(tx.transaction_date) if tx.transaction_date else str(tx.created_at),
            "notes": tx.notes or ""
        })
    return output


@router.post("/transactions")
async def create_transaction(tx_in: TransactionCreate, db: AsyncSession = Depends(get_db)):
    """
    Registra una nuova transazione (BUY, SELL o DIVIDEND) e aggiorna atomicamente il portafoglio.
    - BUY: incrementa o crea la posizione calcolando il nuovo prezzo medio di carico ponderato.
    - SELL: calcola il P&L realizzato, decrementa la posizione (o la rimuove se 0).
    - DIVIDEND: registra l'incasso cedolare.
    """
    t_type = (tx_in.type or "BUY").strip().upper()
    if t_type not in ("BUY", "SELL", "DIVIDEND"):
        raise HTTPException(status_code=400, detail="Il tipo transazione deve essere BUY, SELL o DIVIDEND.")

    ticker = tx_in.ticker.strip().upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="Specificare un ticker valido.")

    # Risolve o crea Stock
    res = await db.execute(select(Stock).where(Stock.ticker == ticker))
    stock = res.scalars().first()
    if not stock:
        info = await MarketDataService.resolve_stock_info(ticker)
        stock = Stock(
            ticker=ticker,
            name=info.get("name", ticker),
            market=info.get("market", "IT" if ticker.endswith(".MI") else "US"),
            currency=info.get("currency", "EUR" if ticker.endswith(".MI") else "USD")
        )
        db.add(stock)
        await db.commit()
        await db.refresh(stock)

    tx_date = tx_in.transaction_date or datetime.now(timezone.utc)
    realized_pnl = None

    # Recupera posizione esistente
    h_res = await db.execute(select(Holding).where(Holding.stock_id == stock.id))
    holding = h_res.scalars().first()

    if t_type == "BUY":
        if tx_in.quantity <= 0 or tx_in.price <= 0:
            raise HTTPException(status_code=400, detail="Quantità e prezzo devono essere maggiori di zero per un acquisto.")
        
        if holding:
            new_qty = holding.quantity + tx_in.quantity
            new_avg = ((holding.quantity * holding.avg_purchase_price) + (tx_in.quantity * tx_in.price)) / new_qty
            holding.quantity = round(new_qty, 4)
            holding.avg_purchase_price = round(new_avg, 4)
            if tx_in.notes:
                holding.notes = tx_in.notes
        else:
            holding = Holding(
                stock_id=stock.id,
                quantity=tx_in.quantity,
                avg_purchase_price=tx_in.price,
                purchase_date=tx_date.date() if isinstance(tx_date, datetime) else tx_date,
                notes=tx_in.notes
            )
            db.add(holding)
        realized_pnl = 0.0

    elif t_type == "SELL":
        if tx_in.quantity <= 0 or tx_in.price <= 0:
            raise HTTPException(status_code=400, detail="Quantità e prezzo devono essere maggiori di zero per una vendita.")
        if not holding or holding.quantity < tx_in.quantity:
            avail = holding.quantity if holding else 0
            raise HTTPException(
                status_code=400,
                detail=f"Quantità insufficiente in portafoglio: possiedi {avail} quote di {ticker}, impossibile venderne {tx_in.quantity}."
            )
        
        # Calcolo P&L realizzato = (Prezzo Vendita - Prezzo Medio di Carico) * Qty - Commissioni
        realized_pnl = round((tx_in.price - holding.avg_purchase_price) * tx_in.quantity - tx_in.fee, 2)
        holding.quantity = round(holding.quantity - tx_in.quantity, 4)
        if holding.quantity <= 0.0001:
            await db.delete(holding)

    elif t_type == "DIVIDEND":
        if tx_in.price < 0:
            raise HTTPException(status_code=400, detail="L'importo del dividendo non può essere negativo.")
        total_div = (tx_in.price * tx_in.quantity) if tx_in.quantity > 0 else tx_in.price
        realized_pnl = round(total_div - tx_in.fee, 2)

    # Crea record transazione
    tx = Transaction(
        stock_id=stock.id,
        type=t_type,
        quantity=tx_in.quantity,
        price=tx_in.price,
        fee=tx_in.fee,
        realized_pnl=realized_pnl,
        currency=stock.currency or "EUR",
        transaction_date=tx_date,
        notes=tx_in.notes or ""
    )
    db.add(tx)
    await db.commit()
    await db.refresh(tx)

    return {
        "status": "success",
        "transaction": {
            "id": tx.id,
            "ticker": ticker,
            "type": tx.type,
            "quantity": tx.quantity,
            "price": tx.price,
            "fee": tx.fee,
            "realized_pnl": tx.realized_pnl,
            "currency": tx.currency,
            "transaction_date": str(tx.transaction_date)
        }
    }


@router.delete("/transactions/{tx_id}")
async def delete_transaction(tx_id: int, db: AsyncSession = Depends(get_db)):
    """Elimina una riga dallo storico transazioni."""
    tx = await db.get(Transaction, tx_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Transazione non trovata.")
    await db.delete(tx)
    await db.commit()
    return {"status": "success", "message": f"Transazione #{tx_id} rimossa."}


@router.get("/realized-pnl")
async def get_realized_pnl(db: AsyncSession = Depends(get_db)):
    """Riepilogo globale di P&L Realizzato, dividendi incassati e commissioni pagate."""
    res = await db.execute(select(Transaction).join(Stock).options(selectinload(Transaction.stock)))
    txs = res.scalars().all()
    usd_to_eur = await MarketDataService.get_fx_rate("USD", "EUR")

    total_realized_capital_gains = 0.0
    total_dividends_collected = 0.0
    total_fees_paid = 0.0
    wins = 0
    losses = 0

    for tx in txs:
        fx = usd_to_eur if (tx.currency == "USD") else 1.0
        fee_eur = tx.fee * fx
        total_fees_paid += fee_eur

        if tx.type == "SELL" and tx.realized_pnl is not None:
            pnl_eur = tx.realized_pnl * fx
            total_realized_capital_gains += pnl_eur
            if pnl_eur >= 0:
                wins += 1
            else:
                losses += 1
        elif tx.type == "DIVIDEND":
            div_val = (tx.price * tx.quantity if tx.quantity > 0 else tx.price) * fx
            total_dividends_collected += div_val

    net_realized_profit = total_realized_capital_gains + total_dividends_collected - total_fees_paid
    win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0.0

    return {
        "total_realized_capital_gains": round(total_realized_capital_gains, 2),
        "total_dividends_collected": round(total_dividends_collected, 2),
        "total_fees_paid": round(total_fees_paid, 2),
        "net_realized_profit": round(net_realized_profit, 2),
        "trade_count": wins + losses,
        "win_trades": wins,
        "loss_trades": losses,
        "win_rate_percent": round(win_rate, 1),
        "transactions_count": len(txs)
    }


@router.get("/dividends")
async def get_dividends_calendar(db: AsyncSession = Depends(get_db)):
    """
    Calendario dividendi del portafoglio:
    Calcola il rendimento da dividendi, Yield on Cost (YoC) e flussi stimati per ogni holding.
    """
    rows = await build_portfolio_rows(db)
    summary = await build_portfolio_summary(db)
    usd_to_eur = await MarketDataService.get_fx_rate("USD", "EUR")

    dividends_list = []
    total_projected_annual_eur = 0.0

    for h in rows:
        ticker = h["ticker"]
        div_yield = MarketDataService.get_stock_dividend_yield(ticker)
        current_price = h["current_price"]
        avg_buy_price = h["avg_purchase_price"]
        qty = h["quantity"]
        fx = h.get("fx_rate_to_eur", 1.0)

        # Calcolo dividendo annuo per azione
        annual_div_per_share = round(current_price * (div_yield / 100.0), 4) if div_yield > 0 else 0.0
        annual_income_native = round(annual_div_per_share * qty, 2)
        annual_income_eur = round(annual_income_native * fx, 2)
        total_projected_annual_eur += annual_income_eur

        # Yield on Cost: dividendo annuo diviso per prezzo medio di acquisto
        yield_on_cost = round((annual_div_per_share / avg_buy_price * 100.0), 2) if avg_buy_price > 0 else 0.0

        dividends_list.append({
            "ticker": ticker,
            "name": h["name"],
            "market": h["market"],
            "currency": h["currency"],
            "quantity": qty,
            "current_price": current_price,
            "avg_purchase_price": avg_buy_price,
            "dividend_yield_pct": div_yield,
            "yield_on_cost_pct": yield_on_cost,
            "annual_dividend_per_share": annual_div_per_share,
            "annual_income_eur": annual_income_eur,
            "monthly_income_eur": round(annual_income_eur / 12.0, 2)
        })

    # Ordina per reddito annuo stimato
    dividends_list.sort(key=lambda x: x["annual_income_eur"], reverse=True)

    return {
        "holdings": dividends_list,
        "total_annual_dividend_eur": round(total_projected_annual_eur, 2),
        "total_monthly_dividend_eur": round(total_projected_annual_eur / 12.0, 2),
        "portfolio_total_value": summary.get("total_value", 0.0),
        "portfolio_yield_on_cost": round(
            (total_projected_annual_eur / summary["total_invested"] * 100.0), 2
        ) if summary.get("total_invested", 0) > 0 else 0.0
    }


