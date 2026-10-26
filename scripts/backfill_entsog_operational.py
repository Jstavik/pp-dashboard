"""Jednorázový backfill konsolidované ENTSOG /operationaldata vrstvy.

Dva režimy:
- history: HISTORY_INDICATORS (Nomination, Renomination, GCV, Wobbe
  Index) — skutečná denní historie, backfilluje se po měsících od
  zadaného startu do dneška.
- open_ended: OPEN_ENDED_INDICATORS (kapacita, interrupce) — validity
  okna, žádná měsíční historie, JEDEN fetch aktuálního měsíce.
- point_flows: Physical Flow + Allocation po bodech od 2019-01-01
  (Analýza toků plynu), po měsících, navazuje.
- periods: kapacity + přerušení PO MĚSÍCÍCH od 2019-01-01 (i skončená
  období), navazuje přes entsog_operational/.backfill_periods.json.
- analyza: point_flows + periods (vše pro /plyn/analyza).
Rate-limit: PPDASH_ENTSOG_SLEEP (s, default 1.0) + opakování při 429/5xx.

Použití:
    python scripts/backfill_entsog_operational.py history [log_path] [start YYYY-MM-DD] [end YYYY-MM-DD]
    python scripts/backfill_entsog_operational.py open_ended [log_path]
    python scripts/backfill_entsog_operational.py point_flows|periods|analyza [log_path] [start] [end]
"""
import sys
from datetime import date
sys.path.insert(0, ".")

from data.entsog_operational import (
    backfill_history, backfill_open_ended, backfill_periods, backfill_point_flows,
    HISTORY_INDICATORS, OPEN_ENDED_INDICATORS,
)

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "history"
    log_path = sys.argv[2] if len(sys.argv) > 2 else f"entsog_operational_{mode}_backfill.log"

    if mode == "history":
        start = date.fromisoformat(sys.argv[3]) if len(sys.argv) > 3 else None
        end = date.fromisoformat(sys.argv[4]) if len(sys.argv) > 4 else None
        backfill_history(HISTORY_INDICATORS, log_path=log_path, start=start, end=end)
    elif mode in ("point_flows", "periods", "analyza"):
        start = date.fromisoformat(sys.argv[3]) if len(sys.argv) > 3 else None
        end = date.fromisoformat(sys.argv[4]) if len(sys.argv) > 4 else None
        if mode in ("point_flows", "analyza"):
            backfill_point_flows(log_path=log_path, start=start, end=end)
        if mode in ("periods", "analyza"):
            backfill_periods(log_path=log_path, start=start, end=end)
    elif mode == "open_ended":
        backfill_open_ended(OPEN_ENDED_INDICATORS, log_path=log_path)
    else:
        print(f"Neznámý mode: {mode!r} (očekává history|open_ended|point_flows|periods|analyza)")
        sys.exit(1)
