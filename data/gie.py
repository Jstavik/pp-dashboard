import os
import pandas as pd

from data.partitioned_store import read_partitioned

GIE_ALL_DIR = "data/history/gie_all_storage"
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
    """Načte GIE data z měsíčně partitionovaného úložiště. Fallback na prázdný DataFrame."""
    def _load():
        df = read_partitioned(GIE_ALL_DIR, fmt="csv")
        if not df.empty:
            df["gasDayStart"] = pd.to_datetime(df["gasDayStart"])
        return df
    try:
        import streamlit as st
        return st.cache_data(ttl=3600, show_spinner=False)(_load)()
    except ImportError:
        return _load()
