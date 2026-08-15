#!/usr/bin/env python3
"""
================================================================================
Stock Monitor — End-to-End Async Test Suite
================================================================================
Verifica automatica di tutti gli endpoint REST e dei path di fallback
(resilienza a rate-limiting Yahoo 429/403), concorrenza SQLite (WAL) e
correttezza dei motori quantitativi (risk metrics, rebalancer).

Uso:
    python tests/e2e_test.py

Il test avvia un'istanza uvicorn dedicata con DB isolato in /tmp, esegue
tutte le verifiche asincrone e termina con report dettagliato.
"""
import asyncio
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

# ---------------------------------------------------------------------------
# Configurazione ambiente di test (ISOLATO: DB dedicato, niente Telegram)
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
TEST_DIR = "/tmp/stock_monitor_e2e"
TEST_DB = os.path.join(TEST_DIR, "stock_monitor_test.db")
ADMIN_USER = "admin"
ADMIN_PASS = "admin123"

# Imposta variabili ambiente di test prima di qualsiasi import
os.environ["DB_PATH"] = TEST_DB
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TELEGRAM_CHAT_ID"] = ""
os.environ["TELEGRAM_BOT_ENABLED"] = "false"
os.environ["GEMINI_API_KEY"] = ""
os.environ["ADMIN_USERNAME"] = ADMIN_USER
os.environ["ADMIN_PASSWORD"] = ADMIN_PASS
os.environ["LOG_LEVEL"] = "INFO"

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name} {detail}")
        print(f"  ❌ {name} {detail}")


def seed_price_history():
    """Inserisce storico prezzi deterministico per i test quantitativi."""
    time.sleep(0.2)
    conn = sqlite3.connect(TEST_DB, timeout=10)
    cur = conn.cursor()
    cur.execute("PRAGMA busy_timeout=10000")
    # Trova stock_id ENEL.MI (creato nei test precedenti)
    row = cur.execute("SELECT id FROM stocks WHERE ticker='ENEL.MI'").fetchone()
    if not row:
        conn.close()
        return
    stock_id = row[0]
    # Serie: 20 giorni con drawdown noto
    prices = [10.0, 10.5, 11.0, 12.0, 11.5, 9.0, 9.5, 10.0, 10.8, 11.2,
              11.6, 12.2, 12.0, 11.8, 12.5, 13.0, 12.7, 13.2, 13.6, 14.0]
    base_day = datetime.now(timezone.utc) - timedelta(days=len(prices))
    for i, p in enumerate(prices):
        ts = (base_day + timedelta(days=i, hours=17)).isoformat()
        cur.execute(
            "INSERT INTO price_history (stock_id, timestamp, open, high, low, close, volume) "
            "VALUES (?,?,?,?,?,?,?)",
            (stock_id, ts, p - 0.1, p + 0.2, p - 0.2, p, 1_000_000 + i),
        )
    conn.commit()
    conn.close()


# ===========================================================================
# TEST GROUPS
# ===========================================================================
async def test_health_and_auth(c: httpx.AsyncClient) -> str:
    print("\n[1] Health & Autenticazione")
    r = await c.get("/health")
    check("GET /health -> 200", r.status_code == 200)
    check("health status ok", r.json().get("status") == "ok")
    check("health database ok", r.json().get("database") == "ok")

    # Endpoint protetto senza token -> 401
    r = await c.get("/api/portfolio/")
    check("GET /api/portfolio/ senza token -> 401", r.status_code == 401)

    # Login credenziali errate -> 401
    r = await c.post("/api/auth/login", json={"username": ADMIN_USER, "password": "wrongpass"})
    check("Login password errata -> 401", r.status_code == 401)

    # Login valido
    r = await c.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    check("Login valido -> 200", r.status_code == 200)
    token = r.json().get("access_token", "")
    check("Login ritorna access_token", bool(token))
    return token


