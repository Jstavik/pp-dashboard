"""Analýza toků plynu (/plyn/analyza) — specializovaný Průzkumník pro plyn.

Dva režimy se společným panelem filtrů (stav v URL + localStorage):
- **Země**: Physical Flow země (ENTSO-G aggregateddata) podle sousední
  země / systému (LNG, zásobníky, těžba, spotřeba jako vlastní kategorie),
  sousedního systému ENTSO-G nebo bodu; vstup kladně, výstup záporně,
  čistá bilance. Pivot období × kategorie, sezónnost, srovnání A vs B.
- **Bod**: kaskáda země → směr → sousední země → bod; toky (fyzický,
  alokace, nominace, renominace) proti kapacitám (technická, rezervovaná),
  využití kapacity, přerušení dopředu i v historii; stejné souhrny.

Data a agregace: core/gas_analysis.py (DuckDB s filtrem, do prohlížeče
jdou jen agregace), grafy: charts/gas_analysis.py.
"""
from datetime import date, timedelta

import pandas as pd

from core import gas_analysis as ga
from web.registry import chart, csv, page, tab
from web.util import date_range_from_params, fmt_value, table

PAGE = "plyn-analyza"
page(PAGE, "Analýza toků plynu", "🔵")

PRESETS = [("3 měs.", 92), ("13 měs.", 396), ("2 roky", 730), ("5 let", 1826), ("Max", "max")]
DEFAULT_DAYS = 396
DIR_LABELS = {"both": "obojí (bilance)", "entry": "vstup (entry)", "exit": "výstup (exit)"}
MODE_LABELS = {"zeme": "🌍 Země", "bod": "📍 Bod"}
SM_LABELS = {"month": "po měsících", "day": "po dnech (7denní průměr)"}


# ── stav z parametrů ────────────────────────────────────────────────────

def _period(p, prefix: str, default: tuple, lo: date, hi: date) -> tuple:
    a, b = p.date(f"{prefix}_from"), p.date(f"{prefix}_to")
    if a and b and a <= b:
        return max(a, lo), min(b, hi)
    return default


def _months(lo: date, hi: date) -> tuple:
    starts = pd.date_range(pd.Timestamp(lo).to_period("M").to_timestamp(), hi, freq="MS")
    ends = [min((s + pd.offsets.MonthEnd(0)).date(), hi) for s in starts]
    return [max(s.date(), lo).isoformat() for s in starts], [e.isoformat() for e in ends]


def state(p) -> dict:
    mode = p.str("mode", "zeme", choices=list(MODE_LABELS))
    s = {"mode": mode, "g": p.str("g", "day", choices=list(ga.GRAINS)),
         "u": p.str("u", "gwhd", choices=list(ga.UNITS)), "sm": p.str("sm", "month", choices=list(SM_LABELS))}
    b = ga.flow_bounds() if mode == "zeme" else ga.oper_bounds()
    if b is None:
        s["empty"] = True
        return s
    min_d, max_d = b
    d_from, d_to = date_range_from_params(p, max_d, DEFAULT_DAYS, min_date=min_d)
    d_from, d_to = max(d_from, min_d), min(d_to, max_d)
    if d_from > d_to:
        d_from = max(min_d, d_to - timedelta(days=DEFAULT_DAYS))
    pa, pb = ga.default_periods(max_d)
    s.update(empty=False, min_d=min_d, max_d=max_d, d_from=d_from, d_to=d_to,
             a=_period(p, "a", pa, min_d, max_d), b=_period(p, "b", pb, min_d, max_d))
    if mode == "zeme":
        codes = ga.countries()
        cc = p.str("cc", "CZ")
        s.update(codes=codes, cc=cc if cc in codes else (codes[0] if codes else "CZ"),
                 dir=p.str("dir", "both", choices=list(DIR_LABELS)),
                 by=p.str("by", "partner", choices=list(ga.BY_LABELS)))
        s["cat_options"] = ga.country_categories(s["cc"], d_from, d_to, s["by"], s["dir"])
        s["cats"] = tuple(c for c in p.list("kat") if c in s["cat_options"]) if not p.str("by_prev") or \
            p.str("by_prev") == s["by"] else ()
        s["gcv"] = ga.DEFAULT_GCV
        s["gcv_src"] = "konverzní faktor"
        return s
    _point_state(p, s)
    return s


