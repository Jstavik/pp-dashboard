"""Data layer for the Energy-Charts API (Fraunhofer ISE, api.energy-charts.info).

No token/registration needed. FINAL SCOPE — 6 endpoints only (narrowed down
from an initial 17-endpoint exploration, see git history for the earlier
version and its findings):

    public_power, public_power_forecast, cbet, cbpf, installed_power, frequency

Permanently out of scope (not "skip for now" — deliberately dropped):
- price, price_current, price_next_day: overlaps with the existing
  ENTSO-E-based DAP price pipeline (data/dap_europe.py) — redundant.
- signal, solar_share, solar_share_daily_avg, wind_onshore_share,
  wind_onshore_share_daily_avg, wind_offshore_share,
  wind_offshore_share_daily_avg, ren_share_daily_avg: derivable from
  public_power (which already carries solar/wind_onshore/wind_offshore/load
  per-timestamp) — no need to hold as separate series. signal and the
  non-daily_avg *_share endpoints were additionally found to be
  non-historical (rolling "now" windows only, see git history).
- total_power, ren_share_forecast: excluded from the original exploration
  already (DE-only / HTTP 500), still excluded.

STORAGE — two tiers, chosen per endpoint volume:
- public_power, cbet, cbpf, public_power_forecast (large: 15-min/hourly
  resolution x ~20 series x 27 countries x 8+ years) use the EXISTING
  monthly-partitioned infrastructure in data/partitioned_store.py (the same
  one entsog_operational.py and generation.py use) — data/history/
  energy_charts/<endpoint>/<YYYY-MM>.parquet, one row per (geo_key,
  series_id, timestamp), all countries sharing the same monthly file so a
  closed month never needs rewriting once every country's data for it is
  in. This avoids the unbounded single-file git-diff growth a flat parquet
  would hit at this volume (a CZ-only trial run this size for a single
  country made a 40MB single file — full 27-country adoption would be
  ~1GB and rewritten whole on every incremental update).
- installed_power (yearly points, tiny) and frequency (see below, tiny
  after resampling) stay in ONE flat tidy parquet
  (data/history/energy_charts.parquet), same spirit as data/ice_cot.py.

FREQUENCY — resampled to 5-minute averages, NEVER stored at raw 1-second
resolution. The API only accepts <=3-day windows per request and is
1-second natively (see _fetch_chunk3day) — raw storage of the full
2022-05-01-to-today history would be ~135-140 million rows. Resampling to
5-minute averages immediately after each chunk fetch (before it's ever
written to disk) cuts that to ~460K rows for the entire history, negligible
next to the other endpoints, while remaining more than adequate resolution
for any dashboard use of grid frequency (this is a stability/quality
indicator, not something anyone reads at 1-second granularity). frequency
is also NOT per-country — there's a single global sensor region,
'DE-Freiburg' (RG Continental Europe) — so it's one combo total, not one
per country.

RESUMABLE BACKFILL: data/history/energy_charts_progress.json tracks, per
(endpoint, geo_key) combo: done, last_attempt, rows_fetched, error.
Re-running run_backfill() skips combos already done=True, so a many-hour,
many-country backfill can be interrupted (Ctrl+C, timeout, crash) and
resumed exactly where it left off. Rows are written incrementally (per
year-chunk / per 3-day-chunk within a combo, not just once the whole combo
finishes) so a mid-combo interruption never loses already-fetched data —
re-running just re-fetches (wastefully but harmlessly; dedup on write keeps
it idempotent) whatever chunk wasn't marked complete.

ENDPOINT QUIRKS (verified live; see git history for the full 17-endpoint
investigation — only the 6 retained ones are summarized here):
- public_power, cbet, cbpf: real historical range, start/end works,
  internally chunked one calendar year per request (a single multi-year
  request would return an unbounded-size payload — not documented as
  disallowed, but chunking yearly keeps each request's response size
  predictable and keeps resumability granular).
- public_power_forecast: real historical range too, but has an extra
  production_type axis (solar/wind_onshore/wind_offshore/load) fetched
  as 4 separate calls per country — forecast_type is fixed to "day-ahead"
  (the one confirmed to hold real historical values). Each production_type
  is tracked as its own progress-key "<country>:<production_type>" (the
  parquet/partitioned geo_key column still just holds the plain country
  code — production_type is already carried by series_id, e.g. "load",
  "solar"). Some countries don't publish a forecast for every
  production_type (e.g. landlocked countries and wind_offshore) — that
  shows up as a 200 response with zero data rows, not an error.
- installed_power: no start/end at all — one call already returns the
  full available yearly series.
- frequency: region= (not country=), only one region currently exists
  ('DE-Freiburg'), HARD 3-day max window per request (400 "Requested
  interval too long" beyond that), 1-second native resolution, data starts
  2022-05-01. See STORAGE above for how this module handles the volume.
"""
import glob
import json
import logging
import os
import time
from datetime import date, datetime, timedelta

