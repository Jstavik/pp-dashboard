"""ENTSO-E do historie — inkrementálně, s navazováním a rate limitem.

Datasety (long formát, měsíční partitiony v podadresáři per zóna/oblast):
- entsoe_prices/<ZONE>/        time, zone, price [EUR/MWh]          — day-ahead (SDAC), od 10/2025 15min
- entsoe_load/<AREA>/          time, area, kind (actual|forecast), mw — skutečná spotřeba + D-1 prognóza
- entsoe_ws_forecast/<AREA>/   time, area, psr (Solar|Wind Onshore|Wind Offshore), mw — day-ahead
- entsoe_flows/<FROM>_<TO>/    time, src, dst, kind (physical|scheduled), mw — směrové toky

Úlohy update_all.py (skupina `entsoe`, vlastní timer a zámek .entsoe.lock):
entsoe_prices, entsoe_load, entsoe_ws, entsoe_flows.

Navazování: start = konec uložených dat − překryv (revize), bez dat od
PPDASH_ENTSOE_START (default 2019-01-01; toky PPDASH_ENTSOE_FLOWS_START,
default 2021-01-01 — jsou po měsících, tj. ~100 requestů na směr a druh).
Každý chunk se hned zapíše (upsert s dedupem), takže přerušený backfill
pokračuje příště tam, kde skončil. Běh má časový rozpočet
(PPDASH_ENTSOE_BUDGET_S, default 2400 s na úlohu) — dlouhý backfill se tak
rozloží do několika běhů timeru a neblokuje ostatní.

Rate limit: ENTSO-E povoluje 400 requestů/min na token (při překročení
blokuje token na 10 min). Všechny requesty v procesu jdou přes jeden
limiter (PPDASH_ENTSOE_RPM, default 250/min — rezerva pro souběžný denní
běh hydro/DAP/výroby/odstávek) a max 4 vlákna.
"""
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

from core.settings import env, history_path
from data.partitioned_store import last_date_partitioned, upsert_partitioned

log = logging.getLogger("ppdash.update")

# ── oblasti ─────────────────────────────────────────────────────────────

NORDIC_ZONES = ["NO_1", "NO_2", "NO_3", "NO_4", "NO_5", "SE_1", "SE_2", "SE_3", "SE_4", "FI", "DK_1", "DK_2"]
PRICE_ZONES = [
    "CZ", "DE_LU", "AT", "SK", "PL", "HU", "SI", "HR", "RO", "BG", "GR", "RS", "ME", "MK",
    "FR", "BE", "NL", "CH", "ES", "PT", "IE_SEM",
    "IT_NORD", "IT_CNOR", "IT_CSUD", "IT_SUD", "IT_CALA", "IT_SICI", "IT_SARD",
    "EE", "LV", "LT", *NORDIC_ZONES,
]
LOAD_AREAS = [
    "CZ", "DE_LU", "AT", "SK", "PL", "HU", "SI", "HR", "RO", "BG", "GR", "FR", "BE", "NL", "CH",
    "ES", "PT", "IT", "EE", "LV", "LT", "NO", "SE", "FI", "DK", *[z for z in NORDIC_ZONES if z != "FI"],
]
WS_AREAS = [
    "CZ", "DE_LU", "AT", "SK", "PL", "HU", "FR", "BE", "NL", "ES", "PT", "IT", "RO", "GR", "BG",
    "EE", "LV", "LT", "FI", "DK_1", "DK_2", "SE_1", "SE_2", "SE_3", "SE_4", "NO_1", "NO_2", "NO_3", "NO_4", "NO_5",
]
# hranice (neorientované); toky se stahují v obou směrech
BORDERS = [
    # CZ a sousedé
    ("CZ", "DE_LU"), ("CZ", "AT"), ("CZ", "PL"), ("CZ", "SK"),
    ("DE_LU", "AT"), ("DE_LU", "PL"), ("DE_LU", "FR"), ("DE_LU", "NL"), ("DE_LU", "BE"),
    ("DE_LU", "CH"), ("DE_LU", "DK_1"), ("DE_LU", "DK_2"), ("DE_LU", "SE_4"), ("DE_LU", "NO_2"),
    ("AT", "HU"), ("AT", "SI"), ("AT", "CH"), ("AT", "IT_NORD"), ("SK", "PL"), ("SK", "HU"),
    ("PL", "LT"), ("PL", "SE_4"),
    # Nordic
    ("NO_1", "NO_2"), ("NO_1", "NO_3"), ("NO_1", "NO_5"), ("NO_2", "NO_5"), ("NO_3", "NO_4"), ("NO_3", "NO_5"),
    ("NO_1", "SE_3"), ("NO_3", "SE_2"), ("NO_4", "SE_1"), ("NO_4", "SE_2"), ("NO_4", "FI"),
    ("NO_2", "DK_1"), ("NO_2", "NL"),
    ("SE_1", "SE_2"), ("SE_2", "SE_3"), ("SE_3", "SE_4"), ("SE_1", "FI"), ("SE_3", "FI"),
    ("SE_3", "DK_1"), ("SE_4", "DK_2"), ("SE_4", "LT"), ("FI", "EE"), ("DK_1", "DK_2"), ("DK_1", "NL"),
]

