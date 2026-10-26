"""Analýza toků plynu (/plyn/analyza) — datová vrstva.

Dva zdroje, každý pro svou úroveň (jeden zdroj pravdy na úroveň):
- **Země** = ENTSO-G /aggregateddata, indikátor Physical Flow
  (history/entsog_flows): země × směr × sousední systém (+ seznam bodů),
  GWh/d. Stejná data jako Power BI (toky_po bodech.pbix).
- **Bod** = ENTSO-G /operationaldata (history/entsog_operational): po
  bodech a operátorech Physical Flow, Allocation, Nomination,
  Renomination (denní), kapacity a přerušení (období periodFrom–periodTo,
  rozepisují se na dny — platí poslední revize podle lastUpdateDateTime).

Všechno počítá DuckDB nad parquet soubory s filtrem (země / bod / okno),
do pandas jdou jen denní řady jedné země nebo bodu (tisíce řádků) a do
prohlížeče jen agregace.
"""
import glob
import logging
import os
from datetime import date, timedelta

import pandas as pd

from config import MSMM3_TO_GWH
from core.cache import ttl_cache
from core.gasflow import partner_of
from core.settings import history_root

log = logging.getLogger("ppdash.gas_analysis")

MIN_DATE = date(2019, 1, 1)
GRAINS = ("day", "week", "month", "year")
GRAIN_LABELS = {"day": "den", "week": "týden", "month": "měsíc", "year": "rok"}
UNITS = ("gwhd", "gwh", "mcmd")
UNIT_LABELS = {"gwhd": "GWh/d", "gwh": "GWh", "mcmd": "mcm/d"}
UNIT_HELP = {"gwhd": "průměr za den", "gwh": "součet za období", "mcmd": "průměr za den, přepočet přes GCV"}
DEFAULT_GCV = MSMM3_TO_GWH   # kWh/m³ (≈ 10,55 GWh na mil. m³, stejně jako GASSCO / flowchart)

FLOW_IND = ("Physical Flow", "Allocation", "Nomination", "Renomination")
CAP_IND = ("Firm Technical", "Firm Booked", "Interruptible Booked")
INT_IND = ("Planned interruption of firm capacity", "Unplanned interruption of firm capacity",
           "Actual interruption of interruptible capacity")
IND_LABELS = {
    "Physical Flow": "Fyzický tok", "Allocation": "Alokace", "Nomination": "Nominace",
    "Renomination": "Renominace", "Firm Technical": "Technická kapacita (firm)",
    "Firm Booked": "Rezervovaná firm", "Interruptible Booked": "Rezervovaná přerušitelná",
    "Planned interruption of firm capacity": "Plánované přerušení firm",
    "Unplanned interruption of firm capacity": "Neplánované přerušení firm",
    "Actual interruption of interruptible capacity": "Skutečné přerušení přerušitelné",
}

COUNTRY_NAMES = {
    "AT": "Rakousko", "BE": "Belgie", "BG": "Bulharsko", "CH": "Švýcarsko", "CZ": "Česko", "DE": "Německo",
    "DK": "Dánsko", "EE": "Estonsko", "ES": "Španělsko", "FI": "Finsko", "FR": "Francie", "GR": "Řecko",
    "HR": "Chorvatsko", "HU": "Maďarsko", "IE": "Irsko", "IT": "Itálie", "LT": "Litva", "LU": "Lucembursko",
    "LV": "Lotyšsko", "NL": "Nizozemsko", "PL": "Polsko", "PT": "Portugalsko", "RO": "Rumunsko",
    "SI": "Slovinsko", "SK": "Slovensko", "SE": "Švédsko", "UA": "Ukrajina", "UK": "Velká Británie",
    "GB": "Velká Británie", "NO": "Norsko", "RU": "Rusko", "RS": "Srbsko", "TR": "Turecko", "MD": "Moldavsko",
    "MK": "Severní Makedonie", "SV": "Jižní koridor / mimo EU", "MA": "Maroko", "AL": "Albánie",
    "BA": "Bosna a Hercegovina", "BY": "Bělorusko",
}
KIND_LABELS = {"LNG": "LNG terminály", "STOR": "Zásobníky", "PROD": "Těžba", "CONS": "Spotřeba / distribuce",
               "OTHER": "Ostatní", "mimo EU": "Mimo EU (neurčeno)"}
