"""Elektřina — záložky dřívější app.py sekce "⚡ Elektřina"."""
import logging
from datetime import date

import pandas as pd

from config import (
    COUNTRIES, COUNTRY_TIMEZONES, COUNTRY_NAMES, psr_lookup,
    OUTAGES_D7_COMPARISON_DAYS, OUTAGES_DELTA_MAX_WINDOW_DAYS, RESERVES_DEFAULT_RANGE_DAYS,
)
from core.cache import ttl_cache
from web.registry import page, tab, chart, csv
from web.services import entsoe_snapshot, reserves_or_empty
from web.util import table, status_item

log = logging.getLogger(__name__)

PAGE = "elektrina"
page(PAGE, "Elektřina", "⚡")

CHART_TYPES = ["Linie", "Plocha", "Sloupcový"]


def _now():
    return pd.Timestamp.now(tz="Europe/Prague")


# ── Odchylka & Generace ─────────────────────────────────────────────────

def _ceps():
    from data.ceps import fetch_ceps_all
    return fetch_ceps_all()


def _ceps_load_series(ceps: dict) -> pd.Series:
    load = ceps["load"]
    col = ("Load including pumping [MW]" if "Load including pumping [MW]" in load.columns
           else "Load [MW]" if "Load [MW]" in load.columns else None)
    return load[col] if col else pd.Series(dtype=float)


def _balancing(p):
    from charts.imbalance import balancing_strategy_ema
    ema_periods = p.int("ema", 4, 1, 8)
    threshold = p.int("thr", 50, 10, 150)
    benefit = p.float("benefit", 8.0, 0.0, 1000.0)
    ceps = _ceps()
    snap = entsoe_snapshot()
    imbal = ceps["imbal_mw"]
    if not imbal.empty:
        series = imbal["odchylka_MW"].resample("5min").mean().dropna()
    elif not snap["df_imbal"].empty:
        series = snap["df_imbal"]["odchylka_MWh"]
    else:
        series = None
    ema = signal = None
    if series is not None and not series.empty:
        ema, signal = balancing_strategy_ema(series, ema_periods, threshold)
    return {"ema_periods": ema_periods, "threshold": threshold, "benefit": benefit,
            "ema": ema, "signal": signal, "snap": snap}


@tab(PAGE, "dashboard", "Odchylka & Generace", refresh=60)
def dashboard(p):
    from charts.generation import render_mix_legend
    ceps = _ceps()
    snap = entsoe_snapshot()
    b = _balancing(p)
    n_int = int((b["signal"] != "STANDBY").sum()) if b["signal"] is not None else None
    now_naive = snap["now"].tz_localize(None)
    return {
        "status": [
            status_item("ČEPS", ceps["now"].date() if ceps.get("now") is not None else None, 1),
            status_item("ENTSO-E", now_naive, 48),
            status_item("DAP", now_naive, 48),
        ],
        "ema": b["ema_periods"], "thr": b["threshold"], "benefit": b["benefit"],
        "n_int": n_int, "benefit_total": (n_int or 0) * 0.25 * b["benefit"],
        "has_load_fc": not snap["load_fc"].empty,
        "has_gen": not snap["gen_raw"].empty,
        "mix_legend": render_mix_legend(snap["gen_raw"]),
    }


@chart(PAGE, "dashboard", "ceps_combined")
def _ch_ceps_combined(p):
    from charts.imbalance import fig_ceps_combined
    ceps = _ceps()
    snap = entsoe_snapshot()
    return fig_ceps_combined(ceps["imbal_mw"], ceps["price"], _ceps_load_series(ceps), snap["load_fc"], ceps["now"])


@chart(PAGE, "dashboard", "ceps_svr")
def _ch_ceps_svr(p):
    from charts.imbalance import fig_ceps_svr
    ceps = _ceps()
    return fig_ceps_svr(ceps["svr_named"], ceps["now"])


@chart(PAGE, "dashboard", "balancing")
def _ch_balancing(p):
    from charts.imbalance import fig_balancing_strategy
    b = _balancing(p)
    if b["ema"] is None:
        return None
    return fig_balancing_strategy(b["snap"]["df_imbal"], b["ema"], b["signal"], b["threshold"], b["snap"]["now"])


