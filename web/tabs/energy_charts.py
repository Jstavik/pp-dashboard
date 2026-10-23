"""Energy-Charts (Fraunhofer ISE) — data, která stahuje úloha energy_charts
(2× denně) do history/energy_charts/ a energy_charts.parquet:
frekvence sítě (5min), přeshraniční obchod/toky (cbet/cbpf), výroba
(public_power) + day-ahead prognóza, instalovaný výkon."""
import glob
import os
from datetime import timedelta

import pandas as pd

from core.cache import ttl_cache
from web.registry import page, tab, chart
from web.util import date_range_from_params, table

PAGE = "energy-charts"
page(PAGE, "Energy-Charts", "🔌")

PRESETS = [("Den", 1), ("Týden", 7), ("Měsíc", 30), ("Kvartál", 90), ("Rok", 365), ("Max", "max")]
RES_CHOICES = ["auto", "15min", "1h", "1D"]
RES_LABELS = {"auto": "auto", "15min": "15 min", "1h": "hodina", "1D": "den"}
COUNTRY_NAMES = {
    "cz": "Česko", "de": "Německo", "at": "Rakousko", "pl": "Polsko", "sk": "Slovensko",
    "hu": "Maďarsko", "fr": "Francie", "nl": "Nizozemsko", "be": "Belgie", "ch": "Švýcarsko",
    "it": "Itálie", "es": "Španělsko", "dk": "Dánsko", "se": "Švédsko", "no": "Norsko",
}


def _ec():
    import data.energy_charts as ec
    return ec


@ttl_cache(ttl=3600, maxsize=16)
def _bounds(endpoint: str, geo_key: str):
    """(první den, poslední den) uložených dat — z názvů měsíčních souborů
    a posledního timestampu (bez čtení celé historie)."""
    ec = _ec()
    if endpoint in ec.PARTITIONED_ENDPOINTS:
        files = sorted(glob.glob(os.path.join(ec._partitioned_base_dir(endpoint), "*.parquet")))
        if not files:
            return None
        last = ec._last_timestamp_partitioned(endpoint, geo_key)
        if last is None:
            return None
        first = pd.Timestamp(os.path.basename(files[0])[:7] + "-01").date()
    else:
        df = ec.load_energy_charts(endpoint=endpoint, geo_key=geo_key)
        if df.empty:
            return None
        ts = pd.to_datetime(df["timestamp"], utc=True)
        first, last = ts.min().date(), ts.max()
    return first, last.tz_convert("Europe/Prague").date()


@ttl_cache(ttl=3600, maxsize=2)
def _countries() -> list:
    """geo_key zemí v public_power (z posledního měsíčního souboru)."""
    import pyarrow.parquet as pq
    ec = _ec()
    files = sorted(glob.glob(os.path.join(ec._partitioned_base_dir("public_power"), "*.parquet")))
    if not files:
        return []
    vals = pq.read_table(files[-1], columns=["geo_key"]).column("geo_key").to_pylist()
    return sorted(set(v for v in vals if v))


def _range(d_from, d_to):
    tz = "Europe/Prague"
    return pd.Timestamp(d_from).tz_localize(tz), pd.Timestamp(d_to + timedelta(days=1)).tz_localize(tz)


def _load(endpoint, geo_key, d_from, d_to, series_ids=None, resolution=None):
    """Wide data za [d_from, d_to] včetně celého posledního dne; resolution
    1h/1D = průměr už při čtení (pyarrow), jinak nativní rozlišení."""
    lo, hi = _range(d_from, d_to)
    return _ec().load_energy_charts_wide(endpoint, geo_key, lo, hi, resolution=resolution,
                                         series_ids=tuple(series_ids) if series_ids else None)


def _state(p, endpoint: str, default_days: int, per_country: bool = True):
    from charts.energy_charts import auto_resolution
    countries = _countries() if per_country else []
    if per_country and not countries:
        return None
    country = p.str("country", "cz" if "cz" in countries else (countries[0] if countries else None),
                    choices=countries) if per_country else None
    geo = country if per_country else _ec().ENDPOINTS["frequency"]["fixed_geo_key"]
    b = _bounds(endpoint, geo)
    if b is None:
        return None
    min_d, max_d = b
    d_from, d_to = date_range_from_params(p, max_d, default_days, min_date=min_d)
    res = p.str("res", "auto", choices=RES_CHOICES)
    eff_res = auto_resolution(d_from, d_to) if res == "auto" else res
    return {"countries": countries, "country": country, "geo": geo,
            "country_name": COUNTRY_NAMES.get(country, (country or "").upper()),
            "names": COUNTRY_NAMES, "min_d": min_d, "max_d": max_d, "d_from": d_from, "d_to": d_to,
            "res": res, "eff_res": eff_res, "res_choices": RES_CHOICES, "res_labels": RES_LABELS,
            "presets": PRESETS}


def _ctx(p, s: dict) -> dict:
    ctx = {k: v for k, v in s.items() if k != "geo"}
    ctx["query"] = p.query(range=None, **{"from": s["d_from"], "to": s["d_to"]})
    return ctx


def _empty_hint():
    return {"empty": True}


# ── Frekvence ───────────────────────────────────────────────────────────

@tab(PAGE, "frekvence", "Frekvence sítě")
def frekvence(p):
    from charts.energy_charts import frequency_stats
    s = _state(p, "frequency", 7, per_country=False)
    if s is None:
        return _empty_hint()
    ctx = _ctx(p, s)
    st = frequency_stats(_load("frequency", s["geo"], s["d_from"], s["d_to"]))
    ctx["stats"] = st
    return ctx


