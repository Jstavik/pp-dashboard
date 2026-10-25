"""Plyn — záložky dřívější app.py sekce "🔵 Plyn".

Fyzické toky ENTSO-G se čtou jako category (celá historie od 2020 ~35 MB
místo ~1 GB), proto už není potřeba přepínač "načíst celou historii" —
sezonnost i tlačítka "Maximum" mají plnou historii vždy. Před předáním do
charts/ se vyfiltrovaný podvýběr předagreguje / převede na object
(web.util.decat), viz data/entsog.py::load_entsog_history.
"""
from datetime import date, timedelta

import pandas as pd

from config import (
    GAS_FLOWS_DEFAULT_RANGE_DAYS, LNG_DEFAULT_RANGE_DAYS, GASSCO_DEFAULT_RANGE_DAYS,
    ENTSOG_NOMINATION_DEFAULT_MONTHS, storage_color,
)
from core.cache import ttl_cache
from web.registry import page, tab, chart, csv
from web.util import decat, options, table, status_item, safe_max_date, date_range_from_params

PAGE = "plyn"
page(PAGE, "Plyn", "🔵")

CHART_TYPES = ["Linie", "Plocha", "Sloupcový"]
FLOW_PRESETS = [("Týden", 7), ("Měsíc", 30), ("Kvartál", 90), ("Půlrok", 182), ("Rok", 365), ("Maximum", "max")]
SHORT_PRESETS = [("Týden", 7), ("Měsíc", 30), ("3M", 90), ("Rok", 365), ("Max", "max")]


def flows() -> pd.DataFrame:
    """Fyzické toky (celá historie, category). Prázdný DataFrame, pokud
    historie chybí (fallback live API v load_entsog_history vrací jiný
    tvar dat — pivot — a web ho nepoužívá)."""
    from data.entsog import load_entsog_history
    df = load_entsog_history(categorical=True)
    if df.empty or "countryLabel" not in df.columns:
        return pd.DataFrame(columns=["countryKey", "countryLabel", "directionKey", "adjacentSystemsKey",
                                     "pointsNames", "indicator", "value_GWh", "date"])
    return df


def _date_bounds(df: pd.DataFrame) -> tuple:
    d = df["date"].dt.tz_convert("Europe/Prague")
    return d.min().date(), d.max().date()


def _cascade(p, df: pd.DataFrame) -> dict:
    """Kaskáda Země → Směr → Systém → Bod (jako dřív st.multiselect)."""
    countries = options(df["countryLabel"])
    sel_c = p.list("countries", default=["Czechia"] if "Czechia" in countries else [], choices=countries)
    d1 = df[df["countryLabel"].isin(sel_c)] if sel_c else df
    dirs = options(d1["directionKey"])
    sel_d = p.list("directions", default=dirs, choices=dirs)
    d2 = d1[d1["directionKey"].isin(sel_d)] if sel_d else d1
    systems = options(d2["adjacentSystemsKey"])
    sel_s = p.list("systems", default=[], choices=systems)
    d3 = d2[d2["adjacentSystemsKey"].isin(sel_s)] if sel_s else d2
    points = options(d3["pointsNames"])
    sel_p = p.list("points", default=[], choices=points)
    d4 = d3[d3["pointsNames"].isin(sel_p)] if sel_p else d3
    return {"countries": countries, "sel_countries": sel_c, "dirs": dirs, "sel_dirs": sel_d,
            "systems": systems, "sel_systems": sel_s, "points": points, "sel_points": sel_p, "df": d4}


def _cascade_query(p, c: dict, **extra) -> str:
    return p.query(countries__set="1", countries=c["sel_countries"], directions__set="1",
                   directions=c["sel_dirs"], systems__set="1", systems=c["sel_systems"],
                   points__set="1", points=c["sel_points"], **extra)


def _ctx_cascade(c: dict) -> dict:
    return {k: v for k, v in c.items() if k != "df"}


# ── Mapa ────────────────────────────────────────────────────────────────

def _gie():
    from data.gie import load_gie_all
    return load_gie_all()


VIEWS = ["schema", "mapa"]
VIEW_LABELS = {"schema": "🔀 Schéma toků (CEE)", "mapa": "🗺️ Geografická mapa"}
UNITS = ["gwh", "mcm"]
UNIT_LABELS = {"gwh": "GWh/d", "mcm": "mcm/d"}
COMPARE = ["d", "w"]
COMPARE_LABELS = {"d": "s předchozím dnem", "w": "s předchozím týdnem"}


def _flow_state(p):
    """Vybraný den (posuvník/kalendář), srovnání, jednotka — pro schéma i export."""
    from core import gasflow as gf
    days = gf.available_days()
    last = days[-1] if days else None
    day = p.date("date", last) if last else None
    if day and days and day not in days:
        older = [d for d in days if d <= day]
        day = older[-1] if older else days[0]
    return {"days": days, "day": day, "compare": p.str("cmp", "d", choices=COMPARE),
            "unit": p.str("unit", "gwh", choices=UNITS)}