@chart(PAGE, "dashboard", "activation")
def _ch_activation(p):
    from data.entsoe import fetch_activation_prices
    from charts.imbalance import fig_activation_prices
    return fig_activation_prices(fetch_activation_prices(), entsoe_snapshot()["now"])


@chart(PAGE, "dashboard", "load")
def _ch_load(p):
    from charts.generation import fig_load
    snap = entsoe_snapshot()
    if snap["load_fc"].empty:
        return None
    ceps = _ceps()
    return fig_load(snap["load_fc"], _ceps_load_series(ceps), ceps["gen"], snap["now"])


@chart(PAGE, "dashboard", "wind_solar")
def _ch_wind_solar(p):
    from data.entsoe import fetch_wind_solar_forecast
    from charts.generation import fig_wind_solar_forecast
    snap = entsoe_snapshot()
    return fig_wind_solar_forecast(fetch_wind_solar_forecast(), snap["now"], gen_raw=snap["gen_raw"])


@chart(PAGE, "dashboard", "gen_area")
def _ch_gen_area(p):
    from charts.generation import fig_generation_area
    snap = entsoe_snapshot()
    if snap["gen_raw"].empty:
        return None
    return fig_generation_area(snap["gen_raw"], snap["now"])


@chart(PAGE, "dashboard", "gen_donut")
def _ch_gen_donut(p):
    from charts.generation import fig_generation_donut
    return fig_generation_donut(entsoe_snapshot()["gen_raw"])


def _reserves_fig(kind: str, days: int, height: int):
    from charts.reserves import fig_reserve_volumes, fig_reserve_prices
    now = _now()
    reserves = reserves_or_empty(now)
    start, end = now.normalize(), now.normalize() + pd.Timedelta(days=days)
    fn = fig_reserve_volumes if kind == "vol" else fig_reserve_prices
    return fn(reserves, now, start, end, height=height)


@chart(PAGE, "dashboard", "res_vol")
def _ch_res_vol(p):
    return _reserves_fig("vol", 1, 300)


@chart(PAGE, "dashboard", "res_pri")
def _ch_res_pri(p):
    return _reserves_fig("pri", 1, 300)


# ── ČEPS real-time ──────────────────────────────────────────────────────

@tab(PAGE, "ceps", "ČEPS", refresh=60)
def ceps_tab(p):
    d = _ceps()
    metrics = []
    df_i, df_f, df_l, df_cb = d["imbal"], d["freq"], d["load"], d["cb"]
    if not df_i.empty:
        v = float(df_i.iloc[-1, 0])
        metrics.append(("Odchylka", f"{v:+.1f} MW", "Surplus" if v >= 0 else "Deficit", v >= 0))
    if not df_f.empty:
        hz = float(df_f.iloc[-1, 0])
        metrics.append(("Frekvence", f"{hz:.3f} Hz", f"{hz - 50:+.3f} Hz", hz >= 50))
    if not df_l.empty and "Load [MW]" in df_l.columns:
        metrics.append(("Zatížení", f"{float(df_l['Load [MW]'].iloc[-1]):,.0f} MW".replace(",", " "), "", None))
    if not df_cb.empty and "Net Export (MW)" in df_cb.columns:
        net = float(df_cb["Net Export (MW)"].iloc[-1])
        metrics.append(("Net Export", f"{net:+.0f} MW", "export" if net >= 0 else "import", net >= 0))
    return {"metrics": metrics, "now": d["now"]}


@chart(PAGE, "ceps", "dashboard")
def _ch_ceps_dashboard(p):
    from charts.imbalance import fig_ceps_dashboard
    return fig_ceps_dashboard(_ceps())


# ── Výroba / Odstávky (EU) ──────────────────────────────────────────────

