"""Obecná vrstva pro append-only historické zdroje (data se zpětně nemění).

Problém: jeden rostoucí soubor (parquet i CSV) se při každé aktualizaci celý
přepíše a git ho commitne jako novou binární verzi — u velkých/často
aktualizovaných zdrojů roste .git nelineárně (desítky až stovky MB na
zdroj za pár měsíců), bez ohledu na formát; ukázalo se, že CSV soubory
s plným sort_values()+to_csv() na celou historii trpí stejným (u nás i
horším) problémem jako parquet.

Řešení: rozdělit historii na měsíční soubory. Jakmile je měsíc uzavřený
(nový běh do něj už nepíše), jeho soubor se v gitu commitne jednou a zůstane
navždy beze změny — roste jen aktuální (otevřený) měsíc.

Zdroje, které se ZPĚTNĚ MĚNÍ (odstávky a jejich revize), tímhle vzorem
neřešíme — tam je rolling-window přepis nevyhnutelný.
"""
import glob
import os
import pandas as pd


def read_partitioned(base_dir: str, fmt: str = "parquet") -> pd.DataFrame:
    """Načte a sloučí všechny měsíční soubory v base_dir (data/history/<zdroj>/*.fmt)."""
    files = sorted(glob.glob(os.path.join(base_dir, f"*.{fmt}")))
    if not files:
        return pd.DataFrame()
    reader = pd.read_parquet if fmt == "parquet" else pd.read_csv
    return pd.concat((reader(f) for f in files), ignore_index=True)


def last_date_partitioned(base_dir: str, date_col: str, fmt: str = "parquet"):
    """Vrátí nejnovější datum v base_dir bez čtení celé historie — stačí
    přečíst poslední (lexikograficky největší, tedy nejnovější) měsíční
    soubor podle názvu {YYYY-MM}.<fmt>."""
    files = sorted(glob.glob(os.path.join(base_dir, f"*.{fmt}")))
    if not files:
        return None
    reader = pd.read_parquet if fmt == "parquet" else pd.read_csv
    df = reader(files[-1])
    if df.empty:
        return None
    return pd.to_datetime(df[date_col], utc=True).max()


def upsert_partitioned(
    new_data: pd.DataFrame,
    base_dir: str,
    date_col: str,
    dedup_subset: list,
    fmt: str = "parquet",
) -> list:
    """Zmerguje new_data do měsíčně partitionovaných souborů v base_dir.

    Přepíše JEN měsíce, které se v new_data vyskytují — starší uzavřené
    měsíce zůstanou nedotčené (a tedy negenerují novou binární verzi v gitu).
    Pokud se výsledný obsah měsíce po mergi nezmění, soubor se nepřepisuje
    vůbec (žádný git diff) — důležité pro zdroje, co při každém běhu
    přeposílají širší okno, než je skutečně nové.

    Vrací [(měsíc, počet řádků po merge, zapsáno: bool), ...] pro logging.
    """
    if new_data.empty:
        return []
    os.makedirs(base_dir, exist_ok=True)

    new_data = new_data.copy()
    new_data[date_col] = pd.to_datetime(new_data[date_col], utc=True)
    months = new_data[date_col].dt.strftime("%Y-%m")

    reader = pd.read_parquet if fmt == "parquet" else pd.read_csv
    touched = []

    for month, grp_new in new_data.groupby(months):
        path = os.path.join(base_dir, f"{month}.{fmt}")

        if os.path.exists(path):
            existing = reader(path)
            # CSV round-trip ztrácí dtype (date_col se vrátí jako string) —
            # bez sjednocení na Timestamp by drop_duplicates/equals tiše
            # selhávaly na mixed-dtype sloupci a duplicity by se hromadily
            # při každém běhu. Parquet dtype zachovává, ale sjednocení
            # neškodí a chová se stejně pro oba formáty.
            existing[date_col] = pd.to_datetime(existing[date_col], utc=True)
            combined = pd.concat([existing, grp_new], ignore_index=True)
            combined = combined.drop_duplicates(subset=dedup_subset, keep="last")
        else:
            existing = None
            combined = grp_new

        combined = combined.sort_values(dedup_subset).reset_index(drop=True)

        if existing is not None:
            existing_sorted = existing.sort_values(dedup_subset).reset_index(drop=True)
            if existing_sorted.equals(combined):
                touched.append((month, len(combined), False))
                continue

        if fmt == "parquet":
            combined.to_parquet(path, index=False)
        else:
            combined.to_csv(path, index=False)
        touched.append((month, len(combined), True))

    return touched
