"""Jednorázová migrace GASSCO nominací z plného CSV přepisu na měsíčně
partitionované úložiště (krok 2 pokračování — viz
scripts/migrate_partitioned_step0.py pro první vlnu zdrojů).

update_gassco() dřív při KAŽDÉM běhu přepisoval celý
data/history/gassco_nominations.csv čerstvě staženým oknem z GASSCO API
(endpoint /ch/2Y/{id}) — funkční jen do té míry, do jaké to okno
skutečně obsahuje celou historii (dnes ano, 2020-10 → dnes, ale nic to
negarantuje do budoucna). upsert_partitioned tohle riziko odstraňuje:
uzavřené měsíce se po zápisu už nepřepisují.

Zdrojový soubor (gassco_nominations.csv) zůstává v repu nedotčený jako
fallback, dokud nebude nová appka ověřená — stejná konvence jako
migrate_partitioned_step0.py.
"""
import sys
sys.path.insert(0, ".")
import pandas as pd

from data.partitioned_store import upsert_partitioned


def migrate_gassco_nominations():
    path = "data/history/gassco_nominations.csv"
    df = pd.read_csv(path, parse_dates=["date"])
    df["date"] = pd.to_datetime(df["date"], utc=True)
    # Sjednoceno na stejné zaokrouhlení jako data/gassco.py::fetch_gassco_nominations()
    # (round(3) tam přidán souběžně s touhle migrací) — jinak by první
    # živý update_gassco() po migraci označil všechny měsíce za změněné
    # jen kvůli tomuhle jednorázovému rozdílu v přesnosti, ne kvůli reálným datům.
    df["value_GWh"] = df["value_GWh"].round(3)
    touched = upsert_partitioned(df, "data/history/gassco_nominations", "date",
                                   ["point", "date"], fmt="csv")
    print(f"gassco_nominations.csv → gassco_nominations/: {len(df)} řádků, {len(touched)} měsíčních souborů")


if __name__ == "__main__":
    migrate_gassco_nominations()
