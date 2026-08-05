import os
from entsoe import EntsoePandasClient
import pandas as pd
import streamlit as st
from config import ENTSOE_TOKEN

NUCLEAR_FR_GEN_PATH = "data/history/nuclear_fr_generation.parquet"

@st.cache_data(ttl=3600)
def load_nuclear_fr() -> dict:
    client = EntsoePandasClient(api_key=ENTSOE_TOKEN)
    now = pd.Timestamp.now(tz="Europe/Paris")
    today = now.normalize()
    year_start = pd.Timestamp(year=now.year, month=1, day=1, tz="Europe/Paris")
    next_year_end = pd.Timestamp(year=now.year + 1, month=12, day=31, tz="Europe/Paris")

    # Odstávky — od začátku aktuálního roku (historie) do konce příštího roku (predikce)
    df_out = client.query_unavailability_of_generation_units(
        "FR", start=year_start, end=next_year_end, docstatus=None
    )
    nuclear = df_out[df_out["plant_type"] == "Nuclear"].copy()
    nuclear["nominal_power"] = pd.to_numeric(nuclear["nominal_power"], errors="coerce").fillna(0)
    nuclear["avail_qty"] = pd.to_numeric(nuclear["avail_qty"], errors="coerce").fillna(0)

    # Nejnovější revision pro každý blok+interval
    latest = nuclear.sort_values("revision").groupby(
        ["production_resource_name", "start", "end"]
    ).last().reset_index()
    latest = latest[latest["docstatus"] != "Cancelled"]
    latest["unavail_mw"] = latest["nominal_power"] - latest["avail_qty"]

    # Aktivní právě teď
    active = latest[
        (latest["start"] <= now) &
        (latest["end"] >= now) &
        (latest["unavail_mw"] > 0)
    ].copy()

    # Celkový instalovaný výkon
    all_units = nuclear.groupby("production_resource_name")["nominal_power"].first()
    all_units = pd.to_numeric(all_units, errors="coerce").fillna(0)
    total_installed = all_units.sum()

    def _daily_unavailability(day_range: pd.DatetimeIndex) -> pd.DataFrame:
        rows = []
        for day in day_range:
            active_day = latest[
                (latest["start"] <= day) &
                (latest["end"] >= day) &
                (latest["unavail_mw"] > 0)
            ]
            rows.append({
                "date": day,
                "unavail_mw": active_day["unavail_mw"].sum(),
                "units_count": len(active_day),
            })
        return pd.DataFrame(rows)

    # Historie: od začátku roku do dneška
    history_days = pd.date_range(year_start, today, freq="D")
    df_history = _daily_unavailability(history_days)

    # Predikce: od zítra do konce příštího roku
    forecast_days = pd.date_range(today + pd.Timedelta(days=1), next_year_end, freq="D")
    df_forecast_long = _daily_unavailability(forecast_days)

    return {
        "active": active,
        "history": df_history,
        "forecast_long": df_forecast_long,
        "total_installed": total_installed,
        "now": now,
    }

@st.cache_data(ttl=86400)
def load_nuclear_fr_generation() -> pd.DataFrame:
    current_year = pd.Timestamp.now().year

    if os.path.exists(NUCLEAR_FR_GEN_PATH):
        df_cached = pd.read_parquet(NUCLEAR_FR_GEN_PATH)
        if not df_cached.empty and df_cached.index.max().year == current_year:
            return df_cached

    client = EntsoePandasClient(api_key=ENTSOE_TOKEN)
    results = []
    for year in range(2020, current_year + 1):
        try:
            start = pd.Timestamp(f"{year}-01-01", tz="Europe/Paris")
            end = pd.Timestamp(f"{year}-12-31", tz="Europe/Paris")
            df = client.query_generation("FR", start=start, end=end, psr_type="B14")
            df.columns = ["nuclear_mw"]
            df["year"] = year
            df["day_of_year"] = df.index.tz_convert("Europe/Paris").day_of_year
            results.append(df)
        except Exception as e:
            print(f"FR generation {year}: {e}")
    df_result = pd.concat(results) if results else pd.DataFrame()

    if not df_result.empty:
        df_result.to_parquet(NUCLEAR_FR_GEN_PATH)

    return df_result