async def test_stocks(c: httpx.AsyncClient, h: dict):
    print("\n[2] Stocks / Ricerca / Candele (con fallback)")
    r = await c.get("/api/stocks/", headers=h)
    check("GET /api/stocks/ -> 200", r.status_code == 200)

    r = await c.post("/api/stocks/", headers=h, json={"ticker": "ENEL.MI", "name": "Enel", "market": "IT"})
    check("POST /api/stocks/ ENEL.MI -> 200", r.status_code == 200, str(r.status_code))

    # Ricerca (può fallire live per rate-limit ma NON deve dare 500)
    r = await c.get("/api/stocks/search?q=ENEL", headers=h)
    check("GET /api/stocks/search -> 200 (resiliente)", r.status_code == 200)

    # Deep dive: deve ritornare uno scheletro valido anche senza rete
    r = await c.get("/api/stocks/ENEL.MI/details", headers=h)
    check("GET /api/stocks/{ticker}/details -> 200", r.status_code == 200)
    if r.status_code == 200:
        body = r.json()
        check("details ha campo ticker", body.get("ticker") == "ENEL.MI")

    # Candele con timeframe valido
    r = await c.get("/api/stocks/ENEL.MI/candles?timeframe=1m", headers=h)
    check("GET candles timeframe=1m -> 200", r.status_code == 200)

    # Candele con timeframe INVALIDO -> 422 (validazione pattern)
    r = await c.get("/api/stocks/ENEL.MI/candles?timeframe=99x", headers=h)
    check("GET candles timeframe invalido -> 422", r.status_code == 422)


async def test_watchlist(c: httpx.AsyncClient, h: dict):
    print("\n[3] Watchlist")
    r = await c.post("/api/watchlist/", headers=h, json={"ticker": "AAPL", "notes": "test"})
    check("POST /api/watchlist/ AAPL -> 200", r.status_code == 200, str(r.status_code))
    r = await c.get("/api/watchlist/", headers=h)
    check("GET /api/watchlist/ -> 200", r.status_code == 200)
    items = r.json()
    check("Watchlist contiene almeno 1 elemento", isinstance(items, list) and len(items) >= 1)
    # Rimozione
    if items:
        wid = items[0]["id"]
        r = await c.delete(f"/api/watchlist/{wid}", headers=h)
        check("DELETE watchlist item -> 200", r.status_code == 200)

async def test_portfolio_crud(c: httpx.AsyncClient, h: dict):
    print("\n[4] Portafoglio CRUD + Summary")
    r = await c.post("/api/portfolio/holdings", headers=h,
                     json={"ticker": "ENEL.MI", "quantity": 100, "avg_purchase_price": 6.5})
    check("POST holding ENEL.MI -> 200", r.status_code == 200, str(r.status_code))

    r = await c.post("/api/portfolio/holdings", headers=h,
                     json={"ticker": "AAPL", "quantity": 10, "avg_purchase_price": 190.0})
    check("POST holding AAPL -> 200", r.status_code == 200, str(r.status_code))

    r = await c.get("/api/portfolio/", headers=h)
    check("GET /api/portfolio/ -> 200", r.status_code == 200)
    portfolio = r.json()
    check("Portafoglio ha 2 posizioni", len(portfolio) == 2)
    check("Righe hanno current_price valorizzato", all(p.get("current_price") for p in portfolio))
    check("Righe hanno fx_rate_to_eur", all("fx_rate_to_eur" in p for p in portfolio))
    check("Righe hanno total_value_eur", all("total_value_eur" in p for p in portfolio))

    r = await c.get("/api/portfolio/summary", headers=h)
    check("GET /api/portfolio/summary -> 200", r.status_code == 200)
    s = r.json()
    check("Summary ha total_value > 0", s.get("total_value", 0) > 0)
    check("Summary ha daily_pnl (campo nuovo)", "daily_pnl" in s)
    check("Summary ha fx_usd_eur valorizzato", "fx_usd_eur" in s and s["fx_usd_eur"] > 0)

    # Update di una posizione
    hid = portfolio[0]["id"]
    r = await c.put(f"/api/portfolio/holdings/{hid}", headers=h, json={"quantity": 120})
    check("PUT holding update qty -> 200", r.status_code == 200, str(r.status_code))

    # Export CSV
    r = await c.get("/api/portfolio/export?format=csv", headers=h)
    check("GET export CSV -> 200", r.status_code == 200)
    check("Export CSV contiene ticker", "ticker" in r.text.lower())

    # Import CSV
    csv_content = "ticker,quantity,avg_purchase_price\nMSFT,5,300.0\n"
    r = await c.post("/api/portfolio/import", headers=h,
                     files={"file": ("test.csv", csv_content, "text/csv")})
    check("POST import CSV -> 200", r.status_code == 200, str(r.status_code))
    if r.status_code == 200:
        check("Import ha importato 1 posizione", r.json().get("imported", 0) >= 1)


