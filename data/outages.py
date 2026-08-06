import os
import pandas as pd
import streamlit as st

from data.partitioned_store import read_snapshot

OUTAGES_PATH = "data/history/outages.parquet"
OUTAGES_SNAPSHOTS_DIR = "data/history/outages_snapshots"

_EMPTY_ACTIVE_COLS = ["country", "plant_type", "production_resource_name", "start", "end",
                       "nominal_power", "avail_qty", "unavail_mw", "docstatus", "businesstype"]
_EMPTY_DAILY_COLS = ["date", "plant_type", "unavail_mw"]


def _empty_outages(now: pd.Timestamp) -> dict:
    return {
        "active": pd.DataFrame(columns=_EMPTY_ACTIVE_COLS),
        "daily_by_type": pd.DataFrame(columns=_EMPTY_DAILY_COLS),
        "now": now,
    }


@st.cache_data(ttl=300)
def load_outages(country: str) -> dict:
    """Odstávky pro danou zemi — čte sdílený data/history/outages.parquet
    (JEDEN soubor pro všechny země, bez filtru na plant_type — všechny
    typy zdrojů). Vrací aktivní odstávky + denní nedostupnost podle
    plant_type od dneška do konce příštího roku.

    outages.parquet je rolling-window refetch (viz
    scripts/update_gas_history.py::update_outages), ne jednorázový plný
    backfill — nemá historii od začátku roku, jen pohled dopředu."""
    now = pd.Timestamp.now(tz="UTC")
    today = now.normalize()
    next_year_end = pd.Timestamp(year=now.year + 1, month=12, day=31, tz="UTC")

    if not os.path.exists(OUTAGES_PATH):
        return _empty_outages(now)

    df = pd.read_parquet(OUTAGES_PATH)
    df = df[df["country"] == country]
    if df.empty:
        return _empty_outages(now)

    active = df[
        (df["start"] <= now) & (df["end"] >= now) & (df["unavail_mw"] > 0)
    ].copy()

    days = pd.date_range(today, next_year_end, freq="D")
    rows = []
    for day in days:
        active_day = df[(df["start"] <= day) & (df["end"] >= day) & (df["unavail_mw"] > 0)]
        for plant_type, grp in active_day.groupby("plant_type"):
            rows.append({"date": day, "plant_type": plant_type, "unavail_mw": grp["unavail_mw"].sum()})
    daily_by_type = pd.DataFrame(rows, columns=_EMPTY_DAILY_COLS)

    return {"active": active, "daily_by_type": daily_by_type, "now": now}


@st.cache_data(ttl=3600)
def load_outages_snapshot(country: str, days_back: int) -> pd.DataFrame:
    """Denní snapshot odstávek pro danou zemi z před days_back dny — pro
    charts.electricity_outages.fig_outages_delta. Čte
    data/history/outages_snapshots/{YYYY-MM-DD}.parquet (zapisuje
    scripts/update_gas_history.py::update_outages, jednou denně,
    write-once). Prázdný DataFrame, pokud pro daný den snapshot ještě
    neexistuje (historie snapshotů roste od nasazení dopředu)."""
    day = pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=days_back)
    df = read_snapshot(OUTAGES_SNAPSHOTS_DIR, day, fmt="parquet")
    if df.empty:
        return pd.DataFrame(columns=_EMPTY_ACTIVE_COLS)
    return df[df["country"] == country].copy()
