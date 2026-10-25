"""Nový vzhled (PRESENTATION_PROPOSAL.md) — výchozí na stránkách Plyn,
Elektřina, Nordic, Trh EU a Počasí (starý přes přepínač, cookie ppdash_vzhled).

Přehledová záložka = jedna obrazovka bez filtrů, odshora:
1. řádek KPI (hodnota + změna týden/týden + odchylka od průměru 5 let),
2. hlavní sezónní graf (letos vs. pásmo 5 let) s cíli a událostmi,
3. small multiples (země / zdroje / zóny se stejným měřítkem),
4. tabulka čísel (co je v grafech).

Stránky jsou registrované jako v2-<stránka> (V2_PAGES). V novém vzhledu
web/app.py::page_view zobrazí jejich záložky (✨) PŘED všemi původními
záložkami stránky — ty zůstávají vizuálně beze změny (jen export a
kopírování obrázku). Data a výpočty sdílí se starými stránkami
(core/explore.py, core/overview.py, data/*).
"""
from datetime import date, timedelta

import pandas as pd

from core import explore as ex
from core.cache import ttl_cache
from web.registry import chart, page, tab
from web.util import fmt_value, table

# původní stránka → stránka nového vzhledu
V2_PAGES = {"plyn": "v2-plyn", "elektrina": "v2-elektrina", "nordic": "v2-nordic", "trh-eu": "v2-trh-eu",
            "pocasi": "v2-pocasi"}

page("v2-plyn", "Plyn — nový vzhled", "🔵")
page("v2-elektrina", "Elektřina ČR — nový vzhled", "⚡")
page("v2-nordic", "Nordic — nový vzhled", "🌊")


def _delta(v, fmt="+,.1f", unit=""):
    if v is None or pd.isna(v):
        return "—"
    return f"{fmt_value(float(v), fmt)}{(' ' + unit) if unit else ''}"


def _at(s: pd.Series, d, tol_days=3):
    """Hodnota řady k datu d (nejbližší starší do tol_days)."""
    if s is None or s.empty:
        return None
    s = s.dropna()
    s = s[s.index <= d]
    if s.empty or (d - s.index[-1]).days > tol_days:
        return None
    return float(s.iloc[-1])


def _vs_mean(sz: dict):
    cur = sz.get("cur", pd.Series(dtype=float)).dropna()
    if cur.empty or "mean" not in sz:
        return None, None
    d = cur.index[-1]
    return float(cur.iloc[-1]), float(cur.iloc[-1] - sz["mean"].get(d, float("nan")))


# ── Plyn: zásobníky ─────────────────────────────────────────────────────

GAS_MULTI = ["DE", "IT", "FR", "NL", "AT", "CZ", "SK", "PL", "HU"]
GAS_NAMES = {"EU": "EU", "DE": "Německo", "IT": "Itálie", "FR": "Francie", "NL": "Nizozemsko", "AT": "Rakousko",
             "CZ": "Česko", "SK": "Slovensko", "PL": "Polsko", "HU": "Maďarsko"}


@ttl_cache(ttl=900, maxsize=4)
def _gie_series():
    from data.gie import load_gie_all
    df = load_gie_all()
    if df.empty:
        return {}
    df = df[df["country_code"].isin(["EU", *GAS_MULTI])].copy()
    df["d"] = pd.to_datetime(df["gasDayStart"], utc=True).dt.tz_convert("Europe/Prague").dt.tz_localize(None)
    out = {}
    for cc, g in df.groupby("country_code"):
        g = g.drop_duplicates("d", keep="last").set_index("d").sort_index()
        out[cc] = {c: pd.to_numeric(g[c], errors="coerce") for c in ("full", "gasInStorage", "netWithdrawal")
                   if c in g}
    return out


def _gas_state():
    from charts.v2 import seasonal
    ser = _gie_series()
    if "EU" not in ser:
        return None
    sz = {cc: seasonal(v["full"]) for cc, v in ser.items()}
    return {"ser": ser, "sz": sz}


@tab("v2-plyn", "prehled", "Přehled")
def gas_overview(p):
    s = _gas_state()
    if s is None:
        return {"empty": True}
    kpis, rows = [], []
    for cc in ["EU", *GAS_MULTI]:
        if cc not in s["sz"] or not s["sz"][cc]:
            continue
        sz, full = s["sz"][cc], s["ser"][cc]["full"].dropna()
        if full.empty:
            continue
        last_d, v = full.index[-1], float(full.iloc[-1])
        w = _at(full, last_d - timedelta(days=7))
        y = _at(full, last_d - timedelta(days=365))
        _, vs5 = _vs_mean(sz)
        twh = _at(s["ser"][cc].get("gasInStorage", pd.Series(dtype=float)), last_d)
        net = s["ser"][cc].get("netWithdrawal", pd.Series(dtype=float)).dropna()
        net7 = float(net[net.index > last_d - timedelta(days=7)].mean()) if not net.empty else None
        rows.append({"země": GAS_NAMES.get(cc, cc), "plnění": v, "d7": v - w if w is not None else None,
                     "vs5": vs5, "vsly": v - y if y is not None else None, "twh": twh,
                     "net7": -net7 if net7 is not None else None, "den": last_d})
        if cc in ("EU", "DE", "IT", "AT", "CZ"):
            kpis.append({"label": f"{GAS_NAMES.get(cc, cc)} plnění", "value": fmt_value(v, ",.1f"), "unit": "%",
                         "sub": f"týden {_delta(rows[-1]['d7'], unit='p.b.')} · vs. průměr 5 let "
                                f"<strong>{_delta(vs5, unit='p.b.')}</strong>"})
    tbl = table(pd.DataFrame(rows), [
        ("země", "Země", None), ("den", "Plynárenský den", "%d.%m.%Y"), ("plnění", "Plnění [%]", ",.1f"),
        ("d7", "Změna 7 dní [p.b.]", "+,.1f"), ("vs5", "vs. průměr 5 let [p.b.]", "+,.1f"),
        ("vsly", "vs. loni [p.b.]", "+,.1f"), ("twh", "Zásoba [TWh]", ",.1f"),
        ("net7", "Vtláčení − těžba, Ø 7 dní [GWh/d]", "+,.0f")])
    return {"kpis": kpis, "table": tbl, "query": p.query()}