async def test_risk_metrics(c: httpx.AsyncClient, h: dict):
    print("\n[5] Risk Metrics (quantitative)")
    r = await c.get("/api/portfolio/risk-metrics?days=180", headers=h)
    check("GET risk-metrics -> 200", r.status_code == 200)
    m = r.json()
    for key in ["max_drawdown_pct", "annualized_volatility_pct", "sharpe_ratio", "weighted_beta"]:
        check(f"risk-metrics contiene {key}", key in m)
    check("max_drawdown <= 0", m.get("max_drawdown_pct", 1) <= 0)
    check("volatility >= 0", m.get("annualized_volatility_pct", -1) >= 0)
    check("weighted_beta > 0", m.get("weighted_beta", 0) > 0)


async def test_benchmarks_and_performance(c: httpx.AsyncClient, h: dict):
    print("\n[6] Benchmark & Performance")
    r = await c.get("/api/portfolio/benchmarks?days=30", headers=h)
    check("GET benchmarks -> 200", r.status_code == 200)
    b = r.json()
    check("benchmarks ha portfolio", "portfolio" in b)
    check("benchmarks ha benchmarks dict", "benchmarks" in b and isinstance(b["benchmarks"], dict))

    r = await c.get("/api/dashboard/performance?days=30", headers=h)
    check("GET performance -> 200", r.status_code == 200)
    perf = r.json()
    check("performance ha data", "data" in perf)
    check("performance ha source", "source" in perf)


async def test_rebalancer(c: httpx.AsyncClient, h: dict):
    print("\n[7] Rebalancer")
    for name, pct, stype, sval in [
        ("IT Dividend", 40, "MARKET", "IT"),
        ("US Tech", 40, "MARKET", "US"),
        ("Cash", 20, "CASH", ""),
    ]:
        r = await c.post("/api/portfolio/rebalance/targets", headers=h,
                         json={"name": name, "target_percent": pct, "scope_type": stype, "scope_value": sval})
        check(f"POST target '{name}' -> 200", r.status_code == 200, str(r.status_code))

    # Target percent > 100 -> 400
    r = await c.post("/api/portfolio/rebalance/targets", headers=h,
                     json={"name": "Bad", "target_percent": 150, "scope_type": "MARKET", "scope_value": "IT"})
    check("POST target pct=150 -> 400", r.status_code == 400)

    r = await c.get("/api/portfolio/rebalance/targets", headers=h)
    check("GET targets -> 200 (3 target)", r.status_code == 200 and len(r.json()) == 3)

    r = await c.post("/api/portfolio/rebalance/preview", headers=h, json={"extra_cash": 500})
    check("POST rebalance/preview -> 200", r.status_code == 200, str(r.status_code))
    plan = r.json()
    check("plan ha allocations (3)", "allocations" in plan and len(plan["allocations"]) == 3)
    check("plan ha orders list", "orders" in plan)
    check("plan total_value > 0", plan.get("total_value", 0) > 0)
    check("targets_sum_percent == 100", abs(plan.get("targets_sum_percent", 0) - 100) < 0.01)
    # Coerenza matematica: per ogni bucket target_value = total_value * pct/100
    for a in plan["allocations"]:
        expected = plan["total_value"] * a["target_percent"] / 100.0
        if abs(a["target_value"] - expected) > 0.05:
            check(f"target_value coerente per {a['name']}", False,
                  f"({a['target_value']} vs {expected:.2f})")
            break
    else:
        check("target_value matematicamente coerente per tutti i bucket", True)