BY_LABELS = {"partner": "sousední země / systém", "adjacent": "sousední systém (ENTSO-G)",
             "point": "bod / skupina bodů"}


def country_name(code: str) -> str:
    return COUNTRY_NAMES.get(code, code)


def partner_label(code: str) -> str:
    if code in KIND_LABELS:
        return KIND_LABELS[code]
    return " / ".join(COUNTRY_NAMES.get(c, c) for c in str(code).split("/"))


def _con():
    from core.explore import _connection
    return _connection()


# ── soubory ─────────────────────────────────────────────────────────────

def _dir(name: str) -> str:
    return str(history_root() / name)


def _files(name: str, d_from: date = None, d_to: date = None) -> list:
    """Měsíční (YYYY-MM) a roční (YYYY) parquet soubory v okně ± 1 měsíc
    (partitiony jsou podle UTC data, den plynu začíná v 6:00 místního)."""
    out = []
    lo = (pd.Timestamp(d_from) - pd.DateOffset(months=1)).strftime("%Y-%m") if d_from else None
    hi = (pd.Timestamp(d_to) + pd.DateOffset(months=1)).strftime("%Y-%m") if d_to else None
    for f in sorted(glob.glob(os.path.join(_dir(name), "*.parquet"))):
        stem = os.path.splitext(os.path.basename(f))[0]
        if stem.startswith("."):
            continue
        key_lo, key_hi = (stem + "-01", stem + "-12") if len(stem) == 4 else (stem, stem)
        if lo and key_hi < lo or hi and key_lo > hi:
            continue
        out.append(f)
    return out


def _src(files: list) -> str:
    return "read_parquet([" + ", ".join("'" + f.replace("'", "''") + "'" for f in files) + "], union_by_name = true)"


# ── Země (entsog_flows, Physical Flow) ──────────────────────────────────

@ttl_cache(ttl=900, maxsize=2)
def flow_bounds():
    """(první, poslední den) Physical Flow v entsog_flows; None bez dat."""
    files = _files("entsog_flows")
    if not files:
        return None
    try:
        lo = _con().execute(f"SELECT min(CAST(date AS DATE)) FROM {_src(files[:2])}").fetchone()[0]
        hi = _con().execute(f"SELECT max(CAST(date AS DATE)) FROM {_src(files[-2:])} "
                            f"WHERE indicator = 'Physical Flow'").fetchone()[0]
    except Exception as e:
        log.warning("gas_analysis flow_bounds: %s", e)
        return None
    if lo is None or hi is None:
        return None
    return max(pd.Timestamp(lo).date(), MIN_DATE), min(pd.Timestamp(hi).date(), date.today())


@ttl_cache(ttl=3600, maxsize=2)
def countries() -> list:
    """Kódy zemí s Physical Flow v posledních ~3 měsících (seřazené podle názvu, CZ první)."""
    b = flow_bounds()
    if b is None:
        return []
    files = _files("entsog_flows", b[1] - timedelta(days=90), b[1])
    rows = _con().execute(f"SELECT DISTINCT countryKey FROM {_src(files)} WHERE indicator = 'Physical Flow' "
                          f"AND countryKey IS NOT NULL").fetchall()
    codes = sorted({r[0] for r in rows}, key=lambda c: (c != "CZ", country_name(c)))
    return codes


