"""Trh EU — data ENTSO-E z historie (úlohy entsoe_*, data/entsoe_history.py):
day-ahead ceny všech bidding zón, spotřeba skutečnost vs. prognóza D-1,
prognóza větru/slunce, přeshraniční toky a plánované výměny.

Dotazy jdou přes engine Průzkumníku (core/explore.py, DuckDB) — stejná
čísla jako v Průzkumníku, agregace na serveru."""
from datetime import timedelta

import pandas as pd

from core import explore as ex
from web.registry import page, tab, chart
from web.util import date_range_from_params, table

PAGE = "trh-eu"
page(PAGE, "Trh EU", "💶")

PRESETS = [("Týden", 7), ("Měsíc", 30), ("Kvartál", 90), ("Rok", 365), ("Max", "max")]
DEFAULT_PRICE_ZONES = ["CZ", "DE_LU", "AT", "SK", "PL", "HU", "FR", "NL"]


def zone_names():
    from data.entsoe_history import ZONE_NAMES
    return ZONE_NAMES


def _state(p, ds: str, default_days: int, area_param: str = None, area_default: str = "CZ"):
    b = ex.bounds(ds)
    if b is None:
        return None
    min_d, max_d = b
    d_from, d_to = date_range_from_params(p, max_d, default_days, min_date=min_d)
    s = {"ds": ds, "min_d": min_d, "max_d": max_d, "d_from": d_from, "d_to": d_to, "presets": PRESETS,
         "names": zone_names(), "query": None}
    if area_param:
        dim = "area" if ds != "entsoe_flows" else "src"
        areas = ex.dim_values(ds, dim)
        if ds == "entsoe_flows":
            areas = sorted(set(areas) | set(ex.dim_values(ds, "dst")))
        s["areas"] = areas
        s["area"] = p.str(area_param, area_default if area_default in areas else (areas[0] if areas else None),
                          choices=areas)
    s["query"] = p.query(range=None, **{"from": d_from, "to": d_to})
    return s


def _q(ds, measure, s, grain="auto", cat=None, filters=(), agg=None, cagg=None):
    return ex.Query(ds, measure, s["d_from"], s["d_to"], grain, cat, agg, cagg, tuple(filters))


# ── ceny ────────────────────────────────────────────────────────────────

@tab(PAGE, "ceny", "Ceny zón")
def ceny(p):
    s = _state(p, "entsoe_prices", 30)
    if s is None:
        return {"empty": True, "what": "entsoe_prices"}
    zones = ex.dim_values("entsoe_prices", "zone")
    sel = p.list("zones", [z for z in DEFAULT_PRICE_ZONES if z in zones], choices=zones)
    s.update({"zones": zones, "sel": sel})
    # poslední den dodávky: base/peak/min/max po zónách
    day = s["max_d"]
    q = ex.Query("entsoe_prices", "price", day, day, "hour", "zone")
    df = ex.run(q).df
    if not df.empty:
        df = df[df["b"].dt.date == day]
        peak = df[df["b"].dt.hour.between(8, 19)]
        t = df.groupby("cat")["v"].agg(base="mean", min="min", max="max")
        t["peak"] = peak.groupby("cat")["v"].mean()
        t["zóna"] = [zone_names().get(z, z) for z in t.index]
        t = t.reset_index(names="kód").sort_values("base", ascending=False)
        s["day"] = day
        s["day_table"] = table(t, [("zóna", "Zóna", None), ("kód", "Kód", None), ("base", "Base", ",.2f"),
                                   ("peak", "Peak 8–20", ",.2f"), ("min", "Min", ",.2f"), ("max", "Max", ",.2f")],
                               max_rows=80)
    return s


@chart(PAGE, "ceny", "line")
def _ch_prices(p):
    from charts.explorer import fig_timeseries
    s = ceny(p)
    if s.get("empty") or not s["sel"]:
        return None
    res = ex.run(_q("entsoe_prices", "price", s, cat="zone", filters=[("zone", tuple(s["sel"]))]))
    if res.empty:
        return None
    df = res.df.assign(cat=res.df["cat"].map(lambda z: zone_names().get(z, z)))
    return fig_timeseries(df, "line", "Day-ahead ceny", "EUR/MWh", ex.GRAIN_LABELS[res.query.grain])


@chart(PAGE, "ceny", "heat")
def _ch_prices_heat(p):
    from charts.market import fig_zone_heatmap
    s = ceny(p)
    if s.get("empty"):
        return None
    res = ex.run(_q("entsoe_prices", "price", s, grain="day", cat="zone"))
    return None if res.empty else fig_zone_heatmap(res.df, zone_names(), "Denní průměr ceny po zónách", "EUR/MWh")


# ── spotřeba ────────────────────────────────────────────────────────────

@tab(PAGE, "spotreba", "Spotřeba a prognóza")
def spotreba(p):
    s = _state(p, "entsoe_load", 14, "area")
    if s is None or not s.get("area"):
        return {"empty": True, "what": "entsoe_load"}
    res = ex.run(_q("entsoe_load", "mw", s, grain="hour", cat="kind", filters=[("area", (s["area"],))]))
    w = res.df.pivot_table(index="b", columns="cat", values="v") if not res.empty else pd.DataFrame()
    if {"actual", "forecast"} <= set(w.columns):
        d = w.dropna()
        err = d["forecast"] - d["actual"]
        s["kpi"] = {"mae": err.abs().mean(), "bias": err.mean(),
                    "mape": (err.abs() / d["actual"].abs()).mean() * 100, "n": len(d),
                    "peak": d["actual"].max(), "avg": d["actual"].mean()}
    return s


