"""Jednorázový backfill ENTSOG Nomination/Renomination pro VŠECHNY body
Evropy (~415), od zadaného startu do dnes. Resumable: přeskočí měsíce,
které už mají partitioned soubor (kromě aktuálního, otevřeného měsíce).
Log průběžně do souboru předaného jako první argument, ať přežije i
přerušení.

Použití:
    python scripts/backfill_entsog_eu_operational.py [log_path] [start YYYY-MM-DD] [end YYYY-MM-DD]
"""
import sys
from datetime import date
sys.path.insert(0, ".")

from data.entsog_operational import backfill_eu_operational

if __name__ == "__main__":
    log_path = sys.argv[1] if len(sys.argv) > 1 else "entsog_eu_operational_backfill.log"
    start = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else None
    end = date.fromisoformat(sys.argv[3]) if len(sys.argv) > 3 else None
    backfill_eu_operational(log_path=log_path, start=start, end=end)