def _gen_state(p):
    from config import GENERATION_COUNTRIES
    sekce = p.str("sekce", "Výroba", choices=["Výroba", "Odstávky"])
    # výroba je pro víc zemí (GENERATION_COUNTRIES), odstávky jen COUNTRIES
    country = p.str("country", "CZ", choices=GENERATION_COUNTRIES if sekce == "Výroba" else COUNTRIES)
    pohled = p.str("pohled", "Teď/denní", choices=["Teď/denní", "Letos", "Sezonnost"])
    tz = COUNTRY_TIMEZONES[country]
    today = pd.Timestamp.now(tz=tz).date()
    day = p.date("day", today)
    start = p.date("start", date(today.year, 1, 1))
    chart_type = p.str("chart_type", "Linie", choices=CHART_TYPES)
    return {"country": country, "sekce": sekce, "pohled": pohled, "tz": tz, "today": today,
            "day": day, "start": start, "chart_type": chart_type}


@ttl_cache(ttl=3600, maxsize=16)
def _source_types(country: str) -> list:
    """Zdroje dané země bez načtení hodnot (jen sloupec source_type)."""
    from core.settings import history_path
    from data.generation import available_source_types
    from data.partitioned_store import read_partitioned
    df = read_partitioned(history_path("generation", country), columns=["source_type"],
                          categories=["source_type"])
    if df.empty:
        return []
    return available_source_types(pd.DataFrame({"source_type": list(df["source_type"].cat.categories)}))


def _outage_state(p, country: str):
    from data.outages import load_outages, list_available_snapshot_dates
    data_out = load_outages(country)
    active = data_out["active"]
    types = sorted(active["plant_type"].unique().tolist()) if not active.empty else []
    sel_types = p.list("types", default=types, choices=types)
    days_forward = p.int("days", 30, 7, 500)
    dates = list_available_snapshot_dates(country)
    date_strs = [d.strftime("%Y-%m-%d") for d in dates]
    compare = p.str("compare", date_strs[0] if date_strs else None, choices=date_strs)
    return {"data": data_out, "active": active, "types": types, "sel_types": sel_types,
            "days": days_forward, "dates": date_strs, "compare": compare}


@tab(PAGE, "vyroba", "Výroba/Odstávky")
def vyroba(p):
    from charts.electricity_outages import fig_outages_table
    s = _gen_state(p)
    from config import GENERATION_COUNTRIES
    ctx = {**s, "countries": GENERATION_COUNTRIES if s["sekce"] == "Výroba" else COUNTRIES,
           "country_names": COUNTRY_NAMES, "chart_types": CHART_TYPES}
    if s["sekce"] == "Výroba":
        if s["pohled"] == "Sezonnost":
            sources = _source_types(s["country"])
            labels = {src: psr_lookup(src)[0] for src in sources}
            default = "Nuclear" if "Nuclear" in sources else (sources[0] if sources else None)
            ctx.update(sources=sources, source_labels=labels,
                       source=p.str("source", default, choices=sources))
        return ctx
    o = _outage_state(p, s["country"])
    active = o["active"]
    ctx.update(
        types=o["types"], sel_types=o["sel_types"], type_labels={t: psr_lookup(t)[0] for t in o["types"]},
        days=o["days"], dates=o["dates"], compare=o["compare"],
        n_active=len(active),
        unavail_now=float(active["unavail_mw"].sum()) if not active.empty else 0.0,
        n_unplanned=int(active["businesstype"].str.contains("Unplanned", na=False).sum()) if not active.empty else 0,
        n_types=active["plant_type"].nunique() if not active.empty else 0,
        outage_table=table(fig_outages_table(o["data"], o["sel_types"] or None), [
            ("Blok", "Blok", None), ("Typ zdroje", "Typ zdroje", None),
            ("Instalovaný výkon (MW)", "Instalovaný (MW)", ",.0f"), ("Dostupný (MW)", "Dostupný (MW)", ",.0f"),
            ("Nedostupný (MW)", "Nedostupný (MW)", ",.0f"), ("Druh", "Druh", None),
            ("Konec odstávky", "Konec odstávky", None),
        ]),
    )
    if o["dates"]:
        oldest = pd.Timestamp(o["dates"][0])
        ctx["oldest"] = oldest
        ctx["full_available"] = oldest + pd.Timedelta(days=OUTAGES_D7_COMPARISON_DAYS)
    ctx["query"] = p.query(**{"types__set": "1", "types": o["sel_types"]}) if o["types"] else str(p)
    return ctx


