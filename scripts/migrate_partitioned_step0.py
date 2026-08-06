"""Jednorázová migrace stávajících monolitických historických souborů do
měsíčně partitionovaného úložiště (viz data/partitioned_store.py, krok 0
refaktoringu). Spustit JEDNOU. Původní soubory zůstávají v repu nedotčené
jako fallback, dokud nebude nová appka ověřená.
"""
import sys
sys.path.insert(0, ".")
import pandas as pd

from data.partitioned_store import upsert_partitioned


def migrate_entsog():
    path = "data/history/entsog_all_flows.parquet"
    df = pd.read_parquet(path)
    key_cols = ["date", "countryKey", "directionKey", "adjacentSystemsKey", "pointsNames"]
    key_cols = [c for c in key_cols if c in df.columns]
    touched = upsert_partitioned(df, "data/history/entsog_flows", "date", key_cols, fmt="parquet")
    print(f"entsog_all_flows.parquet → entsog_flows/: {len(df)} řádků, {len(touched)} měsíčních souborů")


def migrate_gie_all():
    path = "data/history/gie_all_storage.csv"
    df = pd.read_csv(path, parse_dates=["gasDayStart"])
    touched = upsert_partitioned(df, "data/history/gie_all_storage", "gasDayStart",
                                   ["country_code", "gasDayStart"], fmt="csv")
    print(f"gie_all_storage.csv → gie_all_storage/: {len(df)} řádků, {len(touched)} měsíčních souborů")


def migrate_lng():
    path = "data/history/lng_storage.csv"
    df = pd.read_csv(path, parse_dates=["gasDayStart"])
    touched = upsert_partitioned(df, "data/history/lng_storage", "gasDayStart",
                                   ["gasDayStart", "country_code"], fmt="csv")
    print(f"lng_storage.csv → lng_storage/: {len(df)} řádků, {len(touched)} měsíčních souborů")


def migrate_hydro():
    path = "data/history/hydro_reservoirs.csv"
    df = pd.read_csv(path, parse_dates=["date"])
    touched = upsert_partitioned(df, "data/history/hydro_reservoirs", "date",
                                   ["date", "country"], fmt="csv")
    print(f"hydro_reservoirs.csv → hydro_reservoirs/: {len(df)} řádků, {len(touched)} měsíčních souborů")


def migrate_dap_europe():
    path = "data/history/dap_europe.parquet"
    df = pd.read_parquet(path)
    touched = upsert_partitioned(df, "data/history/dap_europe", "date",
                                   ["date", "cc"], fmt="parquet")
    print(f"dap_europe.parquet → dap_europe/: {len(df)} řádků, {len(touched)} měsíčních souborů")


if __name__ == "__main__":
    for label, fn in [
        ("ENTSO-G flows",   migrate_entsog),
        ("GIE storage",     migrate_gie_all),
        ("LNG storage",     migrate_lng),
        ("Hydro reservoirs", migrate_hydro),
        ("DAP Europe",      migrate_dap_europe),
    ]:
        print(f"\n=== {label} ===")
        try:
            fn()
        except Exception as e:
            print(f"  CHYBA: {e}")