def _mismatch_table(df, unit="gwh", limit=None):
    from core.gasflow import fmt_num
    if df is None or df.empty:
        return None
    rows = []
    for r in (df.head(limit) if limit else df).itertuples(index=False):
        rows.append([f"{r.day:%d.%m.%Y}", "hranice (ENTSO-G souhrn)" if r.level == "hranice" else "bod (operátoři)",
                     {"Physical Flow": "fyzický tok", "Allocation": "alokace", "Renomination": "renominace"}[r.indicator],
                     f"{r[3]} → {r[4]}", r.point, fmt_num(r.exit, unit, 1), fmt_num(r.entry, unit, 1),
                     ("+" if r.diff > 0 else "−") + fmt_num(abs(r.diff), unit, 1), f"{r.rel:.0f} %"])
    u = UNIT_LABELS[unit]
    return {"headers": ["Den", "Úroveň", "Veličina", "Směr", "Bod / hranice", f"Exit A ({u})", f"Entry B ({u})",
                        "Rozdíl B − A", "Rozdíl %"], "rows": rows, "total": len(df),
            "truncated": bool(limit and len(df) > limit)}


@tab(PAGE, "mapa", "Mapa")
def mapa(p):
    from data.gassco import load_gassco
    from data.hydro import load_hydro
    view = p.str("view", "schema", choices=VIEWS)
    df = flows()
    df_gie = _gie()
    last_gie = None
    storage = []
    if not df_gie.empty:
        g = df_gie.dropna(subset=["gasDayStart"]).sort_values("gasDayStart")
        last_gie = g["gasDayStart"].max()
        last = g.groupby("country_code").last().reset_index()
        last = last[last["country_code"] != "EU"].sort_values("country_code")
        icons = {"#C62828": "🔴", "#FF8F00": "🟠", "#1565C0": "🔵", "#2E7D32": "🟢"}
        for _, r in last.iterrows():
            full = float(str(r.get("full", 0)).replace(",", ".") or 0)
            net = float(r.get("injection", 0) or 0) - float(r.get("withdrawal", 0) or 0)
            storage.append({"cc": r["country_code"], "full": full, "icon": icons[storage_color(full)],
                            "net": net, "twh": float(r.get("gasInStorage", 0) or 0)})
    ctx = {
        "status": [
            status_item("ENTSO-G", safe_max_date(df, "date"), 48),
            status_item("GIE zásobníky", last_gie, 72),
            status_item("GASSCO", safe_max_date(load_gassco(), "date"), 24),
            status_item("Hydro", safe_max_date(load_hydro(), "date"), 200),
        ],
        "empty": df.empty, "storage": storage, "d2": date.today() - timedelta(days=2),
        "view": view, "views": VIEWS, "view_labels": VIEW_LABELS,
    }
    if view == "schema" and not df.empty:
        from charts.flowchart import render_svg
        from core import gasflow as gf
        st = _flow_state(p)
        ctx.update(st, units=UNITS, unit_labels=UNIT_LABELS, compares=COMPARE, compare_labels=COMPARE_LABELS)
        if st["day"]:
            fc = gf.flowchart(st["day"], st["compare"], st["unit"])
            ctx["svg"] = render_svg(fc, st["unit"])
            ctx["slider_days"] = [d.isoformat() for d in st["days"][-120:]]
            ctx["slider_i"] = ctx["slider_days"].index(st["day"].isoformat()) if st["day"].isoformat() in \
                ctx["slider_days"] else len(ctx["slider_days"]) - 1
            ctx["mismatch"] = _mismatch_table(gf.mismatches(st["day"]), st["unit"], limit=12)
            ctx["thresholds"] = gf.thresholds()
            ctx["export_q"] = p.query(view="schema", date=st["day"].isoformat(), cmp=st["compare"], unit=st["unit"])
    return ctx


@csv("flowchart")
def _csv_flowchart(p):
    from core import gasflow as gf
    st = _flow_state(p)
    if not st["day"]:
        return pd.DataFrame(), "toky_cee.csv"
    df = gf.flowchart_table(gf.flowchart(st["day"], st["compare"], st["unit"])).set_index("typ")
    return df, f"toky_cee_{st['day']:%Y%m%d}.csv"


@csv("entry-exit")
def _csv_entry_exit(p):
    st = _ee_state(p)
    return st["df"].set_index("day"), f"entry_exit_{st['d_to']:%Y%m%d}.csv"


