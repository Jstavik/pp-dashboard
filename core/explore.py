"""Průzkumník — dotazy nad historií přes DuckDB (agregace na serveru).

Katalog datasetů: core/datasets.py::EXPLORE. Dotaz = dataset × míra ×
filtry × časové okno × seskupení (zrnitost × kategorie) × agregace.

Agregace je dvoustupňová (jako „measure“ v Power BI):
1. sloučení kategorií — v každém časovém bodě se přes dimenze, které NEjsou
   zvolenou kategorií, použije `cat_agg` (výroba: součet zemí/zdrojů, cena:
   průměr). Série s různým nativním rozlišením (15 min vs. hodina) se předtím
   zarovnají na `spec.align`, jinak by součet ve čtvrthodinách „skákal“.
2. agregace v čase — v každém kusu (hodina/den/…) `time_agg`
   (sum/avg/min/max/last, nebo energy = průměrný výkon × délka kusu v hodinách,
   tj. MW → MWh i přes změnu času).

Paměť (web na 4 GB LXC vedle aktualizací): DuckDB má vlastní limit
(PPDASH_DUCKDB_MEM, default 512MB; nad něj spilluje do cache/duckdb_tmp),
čte jen potřebné sloupce a jen soubory v časovém okně (měsíční/denní
partitiony podle názvu, podadresáře podle filtru partition dimenze).
Do pandas/prohlížeče jde jen výsledek, a ten má strop MAX_ROWS — při
překročení se zrnitost automaticky zhrubne (s varováním).
"""
import glob
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from core.cache import ttl_cache
from core.datasets import EXPLORE_BY_KEY, ExploreSpec
from core.settings import cache_root, env, history_root

log = logging.getLogger("ppdash.explore")

TZ = "Europe/Prague"
GRAINS = ["raw", "15min", "hour", "day", "week", "month", "quarter", "year"]
GRAIN_LABELS = {"auto": "auto", "raw": "nativní", "15min": "15 min", "hour": "hodina", "day": "den",
                "week": "týden", "month": "měsíc", "quarter": "kvartál", "year": "rok"}
_GRAIN_INTERVAL = {"15min": "15 minutes", "hour": "1 hour", "day": "1 day", "week": "7 days",
                   "month": "1 month", "quarter": "3 months", "year": "1 year"}
_GRAIN_HOURS = {"raw": 0.25, "15min": 0.25, "hour": 1, "day": 24, "week": 168, "month": 730,
                "quarter": 2190, "year": 8760}
_NATIVE_GRAIN = {"1min": "raw", "5min": "raw", "15min": "raw", "1h": "hour", "1D": "day", "1W": "week"}
AGGS = ["sum", "avg", "min", "max", "last", "energy"]
AGG_LABELS = {"sum": "součet", "avg": "průměr", "min": "minimum", "max": "maximum",
              "last": "poslední", "energy": "energie (průměr × hodiny)"}
CAT_AGGS = ["sum", "avg", "min", "max"]

MAX_ROWS = int(env("PPDASH_EXPLORE_MAX_ROWS", "150000"))
MAX_CATS = 30
MAX_DIM_VALUES = 3000
TOTAL = "celkem"

_PART_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")
_FILENAME_PARENT = "regexp_extract(filename, '([^/]+)/[^/]+$', 1)"

_con = None
_con_lock = threading.Lock()


def _connection():
    """Jedna in-memory DuckDB instance na proces; každý dotaz si bere
    vlastní cursor (vlákna FastAPI threadpoolu)."""
    global _con
    if _con is None:
        with _con_lock:
            if _con is None:
                import duckdb
                tmp = cache_root() / "duckdb_tmp"
                tmp.mkdir(parents=True, exist_ok=True)
                _con = duckdb.connect(config={
                    "memory_limit": env("PPDASH_DUCKDB_MEM", "512MB"),
                    "threads": int(env("PPDASH_DUCKDB_THREADS", "2")),
                    "preserve_insertion_order": False,
                    "temp_directory": str(tmp),
                    "max_temp_directory_size": "2GB",
                })
    cur = _con.cursor()
    cur.execute(f"SET TimeZone = '{TZ}'")
    return cur


