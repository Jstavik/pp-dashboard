import pandas as pd
import streamlit as st

from data.partitioned_store import read_partitioned

_EMPTY_GENERATION_COLS = ["date", "country", "source_type", "psr_code", "mw", "year", "day_of_year"]


@st.cache_data(ttl=3600)
def load_generation(country: str, start: pd.Timestamp = None, end: pd.Timestamp = None) -> pd.DataFrame:
    """Výroba podle zdroje pro danou zemi — long formát (date, country,
    source_type, psr_code, mw) ze sjednoceného partitionovaného úložiště
    (data/history/generation/<country>/), doplněné o year/day_of_year pro
    sezonní graf (počítané v místní časové zóně dané země).

    start/end (volitelné) filtrují na [start, end] podle "date" — pokud
    nejsou tz-aware, berou se jako UTC (stejně jako uložený sloupec).
    Předávají se i do read_partitioned jako date_from/date_to (hrubý
    filtr na úrovni měsíčních souborů, ne až po přečtení celého
    datasetu) — jinak by start/end omezovaly jen VÝSLEDEK, ne špičkovou
    paměť při čtení, což byl celý smysl date-scopingu (viz
    entsog_operational.py::load_eu_operational)."""
    from config import COUNTRY_TIMEZONES

    df = read_partitioned(f"data/history/generation/{country}", fmt="parquet",
                           date_from=start, date_to=end)
    if df.empty:
        return pd.DataFrame(columns=_EMPTY_GENERATION_COLS)

    if start is not None:
        start = pd.Timestamp(start)
        start = start.tz_localize("UTC") if start.tzinfo is None else start
        df = df[df["date"] >= start]
    if end is not None:
        end = pd.Timestamp(end)
        end = end.tz_localize("UTC") if end.tzinfo is None else end
        df = df[df["date"] <= end]

    tz = COUNTRY_TIMEZONES.get(country, "UTC")
    local = df["date"].dt.tz_convert(tz)
    df = df.copy()
    df["year"] = local.dt.year
    df["day_of_year"] = local.dt.dayofyear
    # category dtype pro sloupce s málo unikátními hodnotami — source_type/
    # psr_code/country opakované přes miliony řádků jsou jako plain string
    # sloupce řádově dražší v paměti než jejich pár unikátních hodnot
    # (stejný důvod jako entsog_operational.py::_CATEGORY_COLS). "country"
    # má v tomhle datasetu jen JEDNU hodnotu (per-country partitioned
    # adresář), a i tak jako object dtype stojí ~200-230MB na zemi —
    # jako category prakticky nic bez ohledu na počet řádků. Změřeno
    # naživo: 2769.5MB → 255.1MB napříč 7 zeměmi (-91 %).
    for col in ("country", "source_type", "psr_code"):
        if col in df.columns:
            df[col] = df[col].astype("category")
    return df


def available_source_types(df_gen: pd.DataFrame) -> list:
    """Zdroje, které daná země reálně produkuje (podle df_gen), seřazené
    podle GEN_STACK_ORDER — pro selectbox v sezonním grafu. Na rozdíl od
    config.PSR_TYPES (obecná paleta) obsahuje jen zdroje, co se v datech
    skutečně vyskytují."""
    from config import GEN_STACK_ORDER, PSR_CODE_BY_SOURCE_TYPE

    if df_gen.empty:
        return []

    def _key(source_type):
        code = PSR_CODE_BY_SOURCE_TYPE.get(source_type)
        return GEN_STACK_ORDER.index(code) if code in GEN_STACK_ORDER else 999

    return sorted(df_gen["source_type"].unique().tolist(), key=_key)
