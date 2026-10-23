"""Pomocné funkce pro záložky: převody DataFrame → tabulka, stav dat, KPI."""
import datetime as _dt
import math

import pandas as pd


def decat(df: pd.DataFrame) -> pd.DataFrame:
    """category → object (jen pro MALÉ vyfiltrované podvýběry před předáním
    do charts/ — groupby přes category defaultně vrací všechny kombinace
    kategorií, viz data/entsog.py::load_entsog_history)."""
    if df is None or df.empty:
        return df
    cat_cols = [c for c in df.columns if isinstance(df[c].dtype, pd.CategoricalDtype)]
    if not cat_cols:
        return df
    out = df.copy()
    for c in cat_cols:
        out[c] = out[c].astype(object)
    return out


def options(series: pd.Series) -> list:
    """Seřazené unikátní ne-NaN hodnoty (i pro category) jako str."""
    if series is None or len(series) == 0:
        return []
    vals = series.dropna()
    if isinstance(vals.dtype, pd.CategoricalDtype):
        vals = vals.astype(object)
    return sorted({str(v) for v in vals.unique()})


def fmt_value(v, fmt: str = None) -> str:
    if v is None:
        return "—"
    if isinstance(v, float) and math.isnan(v):
        return "—"
    if v is pd.NaT:
        return "—"
    if isinstance(v, pd.Timestamp):
        if v.tzinfo is not None:
            v = v.tz_convert("Europe/Prague")
        return v.strftime(fmt or ("%d.%m.%Y %H:%M" if (v.hour or v.minute) else "%d.%m.%Y"))
    if isinstance(v, _dt.datetime):
        return v.strftime(fmt or "%d.%m.%Y %H:%M")
    if isinstance(v, _dt.date):
        return v.strftime(fmt or "%d.%m.%Y")
    if isinstance(v, (int, float)) and fmt:
        try:
            return cz_number(format(v, fmt))
        except (ValueError, TypeError):
            return str(v)
    if isinstance(v, float):
        return cz_number(f"{v:,.2f}")
    return str(v)


def cz_number(s: str) -> str:
    """'1,234.5' → '1 234,5' (česky: mezera = tisíce, čárka = desetinná),
    záporné znaménko jako typografické minus."""
    s = s.replace(",", "\u00a0").replace(".", ",")
    return "−" + s[1:] if s.startswith("-") else s


def table(df: pd.DataFrame, columns: list = None, max_rows: int = 300) -> dict:
    """{"headers": [...], "rows": [[...]], "total": N, "truncated": bool}.

    columns: [(sloupec, popisek, formát|None), ...] — bez nich všechny
    sloupce. Formát = format-spec pro čísla (",.0f") nebo strftime."""
    if df is None or df.empty:
        return {"headers": [c[1] for c in columns] if columns else [], "rows": [], "total": 0, "truncated": False}
    if columns is None:
        columns = [(c, str(c), None) for c in df.columns]
    columns = [c for c in columns if c[0] in df.columns]
    sub = df.head(max_rows)
    rows = []
    for rec in sub[[c[0] for c in columns]].itertuples(index=False, name=None):
        rows.append([fmt_value(v, c[2]) for v, c in zip(rec, columns)])
    return {"headers": [c[1] for c in columns], "rows": rows, "total": len(df),
            "truncated": len(df) > max_rows}


def status_item(name: str, dt, freshness_hours: float) -> dict:
    """Položka řádku "stav dat" (dřív config.data_status_row)."""
    if dt is None or (isinstance(dt, float) and math.isnan(dt)):
        return {"name": name, "ok": False, "date": "N/A"}
    ts = pd.Timestamp(dt)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("Europe/Prague").tz_localize(None)
    age_h = (pd.Timestamp.now(tz="Europe/Prague").tz_localize(None) - ts).total_seconds() / 3600
    return {"name": name, "ok": age_h <= freshness_hours, "date": ts.strftime("%d.%m.%Y")}


def safe_max_date(df: pd.DataFrame, col: str):
    try:
        if df is None or df.empty or col not in df.columns:
            return None
        s = pd.to_datetime(df[col], utc=True)
        return pd.Timestamp(s.dt.tz_convert("Europe/Prague").dt.date.max())
    except Exception:
        return None


def date_range_from_params(p, max_date: _dt.date, default_days: int, min_date: _dt.date = None,
                           presets: dict = None) -> tuple:
    """Rozsah od–do z parametrů: tlačítko rychlého výběru (range=7|30|...|max)
    má přednost, jinak from/to, jinak default (max_date - default_days)."""
    rng = p.str("range")
    if rng:
        if rng == "max" and min_date is not None:
            return min_date, max_date
        try:
            days = int(rng)
            return max_date - _dt.timedelta(days=days), max_date
        except ValueError:
            pass
    d_from = p.date("from")
    d_to = p.date("to")
    if d_from and d_to and d_from <= d_to:
        return d_from, d_to
    start = max_date - _dt.timedelta(days=default_days)
    if min_date is not None and start < min_date:
        start = min_date
    return start, max_date
