"""Počasí — Open-Meteo (data/weather.py): pozorování ERA5 vs. normál,
předpověď modelů (ECMWF IFS/AIFS, GFS, ICON) + ensemble rozptyl,
HDD/CDD, změna předpovědi proti včerejšku, odkazy na mapy."""
import os

import numpy as np
import pandas as pd

from core.cache import ttl_cache
from core.settings import history_path
from data.partitioned_store import read_partitioned
from web.registry import page, tab, chart
from web.util import table

PAGE = "pocasi"
page(PAGE, "Počasí", "🌦")

MODEL_LABELS = {"ecmwf_ifs025": "ECMWF IFS", "ecmwf_aifs025_single": "ECMWF AIFS", "ecmwf_aifs025": "ECMWF AIFS",
                "gfs_seamless": "GFS", "gfs025": "GFS", "icon_seamless": "ICON", "best_match": "best match"}
MODEL_COLORS = {"ecmwf_ifs025": "#1565C0", "ecmwf_aifs025_single": "#00ACC1", "ecmwf_aifs025": "#00ACC1",
                "gfs_seamless": "#E53935", "gfs025": "#E53935", "icon_seamless": "#43A047"}


def _w():
    import data.weather as w
    return w


@ttl_cache(ttl=600, maxsize=8)
def _daily(region: str) -> pd.DataFrame:
    df = read_partitioned(os.path.join(_w().DAILY_DIR, region), fmt="parquet")
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], utc=True)
    return df


@ttl_cache(ttl=600, maxsize=8)
def _fc(region: str) -> pd.DataFrame:
    lo = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=3)
    df = read_partitioned(os.path.join(_w().FC_DIR, region), fmt="parquet", date_from=lo)
    if not df.empty:
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df = df[df["time"] >= lo]
    return df


@ttl_cache(ttl=600, maxsize=8)
def _fc_daily(region: str) -> pd.DataFrame:
    lo = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=40)
    df = read_partitioned(os.path.join(_w().FC_DAILY_DIR, region), fmt="parquet", date_from=lo)
    if not df.empty:
        df["issued"] = pd.to_datetime(df["issued"], utc=True)
        df["date"] = pd.to_datetime(df["date"], utc=True)
    return df


def _regions() -> list:
    base = history_path("weather_daily")
    fc = history_path("weather_fc")
    found = set()
    for b in (base, fc):
        if os.path.isdir(b):
            found |= {d for d in os.listdir(b) if os.path.isdir(os.path.join(b, d))}
    return [r for r in _w().REGIONS if r in found]


def _state(p, var: str):
    w = _w()
    regions = [r for r in _regions() if var in w.REGIONS[r]]
    if not regions:
        return None
    region = p.str("region", regions[0], choices=regions)
    return {"var": var, "regions": regions, "region": region,
            "names": {r: w.REGIONS[r]["label"] for r in w.REGIONS}, "unit": w.VAR_UNITS[var],
            "unit_h": w.VAR_UNITS_H[var], "label": w.VAR_LABELS[var], "query": p.query()}


def _latest_daily_fc(region: str, var: str) -> pd.DataFrame:
    d = _fc_daily(region)
    if d.empty:
        return d
    d = d[d["var"] == var]
    last = d["issued"].max()
    return d[d["issued"] == last]


def _normal(region: str, var: str) -> pd.DataFrame:
    return _w().normals(_daily(region), var)


def _with_normal(df: pd.DataFrame, nrm: pd.DataFrame) -> pd.DataFrame:
    doy = df["date"].dt.tz_convert("Europe/Prague").dt.dayofyear
    m = nrm.set_index("doy")["mean"]
    return df.assign(normal=doy.map(m).values)


def _outlook_table(s) -> dict:
    """Příštích 7/14 dní po modelech: průměr, odchylka od normálu (a HDD)."""
    w = _w()
    fc = _latest_daily_fc(s["region"], s["var"])
    nrm = _normal(s["region"], s["var"])
    if fc.empty:
        return None
    today = pd.Timestamp.now(tz="Europe/Prague").normalize()
    rows = []
    for model, g in fc.groupby("model"):
        g = _with_normal(g, nrm) if not nrm.empty else g.assign(normal=np.nan)
        loc = g["date"].dt.tz_convert("Europe/Prague")
        for days in (7, 14):
            sel = g[(loc >= today) & (loc < today + pd.DateOffset(days=days))]
            if sel.empty:
                continue
            r = {"model": MODEL_LABELS.get(model, model), "okno": f"{days} dní",
                 "průměr": sel["value"].mean(), "normál": sel["normal"].mean(),
                 "odchylka": sel["value"].mean() - sel["normal"].mean(), "dní": len(sel)}
            if s["var"] == "temp":
                r["HDD"] = w.hdd(sel["value"]).sum()
                r["HDD normál"] = w.hdd(sel["normal"]).sum()
            rows.append(r)
    if not rows:
        return None
    cols = [("model", "Model", None), ("okno", "Okno", None), ("průměr", f"Průměr [{s['unit']}]", ",.2f"),
            ("normál", "Normál", ",.2f"), ("odchylka", "Odchylka", "+,.2f"), ("dní", "Dní", None)]
    if s["var"] == "temp":
        cols += [("HDD", "HDD", ",.0f"), ("HDD normál", "HDD normál", ",.0f")]
    return table(pd.DataFrame(rows), cols)