def unit_for(spec: ExploreSpec, measure_id: str, agg: str) -> str:
    unit = spec.measure(measure_id).unit
    if agg == "energy":
        return {"MW": "MWh", "GW": "GWh", "kW": "kWh"}.get(unit, f"{unit}·h")
    return unit


# ── soubory ─────────────────────────────────────────────────────────────

def files_for(spec: ExploreSpec, filters: dict = None, d_from: date = None, d_to: date = None) -> list:
    """Soubory zdroje prořezané podle časového okna (název {YYYY-MM}[-DD])
    a podle filtru partition dimenze (podadresář)."""
    pattern = str(history_root() / spec.path)
    files = sorted(f for f in glob.glob(pattern) if not os.path.basename(f).startswith("."))
    part = next((d for d in spec.dims if d.partition), None)
    if part is not None and filters and filters.get(part.id):
        wanted = set(filters[part.id])
        files = [f for f in files if os.path.basename(os.path.dirname(f)) in wanted]
    lo = d_from.strftime("%Y-%m") if d_from else None
    # o měsíc víc: data v pražském čase, partitiony v UTC (poslední hodina měsíce)
    hi = (d_to + timedelta(days=1)).strftime("%Y-%m") if d_to else None
    out = []
    for f in files:
        stem = os.path.splitext(os.path.basename(f))[0]
        if _PART_RE.match(stem):
            month = stem[:7]
            if lo and month < (pd.Timestamp(lo + "-01") - pd.Timedelta(days=1)).strftime("%Y-%m"):
                continue
            if hi and month > hi:
                continue
        out.append(f)
    return out


_mirror_lock = threading.Lock()


def _csv_mirror(files: list) -> list:
    """CSV partitiony → parquet zrcadlo v cache/explore_mirror (stejná
    relativní cesta). read_csv(union_by_name) musí odhadovat schéma každého
    souboru zvlášť (GIE ~190 souborů ≈ 8 s na dotaz); parquet zrcadlo se
    přepočítá jen u souborů, které se od posledně změnily (mtime)."""
    root = history_root()
    mroot = cache_root() / "explore_mirror"
    out = []
    with _mirror_lock:
        cur = None
        for f in files:
            rel = os.path.relpath(f, root)
            target = mroot / (os.path.splitext(rel)[0] + ".parquet")
            src_m = os.path.getmtime(f)
            if not target.exists() or target.stat().st_mtime < src_m:
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_suffix(".tmp")
                cur = cur or _connection()
                src = f.replace("'", "''")
                cur.execute(f"COPY (SELECT * FROM read_csv('{src}', header = true)) TO '{tmp}' (FORMAT parquet)")
                os.replace(tmp, target)
                os.utime(target, (src_m, src_m))
            out.append(str(target))
    return out


def _reader(spec: ExploreSpec, files: list) -> str:
    if spec.fmt == "csv":
        files = _csv_mirror(files)
    lst = "[" + ", ".join("'" + f.replace("'", "''") + "'" for f in files) + "]"
    return f"read_parquet({lst}, union_by_name = true, filename = true)"


def _dim_sql(spec: ExploreSpec, dim_id: str) -> str:
    d = spec.dim(dim_id)
    if d is None:
        raise ValueError(f"neznámá dimenze {dim_id!r}")
    if d.expr:
        return d.expr
    return _FILENAME_PARENT if d.partition else f'"{d.id}"'


# ── metadata (rozsah, hodnoty dimenzí) ──────────────────────────────────

