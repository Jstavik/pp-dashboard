"""Jednorázový PLNÝ backfill ENTSOG Allocation (celá historie, 2020 →
dnes). Viz docstring u backfill_entsog_allocation() v update_gas_history.py
pro vysvětlení, proč tohle NENÍ totéž co scheduled update_entsog_allocation().

Použití:
    python scripts/backfill_entsog_allocation.py [log_path] [start YYYY-MM-DD] [end YYYY-MM-DD]
"""
import sys
from datetime import date
sys.path.insert(0, ".")

from scripts.update_gas_history import backfill_entsog_allocation, HISTORY_START

if __name__ == "__main__":
    log_path = sys.argv[1] if len(sys.argv) > 1 else "entsog_allocation_backfill.log"
    start = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else HISTORY_START
    end = date.fromisoformat(sys.argv[3]) if len(sys.argv) > 3 else date.today()
    backfill_entsog_allocation(start=start, end=end, log_path=log_path)