def _point_state(p, s: dict) -> None:
    idx = ga.point_index()
    codes = sorted(idx["country"].unique(), key=lambda c: (c != "CZ", ga.country_name(c))) if not idx.empty else []
    cc = p.str("cc", "CZ")
    cc = cc if cc in codes else (codes[0] if codes else "CZ")
    d = p.str("dir", "entry", choices=["entry", "exit"])
    sub = idx[(idx["country"] == cc) & (idx["dir"] == d)]
    adjs = sorted(sub["adj"].unique(), key=lambda a: (a == "neurčeno", a))
    adj = p.str("adj", "")
    adj = adj if adj in adjs else ""
    pt = p.str("pt", "")
    all_pts = list(sub["point"])
    if pt in all_pts and p.str("adj_prev", adj) == adj:
        # bod vybraný hledáním → sousední systém podle bodu (kaskáda se dorovná)
        if adj and sub.loc[sub["point"] == pt, "adj"].iloc[0] != adj:
            adj = ""
    pts = list(sub[sub["adj"] == adj]["point"]) if adj else all_pts
    if pt not in pts:
        pt = pts[0] if pts else ""
    s.update(codes=codes, cc=cc, dir=d, adjs=adjs, adj=adj, pts=pts, all_pts=all_pts, pt=pt,
             pt_adj=sub.loc[sub["point"] == pt, "adj"].iloc[0] if pt in all_pts else "")
    gcv = ga.point_gcv(cc, d, pt, s["d_from"], s["d_to"]) if pt else None
    s["gcv"] = gcv or ga.DEFAULT_GCV
    s["gcv_src"] = "GCV bodu (průměr období)" if gcv else "konverzní faktor (bod GCV nehlásí)"
    s["sm_ind"] = p.str("si", "", choices=list(ga.FLOW_IND))


# ── výpočty sdílené záložkou a grafy ───────────────────────────────────

def _unit(s) -> str:
    return ga.UNIT_LABELS[s["u"]]


def _total_label(s) -> str:
    return "Bilance" if s["mode"] == "zeme" and s["dir"] == "both" else ("Celkem" if s["mode"] == "zeme" else None)


def _wide(s, d_from=None, d_to=None) -> pd.DataFrame:
    d_from, d_to = d_from or s["d_from"], d_to or s["d_to"]
    if s["mode"] == "zeme":
        return ga.country_daily(s["cc"], s["dir"], s["by"], d_from, d_to, s["cats"])
    if not s.get("pt"):
        return pd.DataFrame()
    return ga.point_daily(s["cc"], s["dir"], s["pt"], d_from, d_to)


def _subject(s) -> str:
    if s["mode"] == "zeme":
        return f"{ga.country_name(s['cc'])} — {DIR_LABELS[s['dir']].split(' (')[0]}"
    return f"{s['pt']} ({s['cc']}, {s['dir']})"


def _period_txt(a, b) -> str:
    return f"{fmt_value(a)}–{fmt_value(b)}"


def _season_series(s) -> tuple:
    """(denní řada, rok, popisek metriky) pro sezónnost — vždy 5 let zpět + rok konce období."""
    ref = s["d_to"].year
    lo = max(date(ref - 5, 1, 1), s["min_d"])
    if s["mode"] == "zeme":
        tot = ga.country_total(s["cc"], s["dir"], lo, s["d_to"], s["cats"], s["by"])
        lbl = "čistá bilance" if s["dir"] == "both" else DIR_LABELS[s["dir"]].split(" (")[0]
        return tot, ref, lbl
    fl = ga.point_flows(s["cc"], s["dir"], s["pt"], lo, s["d_to"]) if s.get("pt") else pd.DataFrame()
    ind = s.get("sm_ind") if s.get("sm_ind") in fl.columns else ga.best_flow(fl)
    if not ind:
        return pd.Series(dtype=float), ref, ""
    return fl[ind].dropna(), ref, ga.IND_LABELS[ind].lower()


