# Weekly Digest Module

## Purpose

Weekly digest ostaja enak po obnasanju, vendar je razdeljen na manjse module za lazje vzdrzevanje.

## Architecture

`services/reports/weekly_digest.py` je orchestrator in backward-compatible vstopna tocka za worker.

Moduli:

- `services/reports/weekly_digest_state.py`
  - `WeeklyDigestState`
  - state update (`update_weekly_digest_state`)
  - JSON persistence (`load_from_file`, `save_to_file`)
  - daily digest send state (`should_send_daily_digest`, `mark_daily_digest_sent`)
- `services/reports/weekly_digest_scheduler.py`
  - weekly send gating (`should_send_weekly_digest`)
  - sent marker (`mark_weekly_digest_sent`)
- `services/reports/weekly_digest_builder.py`
  - HTML weekly output (`build_weekly_digest_html`)
  - short daily text output (`build_daily_digest_message`)

## Compatibility Contract

- Worker se se vedno naslanja na `services/reports/weekly_digest.py`.
- Imena funkcij in podpisi ostanejo enaki.
- Output format (HTML weekly + daily text) ostane nespremenjen.