ZONE_NAMES = {
    "CZ": "Česko", "DE_LU": "Německo+LU", "AT": "Rakousko", "SK": "Slovensko", "PL": "Polsko",
    "HU": "Maďarsko", "SI": "Slovinsko", "HR": "Chorvatsko", "RO": "Rumunsko", "BG": "Bulharsko",
    "GR": "Řecko", "RS": "Srbsko", "ME": "Černá Hora", "MK": "S. Makedonie", "FR": "Francie",
    "BE": "Belgie", "NL": "Nizozemsko", "CH": "Švýcarsko", "ES": "Španělsko", "PT": "Portugalsko",
    "IE_SEM": "Irsko (SEM)", "IT": "Itálie", "IT_NORD": "Itálie sever", "IT_CNOR": "Itálie stř.-sever",
    "IT_CSUD": "Itálie stř.-jih", "IT_SUD": "Itálie jih", "IT_CALA": "Kalábrie", "IT_SICI": "Sicílie",
    "IT_SARD": "Sardinie", "EE": "Estonsko", "LV": "Lotyšsko", "LT": "Litva", "NO": "Norsko",
    "SE": "Švédsko", "DK": "Dánsko", "FI": "Finsko",
    **{f"NO_{i}": f"NO{i}" for i in range(1, 6)}, **{f"SE_{i}": f"SE{i}" for i in range(1, 5)},
    "DK_1": "DK1", "DK_2": "DK2",
}

PRICES_DIR = history_path("entsoe_prices")
LOAD_DIR = history_path("entsoe_load")
WS_DIR = history_path("entsoe_ws_forecast")
FLOWS_DIR = history_path("entsoe_flows")

TZ = "Europe/Prague"


def _start_default(key="PPDASH_ENTSOE_START", default="2019-01-01") -> pd.Timestamp:
    return pd.Timestamp(env(key, default), tz=TZ)


# ── rate limit ──────────────────────────────────────────────────────────

class RateLimiter:
    """Klouzavé okno 60 s, sdílené všemi vlákny procesu."""

    def __init__(self, per_min: int):
        self.per_min = max(1, per_min)
        self.lock = threading.Lock()
        self.stamps = []
        self.count = 0

    def wait(self):
        while True:
            with self.lock:
                now = time.monotonic()
                self.stamps = [t for t in self.stamps if now - t < 60]
                if len(self.stamps) < self.per_min:
                    self.stamps.append(now)
                    self.count += 1
                    return
                delay = 60 - (now - self.stamps[0]) + 0.01
            time.sleep(delay)


LIMITER = RateLimiter(int(env("PPDASH_ENTSOE_RPM", "250")))


class _LimitedSession(requests.Session):
    def request(self, *args, **kwargs):
        LIMITER.wait()
        return super().request(*args, **kwargs)


def make_client():
    from config import ENTSOE_REQUEST_TIMEOUT_S
    from core.updaters import _require_entsoe_token
    from entsoe import EntsoePandasClient
    client = EntsoePandasClient(api_key=_require_entsoe_token(), timeout=max(ENTSOE_REQUEST_TIMEOUT_S, 60),
                                retry_count=2)
    client.session = _LimitedSession()
    return client


# ── normalizace odpovědí entsoe-py ──────────────────────────────────────

def _utc(idx) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(idx)
    return idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")


def norm_prices(s, zone: str) -> pd.DataFrame:
    if s is None or len(s) == 0:
        return pd.DataFrame(columns=["time", "zone", "price"])
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    return pd.DataFrame({"time": _utc(s.index), "zone": zone, "price": pd.to_numeric(s.values, errors="coerce")}
                        ).dropna(subset=["price"])


def norm_load(df, area: str, kind: str) -> pd.DataFrame:
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["time", "area", "kind", "mw"])
    s = df.iloc[:, 0] if isinstance(df, pd.DataFrame) else df
    return pd.DataFrame({"time": _utc(s.index), "area": area, "kind": kind,
                         "mw": pd.to_numeric(s.values, errors="coerce")}).dropna(subset=["mw"])