def _compare(s) -> pd.DataFrame:
    (a0, a1), (b0, b1) = s["a"], s["b"]
    gcv_a = gcv_b = s["gcv"]
    cmp = ga.compare(_wide(s, a0, a1), _wide(s, b0, b1), s["u"], gcv_a, gcv_b, total_label=_total_label(s))
    if s["mode"] == "bod" and not cmp.empty:
        cmp.index = [ga.IND_LABELS.get(i, i) for i in cmp.index]
        cmp.index.name = "ukazatel"
    return cmp


# ── záložka ─────────────────────────────────────────────────────────────

@tab(PAGE, "analyza", "Analýza toků plynu")
def analyza(p):
    s = state(p)
    ctx = {"s": s, "mode_labels": MODE_LABELS, "grain_labels": ga.GRAIN_LABELS, "unit_labels": ga.UNIT_LABELS,
           "unit_help": ga.UNIT_HELP, "dir_labels": DIR_LABELS, "sm_labels": SM_LABELS, "presets": PRESETS,
           "by_labels": ga.BY_LABELS}
    if s.get("empty"):
        return ctx
    ctx["country_labels"] = {c: f"{c} · {ga.country_name(c)}" for c in s["codes"]}
    ctx["slider_from"], ctx["slider_to"] = _months(s["min_d"], s["max_d"])
    mf = pd.Timestamp(s["d_from"]).to_period("M").to_timestamp().date().isoformat()
    ctx["slider_from_i"] = next((i for i, v in enumerate(ctx["slider_from"]) if v >= mf), 0)
    ctx["slider_to_i"] = next((i for i, v in enumerate(ctx["slider_to"]) if v >= s["d_to"].isoformat()),
                              len(ctx["slider_to"]) - 1)
    unit = _unit(s)
    wide = _wide(s)
    agg = ga.aggregate(wide, s["g"], s["u"], s["gcv"])
    pv = ga.pivot_table(agg if s["mode"] == "zeme" else _point_labels(agg), s["g"], s["u"], _total_label(s))
    ctx["pivot"] = _table(_display_pivot(pv), first="období")
    ctx["pivot_rows"] = len(pv)
    ctx["kpis"] = _kpis(s, wide)
    cmp = _compare(s)
    ctx["compare"] = _cmp_table(cmp, unit)
    if s["mode"] == "bod":
        ctx["ind_options"] = [i for i in ga.FLOW_IND if i in wide.columns and wide[i].notna().any()]
        ctx["ind_labels"] = ga.IND_LABELS
        ctx["flow"] = ga.best_flow(wide)
        ints = ga.point_interruptions(s["cc"], s["dir"], s["pt"], s["d_from"], _int_end(s)) if s["pt"] else None
        ctx["ints"] = _ints_table(ints)
        ctx["n_ints"] = 0 if ints is None else len(ints)
    ctx["subject"] = _subject(s)
    ctx["period"] = _period_txt(s["d_from"], s["d_to"])
    ctx["a_txt"], ctx["b_txt"] = _period_txt(*s["a"]), _period_txt(*s["b"])
    ctx["unit"] = unit
    return ctx


def _point_labels(agg: pd.DataFrame) -> pd.DataFrame:
    return agg.rename(columns=ga.IND_LABELS) if agg is not None and not agg.empty else agg


def _display_pivot(pv: pd.DataFrame, max_periods: int = 200) -> pd.DataFrame:
    """Na stránce: souhrnné řádky nahoře, období od nejnovějšího (CSV/XLSX má vše chronologicky)."""
    if pv.empty:
        return pv
    foot = [i for i in pv.index if i in ("Součet", "Průměr", "Min", "Max")]
    body = pv.drop(index=foot).iloc[::-1].head(max_periods)
    return pd.concat([pv.loc[foot], body])