@chart(PAGE, "vyroba", "gen_day")
def _ch_gen_day(p):
    from data.generation import load_generation
    from charts.electricity_generation import fig_generation_stacked
    from web.util import decat
    s = _gen_state(p)
    day_ts = pd.Timestamp(s["day"], tz=s["tz"]).tz_convert("UTC")
    df = load_generation(s["country"], start=day_ts, end=day_ts + pd.Timedelta(days=1))
    return fig_generation_stacked(decat(df), day_ts)


@chart(PAGE, "vyroba", "gen_ytd")
def _ch_gen_ytd(p):
    from data.generation import load_generation
    from charts.electricity_generation import fig_generation_ytd
    s = _gen_state(p)
    start_ts = pd.Timestamp(s["start"], tz=s["tz"]).tz_convert("UTC")
    return fig_generation_ytd(load_generation(s["country"], start=start_ts), start_ts)


@chart(PAGE, "vyroba", "gen_season")
def _ch_gen_season(p):
    from data.generation import load_generation
    from charts.electricity_generation import fig_seasonality
    s = _gen_state(p)
    sources = _source_types(s["country"])
    if not sources:
        return None
    default = "Nuclear" if "Nuclear" in sources else sources[0]
    source = p.str("source", default, choices=sources)
    # jen vybraný zdroj (pyarrow filtr) — celá historie všech zdrojů DE_LU
    # by byla stovky MB
    df = load_generation(s["country"], source_type=source)
    return fig_seasonality(df, source, s["chart_type"])


@chart(PAGE, "vyroba", "outlook")
def _ch_outlook(p):
    from charts.electricity_outages import fig_outlook
    s = _gen_state(p)
    o = _outage_state(p, s["country"])
    return fig_outlook(o["data"], o["days"])


@chart(PAGE, "vyroba", "delta")
def _ch_delta(p):
    from data.outages import load_outages_snapshot
    from charts.electricity_outages import fig_outages_delta
    s = _gen_state(p)
    o = _outage_state(p, s["country"])
    if not o["compare"]:
        return None
    today_utc = pd.Timestamp.now(tz="UTC").normalize()
    days_back = (today_utc - pd.Timestamp(o["compare"], tz="UTC")).days
    df_now = load_outages_snapshot(s["country"], days_back=0)
    df_cmp = load_outages_snapshot(s["country"], days_back=days_back)
    return fig_outages_delta(df_now, df_cmp, window_days=min(o["days"], OUTAGES_DELTA_MAX_WINDOW_DAYS),
                             compare_days_back=days_back)


# ── DAP ceny ────────────────────────────────────────────────────────────

def _dap_series():
    """(D0, D+1) DAP; při nedostupném ENTSO-E (chybí token, výpadek) prázdné
    řady — záložka pak ukáže hlášku místo chyby."""
    from data.entsoe import fetch_dap
    try:
        return fetch_dap(0), fetch_dap(1)
    except Exception as e:
        log.warning("DAP nedostupné: %s", e)
        empty = pd.Series(dtype=float, name="dap_EUR_MWh")
        return empty, empty


def _battery(p):
    return {
        "cap": p.int("cap", 100, 10, 10000), "power": p.int("power", 50, 5, 5000),
        "cycles": p.int("cycles", 2, 1, 5), "cost": p.float("cost", 15.0, 0.0, 100.0),
        "hold": p.bool("hold", False),
    }


def _battery_sim(p):
    from charts.reserves import simulate_battery_dap
    b = _battery(p)
    s_d0, s_d1 = _dap_series()
    prices = pd.concat([s_d0, s_d1]).sort_index().dropna()
    if prices.empty:
        return b, None, None, None
    avg = float(prices.mean())
    df_sim, cycles_done = simulate_battery_dap(prices, b["cap"], b["power"], b["cycles"], b["cost"], b["hold"])
    return b, df_sim, cycles_done, avg