@chart("v2-plyn", "prehled", "eu")
def _ch_gas_eu(p):
    from charts.v2 import fig_seasonal
    from core.events import market_events
    s = _gas_state()
    if s is None:
        return None
    sz = s["sz"]["EU"]
    return fig_seasonal(sz, "EU — plnění zásobníků: letos vs. posledních 5 let", "%",
                        targets=[(90, "cíl 90 % (flexibilně 1. 10.–1. 12.)")],
                        events=market_events("gas", since=f"{sz['year']}-01-01"), y_range=[0, 100])


@chart("v2-plyn", "prehled", "multi")
def _ch_gas_multi(p):
    from charts.v2 import fig_small_multiples
    s = _gas_state()
    if s is None:
        return None
    panels = [(GAS_NAMES.get(cc, cc), s["sz"].get(cc)) for cc in GAS_MULTI]
    return fig_small_multiples(panels, "%", cols=3, fmt=".0f", y_range=[0, 105],
                               title="Země — plnění [%], stejné měřítko, letos vs. pásmo 5 let")


@chart("v2-plyn", "prehled", "net")
def _ch_gas_net(p):
    from charts.v2 import fig_seasonal, seasonal
    s = _gas_state()
    if s is None or "netWithdrawal" not in s["ser"]["EU"]:
        return None
    net = -s["ser"]["EU"]["netWithdrawal"] / 1000  # GWh/d → TWh/d, kladné = vtláčení
    sz = seasonal(net, smooth=7)
    fig = fig_seasonal(sz, "EU — vtláčení (+) / těžba (−), Ø 7 dní", "TWh/d", fmt=".2f", height=360,
                       legend_bottom=True)
    fig.add_hline(y=0, line=dict(color="#8a8780", width=1))
    return fig


# ── Elektřina ČR ────────────────────────────────────────────────────────

GEN_MULTI = [("Nuclear", "Jádro"), ("Fossil Brown coal/Lignite", "Lignit"), ("Solar", "Slunce"),
             ("Wind Onshore", "Vítr"), ("Fossil Gas", "Plyn"), ("Hydro Pumped Storage", "Přečerpávací"),
             ("Hydro Run-of-river and poundage", "Průtočná voda"), ("Biomass", "Biomasa")]


@ttl_cache(ttl=900, maxsize=4)
def _da(cc: str):
    from core.overview import daily_da
    return daily_da(cc, days=6 * 366)


@ttl_cache(ttl=900, maxsize=4)
def _gen_daily(cc: str = "CZ"):
    b = ex.bounds("generation")
    if b is None:
        return pd.DataFrame()
    lo = max(b[0], b[1] - timedelta(days=6 * 366))
    r = ex.run(ex.Query("generation", "mw", lo, b[1], "day", "source_type", "avg", "sum", (("country", (cc,)),)))
    if r.empty:
        return pd.DataFrame()
    df = r.df.copy()
    df["d"] = pd.to_datetime(df["b"]).dt.tz_localize(None) if df["b"].dt.tz is None else \
        pd.to_datetime(df["b"]).dt.tz_convert("Europe/Prague").dt.tz_localize(None)
    return df.pivot_table(index="d", columns="cat", values="v", aggfunc="mean").sort_index() / 1000  # GW


