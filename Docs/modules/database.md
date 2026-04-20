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

- id (INTEGER PRIMARY KEY AUTOINCREMENT)
- timestamp (TEXT; ni unique, uporablja se za časovni zapis)
- total_value (REAL)
- vwce_value (REAL)
- cndx_value (REAL)
- cash (REAL)

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

---

## Migration behavior for portfolio_snapshots primary key

Ob `init_db()` aplikacija preveri shemo `portfolio_snapshots`.

### Kdaj migracija stece

- migracija stece samo ce tabela nima `id` stolpca kot `INTEGER PRIMARY KEY`
- ce je schema ze pravilna, se migracija ne izvede (idempotentno vedenje)

### Kako migracija poteka

- SQLite-safe rebuild pristop:
  - ustvari se `portfolio_snapshots_new` z `id INTEGER PRIMARY KEY AUTOINCREMENT`
  - podatki se kopirajo iz stare tabele (`timestamp`, vrednosti, `raw_positions`, `raw_xml`)
  - stara tabela se zamenja z novo
  - indeks `idx_portfolio_timestamp` se ponovno zagotovi

### Validacija po migraciji

- po poskusu migracije se izvede schema validacija z `PRAGMA table_info(portfolio_snapshots)`
- validacija zahteva:
  - `id` stolpec obstaja
  - `id` je primary key

### Kaj se zgodi ob napaki

- ce migracija faila, sistem zabelezi CRITICAL log s traceback:
  - `CRITICAL: portfolio_snapshots migration failed - DB is in legacy state`
- worker se ne sesuje avtomatsko zaradi migracije, vendar ostane zelo viden ERROR/CRITICAL signal v logih