@ttl_cache(ttl=300, maxsize=16)
def _country_raw(country: str, d_from: date, d_to: date) -> pd.DataFrame:
    """day, dir, adj_key, adj_label, points, v (GWh/d) — Physical Flow jedné země."""
    files = _files("entsog_flows", d_from, d_to)
    cols = ["day", "dir", "adj_key", "adj_label", "points", "v"]
    if not files:
        return pd.DataFrame(columns=cols)
    sql = f"""
SELECT CAST(date AS DATE) AS day, directionKey AS dir, adjacentSystemsKey AS adj_key,
       any_value(adjacentSystemsLabel) AS adj_label, pointsNames AS points, sum(value_GWh) AS v
FROM {_src(files)}
WHERE indicator = 'Physical Flow' AND countryKey = ? AND CAST(date AS DATE) BETWEEN ? AND ?
GROUP BY ALL ORDER BY day"""
    df = _con().execute(sql, [country, d_from, d_to]).df()
    df["day"] = pd.to_datetime(df["day"])
    return df


def _category(raw: pd.DataFrame, by: str) -> pd.Series:
    if by == "adjacent":
        return raw["adj_label"].fillna(raw["adj_key"]).fillna("—").astype(str)
    if by == "point":
        return raw["points"].fillna("—").astype(str).str.replace("|", " + ", regex=False)
    pairs = raw[["adj_key", "points"]].drop_duplicates()
    m = {(k, p): partner_label(partner_of(k, p)) for k, p in pairs.itertuples(index=False)}
    return pd.Series([m[(k, p)] for k, p in zip(raw["adj_key"], raw["points"])], index=raw.index, dtype=object)


def country_categories(country: str, d_from: date, d_to: date, by: str, direction: str) -> list:
    raw = _country_raw(country, d_from, d_to)
    if raw.empty:
        return []
    raw = raw[raw["dir"].isin(_dirs(direction))]
    return sorted(_category(raw, by).unique()) if not raw.empty else []


def _dirs(direction: str) -> tuple:
    return ("entry", "exit") if direction == "both" else (direction,)


DIR_SUFFIX = {"entry": " · vstup", "exit": " · výstup"}


def country_daily(country: str, direction: str, by: str, d_from: date, d_to: date, cats: tuple = ()) -> pd.DataFrame:
    """Široká denní tabulka (index = den, sloupce = kategorie), GWh/d.
    direction both: vstupy kladně, výstupy záporně, sloupce s příponou
    „· vstup/· výstup“. cats omezí kategorie (bez přípony)."""
    raw = _country_raw(country, d_from, d_to)
    if raw.empty:
        return pd.DataFrame()
    raw = raw[raw["dir"].isin(_dirs(direction))].copy()
    if raw.empty:
        return pd.DataFrame()
    raw["cat"] = _category(raw, by)
    if cats:
        raw = raw[raw["cat"].isin(cats)]
    if direction == "both":
        raw["v"] = raw["v"].where(raw["dir"] == "entry", -raw["v"])
        raw["cat"] = raw["cat"] + raw["dir"].map(DIR_SUFFIX)
    wide = raw.pivot_table(index="day", columns="cat", values="v", aggfunc="sum")
    if wide.empty:
        return wide
    days = pd.date_range(wide.index.min(), wide.index.max(), freq="D")
    wide = wide.reindex(days).fillna(0.0)   # chybějící řádek kategorie v den s daty = nulový tok
    order = wide.abs().sum().sort_values(ascending=False).index
    wide = wide[order]
    wide.index.name = "day"
    return wide


def country_total(country: str, direction: str, d_from: date, d_to: date, cats: tuple = (),
                  by: str = "partner") -> pd.Series:
    """Denní součet (u obou směrů čistá bilance vstup − výstup)."""
    wide = country_daily(country, direction, by, d_from, d_to, cats)
    return wide.sum(axis=1) if not wide.empty else pd.Series(dtype=float)


# ── agregace (společné pro Zemi i Bod) ──────────────────────────────────