def norm_ws(df, area: str) -> pd.DataFrame:
    cols = ["time", "area", "psr", "mw"]
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=cols)
    if isinstance(df, pd.Series):
        df = df.to_frame()
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = [" ".join(str(x) for x in c if x and "Aggregated" not in str(x)) or str(c[0])
                      for c in df.columns]
    out = df.reset_index(names="time").melt(id_vars="time", var_name="psr", value_name="mw")
    out["time"] = _utc(out["time"])
    out["area"] = area
    out["mw"] = pd.to_numeric(out["mw"], errors="coerce")
    return out.dropna(subset=["mw"])[cols]


def norm_flow(s, src: str, dst: str, kind: str) -> pd.DataFrame:
    if s is None or len(s) == 0:
        return pd.DataFrame(columns=["time", "src", "dst", "kind", "mw"])
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    return pd.DataFrame({"time": _utc(s.index), "src": src, "dst": dst, "kind": kind,
                         "mw": pd.to_numeric(s.values, errors="coerce")}).dropna(subset=["mw"])


# ── obecné inkrementální stahování ──────────────────────────────────────

class Item:
    """Jedna řada k doplnění: adresář, dedup klíč, fetch(client, a, b) → df."""

    def __init__(self, label, base_dir, dedup, fetch, start, end, overlap, chunk, kind_col=None, kind=None):
        self.label, self.base_dir, self.dedup, self.fetch = label, base_dir, dedup, fetch
        self.start, self.end, self.overlap, self.chunk = start, end, overlap, chunk
        self.kind_col, self.kind = kind_col, kind

    def resume_from(self) -> pd.Timestamp:
        last = _last_time(self.base_dir, self.kind_col, self.kind)
        if last is None:
            return self.start
        return max(self.start, last.tz_convert(TZ) - self.overlap)


def _last_time(base_dir: str, kind_col=None, kind=None):
    """Konec uložených dat; u sdíleného adresáře (actual+forecast) jen pro daný druh."""
    if kind_col is None:
        return last_date_partitioned(base_dir, "time", "parquet")
    import glob
    for f in sorted(glob.glob(os.path.join(base_dir, "*.parquet")), reverse=True):
        df = pd.read_parquet(f, columns=["time", kind_col])
        df = df[df[kind_col] == kind]
        if not df.empty:
            return pd.to_datetime(df["time"], utc=True).max()
    return None


def run_items(items: list, budget_s: float = None, workers: int = 4, client=None) -> dict:
    """Doplní všechny řady (paralelně, s rate limitem a časovým rozpočtem).
    Vrací {"rows", "errors", "done", "pending", "items"}."""
    budget_s = float(budget_s if budget_s is not None else env("PPDASH_ENTSOE_BUDGET_S", "2400"))
    deadline = time.monotonic() + budget_s
    local = threading.local()

    def get_client():
        # vlastní klient (requests.Session) pro každé vlákno
        if client is not None:
            return client
        if getattr(local, "client", None) is None:
            local.client = make_client()
        return local.client

    make_client() if client is None else None  # chybějící token → UpdateError hned
    # actual+forecast (a physical+scheduled) sdílí adresář → zápis serializovat
    dir_locks = {it.base_dir: threading.Lock() for it in items}
    stats = {"rows": 0, "errors": 0, "done": 0, "pending": 0, "nodata": 0}
    lock = threading.Lock()

    def one(it: Item):
        from entsoe.exceptions import NoMatchingDataError
        with dir_locks[it.base_dir]:
            cur = it.resume_from()
        rows = errs = 0
        while cur < it.end:
            if time.monotonic() > deadline:
                with lock:
                    stats["pending"] += 1
                log.info("ENTSO-E %s: rozpočet vyčerpán u %s — pokračuje příští běh", it.label, cur.date())
                break
            b = min(cur + it.chunk, it.end)
            df = None
            for attempt in range(3):
                try:
                    df = it.fetch(get_client(), cur, b)
                    break
                except NoMatchingDataError:
                    df = pd.DataFrame()
                    break
                except Exception as e:
                    name = type(e).__name__
                    if name in ("InvalidBusinessParameterError", "InvalidPSRTypeError"):
                        df = pd.DataFrame()
                        break
                    wait = 60 if "429" in str(e) or "Too Many" in str(e) else 5 * (attempt + 1)
                    log.warning("ENTSO-E %s %s–%s: pokus %d — %s: %s", it.label, cur.date(), b.date(),
                                attempt + 1, name, str(e)[:160])
                    time.sleep(wait)
            if df is None:
                errs += 1
            elif not df.empty:
                with dir_locks[it.base_dir]:
                    touched = upsert_partitioned(df, it.base_dir, "time", it.dedup, fmt="parquet")
                rows += sum(n for _, n, w in touched if w)
            cur = b
        with lock:
            stats["rows"] += rows
            stats["errors"] += errs
            if cur >= it.end:
                stats["done"] += 1
        return rows

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, items))
    stats["items"] = len(items)
    stats["requests"] = LIMITER.count
    log.info("ENTSO-E: %s", stats)
    return stats