@tab(PAGE, "dap", "DAP ceny")
def dap_tab(p):
    from charts.reserves import calc_dap_stats
    now = _now()
    s_d0, s_d1 = _dap_series()
    b, df_sim, cycles_done, _avg = _battery_sim(p)

    def stats(series):
        st = calc_dap_stats(series)
        return [(lbl, st[k]) for lbl, k in [("Base", "base"), ("Peak 8-20", "peak"), ("Off-peak", "offpeak"),
                                             ("Min", "min"), ("Max", "max")]]
    return {
        **b,
        "d0_label": f"D0 — {now.strftime('%d.%m.%Y')}",
        "d1_label": f"D+1 — {(now + pd.Timedelta(days=1)).strftime('%d.%m.%Y')}",
        "stats_d0": stats(s_d0), "stats_d1": stats(s_d1),
        "has_sim": df_sim is not None,
        "revenue": float(df_sim["revenue_eur"].sum()) if df_sim is not None else None,
        "cycles_done": cycles_done,
    }


@chart(PAGE, "dap", "dap")
def _ch_dap(p):
    from charts.reserves import fig_dap
    s_d0, s_d1 = _dap_series()
    if s_d0.empty and s_d1.empty:
        return None
    return fig_dap(s_d0, s_d1, _now())


@chart(PAGE, "dap", "res_vol")
def _ch_dap_res_vol(p):
    return _reserves_fig("vol", 2, 320)


@chart(PAGE, "dap", "res_pri")
def _ch_dap_res_pri(p):
    return _reserves_fig("pri", 2, 320)


@chart(PAGE, "dap", "battery")
def _ch_battery(p):
    from charts.reserves import fig_battery_strategy
    b, df_sim, _cycles, avg = _battery_sim(p)
    if df_sim is None:
        return None
    return fig_battery_strategy(df_sim, avg - b["cost"] / 2, avg + b["cost"] / 2, avg, _now())


# ── Rezervy ─────────────────────────────────────────────────────────────

RESERVE_KEYS = [
    ("afrr_d_amt", "aFRR denní obj."), ("afrr_d_pri", "aFRR denní ceny"),
    ("afrr_y_amt", "aFRR roční obj."), ("afrr_y_pri", "aFRR roční ceny"),
    ("mfrr_d_amt", "mFRR denní obj."), ("mfrr_d_pri", "mFRR denní ceny"),
]


@tab(PAGE, "rezervy", "Rezervy")
def rezervy(p):
    now = _now()
    a04 = (f"{now.year}-01-01 – {now.year}-07-01" if now.month < 7
           else f"{now.year}-07-01 – {now.year + 1}-01-01")
    reserves = reserves_or_empty(now)
    downloads = [(k, lbl, not reserves.get(k, pd.DataFrame()).empty) for k, lbl in RESERVE_KEYS]
    return {"a04": a04, "downloads": downloads}


@chart(PAGE, "rezervy", "vol")
def _ch_rez_vol(p):
    return _reserves_fig("vol", RESERVES_DEFAULT_RANGE_DAYS, 420)


@chart(PAGE, "rezervy", "pri")
def _ch_rez_pri(p):
    return _reserves_fig("pri", RESERVES_DEFAULT_RANGE_DAYS, 420)


@csv("reserves")
def _csv_reserves(p):
    key = p.str("key", "afrr_d_amt", choices=[k for k, _ in RESERVE_KEYS])
    return reserves_or_empty(_now()).get(key, pd.DataFrame()), f"{key}.csv"


# ── Hydro ───────────────────────────────────────────────────────────────

def _hydro_state(p):
    from data.hydro import load_hydro, HYDRO_COUNTRY_NAMES
    df = load_hydro()
    if df.empty:
        return None
    countries = sorted(df["country"].unique().tolist())
    years = sorted(int(y) for y in df["date"].dt.year.unique())
    return {
        "df": df, "countries": countries, "years": years, "names": HYDRO_COUNTRY_NAMES,
        "country": p.str("country", "NO" if "NO" in countries else countries[0], choices=countries),
        "sel_years": [int(y) for y in p.list("years", default=years[-6:], choices=years)],
        "sel_years_grid": [int(y) for y in p.list("years_grid", default=years[-6:], choices=years)],
    }