def _tab(var):
    def render(p):
        s = _state(p, var)
        if s is None:
            return {"empty": True}
        s["outlook"] = _outlook_table(s)
        s["past"] = p.int("past", 45, 7, 400)
        nrm = _normal(s["region"], var)
        s["normal_years"] = nrm["years"].iloc[0] if not nrm.empty else ""
        return s
    return render


for _var, _key, _title in (("temp", "teplota", "Teplota a HDD"), ("wind100", "vitr", "Vítr 100 m"),
                           ("solar", "slunce", "Slunce"), ("precip", "srazky", "Srážky (Nordic hydro)")):
    tab(PAGE, _key, _title)(_tab(_var))


def _chart_outlook(var):
    def fn(p):
        from charts.weather import fig_outlook
        s = _state(p, var)
        if s is None:
            return None
        obs = _daily(s["region"])
        obs = obs[obs["var"] == var] if not obs.empty else obs
        lo = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=int(p.int("past", 45, 7, 400)))
        obs = obs[obs["date"] >= lo] if not obs.empty else obs
        fc = _latest_daily_fc(s["region"], var)
        ens = _w().load_ens(s["region"])
        if not ens.empty:
            ens = ens[ens["var"] == var]
        if obs.empty and fc.empty:
            return None
        return fig_outlook(obs, fc, ens, _normal(s["region"], var), var, f"{s['label']} — {s['names'][s['region']]}",
                           s["unit"], MODEL_LABELS, MODEL_COLORS)
    return fn


def _chart_hourly(var):
    def fn(p):
        from charts.weather import fig_hourly
        s = _state(p, var)
        if s is None:
            return None
        fc = _fc(s["region"])
        if fc.empty:
            return None
        fc = fc[fc["var"] == var]
        ens = _w().load_ens(s["region"])
        ens = ens[ens["var"] == var] if not ens.empty else ens
        return fig_hourly(fc, ens, f"{s['label']} — hodinová předpověď, {s['names'][s['region']]}", s["unit_h"],
                          MODEL_LABELS, MODEL_COLORS)
    return fn


def _chart_change(var):
    def fn(p):
        from charts.weather import fig_forecast_change
        s = _state(p, var)
        if s is None:
            return None
        d = _fc_daily(s["region"])
        if d.empty:
            return None
        d = d[d["var"] == var]
        issues = sorted(d["issued"].unique())
        if len(issues) < 2:
            return None
        return fig_forecast_change(d[d["issued"] == issues[-1]], d[d["issued"] == issues[-2]],
                                   f"Změna předpovědi: {pd.Timestamp(issues[-1]).tz_convert('Europe/Prague'):%d.%m.} "
                                   f"vs. {pd.Timestamp(issues[-2]).tz_convert('Europe/Prague'):%d.%m.}",
                                   s["unit"], MODEL_LABELS, MODEL_COLORS)
    return fn


for _var, _key, _ in (("temp", "teplota", 0), ("wind100", "vitr", 0), ("solar", "slunce", 0), ("precip", "srazky", 0)):
    chart(PAGE, _key, "outlook", displayModeBar="hover")(_chart_outlook(_var))
    chart(PAGE, _key, "hourly")(_chart_hourly(_var))
    chart(PAGE, _key, "change")(_chart_change(_var))


@chart(PAGE, "teplota", "hdd")
def _ch_hdd(p):
    """Kumulované HDD od 1. 7. (topná sezóna) vs. normál a loňsko."""
    from charts.weather import fig_hdd_season
    s = _state(p, "temp")
    if s is None:
        return None
    obs = _daily(s["region"])
    obs = obs[obs["var"] == "temp"] if not obs.empty else obs
    if obs.empty:
        return None
    return fig_hdd_season(obs, _latest_daily_fc(s["region"], "temp"), _normal(s["region"], "temp"),
                          f"Kumulované HDD v topné sezóně — {s['names'][s['region']]}")


@tab(PAGE, "mapy", "Mapy a odkazy")
def mapy(p):
    return {"query": ""}