@ttl_cache(ttl=900, maxsize=64)
def bounds(key: str):
    """(první den, poslední den) dat v pražském čase — čte jen první a
    poslední partition (u podadresářů první/poslední podle měsíce)."""
    spec = EXPLORE_BY_KEY[key]
    files = files_for(spec)
    if not files:
        return None
    by_stem = sorted(files, key=lambda f: os.path.basename(f))
    first = [f for f in by_stem if os.path.basename(f) == os.path.basename(by_stem[0])]
    last = [f for f in by_stem if os.path.basename(f) == os.path.basename(by_stem[-1])]
    where = f"WHERE {spec.where}" if spec.where else ""
    try:
        cur = _connection()
        lo = cur.execute(f"SELECT min({spec.time_expr}) FROM {_reader(spec, first)} {where}").fetchone()[0]
        hi = cur.execute(f"SELECT max({spec.time_expr}) FROM {_reader(spec, last)} {where}").fetchone()[0]
    except Exception as e:
        log.warning("explore bounds %s: %s", key, e)
        return None
    if lo is None or hi is None:
        return None
    lo, hi = pd.Timestamp(lo), pd.Timestamp(hi)
    lo = lo.tz_convert(TZ) if lo.tzinfo else lo
    hi = hi.tz_convert(TZ) if hi.tzinfo else hi
    now = pd.Timestamp.now(tz=TZ)
    return lo.date(), min(hi, now + pd.Timedelta(days=10)).date()


@ttl_cache(ttl=900, maxsize=128)
def dim_values(key: str, dim_id: str, other_filters: tuple = ()) -> list:
    """Hodnoty dimenze (kaskádově omezené filtry ostatních dimenzí)."""
    spec = EXPLORE_BY_KEY[key]
    d = spec.dim(dim_id)
    if d is None:
        return []
    filters = {k: v for k, v in other_filters if k != dim_id}
    if d.partition:  # hodnota = název podadresáře — bez čtení dat
        dirs = {os.path.basename(os.path.dirname(f)) for f in files_for(spec)}
        return sorted(dirs)
    # filtr ostatních dimenzí; časově jen poslední ~2 roky (rychlost; staré
    # hodnoty, které už neexistují, by v nabídce stejně jen překážely)
    b = bounds(key)
    d_from = (b[1] - timedelta(days=730)) if b else None
    files = files_for(spec, filters, d_from=d_from)
    if not files:
        return []
    where, params = _filter_sql(spec, filters)
    if spec.where:
        where.insert(0, spec.where)
    sql = (f"SELECT DISTINCT CAST({_dim_sql(spec, dim_id)} AS VARCHAR) AS v FROM {_reader(spec, files)} "
           f"{'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY 1 LIMIT {MAX_DIM_VALUES}")
    try:
        rows = _connection().execute(sql, params).fetchall()
    except Exception as e:
        log.warning("explore dim_values %s/%s: %s", key, dim_id, e)
        return []
    return [r[0] for r in rows if r[0] not in (None, "")]


def _filter_sql(spec: ExploreSpec, filters: dict) -> tuple:
    where, params = [], []
    for dim_id, vals in (filters or {}).items():
        if not vals or spec.dim(dim_id) is None:
            continue
        where.append(f"CAST({_dim_sql(spec, dim_id)} AS VARCHAR) IN ({', '.join('?' for _ in vals)})")
        params.extend(str(v) for v in vals)
    return where, params


# ── dotaz ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Query:
    ds: str
    measure: str
    d_from: date
    d_to: date
    grain: str = "auto"
    cat: str = None                  # id dimenze nebo None (= celkem)
    time_agg: str = None             # None = výchozí míry
    cat_agg: str = None
    filters: tuple = ()              # ((dim, (hodnoty, …)), …) — seřazené

    @property
    def spec(self) -> ExploreSpec:
        return EXPLORE_BY_KEY[self.ds]

    def filters_dict(self) -> dict:
        return {k: tuple(v) for k, v in self.filters}

    def resolved(self) -> "Query":
        spec, m = self.spec, self.spec.measure(self.measure)
        tagg = self.time_agg if self.time_agg in AGGS else m.time_agg
        if tagg == "energy" and not m.power:
            tagg = "sum"
        cagg = self.cat_agg if self.cat_agg in CAT_AGGS else m.cat_agg
        cat = self.cat if self.cat and spec.dim(self.cat) else None
        grain = self.grain if self.grain in GRAINS else auto_grain(spec, self.d_from, self.d_to)
        grain = max(grain, _NATIVE_GRAIN.get(spec.native, "raw"), key=GRAINS.index)
        if tagg == "energy" and grain == "raw":
            grain = "15min" if spec.native in ("1min", "5min", "15min") else "hour"
        return Query(self.ds, m.id, self.d_from, self.d_to, grain, cat, tagg, cagg,
                     tuple(sorted((k, tuple(sorted(v))) for k, v in self.filters if v and spec.dim(k))))


