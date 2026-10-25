"""ČEPS (SOAP CepsData.asmx) — živá data přenosové soustavy ČR.

ČEPS publikuje minutová data (agregace "MI"; výroba a cena odchylky po
čtvrthodinách), takže web je obnovuje po 60 s a častější dotazování nemá
smysl — nová hodnota stejně nepřibude.

Dva způsoby čtení:
- update_ceps_live() — úloha `ceps` v scripts/update_all.py (systemd timer
  ppdash-live, každou minutu): navazuje od konce uložených dat a ukládá
  minutové řady do historie history/ceps/<dataset>/<YYYY-MM-DD>.parquet
  (long formát time/series_id/series/value, upsert s dedupem → bez
  duplicit, opakovaný běh nic nezdvojí).
- fetch_ceps_all() — pro web: když je úložiště čerstvé (poslední minuta
  odchylky ne starší než CEPS_STORE_MAX_AGE), čte dnešek z úložiště
  (žádné SOAP volání z webu); jinak (timer neběží) živě přes SOAP jako dřív.
"""
import logging
import os
import threading

import pandas as pd

from core.cache import ttl_cache
from core.settings import cache_root, history_path
from data.partitioned_store import last_date_partitioned, read_partitioned, upsert_partitioned

log = logging.getLogger(__name__)

CEPS_WSDL = "https://www.ceps.cz/_layouts/CepsData.asmx?WSDL"
CEPS_NS   = "https://www.ceps.cz/CepsData/StructuredData/1.0"

# Mapování value-id → název pro AktivaceSVRvCR (stejné jako dřív ve
# fetch_ceps_svr) — charts.imbalance.fig_ceps_svr čte právě tyhle sloupce.
SVR_SERIES = {
    "value1": "aFRR+ [MW]",
    "value2": "aFRR- [MW]",
    "value3": "mFRR+ [MW]",
    "value4": "mFRR- [MW]",
    "value7": "mFRR5 [MW]",
}

CEPS_DIR = history_path("ceps")
TZ = "Europe/Prague"

# dataset v úložišti -> (klíč ve fetch_ceps_all, SOAP metoda, parametry)
LIVE_DATASETS = {
    "imbalance":   ("imbal", "AktualniSystemovaOdchylkaCR", {"agregation": "MI", "function": "AVG"}),
    "svr":         ("svr", "AktivaceSVRvCR", {"agregation": "MI", "function": "AVG", "param1": "all"}),
    "load":        ("load", "Load", {"agregation": "MI", "function": "AVG", "version": "RT"}),
    "generation":  ("gen", "Generation", {"agregation": "QH", "function": "AVG", "version": "RT", "para1": "all"}),
    "res":         ("res", "GenerationRES", {"agregation": "MI", "function": "AVG", "version": "RT", "para1": "all"}),
    "frequency":   ("freq", "Frekvence", {}),
    "crossborder": ("cb", "CrossborderPowerFlows", {"agregation": "MI", "function": "AVG", "version": "RT"}),
}
PRICE_DATASET = "imbalance_price"
PRICE_METHOD = "OdhadovanaCenaOdchylky"
PRICE_COL = "Cena odchylky (CZK/MWh)"

# Navazování: překryv (poslední minuty/čtvrthodiny ČEPS ještě dopočítává),
# první běh bez dat stáhne od půlnoci předchozího dne, delší výpadek
# timeru se dohání nejvýš LIVE_MAX_GAP zpět (po dnech).
LIVE_OVERLAP = pd.Timedelta(minutes=30)
LIVE_MAX_GAP = pd.Timedelta(days=7)
# Web čte z úložiště, jen když je poslední minuta odchylky čerstvá.
CEPS_STORE_MAX_AGE = pd.Timedelta(minutes=10)

_client = None
_client_lock = threading.Lock()