@tab("v2-elektrina", "prehled", "Přehled")
def power_overview(p):
    cz, de = _da("CZ"), _da("DE")
    ctx = {"query": p.query(), "kpis": [], "has_da": not cz.empty, "has_gen": not _gen_daily().empty}
    if not cz.empty:
        last = cz.index[-1]
        b = cz["base"].iloc[-1]
        avg30 = cz["base"][cz.index > last - timedelta(days=30)].mean()
        prev = _at(cz["base"], last - timedelta(days=7))
        ctx["kpis"].append({"label": f"CZ base {last:%d.%m.}", "value": fmt_value(b, ",.1f"), "unit": "€/MWh",
                            "sub": f"týden {_delta(b - prev if prev is not None else None, unit='€')} · "
                                   f"vs. Ø 30 dní <strong>{_delta(b - avg30, unit='€')}</strong>"})
        pks = cz["peak"].dropna()  # peak = 8–20 h po–pá → o víkendu není
        if not pks.empty:
            pd_, pk = pks.index[-1], float(pks.iloc[-1])
            ctx["kpis"].append({"label": f"CZ peak {pd_:%d.%m.}", "value": fmt_value(pk, ",.1f"), "unit": "€/MWh",
                                "sub": f"peak − base {_delta(pk - cz['base'].get(pd_), unit='€')}"})
        if not de.empty and last in de.index:
            sp = b - de["base"].get(last)
            sp30 = (cz["base"] - de["base"]).dropna()
            sp30 = sp30[sp30.index > last - timedelta(days=30)].mean()
            ctx["kpis"].append({"label": "Spread CZ − DE base", "value": fmt_value(sp, "+,.1f"), "unit": "€/MWh",
                                "sub": f"Ø 30 dní {_delta(sp30, unit='€')}"})
        rows = []
        for cc, d in (("CZ", cz), ("DE", de)):
            if d.empty:
                continue
            for lbl, col in (("base", "base"), ("peak", "peak")):
                s = d[col].dropna()
                if s.empty:
                    continue
                v = s.iloc[-1]
                rows.append({"trh": f"{cc} {lbl}", "den": s.index[-1], "v": v,
                             "d7": v - (_at(s, s.index[-1] - timedelta(days=7)) or float("nan")),
                             "m30": s[s.index > s.index[-1] - timedelta(days=30)].mean(),
                             "y": s[s.index.year == s.index[-1].year].mean()})
        ctx["table"] = table(pd.DataFrame(rows), [
            ("trh", "Produkt", None), ("den", "Den dodávky", "%d.%m.%Y"), ("v", "Cena [€/MWh]", ",.1f"),
            ("d7", "vs. před týdnem", "+,.1f"), ("m30", "Ø 30 dní", ",.1f"), ("y", "Ø letos", ",.1f")])
    gen = _gen_daily()
    if not gen.empty:
        last = gen.index[-1]
        wk = gen[gen.index > last - timedelta(days=7)].mean()
        tot = wk.sum()
        for src, lbl in GEN_MULTI[:3]:
            if src in wk:
                ctx["kpis"].append({"label": f"{lbl} Ø 7 dní", "value": fmt_value(wk[src], ",.2f"), "unit": "GW",
                                    "sub": f"{fmt_value(wk[src] / tot * 100, ',.0f')} % výroby"})
    return ctx


@chart("v2-elektrina", "prehled", "base")
def _ch_base(p):
    from charts.v2 import fig_seasonal, seasonal
    from core.events import market_events
    cz = _da("CZ")
    if cz.empty:
        return None
    sz = seasonal(cz["base"], smooth=7)
    return fig_seasonal(sz, "CZ base (DA) — klouzavý průměr 7 dní: letos vs. pásmo 5 let", "€/MWh", fmt=".0f",
                        events=market_events("power", since=f"{sz['year']}-01-01"))


@chart("v2-elektrina", "prehled", "recent")
def _ch_recent(p):
    from charts.v2 import fig_lines
    from core.events import feed_events
    cz, de = _da("CZ"), _da("DE")
    if cz.empty:
        return None
    lo = cz.index[-1] - timedelta(days=60)
    ser = {"CZ": cz["base"][cz.index >= lo]}
    if not de.empty:
        ser["DE"] = de["base"][de.index >= lo]
    ev = feed_events(("Odstávka",), limit=4, match=lambda e: e.get("title", "").startswith("CZ"))
    return fig_lines(ser, "Base CZ a DE — posledních 60 dní (události: odstávky CZ ≥ 100 MW)", "€/MWh",
                     fmt=".1f", events=[(pd.Timestamp(t).normalize(), l) for t, l in ev])


@chart("v2-elektrina", "prehled", "spread")
def _ch_spread(p):
    from charts.v2 import fig_bars_signed
    cz, de = _da("CZ"), _da("DE")
    if cz.empty or de.empty:
        return None
    sp = (cz["base"] - de["base"]).dropna()
    sp = sp[sp.index >= sp.index[-1] - timedelta(days=60)] if not sp.empty else sp
    return fig_bars_signed(sp, "Spread CZ − DE base (kladný = CZ dražší)", "€/MWh")


@chart("v2-elektrina", "prehled", "profile")
def _ch_profile(p):
    import plotly.graph_objects as go
    from charts.v2 import _style
    from core.overview import hourly_da
    cz = _da("CZ")
    if cz.empty:
        return None
    day = cz.index[-1]
    fig = go.Figure()
    for zone, name in (("CZ", "CZ"), ("DE_LU", "DE")):
        s = hourly_da(zone, day)
        if s.empty:
            continue
        x = s.index.tz_localize(None) if s.index.tz is not None else s.index
        fig.add_trace(go.Scatter(x=x, y=s.values, mode="lines", name=name, line=dict(width=2, shape="hv"),
                                 hovertemplate=f"{name} %{{x|%H:%M}}: %{{y:.1f}} €/MWh<extra></extra>"))
    if not fig.data:
        return fig
    _style(fig, f"Profil dodávky {day:%d.%m.%Y} — hodiny/čtvrthodiny", "€/MWh", 320)
    fig.update_xaxes(tickformat="%H:%M", dtick=3 * 3600 * 1000, ticklabelmode="instant")
    return fig