def bucket(idx: pd.DatetimeIndex, grain: str) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(idx)
    if grain == "week":
        return idx.normalize() - pd.to_timedelta(idx.weekday, unit="D")
    if grain == "month":
        return idx.to_period("M").to_timestamp()
    if grain == "year":
        return idx.to_period("Y").to_timestamp()
    return idx.normalize()


def period_label(ts: pd.Timestamp, grain: str) -> str:
    ts = pd.Timestamp(ts)
    if grain == "week":
        iso = ts.isocalendar()
        return f"{iso[0]}-T{iso[1]:02d}"
    if grain == "month":
        return ts.strftime("%Y-%m")
    if grain == "year":
        return ts.strftime("%Y")
    return ts.strftime("%Y-%m-%d")


def convert(wide: pd.DataFrame, unit: str, gcv: float = None) -> pd.DataFrame:
    """Denní GWh/d → jednotka (mcm/d = GWh/d ÷ GCV [kWh/m³]); GWh = GWh/d × 1 den."""
    if unit == "mcmd":
        return wide / (gcv or DEFAULT_GCV)
    return wide


def aggregate(wide: pd.DataFrame, grain: str, unit: str, gcv: float = None) -> pd.DataFrame:
    """Široká denní tabulka (GWh/d) → kusy podle zrnitosti: GWh/d a mcm/d =
    průměr dní v kusu (chybějící hodnoty se vynechají), GWh = součet."""
    if wide is None or wide.empty:
        return pd.DataFrame()
    w = convert(wide, unit, gcv)
    b = bucket(w.index, grain)
    g = w.groupby(b)
    out = g.sum(min_count=1) if unit == "gwh" else g.mean()
    out.index.name = "b"
    return out


def pivot_table(agg: pd.DataFrame, grain: str, unit: str, total_label: str = "Celkem") -> pd.DataFrame:
    """Období × kategorie + sloupec celkem + řádky průměr/min/max (a součet u GWh)."""
    if agg is None or agg.empty:
        return pd.DataFrame()
    t = agg.copy()
    if len(t.columns) > 1 and total_label:
        t[total_label] = t.sum(axis=1, min_count=1)
    t.index = [period_label(i, grain) for i in t.index]
    foot = {"Průměr": t.mean(), "Min": t.min(), "Max": t.max()}
    if unit == "gwh":
        foot = {"Součet": t.sum(min_count=1), **foot}
    out = pd.concat([t, pd.DataFrame(foot).T])
    out.index.name = "období"
    return out


# ── sezónnost a srovnání období ────────────────────────────────────────

def seasonal(total: pd.Series, mode: str, unit: str, ref_year: int, gcv: float = None) -> pd.DataFrame:
    """year, x, v — mode month: průměr (GWh/d, mcm/d) / součet (GWh) po měsících;
    day: 7denní klouzavý průměr po dnech roku (x = den v přestupném roce 2000)."""
    if total is None or total.empty:
        return pd.DataFrame(columns=["year", "x", "v"])
    s = convert(total.to_frame("v"), unit, gcv)["v"]
    s = s[(s.index.year >= ref_year - 5) & (s.index.year <= ref_year)]
    if s.empty:
        return pd.DataFrame(columns=["year", "x", "v"])
    if mode == "month":
        g = s.groupby([s.index.year, s.index.month])
        v = g.sum(min_count=1) if unit == "gwh" else g.mean()
        return v.rename_axis(["year", "x"]).rename("v").reset_index()
    sm = s.rolling(7, min_periods=3).mean()
    out = pd.DataFrame({"year": sm.index.year, "v": sm.values,
                        "x": pd.to_datetime({"year": 2000, "month": sm.index.month, "day": sm.index.day})})
    return out.dropna(subset=["v"]).reset_index(drop=True)