def _get_ceps_client():
    """SOAP klient se vytváří LÍNĚ při prvním volání (dřív při importu
    modulu — import data.ceps tak stahoval WSDL po síti a při výpadku ČEPS
    zdržoval start celé aplikace). Neúspěšná inicializace se zkusí znovu
    při dalším volání."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                from zeep import Client as SoapClient
                from zeep.transports import Transport
                wsdl_cache = None
                try:  # WSDL na disk (1 den) — minutový timer ho jinak stahuje při každém běhu
                    from zeep.cache import SqliteCache
                    os.makedirs(cache_root(), exist_ok=True)
                    wsdl_cache = SqliteCache(path=str(cache_root() / "zeep-wsdl.db"), timeout=86400)
                except Exception as e:  # pragma: no cover — jen optimalizace
                    log.debug("zeep WSDL cache nedostupná: %s", e)
                _client = SoapClient(wsdl=CEPS_WSDL, transport=Transport(
                    timeout=20, operation_timeout=30, cache=wsdl_cache))
    return _client


def _parse_ceps(result) -> pd.DataFrame:
    series = {}
    for s in result.findall(f"{{{CEPS_NS}}}series/{{{CEPS_NS}}}serie"):
        series[s.get("id")] = s.get("name")
    rows = []
    for item in result.findall(f"{{{CEPS_NS}}}data/{{{CEPS_NS}}}item"):
        row = {"time": pd.Timestamp(item.get("date")).tz_convert("Europe/Prague")}
        for vid, name in series.items():
            val = item.get(vid)
            row[name] = float(val) if val is not None else 0.0
        rows.append(row)
    return pd.DataFrame(rows).set_index("time") if rows else pd.DataFrame()


def _parse_ceps_ids(result, mapping: dict) -> pd.DataFrame:
    """Jako _parse_ceps, ale sloupce podle pevného value-id mapování."""
    rows = []
    for item in result.findall(f"{{{CEPS_NS}}}data/{{{CEPS_NS}}}item"):
        row = {"time": pd.Timestamp(item.get("date")).tz_convert("Europe/Prague")}
        for vid, name in mapping.items():
            row[name] = float(item.get(vid, 0) or 0)
        rows.append(row)
    return pd.DataFrame(rows).set_index("time") if rows else pd.DataFrame()


def _parse_price(result, start: pd.Timestamp, col: str) -> pd.DataFrame:
    rows = []
    for item in result.findall(f"{{{CEPS_NS}}}data/{{{CEPS_NS}}}item"):
        interval = item.get("value15", "")
        price    = float(item.get("value2", 0) or 0)
        if not interval or price == 0:
            continue
        hh, mm = interval.split("-")[0].split(":")
        ts = start + pd.Timedelta(hours=int(hh), minutes=int(mm))
        rows.append({"time": ts, col: price})
    return pd.DataFrame(rows).set_index("time") if rows else pd.DataFrame()


def _empty_all(now) -> dict:
    empty = pd.DataFrame()
    return {
        "imbal": empty, "svr": empty, "load": empty,
        "gen": empty, "res": empty, "freq": empty,
        "cb": empty, "cena": empty, "now": now,
        "svr_named": empty, "imbal_mw": empty, "price": empty,
    }


def _postprocess(d: dict, raw_svr=None) -> dict:
    """Odvozené klíče (Net Export, svr_named, imbal_mw, price) — stejné pro
    živé SOAP i čtení z úložiště."""
    df_cb = d["cb"]
    if not df_cb.empty:
        actual_cols = [c for c in df_cb.columns if "Actual" in c]
        if actual_cols:
            df_cb = df_cb.copy()
            df_cb["Net Export (MW)"] = df_cb[actual_cols].sum(axis=1)
            d["cb"] = df_cb
    if "svr_named" not in d:
        d["svr_named"] = _parse_ceps_ids(raw_svr, SVR_SERIES) if raw_svr is not None else pd.DataFrame()
    df_imbal, df_cena = d["imbal"], d["cena"]
    d["imbal_mw"] = (df_imbal.iloc[:, [0]].rename(columns={df_imbal.columns[0]: "odchylka_MW"})
                     if not df_imbal.empty else pd.DataFrame())
    d["price"] = (df_cena.rename(columns={PRICE_COL: "cena_CZK_MWh"})
                  if not df_cena.empty else pd.DataFrame())
    return d


@ttl_cache(ttl=60)
def fetch_ceps_all():
    """Všechna ČEPS data za dnešek — jeden cache entry (TTL 60 s).

    Primárně z úložiště (plní ho minutový timer, viz update_ceps_live);
    když není čerstvé, živě přes SOAP. Vrací i odvozené klíče pro dřívější
    samostatné funkce (svr_named, imbal_mw, price) — fetch_ceps_imbalance/
    svr/imbalance_price dřív volaly STEJNÉ SOAP metody ještě jednou zvlášť
    (OPTIMIZATION_REPORT.md), teď jen přebírají výsledek odsud."""
    now   = pd.Timestamp.now(tz=TZ)
    try:
        stored = load_ceps_from_store(now)
    except Exception as e:  # poškozený soubor apod. — spadnout na živé SOAP
        log.warning("ČEPS úložiště nečitelné: %s", e)
        stored = None
    if stored is not None:
        return stored
    return fetch_ceps_live(now)


def fetch_ceps_live(now=None) -> dict:
    """Dnešek živě přes SOAP (bez úložiště)."""
    now   = now or pd.Timestamp.now(tz=TZ)
    start = now.normalize()

    try:
        ceps = _get_ceps_client()
    except Exception as e:
        log.warning("ČEPS SOAP init selhal: %s", e)
        return _empty_all(now)

    def call(method, **kw):
        fn = getattr(ceps.service, method)
        return fn(
            dateFrom=start.to_pydatetime().replace(tzinfo=None),
            dateTo  =now.to_pydatetime().replace(tzinfo=None),
            **kw
        )

    raw = {}

    def safe(key, method, **kw):
        try:
            raw[key] = call(method, **kw)
            return _parse_ceps(raw[key])
        except Exception as e:
            log.info("ČEPS %s selhalo: %s", method, e)
            return pd.DataFrame()

    d = {"now": now}
    for key, method, kw in LIVE_DATASETS.values():
        d[key] = safe(key, method, **kw)

    d["cena"] = pd.DataFrame()
    try:
        r_cena = call(PRICE_METHOD)
        d["cena"] = _parse_price(r_cena, start, PRICE_COL)
    except Exception as e:
        log.info("ČEPS %s selhalo: %s", PRICE_METHOD, e)

    return _postprocess(d, raw.get("svr"))


# ── úložiště (minutová historie) ───────────────────────────────────────

LONG_COLUMNS = ["time", "series_id", "series", "value"]


def _parse_long(result, dataset: str) -> pd.DataFrame:
    """SOAP odpověď → long (time UTC, series_id, series, value). U SVR se
    ukládají i hodnoty bez deklarované série (value3..value7 — z nich
    vzniká svr_named) s prázdným názvem série."""
    series = {s.get("id"): s.get("name") for s in result.findall(f"{{{CEPS_NS}}}series/{{{CEPS_NS}}}serie")}
    rows = []
    for item in result.findall(f"{{{CEPS_NS}}}data/{{{CEPS_NS}}}item"):
        t = pd.Timestamp(item.get("date"))
        t = t.tz_localize(TZ) if t.tzinfo is None else t
        ids = list(series)
        if dataset == "svr":
            ids += [k for k in item.keys() if k.startswith("value") and k not in series]
        for vid in ids:
            val = item.get(vid)
            if val is None:
                continue
            rows.append((t, vid, series.get(vid, ""), float(val)))
    if not rows:
        return pd.DataFrame(columns=LONG_COLUMNS)
    df = pd.DataFrame(rows, columns=LONG_COLUMNS)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    if dataset == "frequency":
        # frekvence může chodit jemněji než po minutě — ukládá se minutový průměr
        df["time"] = df["time"].dt.floor("min")
        df = df.groupby(["time", "series_id", "series"], as_index=False, sort=False)["value"].mean()
    return df


def _price_long(result, day_start: pd.Timestamp) -> pd.DataFrame:
    wide = _parse_price(result, day_start, PRICE_COL)
    if wide.empty:
        return pd.DataFrame(columns=LONG_COLUMNS)
    df = pd.DataFrame({"time": pd.to_datetime(wide.index, utc=True), "series_id": "value2",
                       "series": PRICE_COL, "value": wide[PRICE_COL].to_numpy()})
    return df


def _day_chunks(start: pd.Timestamp, end: pd.Timestamp):
    cur = start
    while cur < end:
        nxt = min(cur.normalize() + pd.DateOffset(days=1), end)
        yield cur, nxt
        cur = nxt


def fetch_ceps_range(dataset: str, start: pd.Timestamp, end: pd.Timestamp, client=None) -> pd.DataFrame:
    """Long data jednoho datasetu za [start, end] (Prague tz), po dnech."""
    client = client or _get_ceps_client()
    frames = []
    for a, b in _day_chunks(start.tz_convert(TZ), end.tz_convert(TZ)):
        kw = {"dateFrom": a.to_pydatetime().replace(tzinfo=None),
              "dateTo": b.to_pydatetime().replace(tzinfo=None)}
        if dataset == PRICE_DATASET:
            res = getattr(client.service, PRICE_METHOD)(**kw)
            frames.append(_price_long(res, a.normalize()))
        else:
            _, method, params = LIVE_DATASETS[dataset]
            res = getattr(client.service, method)(**kw, **params)
            frames.append(_parse_long(res, dataset))
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=LONG_COLUMNS)


def _dataset_dir(dataset: str) -> str:
    return os.path.join(CEPS_DIR, dataset)


def update_ceps_live(now: pd.Timestamp = None) -> dict:
    """Úloha `ceps` (timer ppdash-live, 1× za minutu): pro každý dataset
    navazuje od posledního uloženého času (− LIVE_OVERLAP), cena odchylky
    vždy od začátku dne (odhad se během dne reviduje). Zápis = upsert do
    denních souborů s dedupem na (time, series_id) — nic se nezdvojí,
    beze změny se soubor nepřepisuje."""
    from core.updaters import UpdateError
    now = (now or pd.Timestamp.now(tz=TZ)).tz_convert(TZ)
    client = _get_ceps_client()
    errors, written_rows = [], 0
    for dataset in list(LIVE_DATASETS) + [PRICE_DATASET]:
        base = _dataset_dir(dataset)
        try:
            last = last_date_partitioned(base, "time", "parquet")
            if last is None:
                start = now.normalize() - pd.DateOffset(days=1)
            else:
                start = last.tz_convert(TZ) - LIVE_OVERLAP
            start = max(start, now - LIVE_MAX_GAP)
            if dataset == PRICE_DATASET:
                start = start.normalize()
            df = fetch_ceps_range(dataset, start, now, client=client)
            if df.empty:
                continue
            touched = upsert_partitioned(df, base, "time", ["time", "series_id"], fmt="parquet", period="day")
            written_rows += sum(n for _, n, w in touched if w)
        except Exception as e:
            log.warning("ČEPS %s: %s: %s", dataset, type(e).__name__, e)
            errors.append(dataset)
    if errors and len(errors) == len(LIVE_DATASETS) + 1:
        raise UpdateError(f"ČEPS: selhaly všechny datasety ({errors[0]} …)")
    return {"errors": len(errors), "rows": written_rows}


def load_ceps_store(dataset: str, date_from=None, date_to=None) -> pd.DataFrame:
    """Long data z úložiště (time UTC)."""
    df = read_partitioned(_dataset_dir(dataset), fmt="parquet", date_from=date_from, date_to=date_to,
                          columns=LONG_COLUMNS)
    if df.empty:
        return pd.DataFrame(columns=LONG_COLUMNS)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    if date_from is not None:
        df = df[df["time"] >= pd.Timestamp(date_from)]
    if date_to is not None:
        df = df[df["time"] <= pd.Timestamp(date_to)]
    return df


def _sort_ids(ids) -> list:
    def key(v):
        digits = "".join(ch for ch in str(v) if ch.isdigit())
        return (int(digits) if digits else 0, str(v))
    return sorted(ids, key=key)


def _wide(long: pd.DataFrame) -> pd.DataFrame:
    """Long → stejný wide tvar jako _parse_ceps (index time v Prague,
    sloupce = názvy sérií v pořadí value1, value2, …; chybějící = 0)."""
    long = long[long["series"] != ""]
    if long.empty:
        return pd.DataFrame()
    order = long.drop_duplicates("series_id").set_index("series_id")["series"]
    cols = [order[i] for i in _sort_ids(order.index)]
    w = long.pivot_table(index="time", columns="series", values="value", aggfunc="last")
    w = w.reindex(columns=cols).fillna(0.0)
    w.index = w.index.tz_convert(TZ)
    w.index.name = "time"
    w.columns.name = None
    return w


def load_ceps_from_store(now: pd.Timestamp = None):
    """Dnešek z úložiště ve stejném tvaru jako fetch_ceps_live(), nebo None,
    když úložiště není čerstvé (timer neběží / dnes ještě nic)."""
    now = now or pd.Timestamp.now(tz=TZ)
    last = last_date_partitioned(_dataset_dir("imbalance"), "time", "parquet")
    if last is None or now - last > CEPS_STORE_MAX_AGE:
        return None
    start = now.normalize()
    d = {"now": now}
    for dataset, (key, _, _) in LIVE_DATASETS.items():
        long = load_ceps_store(dataset, date_from=start, date_to=now)
        d[key] = _wide(long)
        if dataset == "svr":
            if long.empty:
                d["svr_named"] = pd.DataFrame()
            else:
                named = long.pivot_table(index="time", columns="series_id", values="value", aggfunc="last")
                named = named.reindex(columns=list(SVR_SERIES)).fillna(0.0).rename(columns=SVR_SERIES)
                named.index = named.index.tz_convert(TZ)
                named.index.name = "time"
                named.columns.name = None
                d["svr_named"] = named
    price = load_ceps_store(PRICE_DATASET, date_from=start, date_to=start + pd.Timedelta(days=1))
    d["cena"] = _wide(price)
    return _postprocess(d)


def fetch_ceps_imbalance():
    """Systémová odchylka ČR z ČEPS — minutová, ~5min zpoždění.
    Vrací (DataFrame se sloupcem odchylka_MW, now) jako dřív."""
    d = fetch_ceps_all()
    return d["imbal_mw"], d["now"]


def fetch_ceps_svr():
    """Aktivace SVR v ČR z ČEPS — minutová, sloupce podle SVR_SERIES."""
    return fetch_ceps_all()["svr_named"]


def fetch_ceps_imbalance_price():
    """Odhadovaná cena odchylky z ČEPS — 15min, sloupec cena_CZK_MWh."""
    return fetch_ceps_all()["price"]
