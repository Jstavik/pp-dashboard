"""Nordic — ceny zón NO1–5/SE1–4/FI/DK1–2 a spready vůči DE a „systémové“
ceně (proxy), hydrobilance (NVE + ENTSO-E nádrže vs. medián/min/max let),
toky (zóny, Nordic ↔ kontinent), Finsko (Fingrid) a Dánsko (Energinet)."""
from datetime import timedelta

import pandas as pd

from core import explore as ex
from web.registry import page, tab, chart
from web.util import date_range_from_params, table

PAGE = "nordic"
page(PAGE, "Nordic", "🌊")

PRESETS = [("Týden", 7), ("Měsíc", 30), ("Kvartál", 90), ("Rok", 365), ("3 roky", 1095), ("Max", "max")]
ZONES = ["NO_1", "NO_2", "NO_3", "NO_4", "NO_5", "SE_1", "SE_2", "SE_3", "SE_4", "FI", "DK_1", "DK_2"]
HYDRO_ZONES = ["NO_1", "NO_2", "NO_3", "NO_4", "NO_5", "SE_1", "SE_2", "SE_3", "SE_4", "FI"]
SYS = "SYS"  # proxy systémové ceny = prostý průměr nordických zón (Nord Pool systémová cena není volně)
REF_LABELS = {"DE_LU": "Německo (DE_LU)", SYS: "systémová cena (proxy)"}
# Nordic = NO+SE+FI; „kontinent“ = sousední zóny mimo hydro systém
NORDIC_CORE = {"NO_1", "NO_2", "NO_3", "NO_4", "NO_5", "SE_1", "SE_2", "SE_3", "SE_4", "FI"}
EXTERNAL = {"DK_1", "DK_2", "DE_LU", "NL", "PL", "LT", "EE"}


def _names():
    from data.entsoe_history import ZONE_NAMES
    return {**ZONE_NAMES, SYS: "systémová (proxy)"}


def _range(p, ds, default_days):
    b = ex.bounds(ds)
    if b is None:
        return None
    d_from, d_to = date_range_from_params(p, b[1], default_days, min_date=b[0])
    return {"min_d": b[0], "max_d": b[1], "d_from": d_from, "d_to": d_to, "presets": PRESETS,
            "names": _names(), "query": p.query(range=None, **{"from": d_from, "to": d_to})}


# ── ceny a spready ──────────────────────────────────────────────────────

def _prices(s, grain="auto") -> pd.DataFrame:
    """wide: index čas, sloupce zóny + DE_LU + SYS."""
    zones = [z for z in ZONES + ["DE_LU"] if z in ex.dim_values("entsoe_prices", "zone")]
    res = ex.run(ex.Query("entsoe_prices", "price", s["d_from"], s["d_to"], grain, "zone", "avg", "avg",
                          (("zone", tuple(zones)),)))
    if res.empty:
        return pd.DataFrame()
    w = res.df.pivot_table(index="b", columns="cat", values="v").sort_index()
    nordic = [z for z in ZONES if z in w.columns]
    if nordic:
        w[SYS] = w[nordic].mean(axis=1)
    w.attrs["grain"] = res.query.grain
    return w


@tab(PAGE, "ceny", "Ceny a spready")
def ceny(p):
    s = _range(p, "entsoe_prices", 90)
    if s is None:
        return {"empty": True, "what": "entsoe_prices"}
    s["ref"] = p.str("ref", "DE_LU", choices=list(REF_LABELS))
    s["ref_labels"] = REF_LABELS
    w = _prices(s, "day")
    if not w.empty:
        avg = w.mean()
        rows = [{"zóna": _names().get(z, z), "průměr": avg[z],
                 "spread_DE": avg[z] - avg.get("DE_LU", float("nan")), "spread_SYS": avg[z] - avg.get(SYS, float("nan")),
                 "min": w[z].min(), "max": w[z].max()}
                for z in ZONES + [SYS, "DE_LU"] if z in w.columns]
        s["summary"] = table(pd.DataFrame(rows), [
            ("zóna", "Zóna", None), ("průměr", "Průměr [EUR/MWh]", ",.2f"), ("spread_DE", "Spread vs DE", ",.2f"),
            ("spread_SYS", "Spread vs systémová", ",.2f"), ("min", "Min denní", ",.2f"), ("max", "Max denní", ",.2f")])
    return s


