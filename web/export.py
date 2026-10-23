"""Export dat za grafem (CSV / XLSX) — z Plotly figury, kterou vrací funkce
grafu (stejné parametry jako /api/chart/...), takže export obsahuje přesně
to, co je v grafu vidět.

Tvar tabulky:
- "long": řádek = bod série (series, typ, osa x, y, případně z/labels/
  values/lat/lon/locations/text) — funguje pro každý typ grafu,
- "wide": index = osa x, sloupec = série — jen když mají všechny série
  x i y stejné délky a x v rámci série neopakuje (typické časové řady).
CSV = wide, když jde, jinak long. XLSX = list "data" (wide/long), list
"long" (vždy) a "info" (titulek, parametry, čas exportu).
"""
import io
import re

import numpy as np
import pandas as pd

TZ = "Europe/Prague"
_ARRAY_ATTRS = ("x", "y", "z", "labels", "values", "parents", "ids", "lat", "lon", "locations",
                "open", "high", "low", "close", "base", "text")


def _to_figure(fig):
    import plotly.graph_objects as go
    if isinstance(fig, go.Figure):
        return fig
    return go.Figure(fig, skip_invalid=True)


def _arr(v):
    if v is None or isinstance(v, (str, bytes, int, float)):
        return None
    try:
        a = np.asarray(v, dtype=object)
    except Exception:
        return None
    return a if a.ndim >= 1 and a.size else None


def figure_title(fig) -> str:
    t = getattr(getattr(fig.layout, "title", None), "text", None) or ""
    return re.sub(r"<[^>]+>", " ", str(t)).replace("&nbsp;", " ").strip()


def _trace_names(fig) -> list:
    """Unikátní názvy sérií; stejné jméno ve víc podgrafech (např.
    "skutečnost" ×3) se odliší osou y, jinak pořadím."""
    names = []
    for i, tr in enumerate(fig.data):
        base = str(getattr(tr, "name", None) or f"{tr.type} {i + 1}")
        name = base
        if name in names:
            axis = getattr(tr, "yaxis", None)
            name = f"{base} ({axis})" if axis and axis != "y" else base
            k = 2
            while name in names:
                name = f"{base} #{k}"
                k += 1
        names.append(name)
    return names


def _table_df(tr) -> pd.DataFrame:
    """go.Table → DataFrame (hlavička bez HTML)."""
    header = list(getattr(tr.header, "values", None) or [])
    cells = list(getattr(tr.cells, "values", None) or [])
    n = max((len(c) for c in cells if c is not None and not isinstance(c, str)), default=0)
    cols = {}
    for i, col in enumerate(cells):
        h = header[i] if i < len(header) else f"sloupec {i + 1}"
        h = re.sub(r"<[^>]+>", "", str(h[0] if isinstance(h, (list, tuple)) else h)).strip() or f"sloupec {i + 1}"
        vals = [col] * n if isinstance(col, str) or col is None else list(col)
        cols[h] = [re.sub(r"<[^>]+>", " ", v).strip() if isinstance(v, str) else v for v in vals]
    return pd.DataFrame(cols)


def figure_long(fig) -> pd.DataFrame:
    fig = _to_figure(fig)
    frames = []
    for name, tr in zip(_trace_names(fig), fig.data):
        if tr.type == "table":
            df = _table_df(tr)
            df.insert(0, "serie", name)
            frames.append(df)
            continue
        cols = {}
        z = _arr(getattr(tr, "z", None)) if "z" in tr else None
        if z is not None and z.ndim == 2:  # heatmapa/contour: z[y][x]
            x = _arr(getattr(tr, "x", None))
            y = _arr(getattr(tr, "y", None))
            ny, nx = z.shape
            x = x if x is not None and len(x) == nx else np.arange(nx)
            y = y if y is not None and len(y) == ny else np.arange(ny)
            xx, yy = np.meshgrid(x, y)
            cols = {"x": xx.ravel(), "y": yy.ravel(), "z": z.ravel()}
        else:
            n = None
            for attr in _ARRAY_ATTRS:
                if attr not in tr:
                    continue
                a = _arr(getattr(tr, attr, None))
                if a is None or a.ndim != 1:
                    continue
                if n is None and attr != "text":
                    n = len(a)
                if n is not None and len(a) == n:
                    cols[attr] = a
        if not cols or all(k == "text" for k in cols):
            continue
        df = pd.DataFrame(cols)
        df.insert(0, "typ", tr.type)
        df.insert(0, "serie", name)
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["serie", "typ"])
    return pd.concat(frames, ignore_index=True)