def _ee_state(p):
    """Záložka Entry/exit: rozsah dní + prahy (výchozí z prostředí)."""
    from core import gasflow as gf
    rel0, abs0 = gf.thresholds()
    days = gf.available_days()
    last = days[-1] if days else date.today()
    d_to = p.date("to", last)
    d_from = p.date("from", d_to - timedelta(days=6))
    if d_from > d_to:
        d_from = d_to
    rel = p.float("rel", rel0, lo=0, hi=100)
    ab = p.float("abs", abs0, lo=0, hi=10000)
    levels = p.list("levels", default=["hranice", "bod"], choices=["hranice", "bod"])
    parts = []
    for d in [x for x in days if d_from <= x <= d_to][-31:]:
        m = gf.mismatches(d, rel, ab)
        if not m.empty:
            parts.append(m)
    df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(
        columns=["day", "level", "indicator", "from", "to", "point", "exit", "entry", "diff", "rel", "flag", "detail"])
    df = df[df["level"].isin(levels)].drop(columns="flag")
    return {"df": df, "d_from": d_from, "d_to": d_to, "rel": rel, "abs": ab, "levels": levels,
            "min_d": days[0] if days else None, "max_d": last}


@tab(PAGE, "entry-exit", "Entry/exit")
def entry_exit(p):
    st = _ee_state(p)
    df = st["df"]
    by_pair = (df.groupby(["from", "to", "level"]).size().reset_index(name="n").sort_values("n", ascending=False)
               if not df.empty else df)
    return {**{k: v for k, v in st.items() if k != "df"}, "n": len(df),
            "table": _mismatch_table(df.assign(_a=df["diff"].abs()).sort_values(["day", "_a"], ascending=False)
                                     .drop(columns="_a"), "gwh", limit=300),
            "pairs": by_pair.head(8).to_dict("records") if not df.empty else [],
            "level_labels": {"hranice": "hranice (ENTSO-G souhrn: fyzický tok, alokace)",
                             "bod": "body (renominace operátorů)"},
            "query": p.query(**{"from": st["d_from"], "to": st["d_to"]})}


@chart(PAGE, "mapa", "map", displayModeBar=True, scrollZoom=True)
def _ch_map(p):
    from data.gassco import load_gassco
    from charts.gas import fig_gas_map
    df = flows()
    if df.empty:
        return None
    from charts.gas import add_mismatch_markers
    from core import gasflow as gf
    cutoff = df["date"].max() - pd.Timedelta(days=14)
    fig = fig_gas_map(decat(df[df["date"] >= cutoff]), df_gassco=load_gassco())
    day = gf.latest_day()
    return add_mismatch_markers(fig, gf.mismatches(day)) if day else fig


# ── Toky ────────────────────────────────────────────────────────────────

def _toky_state(p):
    df = flows()
    if df.empty:
        return None
    c = _cascade(p, df)
    min_d, max_d = _date_bounds(df)
    d_from, d_to = date_range_from_params(p, max_d, GAS_FLOWS_DEFAULT_RANGE_DAYS, min_date=min_d)
    c.update(min_d=min_d, max_d=max_d, d_from=d_from, d_to=d_to,
             chart_type=p.str("chart_type", "Linie", choices=CHART_TYPES))
    return c


@tab(PAGE, "toky", "Toky")
def toky(p):
    s = _toky_state(p)
    if s is None:
        return {"empty": True}
    ctx = _ctx_cascade(s)
    ctx.update(chart_types=CHART_TYPES, presets=FLOW_PRESETS,
               query=_cascade_query(p, s, range=None, **{"from": s["d_from"], "to": s["d_to"]}))
    return ctx


@chart(PAGE, "toky", "timeseries")
def _ch_toky(p):
    from charts.gas import fig_flow_timeseries
    s = _toky_state(p)
    if s is None:
        return None
    df = s["df"]
    ts_from = pd.Timestamp(s["d_from"]).tz_localize("UTC")
    ts_to = pd.Timestamp(s["d_to"]).tz_localize("UTC")
    df = df[(df["date"] >= ts_from) & (df["date"] <= ts_to)]
    # předagregace (země, bod, den) — graf stejně sčítá přes ostatní sloupce
    agg = (df.groupby(["countryLabel", "pointsNames", "date"], observed=True)["value_GWh"]
             .sum().reset_index())
    return fig_flow_timeseries(decat(agg), [], [], [], [], s["chart_type"])


# ── Sezonnost ───────────────────────────────────────────────────────────

def _season_state(p):
    df = flows()
    if df.empty:
        return None
    c = _cascade(p, df)
    years = sorted(int(y) for y in df["date"].dt.tz_convert("Europe/Prague").dt.year.unique())
    c.update(years=years, sel_years=[int(y) for y in p.list("years", default=years[-5:], choices=years)],
             chart_type=p.str("chart_type", "Linie", choices=CHART_TYPES))
    return c


@tab(PAGE, "sezonnost", "Sezonnost")
def sezonnost(p):
    s = _season_state(p)
    if s is None:
        return {"empty": True}
    ctx = _ctx_cascade(s)
    ctx.update(chart_types=CHART_TYPES,
               query=_cascade_query(p, s, years__set="1", years=s["sel_years"]))
    return ctx