@chart(PAGE, "frekvence", "line")
def _ch_freq(p):
    from charts.energy_charts import fig_frequency
    s = _state(p, "frequency", 7, per_country=False)
    if s is None:
        return None
    return fig_frequency(_load("frequency", s["geo"], s["d_from"], s["d_to"]), s["eff_res"])


@chart(PAGE, "frekvence", "hist")
def _ch_freq_hist(p):
    from charts.energy_charts import fig_frequency_hist
    s = _state(p, "frequency", 7, per_country=False)
    if s is None:
        return None
    return fig_frequency_hist(_load("frequency", s["geo"], s["d_from"], s["d_to"]))


# ── Přeshraniční toky ───────────────────────────────────────────────────

@tab(PAGE, "toky", "Přeshraniční toky")
def toky(p):
    from charts.energy_charts import crossborder_summary
    s = _state(p, "cbet", 30)
    if s is None:
        return _empty_hint()
    ctx = _ctx(p, s)
    cols = [("soused", "Soused", None), ("import_GWh", "Import [GWh]", ",.1f"),
            ("export_GWh", "Export [GWh]", ",.1f"), ("saldo_GWh", "Saldo [GWh]", ",.1f")]
    for kind in ("cbet", "cbpf"):
        ctx[f"sum_{kind}"] = table(crossborder_summary(_load(kind, s["country"], s["d_from"], s["d_to"])), cols)
    return ctx


def _cb_chart(p, kind):
    from charts.energy_charts import fig_crossborder
    s = _state(p, "cbet", 30)
    if s is None:
        return None
    return fig_crossborder(_load(kind, s["country"], s["d_from"], s["d_to"], resolution=s["eff_res"]),
                           kind, s["country_name"], s["eff_res"])


@chart(PAGE, "toky", "cbet")
def _ch_cbet(p):
    return _cb_chart(p, "cbet")


@chart(PAGE, "toky", "cbpf")
def _ch_cbpf(p):
    return _cb_chart(p, "cbpf")


# ── Výroba + prognóza ───────────────────────────────────────────────────

@tab(PAGE, "vyroba", "Výroba a prognóza")
def vyroba(p):
    from charts.energy_charts import forecast_errors
    s = _state(p, "public_power", 7)
    if s is None:
        return _empty_hint()
    ctx = _ctx(p, s)
    fc = _load("public_power_forecast", s["country"], s["d_from"], s["d_to"])
    act = _load("public_power", s["country"], s["d_from"], s["d_to"],
                ["load", "solar", "wind_onshore", "wind_offshore"])
    ctx["has_forecast"] = not fc.empty
    ctx["fc_errors"] = table(forecast_errors(act, fc), [
        ("řada", "Řada", None), ("MAE_MW", "MAE [MW]", ",.0f"), ("bias_MW", "Bias [MW]", ",.0f"),
        ("MAPE_pct", "MAPE [%]", ",.1f"), ("n", "Čtvrthodin", ",.0f")])
    return ctx


@chart(PAGE, "vyroba", "stack")
def _ch_public_power(p):
    from charts.energy_charts import fig_public_power
    s = _state(p, "public_power", 7)
    if s is None:
        return None
    return fig_public_power(_load("public_power", s["country"], s["d_from"], s["d_to"], resolution=s["eff_res"]),
                            s["country_name"], s["eff_res"])


@chart(PAGE, "vyroba", "forecast")
def _ch_forecast(p):
    from charts.energy_charts import fig_forecast_vs_actual
    s = _state(p, "public_power", 7)
    if s is None:
        return None
    fc = _load("public_power_forecast", s["country"], s["d_from"], s["d_to"] + timedelta(days=1),
               resolution=s["eff_res"])
    act = _load("public_power", s["country"], s["d_from"], s["d_to"],
                ["load", "solar", "wind_onshore", "wind_offshore"], resolution=s["eff_res"])
    return fig_forecast_vs_actual(act, fc, s["country_name"], s["eff_res"])


# ── Instalovaný výkon ───────────────────────────────────────────────────

def _installed_state(p):
    countries = _countries()
    if not countries:
        return None
    country = p.str("country", "cz" if "cz" in countries else countries[0], choices=countries)
    df = _ec().load_energy_charts(endpoint="installed_power", geo_key=country)
    if df.empty:
        return None
    return {"countries": countries, "country": country, "names": COUNTRY_NAMES,
            "country_name": COUNTRY_NAMES.get(country, country.upper()), "df": df}


@tab(PAGE, "instalovany", "Instalovaný výkon")
def instalovany(p):
    from charts.energy_charts import installed_wide, psr_name_color
    s = _installed_state(p)
    if s is None:
        return _empty_hint()
    w = installed_wide(s.pop("df"))
    w = w.loc[:, w.fillna(0).sum() > 0]
    last_year = w.index.max()
    tbl = w.T.rename(index=lambda c: psr_name_color(c)[0])
    tbl = tbl[[c for c in tbl.columns if c >= last_year - 5]]
    tbl.columns = [str(c) for c in tbl.columns]
    tbl = tbl.reset_index().rename(columns={"index": "Zdroj"})
    s["table"] = table(tbl, [("Zdroj", "Zdroj", None)] + [(c, c, ",.2f") for c in tbl.columns[1:]])
    s["last_year"] = last_year
    s["query"] = p.query(country=s["country"])
    return s


@chart(PAGE, "instalovany", "bars")
def _ch_installed(p):
    from charts.energy_charts import fig_installed
    s = _installed_state(p)
    if s is None:
        return None
    return fig_installed(s["df"], s["country_name"])