def figure_wide(fig):
    """Wide tabulka (index = osa x) nebo None, když to tvar dat nedovolí."""
    fig = _to_figure(fig)
    tables = [tr for tr in fig.data if tr.type == "table"]
    if tables and len(tables) == len(fig.data):
        return pd.concat([_table_df(tr) for tr in tables], ignore_index=True)
    series = {}
    xname = None
    for name, tr in zip(_trace_names(fig), fig.data):
        if tr.type not in ("scatter", "scattergl", "bar", "histogram") and not tr.type.startswith("scatter"):
            return None
        x, y = _arr(getattr(tr, "x", None)), _arr(getattr(tr, "y", None))
        if getattr(tr, "orientation", None) == "h":
            x, y = y, x
        if x is None or y is None or x.ndim != 1 or y.ndim != 1 or len(x) != len(y):
            return None
        s = pd.Series(y, index=pd.Index(x))
        if s.index.has_duplicates:
            return None
        series[name] = s
        if xname is None:
            ax = fig.layout.xaxis
            xname = (getattr(getattr(ax, "title", None), "text", None) or "x") if ax else "x"
    if not series:
        return None
    wide = pd.concat(series, axis=1)
    try:
        wide = wide.sort_index()
    except TypeError:
        pass
    wide.index.name = re.sub(r"<[^>]+>", "", str(xname))
    return wide


def _clean(df: pd.DataFrame, excel: bool) -> pd.DataFrame:
    """Časy: tz-aware → místní (Praha); pro Excel bez tz (Excel tz neumí).
    Číselné sloupce z object polí → čísla."""
    df = df.copy()

    def fix(col: pd.Series) -> pd.Series:
        if col.dtype == object:
            num = pd.to_numeric(col, errors="coerce")
            if num.notna().sum() == col.notna().sum() and col.notna().any():
                return num
            sample = col.dropna().head(20)
            if len(sample) and all(isinstance(v, (pd.Timestamp, np.datetime64)) or
                                   (isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2})?", v))
                                   for v in sample):
                try:
                    col = pd.to_datetime(col, utc=True, format="mixed")
                except (ValueError, TypeError):
                    return col
        if isinstance(col.dtype, pd.DatetimeTZDtype):
            col = col.dt.tz_convert(TZ)
            if excel:
                col = col.dt.tz_localize(None)
        return col

    for c in df.columns:
        df[c] = fix(df[c])
    if not isinstance(df.index, pd.RangeIndex):
        idx = fix(pd.Series(df.index, name=df.index.name))
        df.index = pd.Index(idx, name=df.index.name)
    return df


def to_csv_bytes(fig) -> bytes:
    wide = figure_wide(fig)
    df = _clean(wide, excel=False) if wide is not None else _clean(figure_long(fig), excel=False)
    buf = io.StringIO()
    df.to_csv(buf, index=not isinstance(df.index, pd.RangeIndex))
    # BOM — Excel pak CSV otevře v UTF-8 (diakritika)
    return ("﻿" + buf.getvalue()).encode("utf-8")


def to_xlsx_bytes(fig, info: dict = None) -> bytes:
    fig = _to_figure(fig)
    wide = figure_wide(fig)
    long = _clean(figure_long(fig), excel=True)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        if wide is not None:
            indexed = not isinstance(wide.index, pd.RangeIndex)  # tabulka (go.Table) nemá osu x
            _clean(wide, excel=True).to_excel(xw, sheet_name="data", index=indexed)
            if indexed:
                long.to_excel(xw, sheet_name="long", index=False)
        else:
            long.to_excel(xw, sheet_name="data", index=False)
        meta = {"titulek": figure_title(fig), **(info or {}),
                "exportováno": pd.Timestamp.now(tz=TZ).strftime("%Y-%m-%d %H:%M")}
        pd.DataFrame(list(meta.items()), columns=["položka", "hodnota"]).to_excel(xw, sheet_name="info", index=False)
        for ws in xw.book.worksheets:
            for col in ws.columns:
                width = max((len(str(c.value)) for c in col[:200] if c.value is not None), default=8)
                ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 10), 60)
    return buf.getvalue()