@chart(PAGE, "sezonnost", "season")
def _ch_season(p):
    from charts.gas import fig_flow_seasonality
    s = _season_state(p)
    if s is None:
        return None
    agg = s["df"].groupby("date", observed=True)["value_GWh"].sum().reset_index()
    return fig_flow_seasonality(agg, [], [], [], [], s["sel_years"], s["chart_type"])


# ── Kapacity ────────────────────────────────────────────────────────────

def _cap_state(p):
    from data.entsog_operational import EU_COUNTRY_NAMES
    df = flows()
    df = df[df["indicator"] == "Physical Flow"] if not df.empty else df
    if df.empty:
        return None
    countries = options(df["countryKey"])
    country = p.str("country", "CZ" if "CZ" in countries else countries[0], choices=countries)
    direction = p.str("dir", "entry", choices=["entry", "exit"])
    date_from = pd.Timestamp.now(tz="Europe/Prague").normalize() - pd.DateOffset(days=365)
    sel = df[(df["countryKey"] == country) & (df["directionKey"] == direction) & (df["date"] >= date_from)]
    sel = decat(sel)
    s = {"countries": countries, "country": country, "dir": direction, "names": EU_COUNTRY_NAMES,
         "date_from": date_from, "flow": sel, "points": options(sel["pointsNames"]) if not sel.empty else []}
    if not s["points"]:
        return s
    s["point"] = p.str("point", s["points"][0], choices=s["points"])
    parts = [x.strip() for x in s["point"].split("|")]
    s["single_points"] = parts
    s["single"] = p.str("single", parts[0], choices=parts)
    from data.entsog_operational import load_eu_operational_open_ended
    oe = load_eu_operational_open_ended()
    oe_pt = oe[(oe["pointLabel"] == s["single"]) & (oe["directionKey"] == direction)] if not oe.empty else oe
    s["oe_point"] = oe_pt
    s["operators"] = options(oe_pt["operatorLabel"]) if not oe_pt.empty else []
    if s["operators"]:
        s["operator"] = p.str("operator", s["operators"][0], choices=s["operators"])
    s["show_tech"] = p.bool("tech", True)
    s["show_avail"] = p.bool("avail", False)
    s["show_int"] = p.bool("interrupt", False)
    s["forward"] = p.int("forward", 30, 0, 90)
    return s


@tab(PAGE, "kapacity", "Kapacity")
def kapacity(p):
    s = _cap_state(p)
    if s is None:
        return {"empty": True}
    ctx = {k: v for k, v in s.items() if k not in ("flow", "oe_point")}
    ctx["country_label"] = s["names"].get(s["country"], s["country"])
    return ctx


@chart(PAGE, "kapacity", "stacked")
def _ch_cap_stacked(p):
    from charts.capacity import fig_flow_stacked
    s = _cap_state(p)
    if s is None or s["flow"].empty:
        return None
    return fig_flow_stacked(s["flow"], s["names"].get(s["country"], s["country"]), s["dir"])


@chart(PAGE, "kapacity", "detail")
def _ch_cap_detail(p):
    from data.entsog_operational import active_capacity_dedup
    from charts.capacity import fig_point_capacity
    s = _cap_state(p)
    if s is None or not s.get("operators"):
        return None
    oe = s["oe_point"]
    oe = decat(oe[oe["operatorLabel"] == s["operator"]])
    target = pd.date_range(s["date_from"], pd.Timestamp.now(tz="Europe/Prague").normalize()
                           + pd.Timedelta(days=s["forward"]), freq="D")
    layers = {}
    if s["show_tech"]:
        layers["Firm Technical"] = active_capacity_dedup(oe[oe["indicator"] == "Firm Technical"], target)
    if s["show_avail"]:
        layers["Firm Available"] = active_capacity_dedup(oe[oe["indicator"] == "Firm Available"], target)
    interruptions = pd.DataFrame()
    if s["show_int"]:
        is_int = oe["indicator"].str.contains("interruption", case=False, na=False)
        interruptions = oe[is_int & (oe["value_GWh"] > 0)].copy()
        if not interruptions.empty:
            interruptions["_upd"] = pd.to_datetime(interruptions["lastUpdateDateTime"], utc=True)
            interruptions = (interruptions.sort_values("_upd")
                             .drop_duplicates(subset=["periodFrom_dt", "periodTo_dt", "indicator"], keep="last"))
    flow = s["flow"]
    flow_series = flow[flow["pointsNames"] == s["point"]].groupby("date")["value_GWh"].sum()
    return fig_point_capacity(s["single"], s["operator"], s["dir"], flow_series, layers, interruptions)


# ── Nominace ────────────────────────────────────────────────────────────