import pandas as pd
import pyarrow.parquet as pq
import requests

from core.cache import ttl_cache
from core.settings import history_path
from data.partitioned_store import read_partitioned, upsert_partitioned

logger = logging.getLogger(__name__)

BASE_URL = "https://api.energy-charts.info"
DATA_PATH = history_path("energy_charts.parquet")
PROGRESS_PATH = history_path("energy_charts_progress.json")
PARTITIONED_ROOT = history_path("energy_charts")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "pp-dashboard/1.0 (+https://github.com/Jstavik/pp-dashboard)"})

TIDY_COLUMNS = [
    "endpoint", "geo_key", "geo_type", "series_id", "series_name",
    "timestamp", "value", "unit", "resolution",
]

# param_style: "start_end" (yearly-chunked date range) | "single_call" (no
# date params, one call has everything) | "chunk3day" (frequency's hard
# 3-day window, resampled to 5-min on the way in)
ENDPOINTS = {
    "public_power":           {"param_style": "start_end", "param_name": "country"},
    # "wind" (dřív 5. production_type) vyřazen: je to jen součet
    # wind_onshore + wind_offshore (odvoditelný) a pro CZ server na tenhle
    # dotaz trvale zavírá spojení bez odpovědi (RemoteDisconnected →
    # ConnectionError v progress.json), zatímco wind_onshore pro CZ vrací
    # 200 s nulou řádků — viz REBUILD_NOTES.md.
    "public_power_forecast":  {"param_style": "start_end", "param_name": "country",
                                "production_types": ["solar", "wind_onshore", "wind_offshore", "load"],
                                "extra_params": {"forecast_type": "day-ahead"}},
    "cbet":                   {"param_style": "start_end", "param_name": "country"},
    "cbpf":                   {"param_style": "start_end", "param_name": "country"},
    "installed_power":        {"param_style": "single_call", "param_name": "country",
                                "extra_params": {"time_step": "yearly"}},
    "frequency":              {"param_style": "chunk3day", "param_name": "region",
                                "fixed_geo_key": "DE-Freiburg"},
}

PARTITIONED_ENDPOINTS = {"public_power", "cbet", "cbpf", "public_power_forecast"}
FLAT_ENDPOINTS = {"installed_power", "frequency"}

DEFAULT_PACE_S = 1.0
FREQUENCY_RESAMPLE = "5min"

# Síťové chyby (ConnectionError/RemoteDisconnected, Timeout) — kolik pokusů
# a jaký základ exponenciálního backoffu. Dřív _get žádnou výjimku
# nechytal: jedno zahozené spojení ukončilo celé (endpoint, země) combo
# hned v prvním roce a označilo ho done=False.
NETWORK_RETRIES = 3
NETWORK_BACKOFF_S = 5

# Inkrementální update: o kolik dní před konec uložených dat se začíná
# znovu stahovat (pozdní revize posledních hodnot) a odkud začít, když
# pro combo zatím nejsou žádná data (plná historie je úkol run_backfill).
INCREMENTAL_OVERLAP_DAYS = 2
INCREMENTAL_FRESH_START_DAYS = 7


def _partitioned_base_dir(endpoint: str) -> str:
    return f"{PARTITIONED_ROOT}/{endpoint}"


# ── low-level HTTP ─────────────────────────────────────────────────────────

