# IBKR FLEX MODULE

## Namen

Prevzem portfelja iz IBKR preko Flex Web Service (XML).

---

## Funkcije

### request_flex_report()

- pošlje request na IBKR
- vrne reference_code

---

### fetch_flex_report(reference_code)

- polla IBKR endpoint
- čaka na generacijo reporta
- vrača raw XML string

---

### parse_flex_report(xml_string)

Vrne dict:

- ibkr_timestamp
- account_id
- positions
- cash_balances
- net_liquidation_value
- base_currency
- raw_xml

---

## Parsing pravila

- uporablja xml.etree.ElementTree
- bere atribute (ne child text)
- safe parsing (brez crasha)

---

## Fallbacki

### Net liquidation

če manjka:

→ izračun:
sum(position_value) + cash

---

### Cash

prioriteta:

1. endingCash  
2. endingSettledCash  
3. totalCashValue  
4. slbNetCash  
5. 0.0

---

## Error handling

- noben exception ne sme crashati sistema
- vrne "empty result shape"
- logira error

---

## Pomembno

Ta modul NE:

- zapisuje v DB
- ne pozna app logike
- ne dela business odločitev

Je izključno:
👉 data extraction layer