def _check(stats: dict, label: str) -> dict:
    from core.updaters import UpdateError
    if stats["errors"] and not stats["rows"] and stats["errors"] >= stats["items"]:
        raise UpdateError(f"{label}: všechny řady selhaly")
    return {"errors": stats["errors"], "rows": stats["rows"], "pending": stats["pending"]}


def _now():
    return pd.Timestamp.now(tz=TZ)


# ── úlohy ───────────────────────────────────────────────────────────────

def price_items(zones=None) -> list:
    end = _now().normalize() + pd.Timedelta(days=2)  # zítřek po aukci (~12:45)

    def f(zone):
        return lambda c, a, b: norm_prices(c.query_day_ahead_prices(zone, start=a, end=b), zone)
    return [Item(f"ceny {z}", os.path.join(PRICES_DIR, z), ["time", "zone"], f(z), _start_default(), end,
                 pd.Timedelta(days=2), pd.Timedelta(days=365)) for z in (zones or PRICE_ZONES)]


def load_items(areas=None) -> list:
    now = _now()
    items = []
    for a in (areas or LOAD_AREAS):
        d = os.path.join(LOAD_DIR, a)
        items.append(Item(f"spotřeba {a}", d, ["time", "kind"],
                          (lambda a: lambda c, s, e: norm_load(c.query_load(a, start=s, end=e), a, "actual"))(a),
                          _start_default(), now, pd.Timedelta(days=3), pd.Timedelta(days=365), "kind", "actual"))
        items.append(Item(f"prognóza spotřeby {a}", d, ["time", "kind"],
                          (lambda a: lambda c, s, e: norm_load(c.query_load_forecast(a, start=s, end=e), a,
                                                               "forecast"))(a),
                          _start_default(), now.normalize() + pd.Timedelta(days=2), pd.Timedelta(days=2),
                          pd.Timedelta(days=365), "kind", "forecast"))
    return items


def ws_items(areas=None) -> list:
    end = _now().normalize() + pd.Timedelta(days=2)
    return [Item(f"prognóza OZE {a}", os.path.join(WS_DIR, a), ["time", "psr"],
                 (lambda a: lambda c, s, e: norm_ws(c.query_wind_and_solar_forecast(a, start=s, end=e,
                                                                                    psr_type=None), a))(a),
                 _start_default(), end, pd.Timedelta(days=2), pd.Timedelta(days=365))
            for a in (areas or WS_AREAS)]


def flow_items(borders=None) -> list:
    now = _now()
    start = _start_default("PPDASH_ENTSOE_FLOWS_START", "2021-01-01")
    items = []
    for x, y in (borders or BORDERS):
        for src, dst in ((x, y), (y, x)):
            d = os.path.join(FLOWS_DIR, f"{src}_{dst}")
            items.append(Item(f"tok {src}→{dst}", d, ["time", "kind"],
                              (lambda s_, d_: lambda c, a, b: norm_flow(
                                  c.query_crossborder_flows(s_, d_, start=a, end=b), s_, d_, "physical"))(src, dst),
                              start, now, pd.Timedelta(days=3), pd.Timedelta(days=92), "kind", "physical"))
            items.append(Item(f"plán. výměna {src}→{dst}", d, ["time", "kind"],
                              (lambda s_, d_: lambda c, a, b: norm_flow(
                                  c.query_scheduled_exchanges(s_, d_, start=a, end=b, dayahead=False),
                                  s_, d_, "scheduled"))(src, dst),
                              start, now.normalize() + pd.Timedelta(days=1), pd.Timedelta(days=3),
                              pd.Timedelta(days=92), "kind", "scheduled"))
    return items


def update_prices(**kw) -> dict:
    return _check(run_items(price_items(), **kw), "ENTSO-E ceny")


def update_load(**kw) -> dict:
    return _check(run_items(load_items(), **kw), "ENTSO-E spotřeba")


def update_ws_forecast(**kw) -> dict:
    return _check(run_items(ws_items(), **kw), "ENTSO-E prognóza OZE")


def update_flows(**kw) -> dict:
    return _check(run_items(flow_items(), **kw), "ENTSO-E toky")
