"""Zpětně kompatibilní obal — logika se přesunula do core/updaters.py,
orchestrace do scripts/update_all.py (systemd timer).

Zůstává kvůli .github/workflows/update_gas.yml (na main) a skriptům, které
odsud importují (scripts/backfill_entsog_allocation.py). Chování jako
dřív: spustí všechny "plynové + elektro" úlohy (bez COT a Energy-Charts)
a VŽDY skončí exit kódem 0 — workflow po něm commituje data a chyba
jedné úlohy nesmí zahodit výsledky ostatních. Pro provoz použij
scripts/update_all.py (vrací skutečné exit kódy).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.updaters import (  # noqa: E402,F401 — re-export pro staré importy
    HISTORY_START, fetch_all_pages, update_entsog, update_entsog_allocation,
    backfill_entsog_allocation, fetch_gie_all_countries, update_gie_all,
    update_hydro, update_dap_europe, update_generation, update_outages,
)

if __name__ == "__main__":
    from scripts.update_all import main
    main(["--skip", "cot,energy_charts", "--no-lock"])
    sys.exit(0)