def default_periods(max_day: date) -> tuple:
    """A = poslední celý měsíc, B = stejný měsíc o rok dřív."""
    m_end = date(max_day.year, max_day.month, 1) - timedelta(days=1)
    a = (date(m_end.year, m_end.month, 1), m_end)
    b0 = date(a[0].year - 1, a[0].month, 1)
    return a, (b0, (pd.Timestamp(b0) + pd.offsets.MonthEnd(0)).date())


def compare(wide_a: pd.DataFrame, wide_b: pd.DataFrame, unit: str, gcv_a: float = None, gcv_b: float = None,
            total_label: str = "Celkem") -> pd.DataFrame:
    """Kategorie × (A, B, rozdíl, rozdíl %) — hodnota = průměr za den (GWh/d,
    mcm/d) nebo součet za období (GWh)."""
    def summary(w, gcv):
        if w is None or w.empty:
            return pd.Series(dtype=float)
        w = convert(w, unit, gcv)
        s = w.sum(min_count=1) if unit == "gwh" else w.mean()
        if total_label and len(w.columns) > 1:
            tot = w.sum(axis=1, min_count=1)
            s[total_label] = tot.sum(min_count=1) if unit == "gwh" else tot.mean()
        return s
    a, b = summary(wide_a, gcv_a), summary(wide_b, gcv_b)
    out = pd.DataFrame({"A": a, "B": b})
    if out.empty:
        return out
    out["rozdíl"] = out["A"] - out["B"]
    out["rozdíl %"] = (out["rozdíl"] / out["B"].abs().where(out["B"].abs() > 1e-9)) * 100
    if total_label in out.index:
        out = pd.concat([out.drop(index=total_label), out.loc[[total_label]]])
    out.index.name = "kategorie"
    return out


# ── Bod (entsog_operational) ────────────────────────────────────────────

def _country_like(country: str) -> str:
    return f"{country}%"


@ttl_cache(ttl=900, maxsize=2)
def oper_bounds():
    files = _files("entsog_operational")
    if not files:
        return None
    try:
        hi = _con().execute(
            f"SELECT max(CAST(substr(periodFrom, 1, 10) AS DATE)) FROM {_src(files[-2:])} "
            f"WHERE indicator IN ('Physical Flow', 'Allocation', 'Nomination', 'Renomination')").fetchone()[0]
    except Exception as e:
        log.warning("gas_analysis oper_bounds: %s", e)
        return None
    if hi is None:
        return None
    return MIN_DATE, min(pd.Timestamp(hi).date(), date.today())


@ttl_cache(ttl=3600, maxsize=2)
def point_index() -> pd.DataFrame:
    """country, dir, point, adj_code, adj — body s denními toky/nominacemi
    v posledních ~2 letech. Sousední země/systém se bere z entsog_flows
    (bod je v pointsNames řádku dané země a směru → partner_of); kde se
    název bodu neshoduje, je „neurčeno“ (viz REBUILD_NOTES, NEOVĚŘENO)."""
    cols = ["country", "dir", "point", "adj_code", "adj"]
    b = oper_bounds()
    if b is None:
        return pd.DataFrame(columns=cols)
    files = _files("entsog_operational", b[1] - timedelta(days=730), b[1])
    pts = _con().execute(f"""
SELECT DISTINCT substr(operatorKey, 1, 2) AS country, directionKey AS dir, pointLabel AS point
FROM {_src(files)}
WHERE pointLabel IS NOT NULL AND operatorKey IS NOT NULL AND directionKey IN ('entry', 'exit')
  AND indicator IN ('Physical Flow', 'Allocation', 'Nomination', 'Renomination')""").df()
    fb = flow_bounds()
    if fb is not None:
        ff = _files("entsog_flows", fb[1] - timedelta(days=120), fb[1])
        m = _con().execute(f"""
SELECT DISTINCT countryKey AS country, directionKey AS dir, adjacentSystemsKey AS adj_key, pointsNames AS points
FROM {_src(ff)} WHERE indicator = 'Physical Flow'""").df()
        rows = []
        for c, d, k, p in m.itertuples(index=False):
            code = partner_of(k, p)
            for name in str(p or "").split("|"):
                if name.strip():
                    rows.append((c, d, name.strip(), code))
        mp = pd.DataFrame(rows, columns=["country", "dir", "point", "adj_code"]).drop_duplicates(
            ["country", "dir", "point"])
        pts = pts.merge(mp, on=["country", "dir", "point"], how="left")
    else:
        pts["adj_code"] = None
    pts["adj_code"] = pts["adj_code"].fillna("?")
    pts["adj"] = pts["adj_code"].map(lambda c: "neurčeno" if c == "?" else partner_label(c))
    return pts[cols].sort_values(["country", "dir", "adj", "point"]).reset_index(drop=True)


