# 🤖 AI RULES – Investment App

---

# 🎯 PURPOSE

This document defines strict rules for AI (Cursor or other) when modifying this codebase.

The goal is to:

* prevent breaking working logic
* maintain system stability
* ensure consistent architecture
* avoid overengineering

---

# 🚨 GLOBAL RULES (MUST FOLLOW)

## 1. DO NOT BREAK EXISTING ARCHITECTURE

System flow MUST remain:

```text
market → worker → alert engine → notifier → DB → UI
```

❌ DO NOT:

* merge components
* move logic into UI
* create new architecture layers

---

## 2. DO NOT REFACTOR WITHOUT EXPLICIT REQUEST

❌ DO NOT:

* rename modules
* reorganize folder structure
* rewrite working code

✔ ONLY:

* add minimal changes
* extend existing logic

---

## 3. WORKER IS CRITICAL – DO NOT BREAK

Worker must:

* run in infinite loop
* never crash
* always catch exceptions
* remain independent from UI

❌ DO NOT:

* add blocking logic
* add heavy computations
* introduce instability

---

## 4. ALERT ENGINE IS DETERMINISTIC

Alert system must remain:

* rule-based
* deterministic
* state-driven

❌ DO NOT:

* add randomness
* add AI-based decisions
* modify thresholds without instruction

---

## 5. CONFIG MUST COME FROM .ENV

✔ ALWAYS use:

```python
os.getenv(...)
```

❌ DO NOT:

* hardcode tokens
* hardcode credentials
* override env values in code

.env = single source of truth

---

## 6. DO NOT MODIFY NOTIFICATION LOGIC FLOW

All alerts MUST go through:

```text
AlertEngine → MultiNotifier
```

❌ DO NOT:

* bypass notifier
* send direct emails
* send direct Telegram messages

---

## 7. DATABASE = SIMPLE ONLY

✔ SQLite only (unless explicitly changed)

❌ DO NOT:

* add ORM (SQLAlchemy)
* add complex queries
* add unnecessary tables

---

## 8. UI MUST NOT CONTAIN BUSINESS LOGIC

UI should ONLY:

* display data
* format data
* visualize data

❌ DO NOT:

* calculate alerts
* calculate portfolio logic
* duplicate backend logic

---

## 9. KEEP SYSTEM SIMPLE

This system is intentionally:

* minimal
* deterministic
* long-term oriented

❌ DO NOT:

* overengineer
* add abstractions
* introduce unnecessary layers

---

## 10. ADD FEATURES SAFELY

When adding features:

✔ follow pattern:

* extend existing module
* minimal changes
* safe fallback

❌ DO NOT:

* rewrite core logic
* change behavior silently

---

# ⚠️ HIGH-RISK AREAS (HANDLE CAREFULLY)

These parts must be changed ONLY with explicit instruction:

* worker loop
* alert engine logic
* notifier routing
* config loading
* database schema

---

# 🧠 EXPECTED AI BEHAVIOR

When modifying code:

1. read system_overview.md first
2. understand current architecture
3. apply minimal changes
4. preserve behavior
5. explain changes briefly

---

## IBKR DATA RULES

- IBKR simboli se NE uporabljajo direktno v app logiki
- vedno se uporablja mapping layer

Primer:
SXRV → CNDX

---

## DATA SEPARATION RULE

raw data ≠ business logic

- DB hrani raw IBKR podatke
- app uporablja agregirane vrednosti

---

## PARSING RULE

- parser mora biti robusten
- nikoli ne crasha sistema
- vedno mora imeti fallback

---

## FLOAT PRECISION

- vse finančne vrednosti se zaokrožijo na 2 decimalki

------------------------

# 🔥 FINAL RULE

If unsure:

👉 DO NOT CHANGE CORE LOGIC
👉 ask for clarification instead

---

