"""Odstávky ČEPS — PU/GU odstávky ČR z ENTSO-E (dřív "🔧 ČEPS odstávky")."""
import pandas as pd

from web.registry import page, tab, chart, csv
from web.services import entsoe_snapshot
from web.util import table

PAGE = "odstavky-ceps"
page(PAGE, "Odstávky ČEPS", "🔧")

OUT_COLS = [("unit_name", "Blok", None), ("outage_start", "Od", None), ("outage_end", "Do", None),
            ("installed_MW", "Instalovaný (MW)", ",.0f"), ("available_MW", "Dostupný (MW)", ",.0f"),
            ("unavailable_MW", "Nedostupný (MW)", ",.0f"), ("available_pct", "Dostupnost %", ".1f"),
            ("outage_type", "Typ", None)]


def _level(df_out, lvl):
    return df_out[df_out["unit_level"] == lvl] if not df_out.empty else pd.DataFrame()


@tab(PAGE, "prehled", "PU/GU odstávky")
def prehled(p):
    snap = entsoe_snapshot()
    df_out, changes = snap["df_out"], snap["changes"]
    n_ended, n_chmw = len(changes.get("ended", set())), len(changes["changed_mw"])
    new_df = pd.DataFrame()
    if snap["n_new"] and not df_out.empty:
        keys = df_out[["unit_raw", "outage_start", "outage_end"]].apply(tuple, axis=1)
        new_df = df_out[keys.isin(changes["new"])].sort_values("unavailable_MW", ascending=False)
    return {
        "n_pu": snap["n_pu"], "n_gu": snap["n_gu"], "n_new": snap["n_new"], "n_ended": n_ended, "n_chmw": n_chmw,
        "pu": table(_level(df_out, "PU"), OUT_COLS), "gu": table(_level(df_out, "GU"), OUT_COLS),
        "new": table(new_df, [OUT_COLS[0], ("unit_level", "Úroveň", None)] + OUT_COLS[1:4] + [OUT_COLS[5], OUT_COLS[7]]),
        "changed": table(changes["changed_mw"], [("unit_name", "Blok", None), ("prev_MW", "Dříve (MW)", ",.0f"),
                                                 ("available_MW", "Nyní (MW)", ",.0f"), ("delta_MW", "Δ MW", "+,.0f")]),
        "error": snap["error"],
    }


@chart(PAGE, "prehled", "installed")
def _ch_installed(p):
    from data.entsoe import fetch_installed_capacity
    from charts.outages import fig_installed_capacity
    cap = fetch_installed_capacity()
    return None if cap.empty else fig_installed_capacity(cap)


def _gantt(level):
    from charts.outages import fig_outages_gantt
    snap = entsoe_snapshot()
    return fig_outages_gantt(snap["df_out"], level, pd.Timestamp.now(tz="Europe/Prague"), snap["changes"])


@chart(PAGE, "prehled", "gantt_pu")
def _ch_pu(p):
    return _gantt("PU")


@chart(PAGE, "prehled", "gantt_gu")
def _ch_gu(p):
    return _gantt("GU")


@csv("outages")
def _csv_outages(p):
    lvl = p.str("level", "PU", choices=["PU", "GU"])
    sub = _level(entsoe_snapshot()["df_out"], lvl)
    return sub[[c[0] for c in OUT_COLS if c[0] in sub.columns]].set_index("unit_name") if not sub.empty else sub, \
        f"outages_{lvl}.csv"