def _table(df: pd.DataFrame, first: str) -> dict:
    if df is None or df.empty:
        return None
    d = df.reset_index()
    d.columns = [first] + [str(c) for c in d.columns[1:]]
    cols = [(first, first, None)] + [(c, c, ",.1f") for c in d.columns[1:]]
    return table(d, cols, max_rows=400)


def _cmp_table(cmp: pd.DataFrame, unit: str) -> dict:
    if cmp is None or cmp.empty:
        return None
    d = cmp.reset_index()
    first = d.columns[0]
    return table(d, [(first, str(first), None), ("A", f"A [{unit}]", ",.1f"), ("B", f"B [{unit}]", ",.1f"),
                     ("rozdíl", f"rozdíl A − B [{unit}]", "+,.1f"), ("rozdíl %", "rozdíl %", "+,.1f")])


def _kpis(s, wide: pd.DataFrame) -> list:
    """Průměry za období (GWh/d) + poslední den."""
    if wide is None or wide.empty:
        return []
    out = []
    if s["mode"] == "zeme":
        if s["dir"] == "both":
            ent = wide[[c for c in wide.columns if c.endswith(" · vstup")]].sum(axis=1)
            ext = -wide[[c for c in wide.columns if c.endswith(" · výstup")]].sum(axis=1)
            out += [("Vstup — průměr", ent.mean()), ("Výstup — průměr", ext.mean()),
                    ("Bilance — průměr", (ent - ext).mean())]
            last = (ent - ext)
        else:
            last = wide.sum(axis=1)
            out += [(f"{DIR_LABELS[s['dir']].split(' (')[0].capitalize()} — průměr", last.mean()),
                    ("Maximum dne", last.max()), ("Minimum dne", last.min())]
        out.append((f"Poslední den ({fmt_value(wide.index[-1].date())})", last.iloc[-1]))
    else:
        flow = ga.best_flow(wide)
        if flow:
            f = wide[flow]
            out.append((f"{ga.IND_LABELS[flow]} — průměr", f.mean()))
            if "Firm Technical" in wide.columns and wide["Firm Technical"].notna().any():
                out.append(("Technická kapacita — průměr", wide["Firm Technical"].mean()))
                util = (f / wide["Firm Technical"].where(wide["Firm Technical"] > 0) * 100)
                out.append(("Využití kapacity — průměr", util.mean(), "%"))
            last = f.dropna()
            if not last.empty:
                out.append((f"Poslední den ({fmt_value(last.index[-1].date())})", last.iloc[-1]))
    unit = "GWh/d" if s["u"] != "mcmd" else "mcm/d"
    k = 1 / s["gcv"] if s["u"] == "mcmd" else 1
    res = []
    for item in out:
        lbl, v = item[0], item[1]
        u = item[2] if len(item) > 2 else unit
        val = v if u == "%" else (v * k if pd.notna(v) else v)
        res.append({"label": lbl, "value": fmt_value(val, ",.0f" if u == "%" else ",.1f"), "unit": u})
    return res


def _int_end(s) -> date:
    return max(s["d_to"], date.today() + timedelta(days=365))


def _ints_table(df) -> dict:
    if df is None or df.empty:
        return None
    d = df.copy()
    d["indicator"] = d["indicator"].map(lambda i: ga.IND_LABELS.get(i, i))
    d["updated"] = pd.to_datetime(d["updated"], utc=True).dt.tz_convert("Europe/Prague").dt.strftime("%d.%m.%Y")
    return table(d, [("indicator", "druh", None), ("operator", "operátor", None), ("from", "od", "%d.%m.%Y"),
                     ("to", "do (vč.)", "%d.%m.%Y"), ("v", "přerušeno [GWh/d]", ",.1f"),
                     ("updated", "aktualizace", None)], max_rows=150)


# ── grafy ───────────────────────────────────────────────────────────────

