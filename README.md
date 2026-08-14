# 📈 Stock Monitor

Web application self-hosted per il monitoraggio orario di titoli azionari (Borsa Italiana, USA, Europa), gestione portafoglio con calcolo live di P&L, import/export CSV, modifiche al volo con conferma di sicurezza e 5 consigli finanziari giornalieri generati da **Google Gemini 3.7 Flash** integrando analisi tecnica e notizie multi-fonte.

---

## ✨ Funzionalità Principali

- 📊 **Monitoraggio automatico orario** dei mercati finanziari (IT, US, EU).
- 🧠 **5 Consigli Finanziari Giornalieri** basati su Gemini 3.7 Flash con Target Price, Timeframe e Razionale.
- 💼 **Gestione Portafoglio Completa**:
  - Modifica rapida di quantità e prezzo con ricalcolo P&L istantaneo.
  - Barra di sicurezza e modale con conferma finale prima del salvataggio.
  - Esportazione ed Importazione file CSV con rilevamento automatico colonne.
- 📰 **Motore Notizie & Sentiment Multi-Fonte Zero-Auth** (Yahoo Finance News, Google News RSS, Reddit pubblico).
- 🔒 **Sicurezza & Autenticazione Solida**:
  - Sessioni JWT sicure via cookie `HttpOnly`.
  - Protezione anti brute-force (lockout temporaneo dopo 5 tentativi errati).
  - Gestione utenti riservata esclusivamente all'amministratore.
- 📱 **Notifiche Telegram** (opzionali per alert di prezzo e report giornalieri).
- 🚀 **Deploy NAS a File Singolo**: sul NAS serve solo il file `docker-compose.yml`!

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
      - DB_PATH=data/stock_monitor.db
      - ALERT_CHECK_INTERVAL_MINUTES=15
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

- **Backend**: Python 3.12, FastAPI, SQLAlchemy 2.0 (Async), aiosqlite, APScheduler, yfinance, google-genai (Gemini 3.7 Flash), httpx.
- **Frontend**: Vanilla HTML5, CSS3 Glassmorphism Dark Theme, JavaScript ES Modules, TradingView Lightweight Charts.
- **Database**: SQLite in modalità WAL (Write-Ahead Logging) ad alta concorrenza.