@tab(PAGE, "hydro", "Hydro")
def hydro(p):
    s = _hydro_state(p)
    if s is None:
        return {"empty": True}
    s = dict(s)
    s.pop("df")
    s["query"] = p.query(country=s["country"], years__set="1", years=s["sel_years"],
                         years_grid__set="1", years_grid=s["sel_years_grid"])
    return s


@chart(PAGE, "hydro", "main")
def _ch_hydro_main(p):
    from charts.hydro import fig_hydro_main
    s = _hydro_state(p)
    if s is None or not s["sel_years"]:
        return None
    return fig_hydro_main(s["df"], s["country"], s["sel_years"])


@chart(PAGE, "hydro", "grid")
def _ch_hydro_grid(p):
    from charts.hydro import fig_hydro_grid
    s = _hydro_state(p)
    if s is None or not s["sel_years_grid"]:
        return None
    return fig_hydro_grid(s["df"], s["sel_years_grid"])


# ── Surová data ─────────────────────────────────────────────────────────

def _gen_display(gen_raw: pd.DataFrame) -> pd.DataFrame:
    g = gen_raw.copy()
    g.columns = [psr_lookup(c)[0] for c in g.columns]
    return g


@tab(PAGE, "data", "Data")
def data_tab(p):
    snap = entsoe_snapshot()
    imb = snap["df_imbal"].iloc[::-1]
    gen = _gen_display(snap["gen_raw"]).iloc[::-1] if not snap["gen_raw"].empty else pd.DataFrame()
    view = p.str("view", "Odchylka", choices=["Odchylka", "Generace"])
    df = imb if view == "Odchylka" else gen
    df = df.reset_index().rename(columns={"index": "čas"})
    return {"view": view, "t": table(df, max_rows=500)}


@csv("imbalance")
def _csv_imbalance(p):
    return entsoe_snapshot()["df_imbal"], "odchylka.csv"


@csv("generation")
def _csv_generation(p):
    return _gen_display(entsoe_snapshot()["gen_raw"]), "generace.csv"


# ── DAP mapa ────────────────────────────────────────────────────────────

def _dap_map_state(p):
    from data.dap_europe import load_dap_europe_all
    df = load_dap_europe_all()
    if df.empty:
        return None
    dates = sorted({pd.Timestamp(d).date() for d in df["date"].unique()}, reverse=True)
    sel = p.date("day", dates[0])
    if sel not in dates:
        sel = dates[0]
    return {"df": df, "dates": dates, "sel": sel}


@tab(PAGE, "dapmapa", "DAP mapa")
def dap_mapa(p):
    from data.dap_europe import dap_europe_last_write
    s = _dap_map_state(p)
    if s is None:
        return {"empty": True}
    lw = dap_europe_last_write()
    return {
        "dates": [d.isoformat() for d in s["dates"]],
        "date_labels": {d.isoformat(): d.strftime("%d.%m.%Y") for d in s["dates"]},
        "sel": s["sel"].isoformat(), "sel_label": s["sel"].strftime("%d.%m.%Y"),
        "today_missing": date.today() not in s["dates"], "today": date.today(),
        "latest": s["dates"][0],
        "last_write": lw.strftime("%d.%m.%Y %H:%M") if lw is not None else "neznámo",
    }


@chart(PAGE, "dapmapa", "map", displayModeBar=True, scrollZoom=True,
       toImageButtonOptions={"format": "png", "filename": "dap_mapa_evropa", "height": 800, "width": 1200, "scale": 2})
def _ch_dap_map(p):
    from charts.dap_map import fig_dap_map
    s = _dap_map_state(p)
    if s is None:
        return None
    df = s["df"]
    return fig_dap_map(df[df["date"].map(lambda d: pd.Timestamp(d).date()) == s["sel"]])