def _get(path: str, params: dict, pace: float = DEFAULT_PACE_S):
    """GET with Retry-After-respecting 429 backoff and NETWORK_RETRIES
    retries (exponential backoff) on connection errors / timeouts. Returns
    (status, body) where body is the parsed JSON dict, or the raw text for
    non-JSON / error responses. Raises the last network error if all
    retries fail."""
    url = f"{BASE_URL}{path}"
    attempt = 0
    while True:
        try:
            resp = SESSION.get(url, params=params, timeout=60)
        except (requests.ConnectionError, requests.Timeout) as e:
            attempt += 1
            if attempt >= NETWORK_RETRIES:
                raise
            wait = NETWORK_BACKOFF_S * 2 ** (attempt - 1)
            logger.warning("energy-charts %s %s: %s — pokus %d/%d, čekám %ds",
                           path, params, type(e).__name__, attempt, NETWORK_RETRIES, wait)
            time.sleep(wait)
            continue
        if resp.status_code == 429:
            ra = int(resp.headers.get("retry-after", "10"))
            time.sleep(ra + 1)
            continue
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        time.sleep(pace)
        return resp.status_code, body


def _rows_from_body(body: dict, endpoint: str, geo_key: str, geo_type: str) -> list:
    if not isinstance(body, dict):
        return []
    series_meta = {s["id"]: s.get("name", s["id"]) for s in body.get("series", []) or []}
    unit = body.get("unit")
    resolution = body.get("resolution")
    rows = []
    for point in body.get("data", []) or []:
        ts = point.get("timestamp")
        values = point.get("values", {}) or {}
        for series_id, value in values.items():
            rows.append({
                "endpoint": endpoint,
                "geo_key": geo_key,
                "geo_type": geo_type,
                "series_id": series_id,
                "series_name": series_meta.get(series_id, series_id),
                "timestamp": ts,
                "value": value,
                "unit": unit,
                "resolution": resolution,
            })
    return rows


def resample_5min(rows: list) -> list:
    """Collapse 1-second frequency rows into 5-minute averages. Public (not
    _-prefixed) because the one-off migration script reuses it verbatim on
    already-fetched raw rows, to avoid re-fetching from the API what's
    already on disk."""
    if not rows:
        return rows
    df = pd.DataFrame.from_records(rows)
    df["ts_parsed"] = pd.to_datetime(df["timestamp"], utc=True)
    df["bucket"] = df["ts_parsed"].dt.floor(FREQUENCY_RESAMPLE)
    grouped = (
        df.groupby(["endpoint", "geo_key", "geo_type", "series_id", "series_name", "unit", "bucket"],
                   as_index=False)["value"].mean()
    )
    grouped["timestamp"] = grouped["bucket"].apply(lambda ts: ts.isoformat())
    grouped["resolution"] = "PT5M"
    return grouped[TIDY_COLUMNS].to_dict("records")


# ── per-param-style fetchers (each yields row-batches, one per API call, so
#    the caller can write incrementally) ────────────────────────────────────

def _fetch_start_end_years(endpoint: str, param_name: str, geo_value: str, geo_key: str,
                            geo_type: str, start_year: int, end_year: int,
                            extra_params: dict = None):
    extra_params = extra_params or {}
    for year in range(start_year, end_year + 1):
        y_start = f"{year}-01-01"
        y_end = min(f"{year}-12-31", date.today().isoformat())
        if y_start > date.today().isoformat():
            break
        status, body = _get(f"/v2/{endpoint}", {
            param_name: geo_value, "start": y_start, "end": y_end, **extra_params,
        })
        if status == 200:
            yield year, _rows_from_body(body, endpoint, geo_key, geo_type), None
        elif status in (400, 404):
            # before the geo's data-availability window, or genuinely no data
            yield year, [], None
        else:
            yield year, [], f"HTTP {status}: {str(body)[:300]}"


def _fetch_single_call(endpoint: str, param_name: str, geo_value: str, geo_key: str,
                        geo_type: str, extra_params: dict = None):
    extra_params = extra_params or {}
    status, body = _get(f"/v2/{endpoint}", {param_name: geo_value, **extra_params})
    if status == 200:
        yield "all", _rows_from_body(body, endpoint, geo_key, geo_type), None
    elif status == 404:
        yield "all", [], None
    else:
        yield "all", [], f"HTTP {status}: {str(body)[:300]}"


