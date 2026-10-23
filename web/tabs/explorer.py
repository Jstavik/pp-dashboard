"""Průzkumník à la Power BI — výběr datasetu z katalogu (core/datasets.py::
EXPLORE), filtry (slicery), seskupení zrnitost × kategorie, agregace,
pivot, typ grafu, porovnání let, cross-filtering, uložené pohledy.

Dotazy počítá DuckDB přímo nad parquet/CSV (core/explore.py), do
prohlížeče jde jen výsledek. Stav je celý v URL (sdílení = odkaz); pojmenované
pohledy se ukládají na server do <PPDASH_DATA_DIR>/explorer_views.json.
"""
import json
import threading

import pandas as pd

from core import explore as ex
from core.datasets import EXPLORE, EXPLORE_BY_KEY, dataset_status, schedule_for
from core.settings import data_dir
from web.registry import page, tab, chart
from web.util import date_range_from_params, fmt_value, table

PAGE = "pruzkumnik"
page(PAGE, "Průzkumník", "🔎")

PRESETS = [("Týden", 7), ("Měsíc", 30), ("Kvartál", 90), ("Rok", 365), ("3 roky", 1095), ("Max", "max")]
GRAIN_CHOICES = ["auto"] + ex.GRAINS


# ── stav z parametrů ────────────────────────────────────────────────────

def _filters(p, spec, fresh: bool) -> dict:
    out = {}
    defaults = dict(spec.default_filters)
    for d in spec.dims:
        name = f"f_{d.id}"
        if not fresh and (p.has(name) or p.has(f"{name}__set")):
            vals = p.list(name)
        else:
            vals = list(defaults.get(d.id, ()))
        if vals:
            out[d.id] = tuple(vals)
    return out


def state(p) -> dict:
    keys = [e.key for e in EXPLORE]
    ds = p.str("ds", "generation", choices=keys)
    spec = EXPLORE_BY_KEY[ds]
    # změna datasetu ve formuláři → parametry starého datasetu se zahodí
    fresh = p.str("ds_prev") not in (None, ds)
    b = ex.bounds(ds)
    if b is None:
        return {"spec": spec, "ds": ds, "empty": True}
    min_d, max_d = b
    d_from, d_to = date_range_from_params(p, max_d, spec.default_days, min_date=min_d)
    m_ids = [m.id for m in spec.measures]
    measure = spec.measure(p.str("m", m_ids[0], choices=m_ids) if not fresh else m_ids[0])
    dim_ids = [d.id for d in spec.dims]
    cat = spec.default_cat if fresh or not p.has("cat") else p.str("cat", "", choices=dim_ids + [""])
    tagg = None if fresh else p.str("agg", None, choices=ex.AGGS)
    cagg = None if fresh else p.str("cagg", None, choices=ex.CAT_AGGS)
    grain = p.str("g", "auto", choices=GRAIN_CHOICES)
    chart_type = p.str("chart", "line", choices=_chart_types())
    filters = _filters(p, spec, fresh)
    q = ex.Query(ds, measure.id, d_from, d_to, grain, cat or None, tagg, cagg,
                 tuple(sorted(filters.items())))
    return {"spec": spec, "ds": ds, "q": q, "rq": q.resolved(), "measure": measure, "filters": filters,
            "min_d": min_d, "max_d": max_d, "d_from": d_from, "d_to": d_to, "chart": chart_type,
            "pr": p.str("pr", "time", choices=list(ex.PIVOT_ROWS)),
            "pc": p.str("pc", "cat", choices=list(ex.PIVOT_COLS)), "empty": False}


def _chart_types():
    from charts.explorer import CHART_TYPES
    return CHART_TYPES


def _query_string(s: dict, p) -> str:
    q = s["q"]
    items = [("ds", s["ds"]), ("m", q.measure), ("from", s["d_from"]), ("to", s["d_to"]), ("g", q.grain),
             ("cat", q.cat or ""), ("chart", s["chart"]), ("pr", s["pr"]), ("pc", s["pc"])]
    if q.time_agg:
        items.append(("agg", q.time_agg))
    if q.cat_agg:
        items.append(("cagg", q.cat_agg))
    for d in s["spec"].dims:
        items.append((f"f_{d.id}__set", "1"))
        items.extend((f"f_{d.id}", v) for v in s["filters"].get(d.id, ()))
    from urllib.parse import urlencode
    return urlencode([(k, str(v)) for k, v in items])


