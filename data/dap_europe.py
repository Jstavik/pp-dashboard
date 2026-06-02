import os
import pandas as pd
import streamlit as st

DAP_PATH = "data/history/dap_europe.parquet"

DAP_COUNTRIES = {
    "CZ": "CZ", "DE": "DE_LU", "FR": "FR", "AT": "AT", "SK": "SK",
    "PL": "PL", "HU": "HU", "NL": "NL", "BE": "BE", "ES": "ES",
    "PT": "PT", "IT": "10Y1001A1001A73I", "RO": "10YRO-TEL------P",
    "BG": "10YCA-BULGARIA-R", "GR": "10YGR-HTSO-----Y",
    "RS": "10YCS-SERBIATSOV", "HR": "10YHR-HEP------M",
    "SI": "10YSI-ELES-----O", "CH": "10YCH-SWISSGRIDZ",
    "FI": "10YFI-1--------U", "NO": "10YNO-1--------2",
    "DK": "10YDK-1--------W", "SE": "10Y1001A1001A46L",
}

ISO3 = {
    "CZ": "CZE", "DE": "DEU", "FR": "FRA", "AT": "AUT", "SK": "SVK",
    "PL": "POL", "HU": "HUN", "NL": "NLD", "BE": "BEL", "ES": "ESP",
    "PT": "PRT", "IT": "ITA", "RO": "ROU", "BG": "BGR", "GR": "GRC",
    "RS": "SRB", "HR": "HRV", "SI": "SVN", "CH": "CHE", "FI": "FIN",
    "NO": "NOR", "DK": "DNK", "SE": "SWE",
}

CENTERS = {
    "CZ": (49.8, 15.5), "DE": (51.2, 10.4), "FR": (46.5, 2.5),
    "AT": (47.5, 14.5), "SK": (48.7, 19.5), "PL": (52.0, 19.5),
    "HU": (47.2, 19.3), "NL": (52.3, 5.3),  "BE": (50.5, 4.5),
    "ES": (40.0, -3.5), "PT": (39.5, -8.0), "IT": (42.5, 12.5),
    "RO": (45.8, 24.8), "BG": (42.7, 25.5), "GR": (39.5, 22.0),
    "RS": (44.0, 21.0), "HR": (45.2, 15.5), "SI": (46.1, 14.8),
    "CH": (47.0, 8.3),  "FI": (64.0, 26.0), "NO": (62.0, 10.0),
    "DK": (56.0, 10.0), "SE": (59.0, 15.0),
}


@st.cache_data(ttl=3600)
def load_dap_europe() -> pd.DataFrame:
    if not os.path.exists(DAP_PATH):
        return pd.DataFrame()
    df = pd.read_parquet(DAP_PATH)
    last = df["date"].max()
    return df[df["date"] == last].copy()
