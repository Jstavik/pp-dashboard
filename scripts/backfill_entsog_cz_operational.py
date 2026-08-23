"""Jednorázový backfill ENTSOG Nomination/Renomination pro NET4GAS body
(2020-01 -> dnes). Resumable: přeskočí měsíce, které už mají partitioned
soubor (kromě aktuálního, otevřeného měsíce). Log průběžně do souboru
předaného jako první argument, ať přežije i přerušení.

Použití:
    python scripts/backfill_entsog_cz_operational.py [log_path]
"""
import sys
sys.path.insert(0, ".")

from data.entsog_operational import backfill_cz_operational

if __name__ == "__main__":
    log_path = sys.argv[1] if len(sys.argv) > 1 else "entsog_cz_operational_backfill.log"
    backfill_cz_operational(log_path=log_path)
