"""Reporty — denní a týdenní sestava pro zvolené datum (core/report.py).

Tisk z prohlížeče (print CSS, A4), export PDF (/api/report/<druh>.pdf) a XLSX
(/api/report/<druh>.xlsx). Uložené pohledy Průzkumníku lze přidat (views=…).
"""
import pandas as pd

from web.registry import page, tab, chart

PAGE = "reporty"
page(PAGE, "Reporty", "📋")


def _state(p, kind):
    from core.report import KINDS, SECTIONS, parse_date
    from web.tabs.explorer import load_views
    asof = parse_date(p.str("date"))
    views = load_views()
    names = [v["name"] for v in views]
    sel = p.list("views", [], choices=names)
    if kind == "tydenni":
        period = f"{(asof - pd.Timedelta(days=6)):%d.%m.} – {asof:%d.%m.%Y}"
    else:
        period = f"{asof:%d.%m.%Y}"
    return {"kind": kind, "title": KINDS[kind], "date": asof.date().isoformat(), "period": period,
            "sections": [(sid, t) for sid, t, _ in SECTIONS[kind]], "views": views,
            "sel_views": sel, "sel_view_objs": [v for v in views if v["name"] in sel],
            "today": pd.Timestamp.now(tz="Europe/Prague").date().isoformat(), "query": p.query(),
            "created": pd.Timestamp.now(tz="Europe/Prague").strftime("%d.%m.%Y %H:%M")}


def _make(kind, title):
    @tab(PAGE, kind, title)
    def _tab(p, _kind=kind):
        return _state(p, _kind)

    from core.report import SECTIONS
    for sid, _t, _fn in SECTIONS[kind]:
        def _ch(p, _kind=kind, _sid=sid):
            from core.report import parse_date, section_figure
            return section_figure(_kind, _sid, parse_date(p.str("date")))
        chart(PAGE, kind, sid)(_ch)


_make("denni", "Denní report")
_make("tydenni", "Týdenní report")