def _title(s: dict, rq=None) -> tuple:
    rq = rq or s["rq"]
    m = s["measure"]
    flt = ", ".join(f"{s['spec'].dim(k).label}: {', '.join(v[:4])}{'…' if len(v) > 4 else ''}"
                    for k, v in s["filters"].items())
    sub = (f"{ex.AGG_LABELS[rq.time_agg]} · {ex.GRAIN_LABELS[rq.grain]} · "
           f"{fmt_value(s['d_from'])}–{fmt_value(s['d_to'])}" + (f" · {flt}" if flt else ""))
    return f"{s['spec'].label} — {m.label}", sub


# ── záložka Průzkumník ──────────────────────────────────────────────────

@tab(PAGE, "pohled", "Průzkumník")
def pohled(p):
    s = state(p)
    groups = {}
    for e in EXPLORE:
        groups.setdefault(e.group, []).append((e.key, e.label))
    ctx = {"groups": groups, "ds": s["ds"], "spec": s["spec"], "views": load_views()}
    if s["empty"]:
        ctx["empty"] = True
        ctx["query"] = f"ds={s['ds']}"
        return ctx
    from charts.explorer import CHART_LABELS, CHART_TYPES
    q, rq, spec = s["q"], s["rq"], s["spec"]
    other = tuple(sorted(s["filters"].items()))
    dims = []
    for d in spec.dims:
        others = tuple((k, v) for k, v in other if k != d.id)
        opts = ex.dim_values(s["ds"], d.id, others)
        sel = list(s["filters"].get(d.id, ()))
        dims.append({"id": d.id, "label": d.label, "options": sorted(set(opts) | set(sel)), "selected": sel})
    res = ex.run(q)
    pv = ex.pivot(res, s["pr"], s["pc"], label=_label)
    pv_t = None
    if not pv.empty:
        pv_df = pv.reset_index()
        cols = [(pv_df.columns[0], str(pv_df.columns[0]), None)] + \
               [(c, str(c), _num_fmt(pv_df[c])) for c in pv_df.columns[1:]]
        pv_t = table(pv_df, cols, max_rows=400)
    ctx.update({
        "query": _query_string(s, p), "measure_ids": [m.id for m in spec.measures],
        "measure_labels": {m.id: f"{m.label} [{m.unit}]" if m.unit else m.label for m in spec.measures},
        "m": q.measure, "agg": rq.time_agg, "cagg": rq.cat_agg, "g": q.grain, "rg": res.query.grain,
        "cat": q.cat or "", "chart": s["chart"], "dims": dims,
        "agg_choices": [a for a in ex.AGGS if a != "energy" or s["measure"].power],
        "agg_labels": ex.AGG_LABELS, "cagg_choices": ex.CAT_AGGS, "grain_choices": GRAIN_CHOICES,
        "grain_labels": ex.GRAIN_LABELS, "chart_choices": CHART_TYPES, "chart_labels": CHART_LABELS,
        "cat_choices": [""] + [d.id for d in spec.dims],
        "cat_labels": {"": "— (celkem)", **{d.id: d.label for d in spec.dims}},
        "pr": s["pr"], "pc": s["pc"], "pr_choices": list(ex.PIVOT_ROWS), "pc_choices": list(ex.PIVOT_COLS),
        "pr_labels": ex.PIVOT_ROWS, "pc_labels": ex.PIVOT_COLS,
        "d_from": s["d_from"], "d_to": s["d_to"], "min_d": s["min_d"], "max_d": s["max_d"],
        "presets": PRESETS, "warnings": res.warnings, "n_rows": len(res.df), "n_cats": res.n_cats,
        "elapsed": res.elapsed_s, "unit": res.unit, "pivot": pv_t, "sql": res.sql,
        "schedule": schedule_for(spec), "has_breakdown": bool(rq.cat) and s["chart"] in ("line", "bar", "area"),
    })
    return ctx


def _label(cat: str) -> str:
    from charts.explorer import series_name
    return series_name(cat)


def _num_fmt(col: pd.Series) -> str:
    try:
        m = float(col.abs().max())
    except (TypeError, ValueError):
        return None
    return ",.0f" if m >= 1000 else ",.1f" if m >= 10 else ",.3f"