@chart("v2-elektrina", "prehled", "gen")
def _ch_gen(p):
    from charts.v2 import fig_small_multiples, seasonal
    gen = _gen_daily()
    if gen.empty:
        return None
    panels = [(lbl, seasonal(gen[src], smooth=7)) for src, lbl in GEN_MULTI if src in gen]
    return fig_small_multiples(panels, "GW", cols=4, fmt=".1f", shared_y=False,
                               title="Výroba CZ podle zdroje — Ø GW, klouzavý průměr 7 dní, letos vs. pásmo 5 let")


# ── Nordic ──────────────────────────────────────────────────────────────

NORDIC_ZONES = ["NO_1", "NO_2", "NO_3", "NO_4", "NO_5", "SE_1", "SE_2", "SE_3", "SE_4", "FI", "DK_1", "DK_2"]


@ttl_cache(ttl=900, maxsize=2)
def _nordic_prices():
    b = ex.bounds("entsoe_prices")
    if b is None:
        return pd.DataFrame()
    lo = b[1] - timedelta(days=120)
    r = ex.run(ex.Query("entsoe_prices", "price", lo, b[1], "day", "zone", "avg", "avg",
                        (("zone", tuple(NORDIC_ZONES + ["DE_LU"])),)))
    if r.empty:
        return pd.DataFrame()
    df = r.df.copy()
    df["d"] = pd.to_datetime(df["b"]).dt.tz_convert("Europe/Prague").dt.tz_localize(None)
    return df.pivot_table(index="d", columns="cat", values="v", aggfunc="mean").sort_index()


@ttl_cache(ttl=900, maxsize=2)
def _nve():
    from data.nordic import load_nve
    nve = load_nve()
    if nve.empty:
        return {}
    nve = nve.copy()
    nve["d"] = pd.to_datetime(nve["date"])
    nve["d"] = nve["d"].dt.tz_convert("Europe/Prague").dt.tz_localize(None) if nve["d"].dt.tz is not None else nve["d"]
    return {a: g.drop_duplicates("d").set_index("d")["filling_pct"].sort_index() for a, g in nve.groupby("area")}


def _zname(z):
    return z.replace("_", "")


@tab("v2-nordic", "prehled", "Přehled")
def nordic_overview(p):
    from charts.v2 import seasonal
    ctx = {"query": p.query(), "kpis": []}
    nve = _nve()
    rows = []
    for a in ["NO", "NO1", "NO2", "NO3", "NO4", "NO5"]:
        if a not in nve:
            continue
        s = nve[a].dropna()
        sz = seasonal(s, interpolate=True)
        v, vs5 = _vs_mean(sz)
        if v is None:
            v = float(s.iloc[-1])
        y = _at(s, s.index[-1] - timedelta(days=364), tol_days=7)
        w = _at(s, s.index[-1] - timedelta(days=7), tol_days=4)
        rows.append({"oblast": "Norsko" if a == "NO" else a, "den": s.index[-1], "v": float(s.iloc[-1]),
                     "w": float(s.iloc[-1]) - w if w is not None else None, "vs5": vs5,
                     "vsly": float(s.iloc[-1]) - y if y is not None else None})
        if a in ("NO", "NO2"):
            ctx["kpis"].append({"label": f"Hydro {'Norsko' if a == 'NO' else a}", "value": fmt_value(rows[-1]['v'], ",.1f"),
                                "unit": "%", "sub": f"týden {_delta(rows[-1]['w'], unit='p.b.')} · vs. průměr 5 let "
                                                    f"<strong>{_delta(vs5, unit='p.b.')}</strong>"})
    ctx["hydro_table"] = table(pd.DataFrame(rows), [
        ("oblast", "Oblast", None), ("den", "Týden do", "%d.%m.%Y"), ("v", "Plnění [%]", ",.1f"),
        ("w", "Změna týden [p.b.]", "+,.1f"), ("vs5", "vs. průměr 5 let [p.b.]", "+,.1f"),
        ("vsly", "vs. loni [p.b.]", "+,.1f")])
    pr = _nordic_prices()
    if not pr.empty:
        last = pr.index[-1]
        wk = pr[pr.index > last - timedelta(days=7)].mean()
        de = wk.get("DE_LU")
        for z in ("NO_2", "SE_3", "FI"):
            if z in wk and pd.notna(wk[z]):
                ctx["kpis"].append({"label": f"{_zname(z)} Ø 7 dní", "value": fmt_value(wk[z], ",.1f"), "unit": "€/MWh",
                                    "sub": f"vs. DE {_delta(wk[z] - de if de is not None else None, unit='€')}"})
        prow = []
        for z in NORDIC_ZONES:
            if z not in pr:
                continue
            s = pr[z].dropna()
            if s.empty:
                continue
            prow.append({"zóna": _zname(z), "d": s.iloc[-1], "w": wk.get(z),
                         "m30": s[s.index > last - timedelta(days=30)].mean(),
                         "sp": (wk.get(z) - de) if de is not None else None})
        ctx["price_table"] = table(pd.DataFrame(prow), [
            ("zóna", "Zóna", None), ("d", f"{last:%d.%m.} [€/MWh]", ",.1f"), ("w", "Ø 7 dní", ",.1f"),
            ("m30", "Ø 30 dní", ",.1f"), ("sp", "Spread vs. DE (Ø 7 dní)", "+,.1f")])
    ctx["has_nve"], ctx["has_prices"] = bool(nve), not pr.empty
    return ctx