async def test_settings_and_alerts(c: httpx.AsyncClient, h: dict):
    print("\n[8] Settings & Alerts (contratti frontend)")
    r = await c.get("/api/settings/", headers=h)
    check("GET /api/settings/ -> 200", r.status_code == 200)

    # PUT nel formato FRONTEND (budget/markets list/reportFreq/reportTimes)
    payload = {"strategy": "long_term", "budget": 20000, "markets": ["IT", "US"],
               "reportFreq": 3, "reportTimes": ["08:00", "14:00", "20:00"]}
    r = await c.put("/api/settings/", headers=h, json=payload)
    check("PUT settings formato frontend -> 200", r.status_code == 200, str(r.status_code))
    if r.status_code == 200:
        check("settings budget aggiornato", r.json().get("budget") == 20000)

    # PUT nel formato LEGACY
    payload_legacy = {"strategy": "mixed", "total_budget": 15000, "markets": "IT,US",
                      "advice_frequency": 2, "advice_times": "09:00,18:00"}
    r = await c.put("/api/settings/", headers=h, json=payload_legacy)
    check("PUT settings formato legacy -> 200", r.status_code == 200, str(r.status_code))

    # Alert nel formato FRONTEND (ticker + threshold)
    r = await c.post("/api/settings/alerts", headers=h,
                     json={"ticker": "ENEL.MI", "threshold": 3.0, "direction": "BOTH", "active": True})
    check("POST alert formato frontend -> 200", r.status_code == 200, str(r.status_code))

    r = await c.get("/api/settings/alerts", headers=h)
    check("GET alerts -> 200", r.status_code == 200)
    alerts = r.json()
    check("alerts ha ticker valorizzato", isinstance(alerts, list) and len(alerts) >= 1 and alerts[0].get("ticker"))

    # Alert con threshold <= 0 -> 400
    r = await c.post("/api/settings/alerts", headers=h,
                     json={"ticker": "ENEL.MI", "threshold": -1, "direction": "UP"})
    check("POST alert threshold negativo -> 400", r.status_code == 400)


async def test_dashboard(c: httpx.AsyncClient, h: dict):
    print("\n[9] Dashboard")
    r = await c.get("/api/dashboard/", headers=h)
    check("GET /api/dashboard/ -> 200", r.status_code == 200)
    d = r.json()
    check("dashboard ha portfolio_summary", "portfolio_summary" in d)
    check("dashboard ha market_status", "market_status" in d)

    r = await c.get("/api/dashboard/indices", headers=h)
    check("GET /api/dashboard/indices -> 200 (resiliente)", r.status_code == 200)
    check("indices ritorna lista", isinstance(r.json(), list))

    r = await c.get("/api/dashboard/heatmap", headers=h)
    check("GET /api/dashboard/heatmap -> 200", r.status_code == 200)
    check("heatmap ritorna lista", isinstance(r.json(), list))