@chart(PAGE, "analyza", "main")
def _ch_main(p):
    from charts import gas_analysis as cg
    s = state(p)
    if s.get("empty"):
        return None
    wide = _wide(s)
    agg = ga.aggregate(wide, s["g"], s["u"], s["gcv"])
    unit = _unit(s)
    title = f"{_subject(s)} · {ga.GRAIN_LABELS[s['g']]} · {_period_txt(s['d_from'], s['d_to'])}"
    if s["mode"] == "zeme":
        return cg.fig_country(agg, s["g"], unit, title, _total_label(s) if len(agg.columns) > 1 or
                              s["dir"] == "both" else None)
    flow = ga.best_flow(wide)
    util = None
    if flow and "Firm Technical" in wide.columns:
        u = wide[flow] / wide["Firm Technical"].where(wide["Firm Technical"] > 0) * 100
        util = u.groupby(ga.bucket(u.index, s["g"])).mean()
    return cg.fig_point(agg, s["g"], unit, title, flow, util)


@chart(PAGE, "analyza", "season")
def _ch_season(p):
    from charts import gas_analysis as cg
    s = state(p)
    if s.get("empty"):
        return None
    tot, ref, lbl = _season_series(s)
    df = ga.seasonal(tot, s["sm"], s["u"], ref, s["gcv"])
    unit = _unit(s)
    return cg.fig_season(df, s["sm"], ref, unit, f"Sezónnost — {lbl} ({SM_LABELS[s['sm']]})")


@chart(PAGE, "analyza", "compare")
def _ch_compare(p):
    from charts import gas_analysis as cg
    s = state(p)
    if s.get("empty"):
        return None
    cmp = _compare(s)
    if s["mode"] == "zeme" and len(cmp) > 16:  # nejvýraznější kategorie
        tot = cmp.loc[[_total_label(s)]] if _total_label(s) in cmp.index else cmp.iloc[0:0]
        cmp = pd.concat([cmp.drop(index=tot.index).reindex(
            cmp.drop(index=tot.index)[["A", "B"]].abs().max(axis=1).nlargest(15).index), tot])
    return cg.fig_compare(cmp, _unit(s), _period_txt(*s["a"]), _period_txt(*s["b"]), "Srovnání období A vs B")


@chart(PAGE, "analyza", "int")
def _ch_int(p):
    from charts import gas_analysis as cg
    s = state(p)
    if s.get("empty") or s["mode"] != "bod" or not s.get("pt"):
        return None
    wide = ga.point_periods(s["cc"], s["dir"], s["pt"], ga.INT_IND, s["d_from"], _int_end(s))
    return cg.fig_interruptions(wide, "Přerušení kapacity (historie i plán)", today=date.today())


# ── exporty tabulek ────────────────────────────────────────────────────

def _fname(s, kind: str) -> str:
    who = s.get("pt") or s.get("cc", "")
    safe = "".join(ch if ch.isalnum() else "_" for ch in str(who))[:40]
    return f"plyn_{s['mode']}_{safe}_{kind}_{s.get('d_from')}_{s.get('d_to')}.csv"


@csv("plyn_pivot")
def _csv_pivot(p):
    s = state(p)
    if s.get("empty"):
        return pd.DataFrame(), "plyn_pivot.csv"
    agg = ga.aggregate(_wide(s), s["g"], s["u"], s["gcv"])
    pv = ga.pivot_table(agg if s["mode"] == "zeme" else _point_labels(agg), s["g"], s["u"], _total_label(s))
    return pv.round(3), _fname(s, f"pivot_{s['g']}_{s['u']}")


@csv("plyn_srovnani")
def _csv_compare(p):
    s = state(p)
    if s.get("empty"):
        return pd.DataFrame(), "plyn_srovnani.csv"
    cmp = _compare(s).round(3)
    cmp.columns = [f"A {_period_txt(*s['a'])}", f"B {_period_txt(*s['b'])}", "rozdíl", "rozdíl %"][:len(cmp.columns)]
    return cmp, _fname(s, f"srovnani_{s['u']}")
