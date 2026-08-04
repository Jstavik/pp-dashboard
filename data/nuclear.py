from entsoe import EntsoePandasClient
import pandas as pd
import streamlit as st
from config import ENTSOE_TOKEN

@st.cache_data(ttl=3600)
def load_nuclear_fr(days_back: int = 0, days_forward: int = 90) -> dict:
    client = EntsoePandasClient(api_key=ENTSOE_TOKEN)
    now = pd.Timestamp.now(tz="Europe/Paris")

    # Odstávky
    start = now - pd.Timedelta(days=1)
    end = now + pd.Timedelta(days=days_forward)
    df_out = client.query_unavailability_of_generation_units(
        "FR", start=start, end=end, docstatus=None
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

    # Timeline po dnech
    days = pd.date_range(now.date(), end.date(), freq="D", tz="Europe/Paris")
    daily = []
    for day in days:
        active_day = latest[
            (latest["start"] <= day) &
            (latest["end"] >= day) &
            (latest["unavail_mw"] > 0)
        ]
        daily.append({
            "date": day,
            "unavail_mw": active_day["unavail_mw"].sum(),
            "units_count": len(active_day),
        })
    df_daily = pd.DataFrame(daily)

    return {
        "active": active,
        "daily": df_daily,
        "total_installed": total_installed,
        "now": now,
    }

@st.cache_data(ttl=86400)
def load_nuclear_fr_generation() -> pd.DataFrame:
    client = EntsoePandasClient(api_key=ENTSOE_TOKEN)
    results = []
    for year in range(2020, pd.Timestamp.now().year + 1):
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
    return pd.concat(results) if results else pd.DataFrame()
