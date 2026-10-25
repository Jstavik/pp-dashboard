"""Sdílené výpočty pro víc záložek (hlavička Elektřiny, ČEPS odstávky).

ENTSO-E snapshot (fetch_entsoe_data, TTL 30 min) nese odstávky PU/GU,
ceny odchylky a generaci podle PSR (živě); prognóza zatížení jde
z úložiště (data/entsoe_store.py). Odchylka a výroba
v hlavičce jdou z ČEPS (živě po minutě, OPTIMIZATION_REPORT.md: ČEPS má
menší zpoždění než ENTSO-E); ENTSO-E je jen fallback, když ČEPS nic nevrátí.
"""
import logging
import threading

import pandas as pd

from config import (
    THRESHOLD, C_DEFICIT, C_SURPLUS, C_OK, C_WARN, RESERVES_FALLBACK_RANGE_DAYS, sparkline_svg,
)

log = logging.getLogger(__name__)

CZ_DAYS = ["po", "út", "st", "čt", "pá", "so", "ne"]


class _OutageChanges:
    """Náhrada st.session_state.df_out_prev: pamatuje si odstávky z
    PŘEDCHOZÍHO stažení ENTSO-E (v rámci procesu) a počítá změny vůči němu
    ("od posledního obnovení" = od předposledního stažení dat)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._key = object()  # sentinel — nic ještě nebylo staženo
        self._current = None
        self._changes = None

    def update(self, fetched_at, df_out: pd.DataFrame) -> dict:
        from charts.outages import detect_changes
        if fetched_at is None:  # stažení selhalo — žádné změny, stav neměnit
            return detect_changes(None, df_out)
        with self._lock:
            if fetched_at != self._key:
                prev = self._current
                self._changes = detect_changes(prev, df_out)
                self._current = df_out.copy() if not df_out.empty else None
                self._key = fetched_at
            return self._changes


_changes = _OutageChanges()


def entsoe_snapshot() -> dict:
    from data.entsoe import fetch_entsoe_data
    from data.entsoe_store import load_forecast
    from charts.imbalance import parse_imbalance
    from charts.outages import parse_outages

    error = None
    try:
        imbal_raw, gen_raw, load_actual, load_fc, out_raw, now = fetch_entsoe_data(with_load=False)
    except Exception as e:
        log.warning("ENTSO-E nedostupné: %s", e)
        error = f"{type(e).__name__}: {e}"
        now         = pd.Timestamp.now(tz="Europe/Prague")
        imbal_raw   = pd.DataFrame(columns=["odchylka_MWh", "price_Short", "price_Long"])
        gen_raw     = pd.DataFrame()
        load_actual = pd.Series(dtype="float64", name="actual_MW")
        load_fc     = pd.Series(dtype="float64", name="forecast_MW")
        out_raw     = pd.DataFrame()

    try:
        load_fc = load_forecast()  # úložiště entsoe_load, živě jen když tam dnešek chybí
    except Exception as e:
        log.warning("prognóza zatížení: %s", e)
    df_imbal = parse_imbalance(imbal_raw)
    df_out = parse_outages(out_raw)
    changes = _changes.update(now if error is None else None, df_out)
    n_pu = int((df_out["unit_level"] == "PU").sum()) if not df_out.empty else 0
    n_gu = int((df_out["unit_level"] == "GU").sum()) if not df_out.empty else 0
    return {
        "now": now, "df_imbal": df_imbal, "gen_raw": gen_raw, "load_actual": load_actual,
        "load_fc": load_fc, "df_out": df_out, "changes": changes, "n_pu": n_pu, "n_gu": n_gu,
        "n_new": len(changes["new"]), "error": error,
    }


def reserves_or_empty(now) -> dict:
    from data.entsoe import fetch_reserves
    try:
        return fetch_reserves()
    except Exception as e:
        log.warning("Rezervy nedostupné: %s", e)
        empty = pd.DataFrame()
        return dict(afrr_d_amt=empty, afrr_d_pri=empty, afrr_y_amt=empty, afrr_y_pri=empty,
                    mfrr_d_amt=empty, mfrr_d_pri=empty, start=now.normalize(),
                    end=now.normalize() + pd.DateOffset(days=RESERVES_FALLBACK_RANGE_DAYS), now=now)


def ceps_isp_imbalance(ceps: dict) -> pd.Series:
    """ČEPS minutová odchylka [MW] → energie za ISP [MWh/15 min] (průměr MW
    × 0.25 h) — stejná veličina jako ENTSO-E odchylka, ať platí práh
    THRESHOLD ±20 MWh z původní hlavičky."""
    df = ceps.get("imbal_mw", pd.DataFrame())
    if df is None or df.empty:
        return pd.Series(dtype=float)
    return (df["odchylka_MW"].resample("15min").mean() * 0.25).dropna()


def electricity_header() -> dict:
    """Banner + KPI karty (dřív app.py nad záložkami Elektřiny)."""
    from data.ceps import fetch_ceps_all

    snap = entsoe_snapshot()
    try:
        ceps = fetch_ceps_all()
    except Exception as e:
        log.warning("ČEPS nedostupné: %s", e)
        ceps = {}

    isp = ceps_isp_imbalance(ceps)
    if not isp.empty:
        imbal_series, imbal_src = isp, "ČEPS"
    else:
        imbal_series = snap["df_imbal"]["odchylka_MWh"] if not snap["df_imbal"].empty else pd.Series(dtype=float)
        imbal_src = "ENTSO-E"
    last_imbal = float(imbal_series.iloc[-1]) if not imbal_series.empty else 0.0

    gen_ceps = ceps.get("gen", pd.DataFrame()) if ceps else pd.DataFrame()
    if gen_ceps is not None and not gen_ceps.empty:
        gen_total = gen_ceps.fillna(0).sum(axis=1)
        gen_src = "ČEPS"
    elif not snap["gen_raw"].empty:
        gen_total = snap["gen_raw"].fillna(0).sum(axis=1)
        gen_src = "ENTSO-E"
    else:
        gen_total, gen_src = pd.Series(dtype=float), "—"
    cur_gen = float(gen_total.iloc[-1]) if not gen_total.empty else 0.0

    df_out = snap["df_out"]
    total_unavail = float(df_out["unavailable_MW"].sum()) if not df_out.empty else 0.0
    total_install = float(df_out["installed_MW"].sum()) if not df_out.empty else 0.0
    unavail_pct = total_unavail / total_install * 100 if total_install else 0.0
    df_imbal = snap["df_imbal"]
    last_short = (float(df_imbal["price_Short"].dropna().iloc[-1])
                  if "price_Short" in df_imbal and df_imbal["price_Short"].notna().any() else None)

    if imbal_series.empty:
        state, label = "na", "ODCHYLKA NEDOSTUPNÁ"
    elif last_imbal < -THRESHOLD:
        state, label = "bad", f"DEFICIT {last_imbal:+.1f} MWh"
    elif last_imbal > THRESHOLD:
        state, label = "warn", f"SURPLUS {last_imbal:+.1f} MWh"
    else:
        state, label = "ok", f"VYVÁŽENO {last_imbal:+.1f} MWh"

    data_now = ceps.get("now") if ceps else None
    data_now = data_now or snap["now"]
    age_min = (pd.Timestamp.now(tz="Europe/Prague") - data_now).total_seconds() / 60
    fresh = f"{age_min:.0f} min" if age_min < 60 else f"{age_min / 60:.1f} h ⚠"

    # None = neutrální barva z CSS (čitelná ve světlém i tmavém režimu)
    imbal_col = C_DEFICIT if last_imbal < -THRESHOLD else (C_SURPLUS if last_imbal > THRESHOLD else None)
    unavail_col = C_DEFICIT if unavail_pct > 30 else (C_WARN if unavail_pct > 15 else C_OK)
    changes = snap["changes"]
    return {
        "state": state, "label": label, "now": data_now, "fresh": fresh,
        "now_label": f"{CZ_DAYS[data_now.weekday()]} {data_now:%d.%m.%Y · %H:%M}",
        "has_imbal": not imbal_series.empty, "has_gen": not gen_total.empty,
        "imbal": last_imbal, "imbal_src": imbal_src, "imbal_color": imbal_col, "threshold": THRESHOLD,
        "spark_imbal": sparkline_svg(imbal_series.tail(96).tolist(), C_DEFICIT if last_imbal < 0 else C_SURPLUS),
        "gen": cur_gen, "gen_src": gen_src,
        "spark_gen": sparkline_svg(gen_total.tail(96).tolist(), C_OK) if not gen_total.empty else "",
        "unavail": total_unavail, "install": total_install, "unavail_pct": unavail_pct,
        "unavail_color": unavail_col, "n_pu": snap["n_pu"], "n_gu": snap["n_gu"], "n_new": snap["n_new"],
        "n_ended": len(changes.get("ended", set())), "n_chmw": len(changes["changed_mw"]),
        "price_short": last_short, "entsoe_error": snap["error"],
    }
