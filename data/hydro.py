import os
import pandas as pd

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


def year_color(year: int) -> str:
    """Dynamická barva roku — aktuální rok vždy zelený."""
    PALETTE = [
        "#BDBDBD", "#90A4AE", "#42A5F5", "#1565C0",
        "#FF8F00", "#C62828", "#AD1457", "#6A1B9A",
    ]
    current = pd.Timestamp.now().year
    if year == current:
        return "#2E7D32"
    idx = (current - year - 1) % len(PALETTE)
    return PALETTE[idx]


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