def _point_where(country: str, direction: str, point: str) -> tuple:
    return ("pointLabel = ? AND directionKey = ? AND operatorKey LIKE ?",
            [point, direction, _country_like(country)])


@ttl_cache(ttl=300, maxsize=16)
def point_flows(country: str, direction: str, point: str, d_from: date, d_to: date) -> pd.DataFrame:
    """Široká denní tabulka toků bodu (Physical Flow, Allocation, Nomination,
    Renomination; součet přes operátory dané země), GWh/d."""
    files = _files("entsog_operational", d_from, d_to)
    if not files:
        return pd.DataFrame()
    where, params = _point_where(country, direction, point)
    inds = ", ".join(f"'{i}'" for i in FLOW_IND)
    sql = f"""
SELECT day, indicator, sum(v) AS v FROM (
  SELECT CAST(substr(periodFrom, 1, 10) AS DATE) AS day, indicator, value_GWh AS v
  FROM {_src(files)} WHERE {where} AND indicator IN ({inds}) AND periodType = 'day'
) WHERE day BETWEEN ? AND ? AND v IS NOT NULL GROUP BY ALL ORDER BY day"""
    df = _con().execute(sql, params + [d_from, d_to]).df()
    if df.empty:
        return pd.DataFrame()
    wide = df.pivot_table(index="day", columns="indicator", values="v", aggfunc="sum")
    wide.index = pd.to_datetime(wide.index)
    wide = wide.reindex(pd.date_range(wide.index.min(), wide.index.max(), freq="D"))
    wide.index.name = "day"
    return wide[[c for c in FLOW_IND if c in wide.columns]]


@ttl_cache(ttl=300, maxsize=16)
def point_periods(country: str, direction: str, point: str, indicators: tuple, d_from: date,
                  d_to: date) -> pd.DataFrame:
    """Kapacity / přerušení rozepsané na dny: pro každý den, operátora a
    pointKey platí záznam s nejnovějším lastUpdateDateTime, jehož období
    [periodFrom, periodTo) den pokrývá (periodTo = začátek dalšího dne
    plynu → exkluzivní); pak součet přes operátory. Čte všechny soubory
    (období začínají i roky zpátky), filtr bodu se pushuje do parquetu."""
    files = _files("entsog_operational")
    if not files:
        return pd.DataFrame()
    where, params = _point_where(country, direction, point)
    inds = ", ".join("'" + i.replace("'", "''") + "'" for i in indicators)
    sql = f"""
WITH r AS (
  SELECT indicator, operatorKey, pointKey, value_GWh AS v,
         CAST(substr(periodFrom, 1, 10) AS DATE) AS f, CAST(substr(periodTo, 1, 10) AS DATE) AS t,
         coalesce(TRY_CAST(lastUpdateDateTime AS TIMESTAMPTZ), TIMESTAMPTZ '1970-01-01') AS u
  FROM {_src(files)} WHERE {where} AND indicator IN ({inds}) AND value_GWh IS NOT NULL
),
r2 AS (SELECT * FROM r WHERE f <= ? AND greatest(t, f + 1) > ?),
d AS (SELECT CAST(x AS DATE) AS day FROM generate_series(CAST(? AS DATE), CAST(? AS DATE), INTERVAL 1 DAY) g(x)),
j AS (SELECT d.day, r2.indicator, r2.operatorKey, r2.pointKey, arg_max(r2.v, r2.u) AS v
      FROM d JOIN r2 ON r2.f <= d.day AND d.day < greatest(r2.t, r2.f + 1) GROUP BY ALL)
SELECT day, indicator, sum(v) AS v FROM j GROUP BY ALL ORDER BY day"""
    df = _con().execute(sql, params + [d_to, d_from, d_from, d_to]).df()
    if df.empty:
        return pd.DataFrame()
    wide = df.pivot_table(index="day", columns="indicator", values="v", aggfunc="sum")
    wide.index = pd.to_datetime(wide.index)
    wide.index.name = "day"
    return wide[[c for c in indicators if c in wide.columns]]