def _fetch_chunk3day(endpoint: str, param_name: str, geo_value: str, geo_key: str,
                      geo_type: str, start: date, end: date):
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=2), end)  # 3-day inclusive window
        status, body = _get(f"/v2/{endpoint}", {
            param_name: geo_value, "start": cur.isoformat(), "end": chunk_end.isoformat(),
        })
        label = f"{cur.isoformat()}..{chunk_end.isoformat()}"
        if status == 200:
            raw_rows = _rows_from_body(body, endpoint, geo_key, geo_type)
            # frequency is 1-second native resolution -- never persist raw
            # ticks, see module docstring. Resampled here so every caller
            # (backfill AND any future incremental/daily update) gets this
            # for free.
            yield label, resample_5min(raw_rows), None
        elif status in (400, 404):
            yield label, [], None
        else:
            yield label, [], f"HTTP {status}: {str(body)[:300]}"
        cur = chunk_end + timedelta(days=1)


# ── progress tracking ───────────────────────────────────────────────────────

def _load_progress() -> dict:
    if not os.path.exists(PROGRESS_PATH):
        return {}
    with open(PROGRESS_PATH, encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def _save_progress(progress: dict) -> None:
    os.makedirs(os.path.dirname(PROGRESS_PATH), exist_ok=True)
    with open(PROGRESS_PATH, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2, default=str, ensure_ascii=False)


def _progress_key(endpoint: str, geo_key: str) -> str:
    return f"{endpoint}::{geo_key}"


# ── storage write ────────────────────────────────────────────────────────────

def _write_rows(endpoint: str, rows: list) -> None:
    if not rows:
        return
    new_df = pd.DataFrame.from_records(rows, columns=TIDY_COLUMNS)
    if endpoint in PARTITIONED_ENDPOINTS:
        upsert_partitioned(
            new_df, _partitioned_base_dir(endpoint), date_col="timestamp",
            dedup_subset=["geo_key", "series_id", "timestamp"],
        )
    else:
        _merge_flat(new_df)


def _merge_flat(new_df: pd.DataFrame) -> None:
    """Append into DATA_PATH (installed_power + frequency only), deduping
    on the natural key. Safe to call repeatedly with overlapping data."""
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    dedup_subset = ["endpoint", "geo_key", "series_id", "timestamp"]
    if os.path.exists(DATA_PATH):
        existing = pd.read_parquet(DATA_PATH)
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df
    combined = combined.drop_duplicates(subset=dedup_subset, keep="last")
    combined = combined.sort_values(["endpoint", "geo_key", "series_id", "timestamp"]).reset_index(drop=True)
    combined.to_parquet(DATA_PATH, index=False)


def total_row_count() -> dict:
    """Row count per endpoint, reading parquet metadata only (no full
    load) for the partitioned endpoints."""
    counts = {}
    for endpoint in PARTITIONED_ENDPOINTS:
        base_dir = _partitioned_base_dir(endpoint)
        n = 0
        if os.path.isdir(base_dir):
            for fname in os.listdir(base_dir):
                if fname.endswith(".parquet"):
                    n += pq.ParquetFile(os.path.join(base_dir, fname)).metadata.num_rows
        counts[endpoint] = n
    if os.path.exists(DATA_PATH):
        flat = pd.read_parquet(DATA_PATH, columns=["endpoint"])
        for endpoint in FLAT_ENDPOINTS:
            counts[endpoint] = int((flat["endpoint"] == endpoint).sum())
    else:
        for endpoint in FLAT_ENDPOINTS:
            counts[endpoint] = 0
    return counts


# ── public backfill entry point ─────────────────────────────────────────────

def run_backfill(countries: list, start_date: str = "2018-01-01",
                  end_date: str = None, frequency_start_date: str = None,
                  frequency_end_date: str = None, endpoints: list = None,
                  log=print) -> dict:
    """Resumable backfill across ENDPOINTS x countries. Skips (endpoint,
    geo_key) combos already marked done=True in progress.json. Safe to
    interrupt (Ctrl+C) and re-run — already-done combos are skipped, and
    rows from a combo that was only partially fetched before interruption
    are already durably written (see _fetch_*'s per-chunk yield + write).

    endpoints: optional subset of ENDPOINTS keys to run (default: all 6) --
    useful to target just one endpoint (e.g. a dedicated frequency run)
    without re-touching combos that are already done for the others.

    frequency_start_date / frequency_end_date: frequency is fetched
    independently of `countries` (it's global, one combo). Defaults to the
    real available range (2022-05-01 -> today) if not given.
    """
    end = pd.Timestamp(end_date).date() if end_date else date.today()
    start_year = pd.Timestamp(start_date).year
    end_year = end.year
    progress = _load_progress()
    endpoint_names = endpoints if endpoints is not None else list(ENDPOINTS.keys())

    results = {"done": [], "failed": []}

    for endpoint in endpoint_names:
        cfg = ENDPOINTS[endpoint]
        param_name = cfg["param_name"]
        param_style = cfg["param_style"]

        if param_style == "chunk3day":
            geo_values = [cfg["fixed_geo_key"]]
            geo_type = "region"
        else:
            geo_values = list(countries)
            geo_type = "country"

        # public_power_forecast: one progress-key per (country, production_type)
        if endpoint == "public_power_forecast":
            combos = [(g, ptype) for g in geo_values for ptype in cfg["production_types"]]
        else:
            combos = [(g, None) for g in geo_values]

        for geo_value, ptype in combos:
            geo_key = geo_value if ptype is None else f"{geo_value}:{ptype}"
            pkey = _progress_key(endpoint, geo_key)
            entry = progress.get(pkey, {})
            if entry.get("done"):
                log(f"[skip-done] {pkey}")
                continue

            log(f"[fetch] {pkey}")
            extra_params = dict(cfg.get("extra_params", {}))
            if ptype is not None:
                extra_params["production_type"] = ptype

            rows_total = 0
            error = None
            try:
                if param_style == "start_end":
                    gen = _fetch_start_end_years(endpoint, param_name, geo_value,
                                                  geo_value, geo_type, start_year, end_year, extra_params)
                elif param_style == "single_call":
                    gen = _fetch_single_call(endpoint, param_name, geo_value,
                                              geo_value, geo_type, extra_params)
                elif param_style == "chunk3day":
                    f_start = pd.Timestamp(frequency_start_date or "2022-05-01").date()
                    f_end = pd.Timestamp(frequency_end_date).date() if frequency_end_date else end
                    gen = _fetch_chunk3day(endpoint, param_name, geo_value,
                                            geo_value, geo_type, f_start, f_end)
                else:
                    raise ValueError(f"unknown param_style {param_style!r}")

                for chunk_label, chunk_rows, chunk_error in gen:
                    if chunk_error:
                        error = chunk_error
                        log(f"  [error] {pkey} @ {chunk_label}: {chunk_error}")
                        break
                    if chunk_rows:
                        _write_rows(endpoint, chunk_rows)
                        rows_total += len(chunk_rows)
            except Exception as e:
                error = f"{type(e).__name__}: {e}"

            progress[pkey] = {
                "done": error is None,
                "last_attempt": datetime.now().isoformat(),
                "rows_fetched": rows_total,
                "error": error,
            }
            _save_progress(progress)

            if error:
                results["failed"].append((pkey, error))
            else:
                results["done"].append((pkey, rows_total))

    return results


# ── incremental update (scripts/update_all.py --only energy_charts) ────────

def _last_timestamp_partitioned(endpoint: str, geo_key: str, series_ids: list = None):
    """Poslední uložený timestamp pro (endpoint, geo_key[, series]) —
    prochází měsíční soubory od nejnovějšího a čte jen 3 sloupce
    s filtrem při čtení, takže se nenačítá celá historie."""
    files = sorted(glob.glob(os.path.join(_partitioned_base_dir(endpoint), "*.parquet")))
    filters = [("geo_key", "==", geo_key)]
    if series_ids:
        filters.append(("series_id", "in", list(series_ids)))
    for f in reversed(files):
        t = pq.read_table(f, columns=["timestamp"], filters=filters)
        if t.num_rows:
            return pd.to_datetime(t.column("timestamp").to_pandas(), utc=True).max()
    return None


def _last_timestamp_flat(endpoint: str, geo_key: str):
    if not os.path.exists(DATA_PATH):
        return None
    t = pq.read_table(DATA_PATH, columns=["timestamp"],
                      filters=[("endpoint", "==", endpoint), ("geo_key", "==", geo_key)])
    if not t.num_rows:
        return None
    return pd.to_datetime(t.column("timestamp").to_pandas(), utc=True).max()


def _fetch_range_chunks(endpoint: str, param_name: str, geo_value: str, geo_type: str,
                         start: date, end: date, extra_params: dict = None):
    """Jako _fetch_start_end_years, ale pro libovolný rozsah [start, end]
    rozsekaný na kusy po nejvýš jednom kalendářním roce."""
    extra_params = extra_params or {}
    cur = start
    while cur <= end:
        chunk_end = min(date(cur.year, 12, 31), end)
        status, body = _get(f"/v2/{endpoint}", {
            param_name: geo_value, "start": cur.isoformat(), "end": chunk_end.isoformat(), **extra_params,
        })
        label = f"{cur.isoformat()}..{chunk_end.isoformat()}"
        if status == 200:
            yield label, _rows_from_body(body, endpoint, geo_value, geo_type), None
        elif status in (400, 404):
            yield label, [], None
        else:
            yield label, [], f"HTTP {status}: {str(body)[:300]}"
        cur = chunk_end + timedelta(days=1)


def _incremental_start(last, today: date, overlap_days: int = INCREMENTAL_OVERLAP_DAYS) -> date:
    if last is None:
        return today - timedelta(days=INCREMENTAL_FRESH_START_DAYS)
    return min(last.date() - timedelta(days=overlap_days), today)


def update_energy_charts(countries: list = ("cz",), endpoints: list = None, today: date = None,
                         log=logger.info) -> dict:
    """Denní inkrementální update — na rozdíl od run_backfill se neřídí
    progress.json, ale KONCEM ULOŽENÝCH DAT pro každé combo: stáhne
    [poslední timestamp - INCREMENTAL_OVERLAP_DAYS, dnes] (u forecastu
    do zítřka — day-ahead), po chuncích, a každý chunk hned zapíše
    (upsert/merge s dedupem → idempotentní, přerušení nic neztratí).
    frequency: 3denní chunky, rovnou převzorkované na 5 min.

    Vrací {"done": [(combo, rows)], "failed": [(combo, error)]}."""
    today = today or date.today()
    endpoint_names = endpoints if endpoints is not None else list(ENDPOINTS.keys())
    results = {"done": [], "failed": []}

    for endpoint in endpoint_names:
        cfg = ENDPOINTS[endpoint]
        style = cfg["param_style"]
        param_name = cfg["param_name"]

        if style == "chunk3day":
            geo = cfg["fixed_geo_key"]
            combos = [(geo, None)]
        elif endpoint == "public_power_forecast":
            combos = [(c, p) for c in countries for p in cfg["production_types"]]
        else:
            combos = [(c, None) for c in countries]

        for geo_value, ptype in combos:
            combo = f"{endpoint}::{geo_value}" + (f":{ptype}" if ptype else "")
            rows_total, error = 0, None
            try:
                extra = dict(cfg.get("extra_params", {}))
                if ptype:
                    extra["production_type"] = ptype
                if style == "single_call":
                    gen = _fetch_single_call(endpoint, param_name, geo_value, geo_value, "country", extra)
                elif style == "chunk3day":
                    last = _last_timestamp_flat(endpoint, geo_value)
                    start = _incremental_start(last, today, overlap_days=0)
                    gen = _fetch_chunk3day(endpoint, param_name, geo_value, geo_value, "region", start, today)
                else:
                    if endpoint in PARTITIONED_ENDPOINTS:
                        last = _last_timestamp_partitioned(endpoint, geo_value, [ptype] if ptype else None)
                    else:
                        last = _last_timestamp_flat(endpoint, geo_value)
                    start = _incremental_start(last, today)
                    end = today + timedelta(days=1) if endpoint == "public_power_forecast" else today
                    gen = _fetch_range_chunks(endpoint, param_name, geo_value, "country", start, end, extra)
                for label, chunk_rows, chunk_error in gen:
                    if chunk_error:
                        error = chunk_error
                        break
                    if chunk_rows:
                        _write_rows(endpoint, chunk_rows)
                        rows_total += len(chunk_rows)
            except Exception as e:
                error = f"{type(e).__name__}: {e}"
            if error:
                log(f"[energy_charts] {combo}: CHYBA {error}")
                results["failed"].append((combo, error))
            else:
                log(f"[energy_charts] {combo}: {rows_total} řádků")
                results["done"].append((combo, rows_total))
    return results


# ── public read API ─────────────────────────────────────────────────────────

def _ts_seconds(arr):
    """pyarrow string pole ISO časů s offsetem → (utc_s, local_s) jako
    int64 pyarrow pole (sekundy od epochy; local = místní čas bez offsetu)."""
    import pyarrow as pa
    import pyarrow.compute as pc
    local = pc.cast(pc.strptime(pc.utf8_slice_codeunits(arr, 0, 19), format="%Y-%m-%dT%H:%M:%S", unit="s"),
                    pa.int64())
    tail = pc.utf8_slice_codeunits(arr, 19, 25)
    has_off = pc.greater_equal(pc.utf8_length(tail), 6)
    sign = pc.if_else(pc.equal(pc.utf8_slice_codeunits(tail, 0, 1), "-"), -1, 1)
    hh = pc.cast(pc.if_else(has_off, pc.utf8_slice_codeunits(tail, 1, 3), "0"), pa.int64())
    mm = pc.cast(pc.if_else(has_off, pc.utf8_slice_codeunits(tail, 4, 6), "0"), pa.int64())
    off_s = pc.multiply(pc.add(pc.multiply(hh, 3600), pc.multiply(mm, 60)), sign)
    return pc.subtract(local, off_s), local


def parse_timestamps(values) -> pd.Series:
    """ISO řetězce s offsetem ("2026-09-01T02:00:00+02:00", jak je ukládá
    _rows_from_body) → datetime64[ns, UTC]. Přes pyarrow: místní čas
    (prvních 19 znaků) minus offset — pd.to_datetime(utc=True) na smíšených
    offsetech (+01:00/+02:00) trvá u celé historie CZ public_power (2.5M
    řádků) ~12 s, tohle <1 s. Hodnoty bez offsetu se berou jako UTC."""
    import pyarrow as pa
    arr = pa.array(values, type=pa.string()) if not isinstance(values, (pa.Array, pa.ChunkedArray)) else values
    utc, _ = _ts_seconds(arr)
    out = pd.to_datetime(utc.to_numpy(zero_copy_only=False), unit="s", utc=True)
    return pd.Series(out).astype("datetime64[ns, UTC]")


@ttl_cache(ttl=3600, maxsize=24)
def load_energy_charts_wide(endpoint: str, geo_key: str, date_from, date_to,
                            resolution: str = None, series_ids: tuple = None) -> pd.DataFrame:
    """Wide data pro grafy: index čas (Europe/Prague), sloupce series_id.

    Čte a agreguje v pyarrow (bez materializace tidy řádků v pandas) —
    celá historie CZ public_power (2.5M řádků) tak zabere ~1 s a desítky MB
    místo ~15 s a ~1 GB. resolution: None/"15min"/"5min" = nativní hodnoty,
    "1h" = hodinový průměr (UTC hodiny), "1D" = denní průměr podle
    místního (pražského) dne. date_from/date_to = tz-aware Timestamp."""
    import pyarrow as pa
    import pyarrow.compute as pc
    from data.partitioned_store import _select_files

    filters = [("geo_key", "==", geo_key)]
    if series_ids:
        filters.append(("series_id", "in", list(series_ids)))
    cols = ["timestamp", "series_id", "value"]
    if endpoint in PARTITIONED_ENDPOINTS:
        files = _select_files(_partitioned_base_dir(endpoint), "parquet", date_from, date_to)
        tables = [pq.read_table(f, columns=cols, filters=filters) for f in files]
    elif os.path.exists(DATA_PATH):
        tables = [pq.read_table(DATA_PATH, columns=cols, filters=filters + [("endpoint", "==", endpoint)])]
    else:
        tables = []
    tables = [t for t in tables if t.num_rows]
    if not tables:
        return pd.DataFrame()
    t = pa.concat_tables([tb.cast(pa.schema([("timestamp", pa.string()), ("series_id", pa.string()),
                                             ("value", pa.float64())])) for tb in tables])
    utc, local = _ts_seconds(t.column("timestamp"))
    mask = None
    if date_from is not None:
        mask = pc.greater_equal(utc, int(pd.Timestamp(date_from).timestamp()))
    if date_to is not None:
        m2 = pc.less(utc, int(pd.Timestamp(date_to).timestamp()))
        mask = m2 if mask is None else pc.and_(mask, m2)
    if resolution == "1D":
        key, step = local, 86400
    elif resolution == "1h":
        key, step = utc, 3600
    else:
        key, step = utc, None
    if step:
        key = pc.multiply(pc.divide(key, step), step)  # int64 dělení = floor pro kladné časy
    g = pa.table({"k": key, "series_id": t.column("series_id"), "value": t.column("value")})
    if mask is not None:
        g = g.filter(mask)
    if not g.num_rows:
        return pd.DataFrame()
    agg = g.group_by(["k", "series_id"]).aggregate([("value", "mean")]).to_pandas()
    w = agg.pivot(index="k", columns="series_id", values="value_mean").sort_index()
    w.columns.name = None
    if resolution == "1D":
        idx = pd.to_datetime(w.index, unit="s").tz_localize("Europe/Prague")
    else:
        idx = pd.to_datetime(w.index, unit="s", utc=True).tz_convert("Europe/Prague")
    w.index = pd.DatetimeIndex(idx, name="timestamp")
    return w


@ttl_cache(ttl=3600, maxsize=8)
def load_energy_charts(endpoint: str = None, geo_key: str = None, date_from=None, date_to=None,
                       series_ids: tuple = None, columns: tuple = None) -> pd.DataFrame:
    """Tidy data (TIDY_COLUMNS). date_from/date_to omezí čtení měsíčních
    souborů i řádků, geo_key a series_ids se filtrují už při čtení —
    BEZ nich by public_power pro všechny země a roky znamenalo GB v paměti
    (2.5M řádků jen pro CZ ≈ 1.2 GB jako object). Volající ve webu by
    měl vždy zadat endpoint + geo_key + okno.

    timestamp se vrací jako datetime64[ns, UTC] (v souborech je ISO
    řetězec s offsetem, viz parse_timestamps). columns = jen tyto sloupce
    (timestamp se přidá vždy) — šetří paměť u dlouhých rozsahů."""
    cols = None
    if columns:
        cols = ["timestamp"] + [c for c in columns if c != "timestamp"]
    frames = []
    endpoints_to_read = [endpoint] if endpoint else list(ENDPOINTS.keys())
    filters = []
    if geo_key is not None:
        filters.append(("geo_key", "==", geo_key))
    if series_ids:
        filters.append(("series_id", "in", list(series_ids)))

    for ep in endpoints_to_read:
        if ep in PARTITIONED_ENDPOINTS:
            df = read_partitioned(_partitioned_base_dir(ep), date_from=date_from, date_to=date_to,
                                  filters=filters or None, columns=cols,
                                  categories=["endpoint", "geo_key", "geo_type", "series_id",
                                              "series_name", "unit", "resolution"])
            if not df.empty:
                frames.append(df)

    if os.path.exists(DATA_PATH) and (endpoint is None or endpoint in FLAT_ENDPOINTS):
        flat_filters = list(filters)
        if endpoint is not None:
            flat_filters.append(("endpoint", "==", endpoint))
        flat = pq.read_table(DATA_PATH, filters=flat_filters or None, columns=cols).to_pandas()
        if not flat.empty:
            frames.append(flat)

    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=cols or TIDY_COLUMNS)
    if df.empty:
        return df.reset_index(drop=True)
    if pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    else:
        df["timestamp"] = parse_timestamps(df["timestamp"].astype(str).to_numpy()).to_numpy()
    if date_from is not None or date_to is not None:
        ts = df["timestamp"]
        mask = pd.Series(True, index=df.index)
        if date_from is not None:
            lo = pd.Timestamp(date_from)
            mask &= ts >= (lo.tz_localize("UTC") if lo.tzinfo is None else lo)
        if date_to is not None:
            hi = pd.Timestamp(date_to)
            mask &= ts <= (hi.tz_localize("UTC") if hi.tzinfo is None else hi)
        df = df[mask]
    return df.reset_index(drop=True)
