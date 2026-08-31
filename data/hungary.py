import os
import pandas as pd
import streamlit as st

HU_OUTAGES_PATH = "data/history/hu_outages.parquet"

_EMPTY_ACTIVE_COLS = ["production_resource_name", "plant_type", "start", "end",
                       "nominal_power", "avail_qty", "unavail_mw", "docstatus", "businesstype"]
_EMPTY_DAILY_COLS = ["date", "plant_type", "unavail_mw"]


@st.cache_data(ttl=300)
def load_hu_outages() -> dict:
    """Odstávky HU podle typu zdroje — čte parquet cache (update_hu_outages),
    vrací aktivní odstávky + denní nedostupnost rozpadem podle plant_type od
    dneška do konce příštího roku.

    Na rozdíl od FR nemá parquet historii od začátku roku (update_hu_outages
    stahuje jen rolling window kolem "teď", ne jednorázový plný backfill),
    takže tady je jen pohled dopředu — žádná "historie" větev."""
    now = pd.Timestamp.now(tz="Europe/Budapest")
    today = now.normalize()
    next_year_end = pd.Timestamp(year=now.year + 1, month=12, day=31, tz="Europe/Budapest")

    empty = {
        "active": pd.DataFrame(columns=_EMPTY_ACTIVE_COLS),
        "daily_by_type": pd.DataFrame(columns=_EMPTY_DAILY_COLS),
        "now": now,
    }
    if not os.path.exists(HU_OUTAGES_PATH):
        return empty

    latest = pd.read_parquet(HU_OUTAGES_PATH)
    if latest.empty:
        return empty

    active = latest[
        (latest["start"] <= now) & (latest["end"] >= now) & (latest["unavail_mw"] > 0)
    ].copy()

    days = pd.date_range(today, next_year_end, freq="D")
    rows = []
    for day in days:
        active_day = latest[
            (latest["start"] <= day) & (latest["end"] >= day) & (latest["unavail_mw"] > 0)
        ]
        for plant_type, grp in active_day.groupby("plant_type"):
            rows.append({"date": day, "plant_type": plant_type, "unavail_mw": grp["unavail_mw"].sum()})
    daily_by_type = pd.DataFrame(rows, columns=_EMPTY_DAILY_COLS)

    return {"active": active, "daily_by_type": daily_by_type, "now": now}