def _nom_state(p):
    from data.entsog_operational import (
        operational_index, load_operational_point, load_eu_operational_open_ended,
        EU_COUNTRY_NAMES, HISTORY_START, NOMINATION_CHART_INDICATORS, QUALITY_INDICATORS,
    )
    today = date.today()
    max_months = (today.year - HISTORY_START.year) * 12 + (today.month - HISTORY_START.month) + 1
    months = p.int("months", min(ENTSOG_NOMINATION_DEFAULT_MONTHS, max_months), 1, max_months)
    load_from = (pd.Timestamp(today) - pd.DateOffset(months=months)).date()
    idx = operational_index(load_from)
    if idx.empty:
        return None
    countries = sorted(idx["country"].unique())
    country = p.str("country", "CZ" if "CZ" in countries else countries[0], choices=countries)
    sub = idx[idx["country"] == country]
    indicators = [i for i in NOMINATION_CHART_INDICATORS if i in set(sub["indicator"])]
    indicator = p.str("indicator", indicators[0] if indicators else None, choices=indicators)
    points = sorted(sub["pointLabel"].unique())
    default_point = "VIP Brandov" if country == "CZ" and "VIP Brandov" in points else (points[0] if points else None)
    point = p.str("point", default_point, choices=points)
    s = {"months": months, "max_months": max_months, "countries": countries, "country": country,
         "names": EU_COUNTRY_NAMES, "indicators": indicators, "indicator": indicator, "points": points,
         "point": point, "chart_type": p.str("chart_type", "Linie", choices=CHART_TYPES), "today": today}
    df_pt = decat(load_operational_point(point, country, load_from)) if point else pd.DataFrame()
    s["df_point"] = df_pt
    if not df_pt.empty:
        min_d, max_d = df_pt["periodFrom_dt"].min(), df_pt["periodFrom_dt"].max()
    else:
        min_d = max_d = today
    s["min_d"], s["max_d"] = min_d, max_d
    d_from = p.date("from", max(min_d, max_d - timedelta(days=365)))
    d_to = p.date("to", max_d)
    if d_from > d_to:
        d_from, d_to = min_d, max_d
    s["d_from"], s["d_to"] = d_from, d_to
    oe = load_eu_operational_open_ended()
    s["oe_point"] = (decat(oe[(oe["country"] == country) & (oe["pointLabel"] == point)])
                     if not oe.empty and point else pd.DataFrame())
    s["qualities"] = [q for q in QUALITY_INDICATORS
                      if not df_pt.empty and q in set(df_pt["indicator"].dropna().unique())]
    s["quality"] = p.str("quality", s["qualities"][0] if s["qualities"] else None, choices=s["qualities"])
    return s


@tab(PAGE, "nominace", "Nominace")
def nominace(p):
    s = _nom_state(p)
    if s is None:
        return {"empty": True}
    df_pt, oe = s["df_point"], s["oe_point"]
    raw = pd.DataFrame()
    if not df_pt.empty and s["indicator"]:
        raw = df_pt[df_pt["indicator"] == s["indicator"]].sort_values("periodFrom_dt", ascending=False).head(60)
    cap_cols = [("indicator", "Typ", None), ("directionKey", "Směr", None), ("periodFrom_dt", "Od", None),
                ("periodTo_dt", "Do", None), ("value_GWh", "Hodnota [GWh/d]", ",.2f")]
    cap = intr = pd.DataFrame()
    if not oe.empty:
        is_int = oe["indicator"].str.contains("interruption", case=False, na=False)
        live = oe["periodTo_dt"] >= s["today"]
        cap = oe[~is_int & live].sort_values(["periodFrom_dt", "indicator", "directionKey"])
        intr = oe[is_int & live].sort_values("periodFrom_dt")
    ctx = {k: v for k, v in s.items() if k not in ("df_point", "oe_point")}
    ctx.update(
        chart_types=CHART_TYPES, has_oe=not oe.empty,
        raw=table(raw, [("periodFrom_dt", "Datum", None), ("directionKey", "Směr", None),
                        ("value_GWh", "Hodnota [GWh/d]", ",.2f"), ("operatorLabel", "Operátor", None),
                        ("unit", "Jednotka", None)]),
        cap=table(cap, cap_cols), intr=table(intr, cap_cols),
        query=p.query(months=s["months"], country=s["country"], indicator=s["indicator"], point=s["point"],
                      **{"from": s["d_from"], "to": s["d_to"]}),
    )
    return ctx


@chart(PAGE, "nominace", "nom")
def _ch_nom(p):
    from charts.entsog_operational import fig_cz_operational
    s = _nom_state(p)
    if s is None or s["df_point"].empty or not s["indicator"]:
        return None
    return fig_cz_operational(s["df_point"], s["point"], s["indicator"], s["d_from"], s["d_to"], s["chart_type"])


@chart(PAGE, "nominace", "quality")
def _ch_quality(p):
    from charts.entsog_operational import fig_cz_operational
    s = _nom_state(p)
    if s is None or not s["quality"]:
        return None
    return fig_cz_operational(s["df_point"], s["point"], s["quality"], s["d_from"], s["d_to"], s["chart_type"])