def auto_grain(spec: ExploreSpec, d_from: date, d_to: date) -> str:
    days = (d_to - d_from).days + 1
    if days <= 3:
        g = "raw"
    elif days <= 21:
        g = "hour"
    elif days <= 400:
        g = "day"
    elif days <= 4 * 365:
        g = "week"
    else:
        g = "month"
    return max(g, _NATIVE_GRAIN.get(spec.native, "raw"), key=GRAINS.index)


def _bucket(grain: str, col: str = "t") -> str:
    if grain == "raw":
        return col
    if grain == "15min":
        return f"time_bucket(INTERVAL '15 minutes', {col})"
    return f"date_trunc('{grain}', {col})"


def _agg(agg: str, col: str = "v", tcol: str = "t") -> str:
    return {"sum": f"sum({col})", "avg": f"avg({col})", "min": f"min({col})", "max": f"max({col})",
            "last": f"arg_max({col}, {tcol})"}.get(agg, f"avg({col})")


def _base_sql(q: Query, files: list) -> tuple:
    """SQL CTE `s2` (t, cat, v) — sloučené kategorie v časových bodech."""
    spec, m = q.spec, q.spec.measure(q.measure)
    filters = q.filters_dict()
    lo = pd.Timestamp(q.d_from).tz_localize(TZ)
    hi = pd.Timestamp(q.d_to + timedelta(days=1)).tz_localize(TZ)
    where, params = _filter_sql(spec, filters)
    where = ([spec.where] if spec.where else []) + where
    dims = [d.id for d in spec.dims]
    dim_cols = ", ".join(f"CAST({_dim_sql(spec, d)} AS VARCHAR) AS \"d_{d}\"" for d in dims)
    cat_col = f'"d_{q.cat}"' if q.cat else f"'{TOTAL}'"
    use_align = spec.align and GRAINS.index(q.grain) >= GRAINS.index(spec.align)
    t1 = _bucket(spec.align) if use_align else "t"
    group_dims = ", ".join(f'"d_{d}"' for d in dims)
    sql = f"""
WITH src AS (
  SELECT {spec.time_expr} AS t, CAST({m.expr} AS DOUBLE) AS v, {dim_cols}
  FROM {_reader(spec, files)}
  {'WHERE ' + ' AND '.join(where) if where else ''}
),
s1 AS (
  SELECT {t1} AS t, {group_dims}, avg(v) AS v FROM src
  WHERE t >= ? AND t < ? AND v IS NOT NULL AND isfinite(v)
  GROUP BY ALL
),
s2 AS (
  SELECT t, {cat_col} AS cat, {_agg(q.cat_agg)} AS v FROM s1 GROUP BY ALL
)"""
    return sql, params + [lo.to_pydatetime(), hi.to_pydatetime()]


@dataclass
class Result:
    query: Query
    df: pd.DataFrame                   # b (Timestamp Praha), cat, v
    unit: str
    warnings: list = field(default_factory=list)
    n_cats: int = 0
    elapsed_s: float = 0.0
    sql: str = ""

    @property
    def empty(self) -> bool:
        return self.df is None or self.df.empty


def _energy_sql(grain: str) -> str:
    """CTE e1 (h, cat, e): energie po hodinách (u 15min zrnitosti po
    čtvrthodinách) = průměrný výkon × délka. Součet těchto kusů dává
    energii za delší období — i za neúplný měsíc/den (bez extrapolace)."""
    sub, hours = ("15min", 0.25) if grain in ("raw", "15min") else ("hour", 1.0)
    return f"e1 AS (SELECT {_bucket(sub)} AS t, cat, avg(v) * {hours} AS e FROM s2 GROUP BY ALL)"


def _outer_sql(q: Query) -> str:
    b = _bucket(q.grain)
    if q.time_agg == "energy":
        return (f", {_energy_sql(q.grain)}\n"
                f"SELECT {b} AS b, cat, sum(e) AS v FROM e1 GROUP BY ALL ORDER BY b, cat")
    return f"SELECT {b} AS b, cat, {_agg(q.time_agg)} AS v FROM s2 GROUP BY ALL ORDER BY b, cat"


