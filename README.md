# 📈 Stock Monitor

Web application self-hosted per il monitoraggio orario di titoli azionari (Borsa Italiana, USA, Europa), gestione portafoglio con calcolo live di P&L, import/export CSV, modifiche al volo con conferma di sicurezza e 5 consigli finanziari giornalieri generati da **Google Gemini 3.7 Flash** integrando analisi tecnica e notizie multi-fonte.

---

## ✨ Funzionalità Principali

- 📊 **Monitoraggio automatico orario** dei mercati finanziari (IT, US, EU).
- 🧠 **Consigli Finanziari AI** basati su Gemini 3.7 Flash con Target Price, Timeframe e Razionale + **Analisi on-demand per singolo ticker**.
- 💼 **Gestione Portafoglio Completa**:
  - Modifica rapida di quantità e prezzo con ricalcolo P&L istantaneo.
  - Barra di sicurezza e modale con conferma finale prima del salvataggio.
  - Esportazione ed Importazione file CSV con rilevamento automatico colonne.
- 📊 **Benchmark Comparison**: curva di crescita % del portafoglio a confronto diretto con **S&P 500 (`^GSPC`)** e **FTSE MIB (`FTSEMIB.MI`)** sullo stesso grafico.
- 🛡️ **Metriche di Rischio Quantitative**: Max Drawdown, Volatilità Annualizzata, **Sharpe Ratio**, **Beta pesato** del portafoglio e Rendimento Annualizzato.
- ⚖️ **Smart Portfolio Rebalancer**: allocazioni target per mercato/ticker/liquidità (es. 40% US Tech, 30% IT Dividend, 30% Cash) e generazione automatica degli ordini di ribilanciamento (unità in buy/sell).
- 📈 **Grafici Avanzati** (TradingView Lightweight Charts): toggle **Area vs Candele (OHLC)**, sub-chart **volumi colorati**, linea **Breakeven** (prezzo medio di carico) sulle posizioni in portafoglio.
- 🎨 **UI Institutional Fintech Dark**: skeleton shimmer, flash `pulse-green/pulse-red` sui prezzi live, drawer mobile con gesture touch e micro-animazioni.
- 📰 **Motore Notizie & Sentiment Multi-Fonte Zero-Auth** (Yahoo Finance News, Google News RSS, Reddit pubblico).
- 🔒 **Sicurezza & Autenticazione Solida**: JWT via cookie `HttpOnly`, anti brute-force, gestione utenti admin-only.
- 🤖 **Bot Telegram Interattivo Bidirezionale**:
  - `/value` ➔ report valore, P&L giornaliero e top movers
  - `/radar` ➔ watchlist con prezzi live e segnali RSI
  - `/advice <TICKER>` ➔ analisi AI Gemini on-demand
- 🛡️ **Resilienza Dati Esterna**: circuit breaker + retry con backoff esponenziale + **fallback sull'ultimo prezzo noto (stale-cache/DB)** quando Yahoo Finance risponde 429/403.
- 🧵 **SQLite in WAL ad alta concorrenza**: PRAGMA `busy_timeout=10000`, `journal_mode=WAL` e sessioni async isolate per ogni task.
- 🚀 **Deploy NAS a File Singolo**: sul NAS serve solo il file `docker-compose.nas.yml`!

---

## 🚀 Deploy su NAS con 1 Solo File (`docker-compose.yml`)

Grazie alla GitHub Actions CI/CD inclusa (`.github/workflows/docker-publish.yml`), quando pushi il codice su GitHub l'immagine Docker viene compilata e pubblicata automaticamente sul GitHub Container Registry (`ghcr.io`).

### 1. File unico per il NAS (`docker-compose.yml`)

Crea sul tuo NAS una cartella (es. `/home/fabrizio/docker/stock_monitor`) con all'interno **esclusivamente questo file**:

```yaml
services:
  stock-monitor:
    image: ghcr.io/<TUO-USERNAME-GITHUB>/stock_monitor:latest
    container_name: stock-monitor
    restart: unless-stopped
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
    environment:
      - GEMINI_API_KEY=your_gemini_api_key_here
      - GEMINI_MODEL=gemini-3.7-flash
      - SECRET_KEY=stock-monitor-super-secret-key-2026-nas
      - ADMIN_USERNAME=admin
      - ADMIN_PASSWORD=admin123
      - TELEGRAM_BOT_TOKEN=
      - TELEGRAM_CHAT_ID=
      - TELEGRAM_BOT_ENABLED=true
      - RISK_FREE_RATE=0.02
      - DB_PATH=data/stock_monitor.db
      - ALERT_CHECK_INTERVAL_MINUTES=15
      - LOG_LEVEL=INFO
```

### 2. Avvio

```bash
docker compose up -d
```
L'app sarà accessibile su `http://<IP-NAS>:8000/static/index.html`.

### 3. Aggiornamento all'ultima versione di GitHub

Ogni volta che fai modifiche e pushi su GitHub, per aggiornare il NAS basta eseguire:

```bash
docker compose pull && docker compose up -d
```
*(Tutti i dati, utenti, titoli e storico rimangono intatti nella cartella `./data/`)*

---

## 💻 Come Pushare su GitHub

Dalla directory locale sul tuo computer:

```bash
# 1. Inizializza il repository (se non già fatto)
git init
git add .
git commit -m "Initial commit Stock Monitor"

# 2. Collega il tuo repository remoto su GitHub
git remote add origin https://github.com/<TUO-USERNAME>/stock_monitor.git
git branch -M main

# 3. Pusha il codice
git push -u origin main
```

> **Nota per repository privati**: Se il tuo repo GitHub è privato, vai su **GitHub Settings ➔ Packages** del repository e imposta la visibilità del package su **Public**, oppure esegui `docker login ghcr.io` sul NAS con un Personal Access Token (PAT).

---

## 🛠️ Stack Tecnologico

- **Backend**: Python 3.12, FastAPI, SQLAlchemy 2.0 (Async), aiosqlite, APScheduler, yfinance, google-genai (Gemini 3.7 Flash), httpx, python-telegram-bot.
- **Frontend**: Vanilla HTML5, CSS3 Glassmorphism Dark Theme, JavaScript ES Modules, TradingView Lightweight Charts.
- **Database**: SQLite in modalità WAL (Write-Ahead Logging) ad alta concorrenza (PRAGMA `busy_timeout=10000`, `synchronous=NORMAL`).
- **Analytics**: NumPy & Pandas per metriche di rischio, benchmark e ribilanciamento.

## 🧪 Test End-to-End Automatizzati

Suite asincrona completa (83 verifiche) che copre: autenticazione, CRUD portafoglio/watchlist/stocks, metriche di rischio, benchmark, rebalancer, contratti REST (formato frontend e legacy), path di **fallback** per rate-limiting Yahoo (429/403), concorrenza SQLite e integrità WAL/PRAGMA.

```bash
# Isola un DB temporaneo in /tmp, avvia uvicorn dedicato ed esegue tutte le verifiche
./venv/bin/python tests/e2e_test.py
```