@chart(PAGE, "ceny", "prices")
def _ch_prices(p):
    from charts.explorer import fig_timeseries
    s = ceny(p)
    if s.get("empty"):
        return None
    w = _prices(s)
    if w.empty:
        return None
    df = w.reset_index().melt(id_vars="b", var_name="cat", value_name="v").dropna()
    df["cat"] = df["cat"].map(lambda z: _names().get(z, z))
    return fig_timeseries(df, "line", "Day-ahead ceny Nordic", "EUR/MWh", ex.GRAIN_LABELS[w.attrs["grain"]])


@chart(PAGE, "ceny", "spreads")
def _ch_spreads(p):
    from charts.explorer import fig_timeseries
    s = ceny(p)
    if s.get("empty"):
        return None
    w = _prices(s)
    if w.empty or s["ref"] not in w.columns:
        return None
    sp = w[[z for z in ZONES if z in w.columns]].sub(w[s["ref"]], axis=0)
    df = sp.reset_index().melt(id_vars="b", var_name="cat", value_name="v").dropna()
    df["cat"] = df["cat"].map(lambda z: _names().get(z, z))
    return fig_timeseries(df, "line", f"Spread zón vůči: {REF_LABELS[s['ref']]}", "EUR/MWh",
                          "kladné = zóna dražší než reference")


# ── hydrobilance ────────────────────────────────────────────────────────

@tab(PAGE, "hydro", "Hydrobilance")
def hydro(p):
    from data.nordic import load_nve, load_nve_stats
    nve = load_nve()
    s = {"query": p.query(), "names": {"NO": "Norsko celkem", **{f"NO{i}": f"NO{i}" for i in range(1, 6)}}}
    s["has_nve"] = not nve.empty
    if not nve.empty:
        areas = sorted(nve["area"].unique(), key=lambda a: (a != "NO", a))
        s["areas"], s["area"] = areas, p.str("area", "NO", choices=areas)
        stats = load_nve_stats()
        last = nve.sort_values("date").groupby("area").tail(1).set_index("area")
        rows = []
        for a in areas:
            r = last.loc[a]
            prev = nve[(nve["area"] == a) & (nve["iso_year"] == r["iso_year"] - 1) & (nve["iso_week"] == r["iso_week"])]
            med = _median(nve, stats, a, int(r["iso_week"]))
            rows.append({"oblast": s["names"].get(a, a), "týden": f"{int(r['iso_year'])}/{int(r['iso_week'])}",
                         "plnění": r["filling_pct"], "TWh": r["filling_twh"],
                         "vs_median": r["filling_pct"] - med if med is not None else None,
                         "vs_lonsko": r["filling_pct"] - prev["filling_pct"].iloc[0] if not prev.empty else None})
        s["table"] = table(pd.DataFrame(rows), [
            ("oblast", "Oblast", None), ("týden", "Týden", None), ("plnění", "Plnění [%]", ",.1f"),
            ("TWh", "Zásoba [TWh]", ",.1f"), ("vs_median", "vs. medián [p.b.]", "+,.1f"),
            ("vs_lonsko", "vs. loni [p.b.]", "+,.1f")])
        cur = last.loc[s["area"]]
        s["kpi"] = rows[areas.index(s["area"])] | {"date": cur["date"]}
    s["has_entsoe"] = ex.bounds("hydro") is not None
    return s


def _median(nve, stats, area, week):
    if stats is not None and not stats.empty:
        m = stats[(stats["area"] == area) & (stats["iso_week"] == week)]
        if not m.empty and pd.notna(m["median_pct"].iloc[0]):
            return float(m["median_pct"].iloc[0])
    h = nve[(nve["area"] == area) & (nve["iso_week"] == week)]
    h = h[h["iso_year"] < h["iso_year"].max()]
    return float(h["filling_pct"].median()) if not h.empty else None


@chart(PAGE, "hydro", "nve")
def _ch_nve(p):
    from charts.nordic import fig_reservoir_season
    from data.nordic import load_nve, load_nve_stats
    s = hydro(p)
    if not s.get("has_nve"):
        return None
    nve = load_nve()
    d = nve[nve["area"] == s["area"]]
    st = load_nve_stats()
    st = st[st["area"] == s["area"]] if not st.empty else st
    return fig_reservoir_season(d, st, f"Plnění nádrží — {s['names'].get(s['area'], s['area'])} (NVE)")