_NATIVE_HOURS = {"1min": 1 / 60, "5min": 1 / 12, "15min": 0.25, "1h": 1, "1D": 24, "1W": 168}


def _estimate_rows(q: Query, grain: str) -> float:
    """Odhad počtu řádků výsledku (kusy × kategorie) — ať se nepočítá
    zbytečně jemná zrnitost, která by se stejně zhrubla."""
    hours = ((q.d_to - q.d_from).days + 1) * 24
    step = _NATIVE_HOURS.get(q.spec.native, 1) if grain == "raw" else _GRAIN_HOURS[grain]
    n_cat = 1
    if q.cat:
        flt = q.filters_dict().get(q.cat)
        n_cat = len(flt) if flt else max(1, min(MAX_CATS * 4, len(dim_values(q.ds, q.cat))))
    return hours / max(step, 1e-9) * n_cat


def run(q: Query) -> Result:
    q = q.resolved()
    return _run_cached(q)


@ttl_cache(ttl=300, maxsize=24)
def _run_cached(q: Query) -> Result:
    t0 = time.monotonic()
    spec = q.spec
    unit = unit_for(spec, q.measure, q.time_agg)
    files = files_for(spec, q.filters_dict(), q.d_from, q.d_to)
    empty = pd.DataFrame({"b": pd.Series(dtype=f"datetime64[us, {TZ}]"), "cat": pd.Series(dtype=object),
                          "v": pd.Series(dtype=float)})
    if not files:
        return Result(q, empty, unit, ["Pro zvolené filtry/období nejsou uložená data."])
    warnings = []
    cur = _connection()
    grain = q.grain
    while grain != GRAINS[-1] and _estimate_rows(q, grain) > MAX_ROWS * 1.5:
        grain = GRAINS[GRAINS.index(grain) + 1]
    if grain != q.grain:
        warnings.append(f"Pro zrnitost {GRAIN_LABELS[q.grain]} by bylo příliš mnoho bodů "
                        f"(> {MAX_ROWS:,}) — zhrubnuto na {GRAIN_LABELS[grain]}.".replace(",", " "))
    while True:
        qq = Query(q.ds, q.measure, q.d_from, q.d_to, grain, q.cat, q.time_agg, q.cat_agg, q.filters)
        base, params = _base_sql(qq, files)
        sql = base + "\n" + _outer_sql(qq) + f"\nLIMIT {MAX_ROWS + 1}"
        df = cur.execute(sql, params).df()
        if len(df) <= MAX_ROWS or grain == GRAINS[-1]:
            break
        nxt = GRAINS[GRAINS.index(grain) + 1]
        warnings.append(f"Výsledek by měl přes {MAX_ROWS:,} bodů — zrnitost zhrubnuta "
                        f"{GRAIN_LABELS[grain]} → {GRAIN_LABELS[nxt]}.".replace(",", " "))
        grain = nxt
    if len(df) > MAX_ROWS:
        df = df.head(MAX_ROWS)
        warnings.append(f"Oříznuto na {MAX_ROWS} bodů.")
    df["b"] = pd.to_datetime(df["b"], utc=True).dt.tz_convert(TZ)
    df["cat"] = df["cat"].fillna("—").astype(str)
    n_cats = df["cat"].nunique()
    if n_cats > MAX_CATS:
        top = df.groupby("cat")["v"].apply(lambda s: s.abs().sum()).nlargest(MAX_CATS).index
        df = df[df["cat"].isin(top)].reset_index(drop=True)
        warnings.append(f"Zobrazeno {MAX_CATS} největších z {n_cats} kategorií — zužte filtrem.")
    res = Result(qq, df, unit, warnings, n_cats, time.monotonic() - t0, sql)
    log.debug("explore %s %s %.2fs %d řádků", q.ds, grain, res.elapsed_s, len(df))
    return res


