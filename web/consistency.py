"""Sjednocení všech grafů před odesláním do prohlížeče (web/app.py):

- barva entity: série pojmenovaná zemí/zónou (CZ, DE_LU, Germany, Česko…)
  dostane barvu z core/palette.py — stejná země = stejná barva všude,
  nezávisle na pořadí série v grafu;
- časy v Praze: série s časy s časovou zónou (pandas Timestamp tz-aware,
  typicky UTC z úložiště) se převedou na pražský místní čas — plotly.js
  zónu ignoruje a ukazoval by UTC hodiny;
- prázdná figura (žádná série nebo série bez bodů) → prázdný stav
  „data se dotahují“ místo prázdných os.
"""
import numpy as np
import pandas as pd

from core.palette import entity_color

TZ = "Europe/Prague"

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


def _to_local(arr):
    if arr is None or isinstance(arr, (str, bytes)) or not hasattr(arr, "__len__") or not len(arr):
        return None
    first = next((v for v in arr if v is not None and not (isinstance(v, float) and np.isnan(v))), None)
    if not isinstance(first, pd.Timestamp) or first.tzinfo is None:
        return None
    try:
        idx = pd.DatetimeIndex(pd.to_datetime(list(arr), utc=True)).tz_convert(TZ).tz_localize(None)
    except (ValueError, TypeError):
        return None
    return idx.to_numpy()


def localize_times(fig):
    for tr in getattr(fig, "data", ()):
        for attr in ("x", "y", "base"):
            if attr not in tr:
                continue
            new = _to_local(getattr(tr, attr, None))
            if new is not None:
                setattr(tr, attr, new)
    return fig
