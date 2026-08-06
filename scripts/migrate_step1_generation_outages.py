"""Jednorázová migrace na sjednocené (country-parametrizované) schéma —
krok 1 refaktoringu.

Nová schémata:
  data/history/generation/<country>/{YYYY-MM}.parquet
      long formát: date, country, source_type, psr_code, mw
  data/history/outages.parquet
      long formát (jeden rolling soubor — odstávky se revidují, denní
      split nedává smysl, viz krok 0): country, plant_type,
      production_resource_name, start, end, unavail_mw, nominal_power,
      avail_qty, docstatus, businesstype

Zdrojová data (nuclear_fr_generation.parquet, hu_generation.parquet,
nuclear_fr_outages.parquet, hu_outages.parquet) zůstávají v repu
nedotčená jako fallback, dokud nebude nová appka ověřená (krok 4).
Update funkce ve scripts/update_gas_history.py zatím i nadále píšou do
STARÝCH souborů — na nové schéma je přepne až krok 2.
"""
import sys
sys.path.insert(0, ".")
import pandas as pd

from data.partitioned_store import upsert_partitioned


def migrate_generation_fr():
    df = pd.read_parquet("data/history/nuclear_fr_generation.parquet")
    df.index.name = "date"
    df = df.reset_index()

    out = pd.DataFrame({
        "date": df["date"],
        "country": "FR",
        "source_type": "Nuclear",
        "psr_code": "B14",
        "mw": df["nuclear_mw"],
    })
    touched = upsert_partitioned(out, "data/history/generation/FR", "date",
                                   ["date", "source_type"], fmt="parquet")
    print(f"nuclear_fr_generation.parquet → generation/FR/: {len(out)} řádků, {len(touched)} měsíčních souborů")


def migrate_generation_hu():
    df = pd.read_parquet("data/history/hu_generation.parquet")
    out = df.copy()
    out.insert(1, "country", "HU")
    out = out[["date", "country", "source_type", "psr_code", "mw"]]

    touched = upsert_partitioned(out, "data/history/generation/HU", "date",
                                   ["date", "source_type"], fmt="parquet")
    print(f"hu_generation.parquet → generation/HU/: {len(out)} řádků, {len(touched)} měsíčních souborů")


def migrate_outages():
    cols = ["country", "plant_type", "production_resource_name", "start", "end",
            "unavail_mw", "nominal_power", "avail_qty", "docstatus", "businesstype"]

    fr = pd.read_parquet("data/history/nuclear_fr_outages.parquet").copy()
    fr.insert(0, "country", "FR")
    fr.insert(1, "plant_type", "Nuclear")
    fr["start"] = pd.to_datetime(fr["start"], utc=True)
    fr["end"] = pd.to_datetime(fr["end"], utc=True)

    hu = pd.read_parquet("data/history/hu_outages.parquet").copy()
    hu.insert(0, "country", "HU")
    hu["start"] = pd.to_datetime(hu["start"], utc=True)
    hu["end"] = pd.to_datetime(hu["end"], utc=True)

    combined = pd.concat([fr[cols], hu[cols]], ignore_index=True)
    combined = combined.sort_values(["country", "production_resource_name", "start"]).reset_index(drop=True)
    combined.to_parquet("data/history/outages.parquet", index=False)
    print(f"nuclear_fr_outages.parquet + hu_outages.parquet → outages.parquet: {len(combined)} řádků "
          f"({len(fr)} FR + {len(hu)} HU)")


if __name__ == "__main__":
    for label, fn in [
        ("Generation FR", migrate_generation_fr),
        ("Generation HU", migrate_generation_hu),
        ("Outages FR+HU", migrate_outages),
    ]:
        print(f"\n=== {label} ===")
        try:
            fn()
        except Exception as e:
            print(f"  CHYBA: {e}")