@chart(PAGE, "pohled", "main", displayModeBar="hover")
def _ch_main(p):
    from charts import explorer as ce
    s = state(p)
    if s["empty"]:
        return None
    kind = s["chart"]
    if kind in ("line", "bar", "area"):
        res = ex.run(s["q"])
        if res.empty:
            return None
        title, sub = _title(s, res.query)
        return ce.fig_timeseries(res.df, kind, title, res.unit, sub)
    q = s["q"]
    if kind == "season":
        g = q.grain if q.grain in ("week", "month") else "day"
        res = ex.run(ex.Query(q.ds, q.measure, q.d_from, q.d_to, g, None, q.time_agg, q.cat_agg, q.filters))
        title, _ = _title(s, res.query)
        return None if res.empty else ce.fig_season(ex.seasonal(res), res.query.grain, title, res.unit)
    g = "day" if kind == "heat_year" else "hour"
    res = ex.run(ex.Query(q.ds, q.measure, q.d_from, q.d_to, g, None, q.time_agg, q.cat_agg, q.filters))
    if res.empty:
        return None
    title, _ = _title(s, res.query)
    return ce.fig_heat_year(res.df, title, res.unit) if kind == "heat_year" else \
        ce.fig_heat_hour(res.df, title, res.unit)


@chart(PAGE, "pohled", "breakdown")
def _ch_breakdown(p):
    from charts import explorer as ce
    s = state(p)
    if s["empty"] or not s["rq"].cat:
        return None
    q = s["q"]
    # cross-filter: zoom hlavního grafu posílá užší okno (xf_from/xf_to)
    lo, hi = p.date("xf_from"), p.date("xf_to")
    if lo and hi and lo <= hi:
        q = ex.Query(q.ds, q.measure, max(lo, q.d_from), min(hi, q.d_to), q.grain, q.cat, q.time_agg,
                     q.cat_agg, q.filters)
    df = ex.breakdown(q)
    if df.empty:
        return None
    rq = q.resolved()
    label = s["spec"].dim(rq.cat).label
    period = f"{fmt_value(q.d_from)}–{fmt_value(q.d_to)}"
    return ce.fig_breakdown(df, f"Podle: {label} ({ex.AGG_LABELS[rq.time_agg]}, {period})",
                            ex.unit_for(s["spec"], rq.measure, rq.time_agg),
                            selected=s["filters"].get(rq.cat, ()))


def pivot_export(p) -> tuple:
    """(DataFrame pivotu, základ názvu souboru) pro /api/explorer/pivot."""
    s = state(p)
    if s["empty"]:
        return pd.DataFrame(), s["ds"]
    res = ex.run(s["q"])
    return ex.pivot(res, s["pr"], s["pc"], label=_label), f"pruzkumnik_{s['ds']}_{s['q'].measure}_pivot"


# ── uložené pohledy ─────────────────────────────────────────────────────

_views_lock = threading.Lock()


def _views_path():
    return data_dir() / "explorer_views.json"


def load_views() -> list:
    try:
        return json.loads(_views_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save_view(name: str, query: str) -> list:
    name = (name or "").strip()[:80]
    if not name:
        raise ValueError("chybí název pohledu")
    with _views_lock:
        views = [v for v in load_views() if v.get("name") != name]
        views.append({"name": name, "query": query[:4000],
                      "created": pd.Timestamp.now(tz="Europe/Prague").strftime("%Y-%m-%d %H:%M")})
        views.sort(key=lambda v: v["name"].lower())
        path = _views_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(views, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    return views


def delete_view(name: str) -> list:
    with _views_lock:
        views = [v for v in load_views() if v.get("name") != name]
        _views_path().write_text(json.dumps(views, ensure_ascii=False, indent=1), encoding="utf-8")
    return views


# ── záložka Katalog ─────────────────────────────────────────────────────

@tab(PAGE, "katalog", "Katalog dat")
def katalog(p):
    status = {r["name"]: r for r in dataset_status()}
    rows = []
    for e in EXPLORE:
        b = ex.bounds(e.key)
        st = status.get(e.dataset or "", {})
        rows.append({
            "key": e.key, "group": e.group, "label": e.label, "source": e.source,
            "native": e.native, "schedule": schedule_for(e),
            "first": fmt_value(b[0]) if b else "—", "last": fmt_value(b[1]) if b else "—",
            "age": st.get("age_h"), "ok": st.get("ok", False), "size": st.get("size_mb"),
            "measures": ", ".join(f"{m.label} [{m.unit}]" if m.unit else m.label for m in e.measures),
            "dims": ", ".join(d.label for d in e.dims), "description": e.description,
        })
    return {"rows": rows, "query": ""}