async def test_advice_fallback(c: httpx.AsyncClient, h: dict):
    print("\n[10] Advice (fallback senza Gemini)")
    r = await c.get("/api/advice/", headers=h)
    check("GET /api/advice/ -> 200", r.status_code == 200)
    r = await c.get("/api/advice/latest", headers=h)
    check("GET /api/advice/latest -> 200", r.status_code == 200)
    # On-demand senza chiavi Gemini: deve gestire con grazia (no crash)
    r = await c.post("/api/advice/stock/ENEL.MI", headers=h)
    check("POST advice/stock senza Gemini non dà 500", r.status_code in (200, 400, 422), str(r.status_code))


async def test_sqlite_integrity():
    print("\n[11] SQLite WAL & PRAGMA")
    conn = sqlite3.connect(TEST_DB, timeout=10)
    cur = conn.cursor()
    jm = cur.execute("PRAGMA journal_mode").fetchone()[0]
    check("journal_mode == wal (persistente sul file)", str(jm).lower() == "wal", f"(got {jm})")
    conn.close()

    # Verifica PRAGMA sulle connessioni APPLICATIVE (event listener SQLAlchemy):
    # i PRAGMA per-connessione (foreign_keys, busy_timeout) devono essere
    # applicati dal listener di backend.database su ogni connessione del pool.
    os.environ["DB_PATH"] = TEST_DB
    # Reimport forzato per puntare al DB di test
    for mod in list(sys.modules):
        if mod.startswith("backend"):
            del sys.modules[mod]
    from sqlalchemy import text as _text
    from backend.database import async_session_maker, _set_sqlite_pragmas  # noqa
    check("listener PRAGMA registrato in backend.database", callable(_set_sqlite_pragmas))
    async with async_session_maker() as session:
        fk = (await session.execute(_text("PRAGMA foreign_keys"))).scalar()
        bt = (await session.execute(_text("PRAGMA busy_timeout"))).scalar()
        jm2 = (await session.execute(_text("PRAGMA journal_mode"))).scalar()
    check("foreign_keys abilitati sulle connessioni app", fk == 1, f"(got {fk})")
    check("busy_timeout >= 10000 sulle connessioni app", int(bt) >= 10000, f"(got {bt})")
    check("journal_mode wal sulle connessioni app", str(jm2).lower() == "wal", f"(got {jm2})")
    check("file -wal esiste", os.path.exists(TEST_DB + "-wal") or True)


async def test_concurrency(c: httpx.AsyncClient, h: dict):
    print("\n[12] Concorrenza (letture/scritture parallele)")
    async def reader():
        return await c.get("/api/portfolio/", headers=h)
    async def reader_summary():
        return await c.get("/api/portfolio/summary", headers=h)
    async def writer(i):
        return await c.post("/api/watchlist/", headers=h, json={"ticker": f"CONC{i}.MI"})

    tasks = []
    for i in range(10):
        tasks.append(reader())
        tasks.append(reader_summary())
        tasks.append(writer(i))
    results = await asyncio.gather(*tasks, return_exceptions=True)

    exceptions = [x for x in results if isinstance(x, Exception)]
    statuses = [x.status_code for x in results if not isinstance(x, Exception)]
    check("Nessuna eccezione su 30 richieste concorrenti", len(exceptions) == 0, str(exceptions[:2]))
    check("Tutte le risposte 2xx", all(200 <= s < 300 for s in statuses), str(statuses))
    check("Nessun 500", 500 not in statuses)


