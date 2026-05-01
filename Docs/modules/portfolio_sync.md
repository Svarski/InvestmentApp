# PORTFOLIO SYNC MODULE

## Namen

Orkestracija dnevnega synca IBKR → DB.

---

## Glavna funkcija

run_portfolio_sync()

---

## Flow

1. preveri should_sync_today()
2. request phase: `_request_flex_report_with_backoff()`
   - max 3 poskusi
   - 60s razmik med requesti
   - `[1001]` -> pocaka 60s in retry
   - network error -> pocaka 60s in retry
   - `[1018]` -> fail-fast (next worker cycle bo poskusil znova)
3. polling phase: `_poll_flex_report(reference_code)`
   - uporablja isti `reference_code` (ne ustvarja novega joba)
   - polling vsakih 10s, max 5 minut
   - ce report ni ready (`[1001]` / "not ready"), nadaljuje polling
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
- state update: `in_progress` -> `success` / `failed`
- `last_successful_sync` se ohrani ob `in_progress` in `failed`
- snapshot se NE zapise, ce `total_value <= 0`

---

## Pomembno

Ta modul:

- NE vsebuje UI logike
- NE vsebuje alert logike
- je izoliran sync layer