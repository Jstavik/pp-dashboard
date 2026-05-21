import os
import pandas as pd

GIE_ALL_CSV = "data/history/gie_all_storage.csv"
GIE_KEY     = "628043ec28b2f2395a95f5adad7ec983"

VARIABLES = {
    "full":             ("% plnosti",          "%"),
    "gasInStorage":     ("Plyn v zásobníku",   "TWh"),
    "injection":        ("Injekce",            "GWh/d"),
    "withdrawal":       ("Těžba",              "GWh/d"),
    "netWithdrawal":    ("Net withdrawal",     "GWh/d"),
    "workingGasVolume": ("Working gas volume", "TWh"),
}

FIXED_COUNTRIES = [
    ("CZ", "Česká republika"),
    ("DE", "Německo"),
    ("EU", "Evropská unie"),
    ("NL", "Nizozemsko"),
    ("FR", "Francie"),
    ("SK", "Slovensko"),
]


def load_gie_all() -> pd.DataFrame:
    """Načte GIE data ze souboru. Fallback na prázdný DataFrame."""
    try:
        import streamlit as st
        @st.cache_data(ttl=3600, show_spinner=False)
        def _load():
            if os.path.exists(GIE_ALL_CSV):
                return pd.read_csv(GIE_ALL_CSV, parse_dates=["gasDayStart"])
            return pd.DataFrame()
        return _load()
    except ImportError:
        if os.path.exists(GIE_ALL_CSV):
            return pd.read_csv(GIE_ALL_CSV, parse_dates=["gasDayStart"])
        return pd.DataFrame()