@ttl_cache(ttl=300, maxsize=16)
def point_interruptions(country: str, direction: str, point: str, d_from: date, d_to: date) -> pd.DataFrame:
    """Seznam přerušení (poslední revize každého období a operátora) překrývajících okno."""
    cols = ["indicator", "operator", "from", "to", "v", "updated"]
    files = _files("entsog_operational")
    if not files:
        return pd.DataFrame(columns=cols)
    where, params = _point_where(country, direction, point)
    inds = ", ".join(f"'{i}'" for i in INT_IND)
    sql = f"""
SELECT indicator, any_value(operatorLabel) AS operator, f AS "from", t - 1 AS "to",
       arg_max(v, u) AS v, max(u) AS updated
FROM (
  SELECT indicator, operatorKey, operatorLabel, pointKey, value_GWh AS v,
         CAST(substr(periodFrom, 1, 10) AS DATE) AS f, CAST(substr(periodTo, 1, 10) AS DATE) AS t,
         TRY_CAST(lastUpdateDateTime AS TIMESTAMPTZ) AS u
  FROM {_src(files)} WHERE {where} AND indicator IN ({inds})
) WHERE f <= ? AND t > ? AND coalesce(v, 0) <> 0
GROUP BY indicator, operatorKey, pointKey, f, t ORDER BY "from" DESC LIMIT 500"""
    df = _con().execute(sql, params + [d_to, d_from]).df()
    return df[cols] if not df.empty else pd.DataFrame(columns=cols)


@ttl_cache(ttl=300, maxsize=16)
def point_gcv(country: str, direction: str, point: str, d_from: date, d_to: date):
    """Průměrné GCV bodu v okně [kWh/m³] nebo None (pak DEFAULT_GCV)."""
    files = _files("entsog_operational", d_from, d_to)
    if not files:
        return None
    where, params = _point_where(country, direction, point)
    v = _con().execute(f"""
SELECT avg(value) FROM {_src(files)} WHERE {where} AND indicator = 'GCV'
  AND CAST(substr(periodFrom, 1, 10) AS DATE) BETWEEN ? AND ? AND value BETWEEN 8 AND 14""",
                       params + [d_from, d_to]).fetchone()[0]
    return float(v) if v is not None else None


def point_daily(country: str, direction: str, point: str, d_from: date, d_to: date) -> pd.DataFrame:
    """Toky + kapacity bodu v jedné široké denní tabulce (GWh/d)."""
    fl = point_flows(country, direction, point, d_from, d_to)
    cap = point_periods(country, direction, point, CAP_IND, d_from, d_to)
    if fl.empty and cap.empty:
        return pd.DataFrame()
    out = pd.concat([fl, cap], axis=1)
    out.index.name = "day"
    return out


def best_flow(wide: pd.DataFrame) -> str:
    """Hlavní tok bodu: Physical Flow, jinak Allocation, Renomination, Nomination."""
    for c in FLOW_IND[:2] + ("Renomination", "Nomination"):
        if wide is not None and c in wide.columns and wide[c].notna().any():
            return c
    return None
