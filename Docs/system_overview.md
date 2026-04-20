# 📄 Investment App – System Overview

---

# 🎯 PURPOSE

This application is a **personal investment platform** designed to:

* monitor financial markets
* evaluate predefined alert rules
* notify the user (email / Telegram)
* track portfolio history
* provide decision support for investing

The system is designed to be:

* deterministic (no randomness)
* simple (no unnecessary complexity)
* autonomous (runs without user interaction)
* long-term oriented (aligned with DCA investing strategy)

---

# 🧠 CORE INVESTMENT MODEL

The system is built around a **long-term investing strategy**:

* 80% VWCE (global market)
* 20% CNDX (Nasdaq growth)

Key principles:

* DCA (monthly investing)
* increased investing during drawdowns
* no selling during market downturns
* long-term horizon (20 years)

👉 Strategy definition: 

---

# 🧱 SYSTEM ARCHITECTURE

## High-level data flow:

```
Market Data
   ↓
Worker (background loop)
   ↓
Alert Engine (rules + decisions)
   ↓
Notifier (email / telegram)
   ↓
Database (history)
   ↓
UI (Streamlit dashboard)
```

👉 Clean separation:

* Worker = execution
* Engine = logic
* DB = persistence
* UI = presentation 

---

# ⚙️ COMPONENTS

---

## 1. WORKER (app/worker.py)

### Role:

Background process that runs continuously.

### Responsibilities:

* fetch market data
* evaluate alerts
* send notifications
* store data to DB

### Behavior:

* runs in loop (every X minutes)
* never crashes (safe execution)
* logs all actions

### Important:

* independent from UI
* runs in Docker container

👉 This is the “brain loop” of the system 

---

## 2. ALERT ENGINE (alerts/engine.py)

### Role:

Evaluates market conditions and decides when to trigger alerts.

### Separation:

* detection (rules)
* decision (dedup + reset)

### Supported alerts:

* market drawdowns (VWCE, CNDX, SPY, QQQ)
* VIX spike
* portfolio drop (optional)

### Key features:

* deduplication (no repeated alerts)
* reset logic (alerts can trigger again after recovery)
* deterministic behavior

👉 Engine is production-ready and stable 

---

## 3. STATE (PERSISTENCE – JSON)

### Files:

* `data/alert_state.json`
* `data/weekly_digest_state.json`

### Purpose:

* prevent alert spam
* track triggered levels
* track daily/weekly sends

---

## 4. DATABASE (SQLite)

### File:

* `data/app.db`

### Tables:

#### alerts

* id
* timestamp
* symbol
* type
* level
* message

#### portfolio_snapshots

* id (INTEGER PRIMARY KEY AUTOINCREMENT)
* timestamp
* total_value
* vwce_value
* cndx_value
* cash

### Purpose:

* store history
* enable UI analytics
* allow future features

👉 DB adds “memory” to the system 

---

## 5. NOTIFIER (services/notifiers)

### Channels:

* email
* telegram
* both / none (configurable)

### Key rules:

* controlled by `ALERT_CHANNEL`
* all sending goes through MultiNotifier
* retry logic implemented

### Weekly summary:

* independent from real-time alerts
* separate config

👉 clean delivery layer 

---

## 6. UI (Streamlit)

### Role:

* display data
* visualize portfolio
* show alerts
* show performance

### Sections:

* market overview
* portfolio summary
* alert history
* performance chart

### Important:

* UI does NOT contain business logic
* only displays data

---

## 7. BUYING LADDER (Decision Support)

### Purpose:

Suggest how much to invest.

### Logic:

* based on VWCE drawdown
* uses multiplier system

Example:

* -10% → 1.25x
* -20% → 1.6x
* -30% → 2.0x

---

## 8. ALLOCATION ENGINE

### Purpose:

Suggest where to invest.

### Logic:

* VWCE → defines amount
* CNDX → defines allocation

Rules:

* default = 80/20
* more CNDX if it drops more
* capped to limit risk

👉 creates disciplined investing behavior 

---

# 🔔 ALERT SYSTEM LOGIC

---

## Real-time alerts

Triggered every worker cycle:

```
market data
→ evaluate rules
→ if threshold reached
→ if not already triggered
→ send alert
```

---

## Daily digest

Condition:

```
if current_time >= configured_hour AND not_sent_today:
    send
```

Important:

* runs once per day
* may trigger after restart (expected behavior)

---

## Weekly summary