@chart(PAGE, "spotreba", "line")
def _ch_load(p):
    from charts.market import fig_actual_vs_forecast
    s = spotreba(p)
    if s.get("empty"):
        return None
    res = ex.run(_q("entsoe_load", "mw", s, cat="kind", filters=[("area", (s["area"],))]))
    return None if res.empty else fig_actual_vs_forecast(
        res.df, f"Spotřeba — {zone_names().get(s['area'], s['area'])}", "MW", ex.GRAIN_LABELS[res.query.grain])


@chart(PAGE, "spotreba", "season")
def _ch_load_season(p):
    from charts.explorer import fig_season
    s = spotreba(p)
    if s.get("empty"):
        return None
    lo = s["max_d"] - timedelta(days=5 * 365)
    q = ex.Query("entsoe_load", "mw", lo, s["max_d"], "week", None, "avg", "sum",
                 (("area", (s["area"],)), ("kind", ("actual",))))
    res = ex.run(q)
    return None if res.empty else fig_season(ex.seasonal(res), "week", "Spotřeba — porovnání let (týdenní průměr)",
                                             "MW")


# ── prognóza OZE ────────────────────────────────────────────────────────

@tab(PAGE, "oze", "Prognóza OZE")
def oze(p):
    s = _state(p, "entsoe_ws", 14, "area", "DE_LU")
    if s is None or not s.get("area"):
        return {"empty": True, "what": "entsoe_ws_forecast"}
    return s


@chart(PAGE, "oze", "stack")
def _ch_ws(p):
    from charts.explorer import fig_timeseries
    s = oze(p)
    if s.get("empty"):
        return None
    res = ex.run(_q("entsoe_ws", "mw", s, cat="psr", filters=[("area", (s["area"],))]))
    return None if res.empty else fig_timeseries(
        res.df, "area", f"Prognóza větru a slunce D-1 — {zone_names().get(s['area'], s['area'])}", "MW",
        ex.GRAIN_LABELS[res.query.grain])


@chart(PAGE, "oze", "daily")
def _ch_ws_daily(p):
    from charts.explorer import fig_timeseries
    s = oze(p)
    if s.get("empty"):
        return None
    res = ex.run(_q("entsoe_ws", "mw", s, grain="day", cat="psr", agg="energy",
                    filters=[("area", (s["area"],))]))
    if res.empty:
        return None
    df = res.df.assign(v=res.df["v"] / 1000)
    return fig_timeseries(df, "bar", "Prognóza výroby za den", "GWh")


# ── toky ────────────────────────────────────────────────────────────────

def net_flows(s: dict, kind: str = "physical", grain: str = "auto", agg: str = None) -> pd.DataFrame:
    """b, cat (soused), v = import − export oblasti [MW, s agg=energy MWh] (kladné = import)."""
    a = s["area"]
    exp = ex.run(_q("entsoe_flows", "mw", s, grain, "dst", [("src", (a,)), ("kind", (kind,))], agg)).df
    imp = ex.run(_q("entsoe_flows", "mw", s, grain, "src", [("dst", (a,)), ("kind", (kind,))], agg)).df
    if exp.empty and imp.empty:
        return pd.DataFrame(columns=["b", "cat", "v"])
    m = pd.merge(imp, exp, on=["b", "cat"], how="outer", suffixes=("_imp", "_exp")).fillna(0)
    m["v"] = m["v_imp"] - m["v_exp"]
    return m[["b", "cat", "v"]].sort_values(["b", "cat"])


@tab(PAGE, "toky", "Přeshraniční toky")
def toky(p):
    s = _state(p, "entsoe_flows", 30, "area")
    if s is None or not s.get("area"):
        return {"empty": True, "what": "entsoe_flows"}
    s["kind"] = p.str("kind", "physical", choices=["physical", "scheduled"])
    rows = []
    for kind in ("physical", "scheduled"):
        d = net_flows(s, kind, "day", "energy")
        if d.empty:
            continue
        hours = ((s["d_to"] - s["d_from"]).days + 1) * 24
        for nb, v in d.groupby("cat")["v"]:
            rows.append({"kind": kind, "soused": zone_names().get(nb, nb),
                         "saldo_GWh": v.sum() / 1000, "prumer_MW": v.sum() / hours})
    if rows:
        t = pd.DataFrame(rows).pivot_table(index="soused", columns="kind", values=["saldo_GWh", "prumer_MW"])
        t.columns = [f"{a}_{b}" for a, b in t.columns]
        t = t.reset_index()
        s["summary"] = table(t, [("soused", "Soused", None),
                                 ("saldo_GWh_physical", "Fyzicky [GWh]", ",.1f"),
                                 ("saldo_GWh_scheduled", "Plánovaně [GWh]", ",.1f"),
                                 ("prumer_MW_physical", "Průměr fyz. [MW]", ",.0f"),
                                 ("prumer_MW_scheduled", "Průměr plán. [MW]", ",.0f")])
    return s


@chart(PAGE, "toky", "net")
def _ch_net(p):
    from charts.explorer import fig_timeseries
    s = toky(p)
    if s.get("empty"):
        return None
    kind = p.str("kind", "physical", choices=["physical", "scheduled"])
    d = net_flows(s, kind)
    if d.empty:
        return None
    d = d.assign(cat=d["cat"].map(lambda z: zone_names().get(z, z)))
    title = f"{'Fyzické toky' if kind == 'physical' else 'Plánované výměny'} — {zone_names().get(s['area'], s['area'])}"
    return fig_timeseries(d, "bar", title, "MW", "kladné = import, záporné = export")
