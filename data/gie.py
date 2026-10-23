import pandas as pd

from core.cache import ttl_cache
from core.settings import history_path, gie_api_key
from data.partitioned_store import read_partitioned

GIE_ALL_DIR = history_path("gie_all_storage")
# Klíč GIE AGSI+/ALSI jen z env (GIE_API_KEY) — dřív natvrdo v repu.
GIE_KEY     = gie_api_key()

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

# Sloupce, které appka z GIE CSV čte (zbytek ~15 sloupců se nepoužívá).
GIE_COLUMNS = ["gasDayStart", "country_code"] + list(VARIABLES.keys())


@ttl_cache(ttl=3600, maxsize=2)
def load_gie_all() -> pd.DataFrame:
    """Načte GIE data z měsíčně partitionovaného úložiště. Fallback na prázdný DataFrame."""
    df = read_partitioned(GIE_ALL_DIR, fmt="csv", columns=GIE_COLUMNS)
    if not df.empty:
        df["gasDayStart"] = pd.to_datetime(df["gasDayStart"])
    return df
