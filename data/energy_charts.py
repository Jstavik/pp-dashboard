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
  production_type axis (solar/wind_onshore/wind_offshore/load/wind) fetched
  as 5 separate calls per country — forecast_type is fixed to "day-ahead"
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
import json
import os
import time
from datetime import date, datetime, timedelta

import pandas as pd
import pyarrow.parquet as pq
import requests
import streamlit as st

from data.partitioned_store import read_partitioned, upsert_partitioned

BASE_URL = "https://api.energy-charts.info"
DATA_PATH = "data/history/energy_charts.parquet"
PROGRESS_PATH = "data/history/energy_charts_progress.json"
PARTITIONED_ROOT = "data/history/energy_charts"

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
    "public_power_forecast":  {"param_style": "start_end", "param_name": "country",
                                "production_types": ["solar", "wind_onshore", "wind_offshore", "load", "wind"],
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


def _partitioned_base_dir(endpoint: str) -> str:
    return f"{PARTITIONED_ROOT}/{endpoint}"


# ── low-level HTTP ─────────────────────────────────────────────────────────

def _get(path: str, params: dict, pace: float = DEFAULT_PACE_S):
    """GET with Retry-After-respecting 429 backoff. Returns (status, body)
    where body is the parsed JSON dict, or the raw text for non-JSON /
    error responses."""
    url = f"{BASE_URL}{path}"
    while True:
        resp = SESSION.get(url, params=params, timeout=30)
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


# ── public read API ─────────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def load_energy_charts(endpoint: str = None, geo_key: str = None) -> pd.DataFrame:
    frames = []
    endpoints_to_read = [endpoint] if endpoint else list(ENDPOINTS.keys())

    for ep in endpoints_to_read:
        if ep in PARTITIONED_ENDPOINTS:
            df = read_partitioned(_partitioned_base_dir(ep))
            if not df.empty:
                frames.append(df)

    if os.path.exists(DATA_PATH):
        flat = pd.read_parquet(DATA_PATH)
        if endpoint is not None:
            flat = flat[flat["endpoint"] == endpoint]
        if not flat.empty:
            frames.append(flat)

    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=TIDY_COLUMNS)
    if geo_key is not None:
        df = df[df["geo_key"] == geo_key]
    return df.reset_index(drop=True)