* independent system
* uses its own state
* not tied to real-time alerts
* module split:
  * `services/reports/weekly_digest.py` (orchestrator, backward-compatible API)
  * `services/reports/weekly_digest_state.py` (state + JSON persistence)
  * `services/reports/weekly_digest_scheduler.py` (weekly send scheduling)
  * `services/reports/weekly_digest_builder.py` (HTML/text digest generation)

---

# ⚙️ CONFIGURATION (.env)

### Single source of truth

All config must come from `.env`.

### Examples:

```
ALERT_CHANNEL=both
WEEKLY_SUMMARY_ENABLED=true
WEEKLY_SUMMARY_CHANNEL=email
DAILY_DIGEST_HOUR=9
```

### Rules:

* no hardcoded secrets in code
* env always overrides defaults

👉 prevents config bugs 

---

# 🧱 DEPLOYMENT ARCHITECTURE

### Environment:

* Hetzner VPS
* Docker + docker-compose

### Services:

#### app

* Streamlit UI
* exposed on port 8501

#### worker

* background process
* no ports

### Access:

```
http://SERVER_IP:8501
```

👉 production-ready setup 

---

# 🔄 DEPLOY WORKFLOW

```
git push
→ server: git pull
→ docker-compose up -d --build
```

👉 standard DevOps flow 

---

# 🔐 AUTH SYSTEM (OPTIONAL LAYER)

Architecture:

```
Internet
→ Nginx
→ FastAPI auth
→ Streamlit
```

* login page (HTML)
* session-based auth
* WebSocket bypass via Nginx

👉 required for production security 

---

# 🧠 DESIGN PRINCIPLES

The system follows:

### 1. Simplicity

* no unnecessary abstractions
* no overengineering

### 2. Determinism

* same input → same output
* no AI / randomness (yet)

### 3. Separation of concerns

* worker ≠ UI
* logic ≠ presentation
* DB ≠ execution

### 4. Stability over features

* system must never crash
* alerts must not spam

---

# ⚠️ NON-GOALS (INTENTIONAL)

The system intentionally does NOT include:

* AI predictions
* trend detection (bull/bear)
* trading automation
* high-frequency logic

Reason:

* not needed for long-term investing
* would add unnecessary complexity

👉 simplicity is a feature

---

# 🚀 CURRENT STATE

The system is:

* production-ready
* running 24/7
* sending alerts
* storing history
* providing UI insights

👉 This is NOT a prototype
👉 This is a working product

---

# 🔮 FUTURE DIRECTIONS (OPTIONAL)

Possible upgrades:

1. Smart alerts (volatility-based)
2. IBKR or CSV portfolio sync
3. mobile-first UI improvements
4. PostgreSQL (scaling)
5. analytics dashboard

---

# 🔥 FINAL SUMMARY

This system:

* monitors markets
* evaluates rules
* notifies user
* stores history
* supports investing decisions

---

👉 It is a **personal investing engine**

not just a dashboard.

---

## 📊 IBKR FLEX INTEGRACIJA (PORTFOLIO DATA SOURCE)

### Opis

Aplikacija uporablja IBKR Flex Web Service za dnevni prevzem portfelja.

Gre za batch XML pristop (brez live API povezave).

---

### Data flow

IBKR Flex → services/ibkr_flex.py → services/portfolio_sync.py → SQLite (portfolio_snapshots) → UI

---

### Komponente

#### 1. services/ibkr_flex.py
- request_flex_report()
- fetch_flex_report()
- parse_flex_report()

Odgovoren za:
- komunikacijo z IBKR
- parsing XML
- fallback logiko

---

#### 2. services/portfolio_sync.py

Glavna funkcija:
run_portfolio_sync()

Flow:
1. request_flex_report
2. fetch_flex_report
3. parse_flex_report
4. calculate_portfolio_summary
5. insert v DB (portfolio_snapshots)

---

### Shranjeni podatki

Tabela: portfolio_snapshots

- timestamp (UTC)
- total_value
- vwce_value
- cndx_value
- cash
- raw_positions (JSON)
- raw_xml (celoten XML)

---

### Pomembna pravila

- XML se vedno shrani cel (raw_xml)
- parsing mora biti robusten (fallbacki)
- sistem ne sme crashati zaradi slabega XML
- vse vrednosti so v float (zaokrožene na 2 decimalki)

---

### Fallback logika

Če IBKR ne vrne NetLiquidation:

total_value = sum(position.market_value) + cash

---

### Mapping layer

IBKR simboli se mapirajo na app simbole:

SXRV → CNDX

Mapping se izvaja v:
services/portfolio_sync.py

Raw podatki v DB ostanejo nespremenjeni.