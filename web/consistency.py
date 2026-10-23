"""Sjednocení všech grafů před odesláním do prohlížeče (web/app.py):

- barva entity: série pojmenovaná zemí/zónou (CZ, DE_LU, Germany, Česko…)
  dostane barvu z core/palette.py — stejná země = stejná barva všude,
  nezávisle na pořadí série v grafu;
- prázdná figura (žádná série nebo série bez bodů) → prázdný stav
  „data se dotahují“ místo prázdných os.
"""
from core.palette import entity_color

_XY = {"scatter", "scattergl", "bar", "histogram", "box", "violin"}


def _has_points(tr) -> bool:
    t = tr.type or "scatter"
    if t == "table":
        cells = getattr(tr, "cells", None)
        vals = getattr(cells, "values", None) if cells is not None else None
        return bool(vals) and any(len(v) for v in vals if v is not None)
    for attr in ("y", "x", "z", "values", "lat", "locations"):
        v = getattr(tr, attr, None) if attr in tr else None
        if v is not None and len(v):
            return True
    return False


def is_empty(fig) -> bool:
    data = getattr(fig, "data", None)
    if data is None:
        return False
    return len(data) == 0 or not any(_has_points(tr) for tr in data)


def apply_entity_colors(fig):
    for tr in getattr(fig, "data", ()):
        if (tr.type or "scatter") not in _XY:
            continue
        c = entity_color(getattr(tr, "name", None))
        if not c:
            continue
        if tr.type == "bar" or tr.type == "histogram":
            mc = tr.marker.color if tr.marker else None
            if mc is None or isinstance(mc, str):
                tr.marker.color = c
        else:
            mode = tr.mode or "lines"
            if tr.line is not None:
                tr.line.color = c
            if "markers" in mode and (tr.marker.color is None or isinstance(tr.marker.color, str)):
                tr.marker.color = c
            if tr.fill and tr.fill != "none" and isinstance(tr.fillcolor, str) and tr.fillcolor.startswith("#"):
                tr.fillcolor = c
    return fig