@chart("v2-nordic", "prehled", "hydro")
def _ch_hydro(p):
    from charts.v2 import fig_seasonal, seasonal
    nve = _nve()
    if "NO" not in nve:
        return None
    sz = seasonal(nve["NO"], interpolate=True)
    return fig_seasonal(sz, "Norsko — plnění nádrží (NVE): letos vs. posledních 5 let", "%", y_range=[0, 100])


@chart("v2-nordic", "prehled", "hydro_multi")
def _ch_hydro_multi(p):
    from charts.v2 import fig_small_multiples, seasonal
    nve = _nve()
    panels = [(a, seasonal(nve[a], interpolate=True)) for a in ["NO1", "NO2", "NO3", "NO4", "NO5"] if a in nve]
    return fig_small_multiples(panels, "%", cols=5, fmt=".0f", y_range=[0, 105], height_per_row=200,
                               title="Elspot oblasti NO1–NO5 — plnění [%], stejné měřítko")


@chart("v2-nordic", "prehled", "spreads")
def _ch_spreads(p):
    from charts.v2 import fig_multiples_ts
    pr = _nordic_prices()
    if pr.empty or "DE_LU" not in pr:
        return None
    sp = pr.sub(pr["DE_LU"], axis=0).rolling(7, min_periods=1).mean()
    panels = [(_zname(z), sp[z]) for z in NORDIC_ZONES if z in sp]
    return fig_multiples_ts(panels, "€/MWh", cols=4, fmt=".0f",
                            title="Spread zóny − DE (Ø 7 dní, €/MWh), stejné měřítko — záporný = levnější než DE")


# ── Elektřina ČR: odstávky (ENTSO-E nedostupnost výroby) ────────────────

OUT_TYPES = {"Nuclear": "Jádro", "Fossil Brown coal/Lignite": "Lignit", "Fossil Hard coal": "Černé uhlí",
             "Fossil Gas": "Plyn", "Hydro Pumped Storage": "Přečerpávací", "Hydro Water Reservoir": "Akumulační voda",
             "Hydro Run-of-river and poundage": "Průtočná voda", "Biomass": "Biomasa"}


@ttl_cache(ttl=900, maxsize=4)
def _outage_daily(country: str = "CZ", back: int = 30, ahead: int = 90):
    """Nedostupný výkon po dnech (stav v poledne) a typech zdroje; jeden
    blok = max přes překrývající se hlášení (verze/změny se nesčítají)."""
    from data.outages import OUTAGES_PATH
    import os
    if not os.path.exists(OUTAGES_PATH):
        return pd.DataFrame(), pd.DataFrame()
    o = pd.read_parquet(OUTAGES_PATH)
    o = o[o["country"] == country].copy()
    if o.empty:
        return pd.DataFrame(), pd.DataFrame()
    today = pd.Timestamp.now(tz="Europe/Prague").normalize()
    days = pd.date_range(today - pd.DateOffset(days=back), today + pd.DateOffset(days=ahead), freq="D")
    o["start"] = pd.to_datetime(o["start"], utc=True)
    o["end"] = pd.to_datetime(o["end"], utc=True)
    rows = []
    for d in days:
        noon = (d + pd.Timedelta(hours=12)).tz_convert("UTC")
        a = o[(o["start"] <= noon) & (o["end"] > noon)]
        if a.empty:
            continue
        a = a.sort_values("unavail_mw").groupby("production_resource_name", observed=True).last()
        for typ, mw in a.groupby("plant_type")["unavail_mw"].sum().items():
            rows.append({"d": d.tz_localize(None), "typ": OUT_TYPES.get(typ, typ), "mw": mw})
    piv = (pd.DataFrame(rows).pivot_table(index="d", columns="typ", values="mw", aggfunc="sum").fillna(0)
           if rows else pd.DataFrame())
    noon = (today + pd.Timedelta(hours=12)).tz_convert("UTC")
    now = o[(o["start"] <= noon) & (o["end"] > noon)].sort_values("unavail_mw").groupby(
        "production_resource_name", observed=True).last().reset_index()
    return piv, now


