import os
import requests
import pandas as pd
import streamlit as st
from datetime import date, timedelta

POINTS_CONFIG = {
    "Brandov/Waidhaus (DE)": {"lat": 50.608, "lon": 13.388, "flag": "🇩🇪"},
    "Lanžhot (SK)":          {"lat": 48.722, "lon": 17.044, "flag": "🇸🇰"},
    "Český Těšín (PL)":      {"lat": 49.748, "lon": 18.622, "flag": "🇵🇱"},
    "Zásobníky":             {"lat": 49.750, "lon": 15.800, "flag": "🏭"},
    "Distribuce":            {"lat": 49.400, "lon": 16.200, "flag": "🔵"},
    "Koneční spotřebitelé":  {"lat": 49.100, "lon": 15.500, "flag": "🏠"},
}

def _short_name(s: str) -> str:
    if "Brandov" in s or "Waidhaus" in s: return "Brandov/Waidhaus (DE)"
    if "Lanžhot" in s:                     return "Lanžhot (SK)"
    if "Těšín"   in s or "Cieszyn" in s:   return "Český Těšín (PL)"
    if "Storage" in s or "VGS" in s:       return "Zásobníky"
    if "Distribution" in s:                return "Distribuce"
    if "Final" in s:                        return "Koneční spotřebitelé"
    return s[:35]

def fetch_entsog_flows(days: int = 90) -> pd.DataFrame:
    def _impl(d: int) -> pd.DataFrame:
        end   = date.today()
        start = end - timedelta(days=d)
        url = (
            "https://transparency.entsog.eu/api/v1/aggregateddata"
            f"?from={start}&to={end}"
            "&indicator=Physical%20Flow&periodType=day"
            "&timezone=CET&limit=10000&format=json&countryKey=CZ"
        )
        try:
            resp = requests.get(url, timeout=30)
            df   = pd.DataFrame(resp.json()["aggregateddata"])
            df["value_GWh"] = pd.to_numeric(df["value"], errors="coerce") / 1_000_000
            df["date"]      = pd.to_datetime(df["periodFrom"], utc=True).dt.tz_convert("Europe/Prague").dt.date
            df["point"]     = df["pointsNames"].apply(_short_name)
            entry = df[df["directionKey"]=="entry"].groupby(["date","point"])["value_GWh"].sum()
            exit_ = df[df["directionKey"]=="exit" ].groupby(["date","point"])["value_GWh"].sum()
            pivot = (entry.unstack(fill_value=0) - exit_.unstack(fill_value=0)).fillna(0)
            pivot.index = pd.to_datetime(pivot.index)
            for pt in POINTS_CONFIG:
                if pt not in pivot.columns:
                    pivot[pt] = 0.0
            return pivot
        except Exception:
            return pd.DataFrame()

    return st.cache_data(ttl=300, show_spinner=False)(_impl)(days)


def load_entsog_history(date_from=None, date_to=None) -> pd.DataFrame:
    """Fyzické toky ENTSO-G (data/history/entsog_flows/, měsíčně
    partitionované). date_from/date_to (volitelné) omezí čtení na měsíční
    soubory v daném rozsahu (viz partitioned_store.read_partitioned) —
    stejný vzor jako data/entsog_operational.py::load_eu_operational.

    date_from/date_to jsou SKUTEČNÉ argumenty cachované funkce (ne closure
    proměnné), aby je Streamlit správně zahrnul do cache klíče.

    POZOR: na rozdíl od load_eu_operational (kde okno bez ztráty
    funkčnosti stačí Nominaci) tenhle dataset krmí i víceleté srovnávací
    funkce (app.py tab_season, tab_bar tlačítko "Maximum", tab_lng
    "Roky (sezonnost)" + tlačítko "Max") — ty explicitně potřebují CELOU
    historii, ne jen okno. Volající v app.py proto volá bez argumentů
    (plná historie) jen když je to skutečně potřeba (session_state flag
    nastavený příslušným checkboxem/tlačítkem), jinak s oknovaným
    date_from (viz ENTSOG_FLOWS_DEFAULT_WINDOW_MONTHS)."""
    return st.cache_data(ttl=300, show_spinner=False)(_load_entsog_history)(date_from, date_to)


def _load_entsog_history(date_from=None, date_to=None) -> pd.DataFrame:
    from data.partitioned_store import read_partitioned
    df = read_partitioned("data/history/entsog_flows", fmt="parquet", date_from=date_from, date_to=date_to)
    if not df.empty:
        # entsog_flows teď sdílí úložiště i s Allocation (viz
        # scripts/update_gas_history.py::update_entsog_allocation) —
        # BEZ tohohle filtru by se Allocation řádky namíchaly do
        # fyzických toků a ticho rozbily/zdvojnásobily existující
        # Mapa/Toky/Sezonnost grafy. "indicator" chybí jen u řádků
        # zapsaných PŘED migrací (žádné už by neměly zbýt, ale
        # .isin(["Physical Flow", NaN]) je bezpečná pojistka).
        if "indicator" in df.columns:
            df = df[df["indicator"].isin(["Physical Flow"]) | df["indicator"].isna()]
        df["date"] = pd.to_datetime(df["date"], utc=True)
        return df
    if date_from is None and date_to is None:
        return fetch_entsog_flows(days=90)
    return df


