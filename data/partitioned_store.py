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


def read_partitioned(base_dir: str, fmt: str = "parquet", date_from=None, date_to=None, row_filter=None) -> pd.DataFrame:
    """Načte a sloučí měsíční soubory v base_dir (data/history/<zdroj>/*.fmt).

    date_from/date_to (volitelné) omezí čtení jen na soubory {YYYY-MM}.fmt
    ležící (byť částečně) v zadaném rozsahu — filtruje se podle NÁZVU
    souboru, ne podle obsahu, takže se soubory mimo okno vůbec neotvírají.
    Bez date_from/date_to (default) čte úplně všechno, beze změny chování
    pro stávající volající.

    row_filter (volitelné, DataFrame -> DataFrame) se aplikuje PER SOUBOR
    hned po přečtení, PŘED concat — pro zdroje, kde je potřeba projít
    celou historii (date-okno nejde použít, viz
    entsog_operational.py::load_eu_operational_open_ended), ale výsledek
    je řádkově malý (jen pár sloupcových hodnot z každého souboru). Drží
    špičkovou paměť na velikosti jednoho souboru + filtrovaného výsledku,
    ne na velikosti celého datasetu."""
    files = sorted(glob.glob(os.path.join(base_dir, f"*.{fmt}")))
    if date_from is not None or date_to is not None:
        lo = pd.Timestamp(date_from).strftime("%Y-%m") if date_from is not None else None
        hi = pd.Timestamp(date_to).strftime("%Y-%m") if date_to is not None else None
        def _in_range(path):
            month = os.path.splitext(os.path.basename(path))[0]
            if lo is not None and month < lo:
                return False
            if hi is not None and month > hi:
                return False
            return True
        files = [f for f in files if _in_range(f)]
    if not files:
        return pd.DataFrame()
    reader = pd.read_parquet if fmt == "parquet" else pd.read_csv
    frames = (reader(f) for f in files)
    if row_filter is not None:
        frames = (row_filter(df) for df in frames)
    return pd.concat(frames, ignore_index=True)


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


def write_daily_snapshot(df: pd.DataFrame, base_dir: str, day: pd.Timestamp = None, fmt: str = "parquet") -> bool:
    """Zapíše celý df jako denní snapshot base_dir/{YYYY-MM-DD}.<fmt> —
    write-once, na rozdíl od upsert_partitioned se jednou zapsaný den UŽ
    NIKDY nepřepisuje (žádný merge, žádná "obsah beze změny" kontrola).
    Pokud dnešní soubor už existuje, no-op (vrátí False) — bezpečné volat
    při každém scheduled běhu, zapíše se jen jednou za den.

    Používá se pro rolling zdroje (odstávky), kde potřebujeme "jak to
    vypadalo před N dny" bez závislosti na git historii (shallow clone na
    produkci by to nemusel mít, navíc koliduje s budoucím úklidem historie).
    """
    day = (day or pd.Timestamp.now(tz="UTC")).normalize()
    os.makedirs(base_dir, exist_ok=True)
    path = os.path.join(base_dir, f"{day.strftime('%Y-%m-%d')}.{fmt}")
    if os.path.exists(path):
        return False
    if fmt == "parquet":
        df.to_parquet(path, index=False)
    else:
        df.to_csv(path, index=False)
    return True


def read_snapshot(base_dir: str, day: pd.Timestamp, fmt: str = "parquet") -> pd.DataFrame:
    """Načte konkrétní denní snapshot base_dir/{YYYY-MM-DD}.<fmt>.
    Prázdný DataFrame, pokud pro daný den snapshot neexistuje."""
    day = pd.Timestamp(day).normalize()
    path = os.path.join(base_dir, f"{day.strftime('%Y-%m-%d')}.{fmt}")
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_parquet(path) if fmt == "parquet" else pd.read_csv(path)


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
    # Měsíční klíč pro groupby se počítá do samostatné (nepersistované)
    # Series — new_data[date_col] se NIKDY nepřepisuje. Zápis musí zachovat
    # PŮVODNÍ dtype volajícího (parquet round-trip je beze ztráty; některé
    # zdroje mají date_col jako obyčejné date objekty, ne Timestamp — např.
    # entsog_capacity periodFrom_dt, dap_europe "date" — a downstream kód
    # je tak i porovnává. Přepsání na tz-aware Timestamp by je tiše rozbilo).
    month_key = pd.to_datetime(new_data[date_col], utc=True)
    months = month_key.dt.strftime("%Y-%m")

    reader = pd.read_parquet if fmt == "parquet" else pd.read_csv
    touched = []

    for month, grp_new in new_data.groupby(months):
        # dedup uvnitř samotného new_data batche — bez tohohle by se
        # duplicity vyskytující se přímo v jednom fetchi (ne jen mezi
        # existing/latest) zapsaly beze změny do NOVÉHO měsíčního
        # souboru (větev "existing is None" níž new_data jinak nijak
        # nefiltruje). Ověřeno na entsog_capacity — API vrací pravé
        # duplicitní řádky v rámci jednoho stažení.
        grp_new = grp_new.drop_duplicates(subset=dedup_subset, keep="last")
        path = os.path.join(base_dir, f"{month}.{fmt}")

        if os.path.exists(path):
            existing = reader(path)
            if fmt == "csv":
                # CSV round-trip ztrácí dtype (date_col se vrátí jako
                # string) — bez sjednocení na Timestamp by
                # drop_duplicates/equals tiše selhávaly na mixed-dtype
                # sloupci a duplicity by se hromadily při každém běhu.
                # Parquet dtype zachovává beze ztráty, takže tohle
                # potřebuje jen CSV — jinak by se přepsal i zdrojům, kde
                # na přesném dtype date_col záleží downstream.
                existing[date_col] = pd.to_datetime(existing[date_col], utc=True)
                grp_new = grp_new.copy()
                grp_new[date_col] = pd.to_datetime(grp_new[date_col], utc=True)
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