# ── Zásobníky ───────────────────────────────────────────────────────────

def _stor_state(p):
    from data.gie import VARIABLES
    df = _gie()
    if df.empty:
        return None
    years = sorted(int(y) for y in df["gasDayStart"].dt.year.unique())
    countries = sorted(df["country_code"].unique().tolist())
    var_keys = list(VARIABLES.keys())
    return {
        "df": df, "years": years, "countries": countries, "var_keys": var_keys,
        "var_labels": {k: v[0] for k, v in VARIABLES.items()},
        "country": p.str("country", "CZ" if "CZ" in countries else countries[0], choices=countries),
        "var": p.str("var", var_keys[0], choices=var_keys),
        "sel_years": [int(y) for y in p.list("years", default=years[-5:], choices=years)],
        "var_grid": p.str("var_grid", var_keys[0], choices=var_keys),
        "sel_years_grid": [int(y) for y in p.list("years_grid", default=years[-5:], choices=years)],
    }


@tab(PAGE, "zasobniky", "Zásobníky")
def zasobniky(p):
    s = _stor_state(p)
    if s is None:
        return {"empty": True}
    ctx = {k: v for k, v in s.items() if k != "df"}
    ctx["query"] = p.query(years__set="1", years=s["sel_years"], years_grid__set="1", years_grid=s["sel_years_grid"])
    return ctx


@chart(PAGE, "zasobniky", "main")
def _ch_stor_main(p):
    from charts.storage import fig_storage_main
    s = _stor_state(p)
    if s is None or not s["sel_years"]:
        return None
    return fig_storage_main(s["df"], s["country"], s["var"], s["sel_years"])


@chart(PAGE, "zasobniky", "grid")
def _ch_stor_grid(p):
    from charts.storage import fig_storage_grid
    s = _stor_state(p)
    if s is None or not s["sel_years_grid"]:
        return None
    return fig_storage_grid(s["df"], s["var_grid"], s["sel_years_grid"])


# ── LNG ─────────────────────────────────────────────────────────────────

def _lng_state(p):
    from data.lng import load_lng
    df = flows()
    lng = (df[(df["adjacentSystemsKey"] == "LNG Terminals") & (df["directionKey"] == "entry")]
           if not df.empty else df)
    lng = decat(lng)
    s = {"lng": lng}
    if not lng.empty:
        d = lng["date"].dt.tz_convert("Europe/Prague")
        max_d, min_d = d.dt.date.max(), d.dt.date.min()
        s["countries"] = options(lng["countryLabel"])
        s["years"] = sorted(int(y) for y in d.dt.year.unique())
    else:
        max_d = min_d = date.today()
        s["countries"], s["years"] = [], []
    s["sel_countries"] = p.list("countries", default=[], choices=s["countries"])
    s["agg"] = p.str("agg", "Denní", choices=["Denní", "Týdenní", "Měsíční"])
    s["chart_type"] = p.str("chart_type", "Linie", choices=CHART_TYPES)
    s["d_from"], s["d_to"] = date_range_from_params(p, max_d, LNG_DEFAULT_RANGE_DAYS, min_date=min_d)
    s["sel_years"] = [int(y) for y in p.list("years", default=s["years"][-5:], choices=s["years"])]
    alsi = load_lng()
    s["alsi"] = alsi
    s["alsi_countries"] = sorted(alsi["country_code"].dropna().unique().tolist()) if not alsi.empty else []
    s["alsi_years"] = sorted(int(y) for y in alsi["gasDayStart"].dt.year.unique()) if not alsi.empty else []
    s["sel_alsi_countries"] = p.list("alsi_countries", default=[], choices=s["alsi_countries"])
    s["sel_alsi_years"] = [int(y) for y in p.list("alsi_years", default=s["alsi_years"][-5:], choices=s["alsi_years"])]
    return s


def _alsi_filtered(s):
    a = s["alsi"]
    return a[a["country_code"].isin(s["sel_alsi_countries"])] if s["sel_alsi_countries"] else a


@tab(PAGE, "lng", "LNG")
def lng_tab(p):
    s = _lng_state(p)
    ctx = {k: v for k, v in s.items() if k not in ("lng", "alsi")}
    ctx.update(chart_types=CHART_TYPES, presets=SHORT_PRESETS, has_flows=not s["lng"].empty,
               has_alsi=not s["alsi"].empty,
               query=p.query(range=None, years__set="1", years=s["sel_years"],
                             alsi_years__set="1", alsi_years=s["sel_alsi_years"],
                             **{"from": s["d_from"], "to": s["d_to"]}))
    return ctx


@chart(PAGE, "lng", "monthly")
def _ch_lng_monthly(p):
    from charts.lng import fig_lng_monthly_bars
    s = _lng_state(p)
    return None if s["lng"].empty else fig_lng_monthly_bars(s["lng"], s["sel_countries"])


