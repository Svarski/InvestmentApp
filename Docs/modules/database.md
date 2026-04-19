# Database

## Type

SQLite

## Purpose

* store alert history
* store portfolio snapshots

## Tables

* alerts
* portfolio_snapshots

## Rules

* keep schema simple
* use direct SQL
* no ORM

## Do NOT

* overengineer
* add complex relations

## Portfolio snapshots (IBKR)

Tabela: portfolio_snapshots

Dodatna polja:

- raw_positions (TEXT, JSON)
- raw_xml (TEXT)

---

### raw_positions

- serializiran JSON iz IBKR
- vsebuje vse pozicije (ne samo VWCE/CNDX)

---

### raw_xml

- celoten IBKR XML
- služi kot:
  - audit log
  - debugging
  - fallback parsing

---

### Pomembno

- DB hrani raw podatke
- app dela agregacijo ločeno