@tab("v2-elektrina", "odstavky", "Odstávky")
def power_outages(p):
    piv, now = _outage_daily()
    ctx = {"query": p.query(), "kpis": [], "empty": piv.empty}
    if piv.empty:
        return ctx
    today = pd.Timestamp.now(tz="Europe/Prague").normalize().tz_localize(None)
    tot = piv.sum(axis=1)
    cur = float(tot.get(today, 0.0))
    nxt = tot[(tot.index > today) & (tot.index <= today + timedelta(days=30))]
    prev = tot[(tot.index >= today - timedelta(days=30)) & (tot.index < today)]
    planned = now[now["businesstype"].astype(str).str.contains("Planned", na=False)]["unavail_mw"].sum()
    ctx["kpis"] = [
        {"label": "Nedostupné dnes", "value": fmt_value(cur, ",.0f"), "unit": "MW",
         "sub": f"{len(now)} bloků · plánované {fmt_value(planned, ',.0f')} MW"},
        {"label": "Max. příštích 30 dní", "value": fmt_value(nxt.max() if not nxt.empty else 0, ",.0f"), "unit": "MW",
         "sub": f"{nxt.idxmax():%d.%m.}" if not nxt.empty and nxt.max() > 0 else "—"},
        {"label": "Ø posledních 30 dní", "value": fmt_value(prev.mean() if not prev.empty else 0, ",.0f"), "unit": "MW",
         "sub": f"dnes vs. Ø 30 dní <strong>{_delta(cur - prev.mean() if not prev.empty else None, fmt='+,.0f', unit='MW')}</strong>"},
    ]
    big = now.sort_values("unavail_mw", ascending=False).head(12).assign(
        typ=lambda d: d["plant_type"].map(lambda t: OUT_TYPES.get(t, t)),
        druh=lambda d: d["businesstype"].astype(str).str.replace("Planned maintenance", "plánovaná")
        .str.replace("Unplanned outage", "neplánovaná"),
        do=lambda d: pd.to_datetime(d["end"], utc=True).dt.tz_convert("Europe/Prague").dt.tz_localize(None))
    ctx["table"] = table(big, [("production_resource_name", "Blok", None), ("typ", "Zdroj", None),
                               ("unavail_mw", "Nedostupné [MW]", ",.0f"), ("nominal_power", "Instalováno [MW]", ",.0f"),
                               ("druh", "Druh", None), ("do", "Do", "%d.%m.%Y %H:%M")])
    return ctx


@chart("v2-elektrina", "odstavky", "unavail")
def _ch_outages(p):
    import plotly.graph_objects as go
    from charts.v2 import _style
    piv, _ = _outage_daily()
    if piv.empty:
        return None
    fig = go.Figure()
    order = piv.sum().sort_values(ascending=False).index
    for typ in order:
        fig.add_trace(go.Scatter(x=piv.index, y=piv[typ].round(0), name=typ, mode="lines", stackgroup="u",
                                 line=dict(width=0.5), hovertemplate=f"{typ}: %{{y:,.0f}} MW<extra></extra>"))
    _style(fig, "CZ — nedostupný výkon podle zdroje (stav v poledne): 30 dní zpět, 90 dní dopředu", "MW", 400,
           legend_bottom=True)
    today = pd.Timestamp.now(tz="Europe/Prague").normalize().tz_localize(None)
    fig.add_vline(x=today, line=dict(color="#8a8780", width=1, dash="dot"))
    fig.add_annotation(x=today, y=1, yref="paper", text="dnes", showarrow=False, yanchor="bottom",
                       font=dict(size=11))
    fig.update_xaxes(tickformat="%d.%m.", dtick=None, ticklabelmode="instant")
    fig.update_layout(hovermode="x unified")
    return fig


# ── Trh EU: ceny zón ────────────────────────────────────────────────────

page("v2-trh-eu", "Trh EU — nový vzhled", "💶")
EU_ZONES = ["CZ", "DE_LU", "AT", "SK", "PL", "HU", "FR", "NL", "IT_NORD"]


@ttl_cache(ttl=900, maxsize=2)
def _eu_prices():
    b = ex.bounds("entsoe_prices")
    if b is None:
        return pd.DataFrame()
    lo = max(b[0], b[1] - timedelta(days=6 * 366))
    r = ex.run(ex.Query("entsoe_prices", "price", lo, b[1], "day", "zone", "avg", "avg",
                        (("zone", tuple(EU_ZONES)),)))
    if r.empty:
        return pd.DataFrame()
    df = r.df.copy()
    df["d"] = pd.to_datetime(df["b"]).dt.tz_convert("Europe/Prague").dt.tz_localize(None)
    return df.pivot_table(index="d", columns="cat", values="v", aggfunc="mean").sort_index()


@ttl_cache(ttl=900, maxsize=2)
def _eu_load(area: str = "CZ"):
    b = ex.bounds("entsoe_load")
    if b is None:
        return pd.Series(dtype=float)
    lo = max(b[0], b[1] - timedelta(days=6 * 366))
    r = ex.run(ex.Query("entsoe_load", "mw", lo, b[1], "day", None, "avg", "sum",
                        (("area", (area,)), ("kind", ("actual",)))))
    if r.empty:
        return pd.Series(dtype=float)
    df = r.df
    d = pd.to_datetime(df["b"])
    d = d.dt.tz_convert("Europe/Prague").dt.tz_localize(None) if d.dt.tz is not None else d
    return pd.Series(df["v"].values / 1000, index=d).sort_index()  # GW


def _zn(z):
    from data.entsoe_history import ZONE_NAMES
    return {"DE_LU": "DE", "IT_NORD": "IT sever"}.get(z, ZONE_NAMES.get(z, z) if len(z) > 3 else z)


