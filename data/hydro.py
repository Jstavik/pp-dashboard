import pandas as pd

from config import year_color  # noqa: F401 — re-export pro charts/hydro.py
from core.cache import ttl_cache
from core.settings import history_path
from data.partitioned_store import read_partitioned

HYDRO_DIR = history_path("hydro_reservoirs")

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


@ttl_cache(ttl=3600, maxsize=2)
def load_hydro() -> pd.DataFrame:
    df = read_partitioned(HYDRO_DIR, fmt="csv")
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], utc=True)
    return df
