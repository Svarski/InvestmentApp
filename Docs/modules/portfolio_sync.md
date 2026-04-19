# PORTFOLIO SYNC MODULE

## Namen

Orkestracija dnevnega synca IBKR → DB.

---

## Glavna funkcija

run_portfolio_sync()

---

## Flow

1. preveri should_sync_today()
2. request_flex_report()
3. fetch_flex_report()
4. parse_flex_report()
5. calculate_portfolio_summary()
6. insert v DB
7. update sync state

---

## should_sync_today()

- uporablja data/ibkr_sync_state.json
- omogoča 1x dnevni sync
- fail-open (če error → True)

---

## calculate_portfolio_summary()

Vrne:

- total_value
- vwce_value
- cndx_value
- cash
- raw_positions (JSON)

---

## Symbol mapping

Mapping:

SXRV → CNDX

Uporablja se SAMO za agregacijo.

Raw podatki ostanejo nespremenjeni.

---

## Total value fallback

Če IBKR ne vrne net liquidation:

total = sum(position.market_value) + cash

---

## DB zapis

Tabela: portfolio_snapshots

Vedno shrani:

- raw_positions
- raw_xml

---

## Robustnost

- try/except na celotnem flowu
- DB error ne crasha workerja
- state update samo ob uspehu

---

## Pomembno

Ta modul:

- NE vsebuje UI logike
- NE vsebuje alert logike
- je izoliran sync layer