@tab("v2-trh-eu", "prehled", "Přehled")
def eu_overview(p):
    from charts.v2 import seasonal
    pr = _eu_prices()
    ctx = {"query": p.query(), "kpis": [], "has_prices": not pr.empty, "has_load": not _eu_load().empty}
    if pr.empty:
        return ctx
    last = pr.index[-1]
    de = pr.get("DE_LU")
    rows = []
    for z in [z for z in EU_ZONES if z in pr]:
        s = pr[z].dropna()
        if s.empty:
            continue
        sz = seasonal(s, smooth=7)
        cur7, vs5 = _vs_mean(sz)
        w7 = s[s.index > s.index[-1] - timedelta(days=7)].mean()
        m30 = s[s.index > s.index[-1] - timedelta(days=30)].mean()
        sp = (s - de).dropna() if de is not None and z != "DE_LU" else pd.Series(dtype=float)
        rows.append({"zóna": _zn(z), "den": s.index[-1], "v": float(s.iloc[-1]), "w7": w7, "m30": m30,
                     "vs5": vs5, "sp7": sp[sp.index > last - timedelta(days=7)].mean() if not sp.empty else None})
        if z in ("CZ", "DE_LU", "FR", "PL", "HU"):
            ctx["kpis"].append({"label": f"{_zn(z)} {s.index[-1]:%d.%m.}", "value": fmt_value(float(s.iloc[-1]), ",.1f"),
                                "unit": "€/MWh", "sub": f"Ø 7 dní {fmt_value(w7, ',.1f')} · vs. průměr 5 let "
                                                        f"<strong>{_delta(vs5, unit='€')}</strong>"})
    ctx["table"] = table(pd.DataFrame(rows), [
        ("zóna", "Zóna", None), ("den", "Den dodávky", "%d.%m.%Y"), ("v", "Base [€/MWh]", ",.1f"),
        ("w7", "Ø 7 dní", ",.1f"), ("m30", "Ø 30 dní", ",.1f"), ("vs5", "Ø 7 dní vs. průměr 5 let", "+,.1f"),
        ("sp7", "Spread vs. DE (Ø 7 dní)", "+,.1f")])
    return ctx


@chart("v2-trh-eu", "prehled", "multi")
def _ch_eu_multi(p):
    from charts.v2 import fig_small_multiples, seasonal
    pr = _eu_prices()
    if pr.empty:
        return None
    panels = [(_zn(z), seasonal(pr[z], smooth=7)) for z in EU_ZONES if z in pr]
    return fig_small_multiples(panels, "€/MWh", cols=3, fmt=".0f",
                               title="Base cena zón (Ø 7 dní) — letos vs. pásmo 5 let, stejné měřítko")


@chart("v2-trh-eu", "prehled", "spreads")
def _ch_eu_spreads(p):
    from charts.v2 import fig_multiples_ts
    pr = _eu_prices()
    if pr.empty or "DE_LU" not in pr:
        return None
    lo = pr.index[-1] - timedelta(days=90)
    panels = [(f"{_zn(z)} − DE", (pr[z] - pr["DE_LU"])[pr.index >= lo]) for z in EU_ZONES if z in pr and z != "DE_LU"]
    return fig_multiples_ts(panels, "€/MWh", cols=4, fmt=".1f",
                            title="Spread proti DE (base, denně, 90 dní; kladný = dražší než DE)")


@chart("v2-trh-eu", "prehled", "load")
def _ch_eu_load(p):
    from charts.v2 import fig_seasonal, seasonal
    s = _eu_load("CZ")
    if s.empty:
        return None
    return fig_seasonal(seasonal(s, smooth=7), "CZ — spotřeba (Ø GW, klouzavý průměr 7 dní): letos vs. pásmo 5 let",
                        "GW", fmt=".2f", height=360)


# ── Počasí ──────────────────────────────────────────────────────────────

page("v2-pocasi", "Počasí — nový vzhled", "🌦")
WX_REGIONS = ["CZ", "DE", "NORDIC"]
WX_NAMES = {"CZ": "Česko", "DE": "Německo", "NORDIC": "Nordic"}


@ttl_cache(ttl=900, maxsize=16)
def _wx(region: str, var: str):
    """(denní řada pozorování, normál po dnech v roce, poslední předpověď ECMWF IFS po dnech)."""
    from web.tabs import weather as wt
    import data.weather as w
    d = wt._daily(region)
    if d.empty or var not in set(d["var"]):
        return pd.Series(dtype=float), pd.Series(dtype=float), pd.Series(dtype=float)
    dv = d[d["var"] == var]
    idx = pd.to_datetime(dv["date"], utc=True).dt.tz_convert("Europe/Prague").dt.tz_localize(None).dt.normalize()
    obs = pd.Series(dv["value"].values, index=idx).sort_index()
    obs = obs[~obs.index.duplicated(keep="last")]
    nrm = w.normals(d, var)
    normal = nrm.set_index("doy")["mean"] if not nrm.empty else pd.Series(dtype=float)
    fc = wt._latest_daily_fc(region, var)
    fcs = pd.Series(dtype=float)
    if not fc.empty:
        m = fc[fc["model"].astype(str).str.startswith("ecmwf_ifs")]
        m = m if not m.empty else fc
        fi = pd.to_datetime(m["date"], utc=True).dt.tz_convert("Europe/Prague").dt.tz_localize(None).dt.normalize()
        fcs = pd.Series(m["value"].values, index=fi).groupby(level=0).mean().sort_index()
        fcs = fcs[fcs.index > obs.index[-1]] if not obs.empty else fcs
    return obs, normal, fcs


def _anom(s, normal):
    if s.empty or normal.empty:
        return pd.Series(dtype=float)
    return pd.Series(s.values - s.index.dayofyear.map(normal).values, index=s.index)


