import logging
import requests, os, time
import pandas as pd
from datetime import date, timedelta

from data.partitioned_store import read_partitioned, upsert_partitioned, last_date_partitioned
from config import GIE_ALSI_REVISION_WINDOW_DAYS
from core.cache import ttl_cache
from core.settings import history_path, gie_api_key

log = logging.getLogger(__name__)

LNG_DIR  = history_path("lng_storage")


def _alsi_key() -> str:
    """GIE ALSI klíč jen z env (GIE_API_KEY) — dřív natvrdo v repu."""
    key = gie_api_key()
    if not key:
        raise RuntimeError("GIE_API_KEY není nastavený (env / ~/.config/ppdash/env)")
    return key

# Přidej nové země sem pokud ALSI přidá nové
COUNTRIES_ALSI = [
    "BE","FR","NL","ES","IT","DE",
    "PT","GR","HR","FI","LT","PL"
]


def fetch_lng_all(from_date: date = None, to_date: date = None) -> pd.DataFrame:
    """Stáhne LNG zásobníky ze GIE ALSI API — všechny země + EU.

    from_date/to_date (volitelné) — ALSI API from/to param PODPORUJE
    (ověřeno živě 2026-09-09, stejná platforma jako AGSI storage —
    viz scripts/update_gas_history.py::fetch_gie_all_countries) —
    starší komentář, že "ALSI API nemá časové okno", byl mylný. Bez
    zadání (default) stahuje pořád celou historii jako dřív."""
    all_frames = []
    alsi_key = _alsi_key()

    date_param = ""
    if from_date is not None:
        date_param += f"&from={from_date}"
    if to_date is not None:
        date_param += f"&to={to_date}"

    def fetch_pages(query: str, cc: str) -> list:
        """Všechny stránky pro jeden dotaz. Dřív `break` po poslední
        stránce / chybě / prázdné odpovědi ukončil jen smyčku pokusů a
        stránkování běželo dál (až 29 requestů na zemi)."""
        rows_all = []
        for page in range(1, 30):
            data = None
            for attempt in range(3):
                try:
                    r = requests.get(
                        f"https://alsi.gie.eu/api?{query}{date_param}&size=300&page={page}",
                        headers={"x-key": alsi_key},
                        timeout=20,
                    )
                    data = r.json() if r.status_code == 200 else {}
                    break
                except Exception as e:
                    log.warning(f"ALSI {cc} page {page}: {e}")
                    time.sleep(2)
            rows = (data or {}).get("data", [])
            if not rows:
                break
            for row in rows:
                row["country_code"] = cc
            rows_all.extend(rows)
            if page >= data.get("last_page", 1):
                break
            time.sleep(0.3)
        return rows_all

    for cc in COUNTRIES_ALSI:
        all_frames.extend(fetch_pages(f"country={cc}", cc))
    all_frames.extend(fetch_pages("type=eu", "EU"))

    if not all_frames:
        return pd.DataFrame()

    df = pd.DataFrame(all_frames)
    df["gasDayStart"] = pd.to_datetime(df["gasDayStart"])

    # Rozbal nested inventory a dtmi
    df["inventory_gwh"] = df["inventory"].apply(
        lambda x: float(x["gwh"])
        if isinstance(x, dict) and x.get("gwh") not in ["-", "", None]
        else None
    ) if "inventory" in df.columns else None

    df["dtmi_gwh"] = df["dtmi"].apply(
        lambda x: float(x["gwh"])
        if isinstance(x, dict) and x.get("gwh") not in ["-", "", None]
        else None
    ) if "dtmi" in df.columns else None

    df["full_pct"] = (
        df["inventory_gwh"] / df["dtmi_gwh"] * 100
    ).round(1)

    for col in ["sendOut", "dtrs", "contractedCapacity", "availableCapacity"]:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(",", "."),
                errors="coerce"
            )

    keep = ["gasDayStart", "country_code", "name", "inventory_gwh",
            "dtmi_gwh", "full_pct", "sendOut", "dtrs",
            "contractedCapacity", "availableCapacity", "status"]
    return df[[c for c in keep if c in df.columns]]


def update_lng():
    """LNG terminály — měsíčně partitionované úložiště (viz
    data/partitioned_store.py). fetch_lng_all(from_date, to_date) — ALSI
    API from/to param podporuje (ověřeno živě, viz docstring tam), takže
    se stahuje jen okno [last_date - GIE_ALSI_REVISION_WINDOW_DAYS,
    dnešek], ne celá historie znovu při každém běhu (dřívější chování,
    opraveno 2026-09-09 — starý komentář o "ALSI nemá date param" byl
    mylný)."""
    last_date = last_date_partitioned(LNG_DIR, "gasDayStart", "csv")
    if last_date is not None:
        from_date = (last_date - timedelta(days=GIE_ALSI_REVISION_WINDOW_DAYS)).date()
        log.info(f"LNG ALSI: existující data do {last_date.date()}, stahuji od {from_date}")
    else:
        from_date = None
        log.info("LNG ALSI: nový soubor, plný backfill")

    new_data = fetch_lng_all(from_date=from_date, to_date=date.today())
    if new_data.empty:
        raise RuntimeError("LNG ALSI: žádná data (API nic nevrátilo)")

    touched = upsert_partitioned(new_data, LNG_DIR, "gasDayStart",
                                   ["gasDayStart", "country_code"], fmt="csv")
    written = [(m, n) for m, n, w in touched if w]
    log.info(f"LNG ALSI: {sum(n for _, n in written)} řádků v {len(written)} přepsaných měsících "
             f"({len(touched) - len(written)} beze změny) → {LNG_DIR}/")


@ttl_cache(ttl=3600, maxsize=2)
def load_lng() -> pd.DataFrame:
    df = read_partitioned(LNG_DIR, fmt="csv")
    if not df.empty:
        df["gasDayStart"] = pd.to_datetime(df["gasDayStart"])
    return df
