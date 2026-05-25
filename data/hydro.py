import os
import pandas as pd
from config import year_color

HYDRO_CSV = "data/history/hydro_reservoirs.csv"

HYDRO_GRID_COUNTRIES = ["NO", "ES", "FR", "IT", "AT", "RO"]

HYDRO_COUNTRY_NAMES = {
    "FR": "Francie",    "AT": "Rakousko",           "CH": "Švýcarsko",
    "ES": "Španělsko",  "PT": "Portugalsko",         "IT": "Itálie",
    "NO": "Norsko",     "SE": "Švédsko",             "FI": "Finsko",
    "RO": "Rumunsko",   "BG": "Bulharsko",           "GR": "Řecko",
    "HR": "Chorvatsko", "SI": "Slovinsko",           "RS": "Srbsko",
    "ME": "Černá Hora", "MK": "Severní Makedonie",
    "AL": "Albánie",    "LT": "Litva",               "LV": "Lotyšsko",
}


def year_width(year: int) -> float:
    return 2.5 if year == pd.Timestamp.now().year else 1.5


def load_hydro() -> pd.DataFrame:
    try:
        import streamlit as st
        @st.cache_data(ttl=3600, show_spinner=False)
        def _load():
            if os.path.exists(HYDRO_CSV):
                df = pd.read_csv(HYDRO_CSV, parse_dates=["date"])
                df["date"] = pd.to_datetime(df["date"], utc=True)
                return df
            return pd.DataFrame()
        return _load()
    except ImportError:
        if os.path.exists(HYDRO_CSV):
            df = pd.read_csv(HYDRO_CSV, parse_dates=["date"])
            df["date"] = pd.to_datetime(df["date"], utc=True)
            return df
        return pd.DataFrame()