@tab("v2-pocasi", "prehled", "Přehled")
def wx_overview(p):
    import data.weather as w
    ctx = {"query": p.query(), "kpis": [], "rows": []}
    rows = []
    for r in WX_REGIONS:
        for var in ("temp", "wind100", "solar"):
            obs, normal, fc = _wx(r, var)
            if obs.empty:
                continue
            o7 = obs[obs.index > obs.index[-1] - timedelta(days=7)]
            a7 = _anom(o7, normal).mean() if not normal.empty else None
            f7 = fc[fc.index <= obs.index[-1] + timedelta(days=7)]
            fa7 = _anom(f7, normal).mean() if not f7.empty and not normal.empty else None
            row = {"oblast": WX_NAMES[r], "veličina": w.VAR_LABELS.get(var, var), "jedn": w.VAR_UNITS[var],
                   "do": obs.index[-1], "o7": o7.mean(), "a7": a7, "f7": f7.mean() if not f7.empty else None, "fa7": fa7}
            if var == "temp":
                f14 = fc[fc.index <= obs.index[-1] + timedelta(days=14)]
                if not f14.empty and not normal.empty:
                    row["hdd"] = float(w.hdd(f14.values).sum())
                    row["hdd_n"] = float(w.hdd(f14.index.dayofyear.map(normal).values).sum())
                ctx["kpis"].append({"label": f"{WX_NAMES[r]} teplota, příštích 7 dní",
                                    "value": fmt_value(fa7, "+,.1f") if fa7 is not None and fa7 == fa7 else "—",
                                    "unit": "°C vs. normál",
                                    "sub": f"posledních 7 dní {_delta(a7, unit='°C')}" +
                                           (f" · HDD 14 dní {fmt_value(row['hdd'], ',.0f')} (normál "
                                            f"{fmt_value(row['hdd_n'], ',.0f')})" if "hdd" in row else "")})
            rows.append(row)
    ctx["table"] = table(pd.DataFrame(rows), [
        ("oblast", "Oblast", None), ("veličina", "Veličina", None), ("jedn", "Jednotka", None),
        ("do", "Pozorování do", "%d.%m.%Y"), ("o7", "Ø 7 dní", ",.1f"), ("a7", "vs. normál", "+,.1f"),
        ("f7", "Předpověď Ø 7 dní", ",.1f"), ("fa7", "vs. normál", "+,.1f"), ("hdd", "HDD 14 dní", ",.0f"),
        ("hdd_n", "HDD normál", ",.0f")]) if rows else None
    ctx["has_data"] = bool(rows)
    return ctx


def _wx_season_fig(region, var, title, unit, fmt=".1f", smooth=7, height=400):
    from charts.v2 import fig_seasonal, seasonal
    import plotly.graph_objects as go
    obs, normal, fc = _wx(region, var)
    if obs.empty:
        return None
    sz = seasonal(obs, smooth=smooth)
    fig = fig_seasonal(sz, title, unit, fmt=fmt, height=height)
    if not fc.empty:
        # navázat předpověď na poslední vyhlazenou hodnotu
        both = pd.concat([obs[obs.index > obs.index[-1] - timedelta(days=smooth)], fc])
        sm = both.rolling(smooth, min_periods=1).mean()[fc.index]
        x = [obs.index[-1]] + list(fc.index)
        y = [float(sz["cur"].dropna().iloc[-1]) if not sz["cur"].dropna().empty else None] + list(sm.round(2).values)
        fig.add_trace(go.Scatter(x=x, y=y, mode="lines", name="předpověď ECMWF", line=dict(width=2, dash="dot"),
                                 hovertemplate=f"předpověď %{{x|%d.%m.}}: %{{y:{fmt}}} {unit}<extra></extra>"))
    return fig


@chart("v2-pocasi", "prehled", "temp")
def _ch_wx_temp(p):
    return _wx_season_fig("CZ", "temp", "Česko — teplota (Ø 7 dní): letos vs. pásmo 5 let + předpověď ECMWF", "°C")


@chart("v2-pocasi", "prehled", "anom")
def _ch_wx_anom(p):
    from charts.v2 import fig_multiples_ts
    panels = []
    for r in WX_REGIONS:
        obs, normal, fc = _wx(r, "temp")
        if obs.empty or normal.empty:
            continue
        s = pd.concat([obs[obs.index > obs.index[-1] - timedelta(days=60)], fc])
        panels.append((f"{WX_NAMES[r]} (pozorování + předpověď)", _anom(s, normal)))
    if not panels:
        return None
    return fig_multiples_ts(panels, "°C", cols=3, fmt="+.1f",
                            title="Odchylka teploty od normálu — 60 dní pozorování + předpověď ECMWF, stejné měřítko")


@chart("v2-pocasi", "prehled", "wind")
def _ch_wx_wind(p):
    return _wx_season_fig("DE", "wind100", "Německo — vítr ve 100 m (Ø 7 dní): letos vs. pásmo 5 let + předpověď",
                          "m/s", height=340)


@chart("v2-pocasi", "prehled", "solar")
def _ch_wx_solar(p):
    return _wx_season_fig("DE", "solar", "Německo — sluneční záření (Ø 7 dní): letos vs. pásmo 5 let + předpověď",
                          "kWh/m²/den", fmt=".2f", height=340)