@ttl_cache(ttl=300, maxsize=24)
def breakdown(q: Query) -> pd.DataFrame:
    """Souhrn za celé období po kategoriích (cat, v) — pro cross-filter graf."""
    q = q.resolved()
    if not q.cat:
        return pd.DataFrame(columns=["cat", "v"])
    files = files_for(q.spec, q.filters_dict(), q.d_from, q.d_to)
    if not files:
        return pd.DataFrame(columns=["cat", "v"])
    base, params = _base_sql(q, files)
    if q.time_agg == "energy":
        sql = base + f", {_energy_sql(q.grain)}\nSELECT cat, sum(e) AS v FROM e1 GROUP BY cat"
    else:
        sql = base + f"\nSELECT cat, {_agg(q.time_agg)} AS v FROM s2 GROUP BY cat"
    sql = f"SELECT * FROM ({sql}) ORDER BY abs(v) DESC NULLS LAST LIMIT 200"
    df = _connection().execute(sql, params).df()
    df["cat"] = df["cat"].fillna("—").astype(str)
    return df


# ── odvozené tvary (pivot, sezónnost, heatmapy) — z malého výsledku ─────

PIVOT_ROWS = {"time": "čas", "cat": "kategorie", "year": "rok", "month": "měsíc"}
PIVOT_COLS = {"cat": "kategorie", "year": "rok", "month": "měsíc", "none": "—"}


def _time_labels(b: pd.Series, grain: str) -> pd.Series:
    fmt = {"year": "%Y", "quarter": None, "month": "%Y-%m", "week": "%Y-%m-%d", "day": "%Y-%m-%d"}.get(grain)
    if grain == "quarter":
        return b.dt.year.astype(str) + "-Q" + b.dt.quarter.astype(str)
    return b.dt.strftime(fmt or "%Y-%m-%d %H:%M")


def pivot(res: Result, rows: str = "time", cols: str = "cat", label=None) -> pd.DataFrame:
    """Kontingenční tabulka nad výsledkem. Hodnoty v buňce se slučují
    stejnou agregací v čase (energy/sum = součet, avg = průměr kusů)."""
    df = res.df
    if df.empty:
        return pd.DataFrame()
    d = pd.DataFrame({"v": df["v"].values, "cat": df["cat"].map(label).values if label else df["cat"].values})
    d["time"] = _time_labels(df["b"], res.query.grain).values
    d["year"] = df["b"].dt.year.values
    d["month"] = df["b"].dt.month.values
    agg = {"sum": "sum", "energy": "sum", "avg": "mean", "min": "min", "max": "max", "last": "last"}[res.query.time_agg]
    rows = rows if rows in PIVOT_ROWS else "time"
    cols = cols if cols in PIVOT_COLS else "cat"
    if cols == rows:
        cols = "none"
    if cols == "none":
        out = d.groupby(rows, sort=True)["v"].agg(agg).to_frame(res.unit or "hodnota")
    else:
        out = d.pivot_table(index=rows, columns=cols, values="v", aggfunc=agg, sort=True)
        out.columns = [str(c) for c in out.columns]
        if agg in ("sum",) and len(out.columns) > 1:
            out["Σ"] = out.sum(axis=1, min_count=1)
    out.index.name = PIVOT_ROWS[rows]
    return out


def seasonal(res: Result) -> pd.DataFrame:
    """year, x (den v roce / týden / měsíc podle zrnitosti), v — přes
    všechny kategorie sloučené stejnou agregací (série = rok)."""
    df = res.df
    if df.empty:
        return pd.DataFrame(columns=["year", "x", "v"])
    g = res.query.grain
    b = df["b"]
    if g in ("month", "quarter", "year"):
        x = b.dt.month
    elif g == "week":
        x = b.dt.isocalendar().week.astype(int).values
    else:
        x = b.dt.dayofyear
    d = pd.DataFrame({"year": b.dt.year.values, "x": x, "v": df["v"].values})
    agg = {"avg": "mean", "min": "min", "max": "max", "last": "last"}.get(res.query.time_agg, "sum")
    if res.query.cat:  # kategorie do sezónního srovnání nejdou — sloučí se
        agg = "sum" if res.query.cat_agg == "sum" else "mean"
    return d.groupby(["year", "x"], as_index=False)["v"].agg(agg)
