import os
from entsoe import EntsoePandasClient
import pandas as pd
import streamlit as st
from config import ENTSOE_TOKEN

NUCLEAR_FR_GEN_PATH = "data/history/nuclear_fr_generation.parquet"
NUCLEAR_FR_OUT_PATH = "data/history/nuclear_fr_outages.parquet"

_EMPTY_ACTIVE_COLS = ["production_resource_name", "start", "end", "nominal_power",
                      "avail_qty", "unavail_mw", "docstatus", "businesstype"]
_EMPTY_DAILY_COLS = ["date", "unavail_mw", "units_count"]


def _empty_nuclear_fr(now: pd.Timestamp) -> dict:
    return {
        "active": pd.DataFrame(columns=_EMPTY_ACTIVE_COLS),
        "history": pd.DataFrame(columns=_EMPTY_DAILY_COLS),
        "forecast_long": pd.DataFrame(columns=_EMPTY_DAILY_COLS),
        "total_installed": 0.0,
        "now": now,
    }


@st.cache_data(ttl=300)
def load_nuclear_fr() -> dict:
    """Čte odstávky FR z parquet cache (aktualizuje
    scripts/update_gas_history.py::update_nuclear_fr_outages).

    Žádné živé volání ENTSO-E — jen filtrování/agregace nad parquetem,
    proto rychlé i na cold startu.
    """
    now = pd.Timestamp.now(tz="Europe/Paris")
    today = now.normalize()
    year_start = pd.Timestamp(year=now.year, month=1, day=1, tz="Europe/Paris")
    next_year_end = pd.Timestamp(year=now.year + 1, month=12, day=31, tz="Europe/Paris")

    if not os.path.exists(NUCLEAR_FR_OUT_PATH):
        return _empty_nuclear_fr(now)

    latest = pd.read_parquet(NUCLEAR_FR_OUT_PATH)
    if latest.empty:
        return _empty_nuclear_fr(now)

    # Aktivní právě teď
    active = latest[
        (latest["start"] <= now) &
        (latest["end"] >= now) &
        (latest["unavail_mw"] > 0)
    ].copy()

    # Celkový instalovaný výkon (podle bloků přítomných v parquetu)
    all_units = latest.groupby("production_resource_name")["nominal_power"].first()
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
