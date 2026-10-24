"""Elektřina ČR: ENTSO-E z úložiště místo živého API (DATA_AUDIT, doporučení 1).

Stránka Elektřina dřív při každém otevření (cache 15–60 min) volala ENTSO-E
pro data, která úlohy entsoe_* ukládají: DA ceny CZ (dnes/zítra), prognózu
zatížení D-1 a prognózu větru/slunce. Teď se čtou z úložiště ve STEJNÉM
tvaru, jaký vracely živé funkce v data/entsoe.py (grafy beze změny).

Živé API se zavolá jen jako záloha, když v úložišti daný den chybí (např.
zítřejší ceny před odpoledním během ppdash-update-entsoe-prices nebo před
prvním backfillem) — `source()` to hlásí do stavu stránky.

Živě dál (nejsou v úložišti nebo jsou potřeba po minutách): systémová
odchylka a její cena, výroba podle zdrojů za dnešek, odstávky PU/GU
(úložiště má jen GU a 1× denně), aktivační ceny, rezervy.
"""
import logging
import os

import pandas as pd

from core.cache import ttl_cache

log = logging.getLogger(__name__)
TZ = "Europe/Prague"
_SOURCES: dict = {}  # druh dat → "úložiště" | "živě" (poslední použitý zdroj, pro stavový řádek)


def source(kind: str) -> str:
    return _SOURCES.get(kind, "—")


def _read(base: str, area: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    from data.partitioned_store import read_partitioned
    d = os.path.join(base, area)
    if not os.path.isdir(d):
        return pd.DataFrame()
    df = read_partitioned(d, fmt="parquet", date_from=start.tz_convert("UTC").date(),
                          date_to=end.tz_convert("UTC").date())
    if df.empty:
        return df
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert(TZ)
    return df[(df["time"] >= start) & (df["time"] < end)]


@ttl_cache(ttl=300, maxsize=4)
def stored_dap(day_offset: int = 0) -> pd.Series:
    """DA cena CZ pro den (dnes + offset) z entsoe_prices — tvar jako fetch_dap
    (Series dap_EUR_MWh, Praha, hodinové ceny rozepsané na 15 min)."""
    from data.entsoe_history import PRICES_DIR
    start = pd.Timestamp.now(tz=TZ).normalize() + pd.Timedelta(days=day_offset)
    end = start + pd.Timedelta(days=1)
    df = _read(PRICES_DIR, "CZ", start, end)
    if df.empty:
        return pd.Series(dtype=float, name="dap_EUR_MWh")
    s = df.drop_duplicates("time", keep="last").set_index("time")["price"].sort_index().rename("dap_EUR_MWh")
    if len(s) <= 25:
        idx_15 = pd.date_range(start=start, periods=96, freq="15min", tz=TZ)
        s = s.reindex(idx_15, method="ffill")
    return s.dropna()


def dap(day_offset: int = 0) -> pd.Series:
    s = stored_dap(day_offset)
    if not s.empty:
        _SOURCES[f"dap{day_offset}"] = "úložiště"
        return s
    from data.entsoe import fetch_dap
    _SOURCES[f"dap{day_offset}"] = "živě"
    return fetch_dap(day_offset)


@ttl_cache(ttl=300, maxsize=2)
def stored_load_forecast() -> pd.Series:
    """Prognóza zatížení CZ (D-1) dnes + zítra — tvar jako v fetch_entsoe_data (forecast_MW)."""
    from data.entsoe_history import LOAD_DIR
    start = pd.Timestamp.now(tz=TZ).normalize()
    df = _read(LOAD_DIR, "CZ", start, start + pd.Timedelta(days=2))
    if df.empty or "kind" not in df:
        return pd.Series(dtype="float64", name="forecast_MW")
    df = df[df["kind"].astype(str) == "forecast"].drop_duplicates("time", keep="last")
    return df.set_index("time")["mw"].sort_index().astype("float64").rename("forecast_MW")


def load_forecast() -> pd.Series:
    s = stored_load_forecast()
    if not s.empty:
        _SOURCES["load_fc"] = "úložiště"
        return s
    _SOURCES["load_fc"] = "živě"
    return _live_load_forecast()


@ttl_cache(ttl=60 * 30, disk=True)
def _live_load_forecast() -> pd.Series:
    from data.entsoe import _get_client
    start = pd.Timestamp.now(tz=TZ).normalize()
    try:
        s = _get_client().query_load_forecast("CZ", start=start, end=start + pd.Timedelta(days=2))
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        return s.rename("forecast_MW")
    except Exception as e:
        log.warning("prognóza zatížení živě: %s", e)
        return pd.Series(dtype="float64", name="forecast_MW")


@ttl_cache(ttl=300, maxsize=2)
def stored_wind_solar_forecast() -> pd.DataFrame:
    """Prognóza větru/slunce CZ dnes + zítra — tvar jako fetch_wind_solar_forecast
    (sloupce = typ zdroje, index Praha)."""
    from data.entsoe_history import WS_DIR
    start = pd.Timestamp.now(tz=TZ).normalize()
    df = _read(WS_DIR, "CZ", start, start + pd.Timedelta(days=2))
    if df.empty:
        return pd.DataFrame()
    w = df.pivot_table(index="time", columns="psr", values="mw", aggfunc="last").sort_index()
    w.columns = [str(c) for c in w.columns]
    w.columns.name = None
    w.index.name = None
    return w


def wind_solar_forecast() -> pd.DataFrame:
    w = stored_wind_solar_forecast()
    if not w.empty:
        _SOURCES["ws"] = "úložiště"
        return w
    from data.entsoe import fetch_wind_solar_forecast
    _SOURCES["ws"] = "živě"
    return fetch_wind_solar_forecast()