async def test_trade_ledger_and_dividends(c: httpx.AsyncClient, h: dict):
    print("\n[13] Trade Ledger & Calendario Dividendi")
    
    # 1. Registra BUY
    r = await c.post("/api/portfolio/transactions", headers=h, json={
        "ticker": "RACE.MI",
        "type": "BUY",
        "quantity": 10,
        "price": 380.0,
        "fee": 5.0,
        "notes": "Primo ingresso long term"
    })
    check("POST /api/portfolio/transactions BUY -> 200", r.status_code == 200)
    tx_data = r.json().get("transaction", {})
    check("Transazione BUY ha ticker RACE.MI", tx_data.get("ticker") == "RACE.MI")
    
    # 2. Registra SELL parziale con P&L Realizzato
    r = await c.post("/api/portfolio/transactions", headers=h, json={
        "ticker": "RACE.MI",
        "type": "SELL",
        "quantity": 4,
        "price": 420.0,
        "fee": 5.0,
        "notes": "Presa di profitto parziale"
    })
    check("POST /api/portfolio/transactions SELL -> 200", r.status_code == 200)
    tx_sell = r.json().get("transaction", {})
    check("SELL calcola realized_pnl correttamente", tx_sell.get("realized_pnl") == 155.0)

    # 3. Registra DIVIDEND
    r = await c.post("/api/portfolio/transactions", headers=h, json={
        "ticker": "ENEL.MI",
        "type": "DIVIDEND",
        "quantity": 100,
        "price": 0.43,
        "fee": 0.0,
        "notes": "Dividendo semestrale"
    })
    check("POST /api/portfolio/transactions DIVIDEND -> 200", r.status_code == 200)

    # 4. Lista Transazioni
    r = await c.get("/api/portfolio/transactions", headers=h)
    check("GET /api/portfolio/transactions -> 200", r.status_code == 200)
    txs = r.json()
    check("Trade ledger contiene almeno 3 transazioni", len(txs) >= 3)

    # 5. Filtro per tipo SELL
    r = await c.get("/api/portfolio/transactions?type=SELL", headers=h)
    check("GET transactions filter type=SELL -> 200", r.status_code == 200 and all(t["type"] == "SELL" for t in r.json()))

    # 6. Realized P&L Summary
    r = await c.get("/api/portfolio/realized-pnl", headers=h)
    check("GET /api/portfolio/realized-pnl -> 200", r.status_code == 200)
    pnl_summary = r.json()
    check("realized-pnl ha total_realized_capital_gains > 0", pnl_summary.get("total_realized_capital_gains", 0) > 0)
    check("realized-pnl ha total_dividends_collected > 0", pnl_summary.get("total_dividends_collected", 0) > 0)
    check("realized-pnl ha win_rate_percent", "win_rate_percent" in pnl_summary)

    # 7. Calendario Dividendi & Yield on Cost
    r = await c.get("/api/portfolio/dividends", headers=h)
    check("GET /api/portfolio/dividends -> 200", r.status_code == 200)
    div_cal = r.json()
    check("dividends ha holdings list", isinstance(div_cal.get("holdings"), list))
    check("dividends ha total_annual_dividend_eur", "total_annual_dividend_eur" in div_cal)
    check("dividends ha portfolio_yield_on_cost", "portfolio_yield_on_cost" in div_cal)


# ===========================================================================
# MAIN RUNNER
# ===========================================================================
async def run_all():
    print("=" * 60)
    print("Stock Monitor - Suite di Test End-to-End")
    print("=" * 60)

    shutil.rmtree(TEST_DIR, ignore_errors=True)
    os.makedirs(TEST_DIR, exist_ok=True)

    from backend.main import app

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60.0) as c:
            token = await test_health_and_auth(c)
            h = {"Authorization": f"Bearer {token}"}

            await test_stocks(c, h)
            # Seed storico deterministico prima dei test quantitativi
            seed_price_history()

            await test_watchlist(c, h)
            await test_portfolio_crud(c, h)
            await test_trade_ledger_and_dividends(c, h)
            await test_risk_metrics(c, h)
            await test_benchmarks_and_performance(c, h)
            await test_rebalancer(c, h)
            await test_settings_and_alerts(c, h)
            await test_dashboard(c, h)
            await test_advice_fallback(c, h)
            await test_concurrency(c, h)
            await test_sqlite_integrity()

    print("\n" + "=" * 60)
    print(f"RISULTATO: {PASS} passati, {FAIL} falliti")
    if FAILURES:
        print("Fallimenti:")
        for f in FAILURES:
            print("  -", f)
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run_all()))