@chart(PAGE, "lng", "sendout")
def _ch_lng_sendout(p):
    from charts.lng import fig_lng_sendout_timeseries
    s = _lng_state(p)
    if s["lng"].empty:
        return None
    return fig_lng_sendout_timeseries(s["lng"], s["sel_countries"], pd.Timestamp(s["d_from"]),
                                      pd.Timestamp(s["d_to"]), s["agg"], s["chart_type"])


@chart(PAGE, "lng", "season")
def _ch_lng_season(p):
    from charts.lng import fig_lng_seasonality
    s = _lng_state(p)
    return None if s["lng"].empty else fig_lng_seasonality(s["lng"], s["sel_countries"], s["sel_years"])


@chart(PAGE, "lng", "inventory")
def _ch_lng_inventory(p):
    from charts.lng import fig_lng_inventory
    s = _lng_state(p)
    return None if s["alsi"].empty else fig_lng_inventory(_alsi_filtered(s))


@chart(PAGE, "lng", "alsi_season")
def _ch_lng_alsi_season(p):
    from charts.lng import fig_lng_storage_seasonality
    s = _lng_state(p)
    return None if s["alsi"].empty else fig_lng_storage_seasonality(_alsi_filtered(s), s["sel_alsi_years"])


# ── GASSCO ──────────────────────────────────────────────────────────────

def _gassco_state(p):
    from data.gassco import load_gassco
    df = load_gassco()
    if df.empty:
        return None
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], utc=True)
    points = sorted(df["point"].unique().tolist())
    years = sorted(int(y) for y in df["date"].dt.year.unique())
    max_d = df["date"].max().date()
    d_from, d_to = date_range_from_params(p, max_d, GASSCO_DEFAULT_RANGE_DAYS, min_date=df["date"].min().date())
    return {
        "df": df, "points": points, "years": years, "d_from": d_from, "d_to": d_to,
        "sel_points": p.list("points", default=[x for x in ["Emden", "Dornum", "Zeebrugge", "Nybro"] if x in points],
                             choices=points),
        "sel_season_points": p.list("season_points", default=points, choices=points),
        "sel_years": [int(y) for y in p.list("years", default=years[-5:], choices=years)],
    }


def _umm_state(p):
    from data.gassco import (load_gassco_umm, list_available_umm_snapshot_dates, load_gassco_umm_snapshot,
                             _latest_per_base_id)
    umm = load_gassco_umm()
    if umm.empty:
        return None
    latest = _latest_per_base_id(umm)
    now_ts = pd.Timestamp.now(tz="UTC")
    active = latest[(latest["eventStatus"] != "Dismissed") & (latest["eventStop"] >= now_ts)]
    cancel_days = p.int("cancel_days", 14, 1, 90)
    cancel_since = now_ts - pd.Timedelta(days=cancel_days)
    cancelled = latest[(latest["eventStatus"] == "Dismissed") & (latest["publicationDateTime"] >= cancel_since)]
    outlook = p.int("outlook", 90, 7, 500)
    cutoff = now_ts.normalize() + pd.DateOffset(days=outlook)
    in_window = active[active["eventStart"] <= cutoff]
    dates = [d.strftime("%Y-%m-%d") for d in list_available_umm_snapshot_dates()]
    compare = p.str("compare", dates[0] if dates else None, choices=dates)
    s = {"umm": umm, "active": active, "cancelled": cancelled, "cancel_days": cancel_days,
         "cancel_since": cancel_since, "outlook": outlook, "in_window": in_window, "dates": dates,
         "compare": compare, "view": p.str("view", "Vedle sebe", choices=["Vedle sebe", "Rozdíl"]),
         "now": now_ts}
    if compare:
        days_back = (now_ts.normalize() - pd.Timestamp(compare, tz="UTC")).days
        past = load_gassco_umm_snapshot(days_back)
        past_latest = _latest_per_base_id(past)
        s["past"] = past
        s["past_active"] = (past_latest[past_latest["eventStatus"] != "Dismissed"]
                            if not past_latest.empty else past_latest)
    return s


@tab(PAGE, "gassco", "GASSCO")
def gassco(p):
    s = _gassco_state(p)
    if s is None:
        return {"empty": True}
    ctx = {k: v for k, v in s.items() if k != "df"}
    u = _umm_state(p)
    ctx["umm"] = None
    if u is not None:
        nearest = None
        if u["in_window"].empty and not u["active"].empty:
            ns = u["active"]["eventStart"].min()
            nearest = {"date": ns, "days": (ns.normalize() - u["now"].normalize()).days}
        ctx["umm"] = {k: u[k] for k in ("cancel_days", "outlook", "dates", "compare", "view")}
        ctx["umm"].update(has_active=not u["active"].empty, in_window=not u["in_window"].empty, nearest=nearest)
    ctx["presets"] = SHORT_PRESETS
    ctx["query"] = p.query(range=None, points__set="1", points=s["sel_points"], season_points__set="1",
                           season_points=s["sel_season_points"], years__set="1", years=s["sel_years"],
                           **{"from": s["d_from"], "to": s["d_to"]})
    return ctx


