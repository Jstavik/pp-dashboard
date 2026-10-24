"""Nový vzhled — prototyp na 3 stránkách (PRESENTATION_PROPOSAL.md).

Každá stránka = jedna přehledová obrazovka bez záložek, odshora:
1. řádek KPI (hodnota + změna týden/týden + odchylka od průměru 5 let),
2. hlavní sezónní graf (letos vs. pásmo 5 let) s cíli a událostmi,
3. small multiples (země / zdroje / zóny se stejným měřítkem),
4. tabulka čísel (co je v grafech) + odkaz na detail ve starém vzhledu.

Stránky jsou registrované jako v2-plyn, v2-elektrina, v2-nordic; /plyn,
/elektrina a /nordic je zobrazí, když je zapnutý nový vzhled (cookie
ppdash_vzhled=novy, přepínač na stránce). Data a výpočty sdílí se starými
stránkami (core/explore.py, core/overview.py, data/*).
"""
from datetime import date, timedelta

import pandas as pd

from core import explore as ex
from core.cache import ttl_cache
from web.registry import chart, page, tab
from web.util import fmt_value, table

# původní stránka → stránka nového vzhledu
V2_PAGES = {"plyn": "v2-plyn", "elektrina": "v2-elektrina", "nordic": "v2-nordic"}

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