@chart(PAGE, "hydro", "entsoe")
def _ch_hydro_entsoe(p):
    from charts.nordic import fig_country_hydro
    s = hydro(p)
    if not s.get("has_entsoe"):
        return None
    b = ex.bounds("hydro")
    frames = []
    for cc in ("NO", "SE", "FI"):
        res = ex.run(ex.Query("hydro", "gwh", b[1] - timedelta(days=8 * 365), b[1], "week", None, "avg", "sum",
                              (("country", (cc,)),)))
        if not res.empty:
            frames.append(ex.seasonal(res).assign(country=cc))
    return fig_country_hydro(pd.concat(frames)) if frames else None


# ── toky ────────────────────────────────────────────────────────────────

@tab(PAGE, "toky", "Toky")
def toky(p):
    s = _range(p, "entsoe_flows", 30)
    if s is None:
        return {"empty": True, "what": "entsoe_flows"}
    areas = sorted(set(ex.dim_values("entsoe_flows", "src")) & set(ZONES))
    if not areas:
        return {"empty": True, "what": "entsoe_flows (Nordic)"}
    s["areas"], s["area"] = areas, p.str("area", "NO_2" if "NO_2" in areas else areas[0], choices=areas)
    s["kind"] = p.str("kind", "physical", choices=["physical", "scheduled"])
    return s


@chart(PAGE, "toky", "zone")
def _ch_zone_flows(p):
    from charts.explorer import fig_timeseries
    from web.tabs.market import net_flows
    s = toky(p)
    if s.get("empty"):
        return None
    d = net_flows(s, s["kind"])
    if d.empty:
        return None
    d = d.assign(cat=d["cat"].map(lambda z: _names().get(z, z)))
    return fig_timeseries(d, "bar", f"Saldo toků — {_names().get(s['area'], s['area'])}", "MW",
                          "kladné = import, záporné = export")


@chart(PAGE, "toky", "continent")
def _ch_continent(p):
    from charts.explorer import fig_timeseries
    s = toky(p)
    if s.get("empty"):
        return None
    out = []
    for src_set, dst_set, sign in ((NORDIC_CORE, EXTERNAL, -1), (EXTERNAL, NORDIC_CORE, 1)):
        res = ex.run(ex.Query("entsoe_flows", "mw", s["d_from"], s["d_to"], "auto", "dst" if sign < 0 else "src",
                              "avg", "sum", (("kind", (s["kind"],)), ("src", tuple(sorted(src_set))),
                                             ("dst", tuple(sorted(dst_set))))))
        if not res.empty:
            out.append(res.df.assign(v=res.df["v"] * sign))
    if not out:
        return None
    d = pd.concat(out).groupby(["b", "cat"], as_index=False)["v"].sum()
    d["cat"] = d["cat"].map(lambda z: _names().get(z, z))
    return fig_timeseries(d, "bar", "Nordic (NO+SE+FI) ↔ okolí", "MW",
                          "saldo po sousedních oblastech, kladné = import do Nordic")


# ── Finsko a Dánsko ─────────────────────────────────────────────────────

@tab(PAGE, "fidk", "Finsko a Dánsko")
def fidk(p):
    s = {"query": p.query()}
    for ds in ("fingrid", "energinet"):
        b = ex.bounds(ds)
        s[ds] = b is not None
        if b:
            s[f"{ds}_range"] = b
    from data.nordic import fingrid_api_key
    s["fingrid_key"] = bool(fingrid_api_key())
    return s


@chart(PAGE, "fidk", "fingrid")
def _ch_fingrid(p):
    from charts.explorer import fig_timeseries
    b = ex.bounds("fingrid")
    if b is None:
        return None
    res = ex.run(ex.Query("fingrid", "mw", b[1] - timedelta(days=14), b[1], "hour", "series", "avg", "sum",
                          (("series", ("spotřeba", "výroba", "vítr", "jádro", "voda")),)))
    return None if res.empty else fig_timeseries(res.df, "line", "Finsko — Fingrid (14 dní)", "MW")


@chart(PAGE, "fidk", "energinet")
def _ch_energinet(p):
    from charts.explorer import fig_timeseries
    b = ex.bounds("energinet")
    if b is None:
        return None
    want = ("GrossConsumptionMWh", "OffshoreWindGe100MW_MWh", "OnshoreWindGe50kW_MWh", "SolarPowerGe40kW_MWh",
            "CentralPowerMWh", "LocalPowerMWh")
    res = ex.run(ex.Query("energinet", "v", b[1] - timedelta(days=14), b[1], "hour", "series", "avg", "sum",
                          (("dataset", ("ProductionConsumptionSettlement",)), ("series", want))))
    return None if res.empty else fig_timeseries(res.df, "line", "Dánsko DK1+DK2 — Energinet (14 dní)", "MWh/h")