@chart(PAGE, "gassco", "kpi")
def _ch_gassco_kpi(p):
    from charts.gassco import fig_gassco_kpi
    s = _gassco_state(p)
    return None if s is None else fig_gassco_kpi(s["df"])


@chart(PAGE, "gassco", "timeseries")
def _ch_gassco_ts(p):
    from charts.gassco import fig_gassco_timeseries
    s = _gassco_state(p)
    if s is None:
        return None
    return fig_gassco_timeseries(s["df"], s["sel_points"], pd.Timestamp(s["d_from"], tz="UTC"),
                                 pd.Timestamp(s["d_to"], tz="UTC"))


@chart(PAGE, "gassco", "season")
def _ch_gassco_season(p):
    from charts.gassco import fig_gassco_seasonality
    s = _gassco_state(p)
    return None if s is None else fig_gassco_seasonality(s["df"], s["sel_season_points"], s["sel_years"])


@chart(PAGE, "gassco", "umm_active")
def _ch_umm_active(p):
    from charts.gassco import fig_gassco_umm_active
    u = _umm_state(p)
    return None if u is None else fig_gassco_umm_active(u["active"])


@chart(PAGE, "gassco", "umm_cancelled")
def _ch_umm_cancelled(p):
    from charts.gassco import fig_gassco_umm_cancelled
    u = _umm_state(p)
    return None if u is None else fig_gassco_umm_cancelled(u["cancelled"], u["cancel_since"].strftime("%d.%m.%Y"))


@chart(PAGE, "gassco", "umm_outlook")
def _ch_umm_outlook(p):
    from charts.gassco import fig_gassco_umm_outlook
    u = _umm_state(p)
    if u is None or u["in_window"].empty:
        return None
    return fig_gassco_umm_outlook(u["active"], u["outlook"])


@chart(PAGE, "gassco", "umm_delta_view")
def _ch_umm_delta_view(p):
    from charts.gassco import fig_gassco_umm_delta_bars, fig_gassco_umm_delta_diff
    u = _umm_state(p)
    if u is None or not u.get("compare"):
        return None
    label = pd.Timestamp(u["compare"]).strftime("%d.%m.%Y")
    fn = fig_gassco_umm_delta_bars if u["view"] == "Vedle sebe" else fig_gassco_umm_delta_diff
    return fn(u["active"], u["past_active"], u["outlook"], compare_label=label)


@chart(PAGE, "gassco", "umm_delta")
def _ch_umm_delta(p):
    from data.gassco import compute_umm_delta
    from charts.gassco import fig_gassco_umm_delta
    u = _umm_state(p)
    if u is None or not u.get("compare"):
        return None
    return fig_gassco_umm_delta(compute_umm_delta(u["umm"], u["past"]))


# ── Historie (CZ hraniční přechody) ─────────────────────────────────────

@ttl_cache(ttl=300, maxsize=1)
def _pivot_gas() -> pd.DataFrame:
    from data.entsog import _short_name
    df = flows()
    if df.empty:
        return pd.DataFrame()
    cz = decat(df[df["countryLabel"] == "Czechia"])
    cz["date_prague"] = cz["date"].dt.tz_convert("Europe/Prague").dt.normalize()
    cz["point_short"] = cz["pointsNames"].map(_short_name)
    entry = cz[cz["directionKey"] == "entry"].groupby(["date_prague", "point_short"])["value_GWh"].sum()
    exit_ = cz[cz["directionKey"] == "exit"].groupby(["date_prague", "point_short"])["value_GWh"].sum()
    pivot = (entry.unstack(fill_value=0) - exit_.unstack(fill_value=0)).fillna(0)
    pivot.index = pd.to_datetime(pivot.index)
    return pivot


@tab(PAGE, "historie", "Historie")
def historie(p):
    pivot = _pivot_gas()
    if pivot.empty:
        return {"empty": True}
    pts = [c for c in pivot.columns if pivot[c].abs().sum() > 0]
    return {"points": pts, "point": p.str("point", pts[0] if pts else None, choices=pts)}


@chart(PAGE, "historie", "point")
def _ch_hist_point(p):
    from charts.gas import fig_gas_point_history
    pivot = _pivot_gas()
    if pivot.empty:
        return None
    pts = [c for c in pivot.columns if pivot[c].abs().sum() > 0]
    point = p.str("point", pts[0] if pts else None, choices=pts)
    return fig_gas_point_history(pivot, point) if point else None


# Entry/exit jako poslední záložka (registruje se hned za Mapou kvůli sdíleným funkcím)
PAGES_TABS = __import__("web.registry", fromlist=["PAGES"]).PAGES[PAGE].tabs
PAGES_TABS.append(PAGES_TABS.pop(PAGES_TABS.index("entry-exit")))
