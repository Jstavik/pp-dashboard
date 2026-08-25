"""Jednorázový backfill konsolidované ENTSOG /operationaldata vrstvy.

Dva režimy:
- history: HISTORY_INDICATORS (Nomination, Renomination, GCV, Wobbe
  Index) — skutečná denní historie, backfilluje se po měsících od
  zadaného startu do dneška.
- open_ended: OPEN_ENDED_INDICATORS (kapacita, interrupce) — validity
  okna, žádná měsíční historie, JEDEN fetch aktuálního měsíce.

Použití:
    python scripts/backfill_entsog_operational.py history [log_path] [start YYYY-MM-DD] [end YYYY-MM-DD]
    python scripts/backfill_entsog_operational.py open_ended [log_path]
"""
import sys
from datetime import date
sys.path.insert(0, ".")

from data.entsog_operational import (
    backfill_history, backfill_open_ended,
    HISTORY_INDICATORS, OPEN_ENDED_INDICATORS,
)

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "history"
    log_path = sys.argv[2] if len(sys.argv) > 2 else f"entsog_operational_{mode}_backfill.log"

    if mode == "history":
        start = date.fromisoformat(sys.argv[3]) if len(sys.argv) > 3 else None
        end = date.fromisoformat(sys.argv[4]) if len(sys.argv) > 4 else None
        backfill_history(HISTORY_INDICATORS, log_path=log_path, start=start, end=end)
    elif mode == "open_ended":
        backfill_open_ended(OPEN_ENDED_INDICATORS, log_path=log_path)
    else:
        print(f"Neznámý mode: {mode!r} (očekává 'history' nebo 'open_ended')")
        sys.exit(1)
