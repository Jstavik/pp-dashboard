"""Data layer for the Energy-Charts API (Fraunhofer ISE, api.energy-charts.info).

No token/registration needed. 17 endpoints covered (2 excluded — see below).

Storage: ONE flat tidy parquet (data/history/energy_charts.parquet), same
spirit as data/ice_cot.py — one row per (endpoint, geo_key, series_id,
timestamp). Not partitioned like data/partitioned_store.py sources: this
mirrors ice_cot's choice, appropriate for the endpoints that are genuinely
historical (15-min/daily/yearly resolution); the one 1-second-resolution
endpoint (frequency) is deliberately NOT fully backfilled — see below.

Resumable backfill: data/history/energy_charts_progress.json tracks, per
(endpoint, geo_key) combo: done, last_attempt, rows_fetched, error. Re-running
run_backfill() skips combos already done=True, so a multi-hour/multi-country
backfill can be interrupted (Ctrl+C, timeout, crash) and resumed exactly
where it left off. Rows are written to the parquet incrementally (per
year-chunk within a combo, not just per combo) so a mid-combo interruption
never loses already-fetched years — re-running just re-fetches (wastefully
but harmlessly, dedup handles it) whatever year wasn't marked complete.

EXCLUDED (out of scope, confirmed live 2026-09-24):
- total_power: "Currently only available for Germany" — 404 for every
  other country.
- ren_share_forecast: HTTP 500 for every country tried — broken
  server-side, not a rate-limit artifact.

ENDPOINT QUIRKS THAT SHAPE THIS MODULE (verified live 2026-09-24; the API
has no written docs beyond the machine-readable /v2 catalog + openapi.json,
and even those don't mention most of this):

- solar_share, wind_onshore_share, wind_offshore_share, signal,
  price_current, price_next_day: IGNORE start/end entirely (or don't
  accept them at all) — every call returns a rolling ~1-2 day window
  around "now", regardless of what dates are passed. NOT historically
  backfillable. Only useful as a periodic snapshot if this module is run
  on a schedule going forward — this backfill captures ONE snapshot per
  combo and marks it done, it does not attempt to "reach back" to 2018
  for these (there is nothing there to reach).
- wind_offshore_share (and its *_daily_avg sibling) 404 "no content
  available" for landlocked countries — treated as a skip, not a failure.
- solar_share_daily_avg, wind_onshore_share_daily_avg,
  wind_offshore_share_daily_avg, ren_share_daily_avg: use year=<int>, NOT
  start/end. Genuinely historical — looped one call per calendar year.
- installed_power: no start/end at all — one call already returns the
  full available yearly series (CZ: 2014→2025 in one response).
- public_power_forecast: genuinely historical back to at least 2018
  (unlike its *_share siblings above), BUT has an extra production_type
  axis (solar/wind_onshore/wind_offshore/load/wind) that must be fetched
  as 5 separate calls — this module fixes forecast_type="day-ahead" (the
  one confirmed to hold real historical values) and loops all 5
  production_type values, each tracked as its own progress-key
  "<country>:<production_type>" (the parquet's geo_key column still just
  holds the plain country code — production_type is already carried by
  series_id, e.g. "load", "solar").
- frequency: uses region= (not country=) — there is currently only ONE
  region, 'DE-Freiburg' (RG Continental Europe), i.e. this is NOT
  per-country. 1-second resolution. HARD 3-day max window per request
  (anything longer → 400 "Requested interval too long: maximum allowed
  timespan is 3 days."). Data starts 2022-05-01. A full backfill from
  2022-05-01 to today is 500+ chunked requests and, at 1 row/second,
  ~135-140 million rows — utterly disproportionate to the other 16
  endpoints (each a few hundred to a few thousand rows for CZ). Chunking
  is implemented generically (chunk3day) so a dedicated full run can do
  it later, but run_backfill's caller is expected to pass a deliberately
  short start date for `frequency` (see backfill script) rather than
  2022-05-01 — see the module's own backfill entry point for the actual
  scoping used in the CZ validation run.
- price/price_current/price_next_day use bzn= (bidding zone, e.g. "CZ",
  "DE-LU" — UPPERCASE, composite for some countries) — a DIFFERENT code
  scheme than every other endpoint's country= (lowercase, e.g. "cz",
  "de"). See COUNTRY_TO_BZN below for the mapping between the two.
"""
import json
import os
import time
from datetime import date, datetime, timedelta

import pandas as pd
import requests
import streamlit as st

BASE_URL = "https://api.energy-charts.info"
DATA_PATH = "data/history/energy_charts.parquet"
PROGRESS_PATH = "data/history/energy_charts_progress.json"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "pp-dashboard/1.0 (+https://github.com/Jstavik/pp-dashboard)"})

TIDY_COLUMNS = [
    "endpoint", "geo_key", "geo_type", "series_id", "series_name",
    "timestamp", "value", "unit", "resolution",
]

# param_style: "start_end" (yearly-chunked date range) | "year" (year= param,
# looped) | "single_call" (no date params, one call has everything) |
# "rolling_snapshot" (dates ignored, one "now" fetch) | "chunk3day"
# (frequency's hard 3-day window)
ENDPOINTS = {
    "public_power":                  {"geo_type": "country", "param_style": "start_end", "param_name": "country"},
    "public_power_forecast":         {"geo_type": "country", "param_style": "start_end", "param_name": "country",
                                       "production_types": ["solar", "wind_onshore", "wind_offshore", "load", "wind"],
                                       "extra_params": {"forecast_type": "day-ahead"}},
    "cbet":                          {"geo_type": "country", "param_style": "start_end", "param_name": "country"},
    "cbpf":                          {"geo_type": "country", "param_style": "start_end", "param_name": "country"},
    "installed_power":               {"geo_type": "country", "param_style": "single_call", "param_name": "country",
                                       "extra_params": {"time_step": "yearly"}},
    "signal":                        {"geo_type": "country", "param_style": "rolling_snapshot", "param_name": "country"},
    "solar_share":                   {"geo_type": "country", "param_style": "rolling_snapshot", "param_name": "country"},
    "solar_share_daily_avg":         {"geo_type": "country", "param_style": "year", "param_name": "country"},
    "wind_onshore_share":            {"geo_type": "country", "param_style": "rolling_snapshot", "param_name": "country"},
    "wind_onshore_share_daily_avg":  {"geo_type": "country", "param_style": "year", "param_name": "country"},
    "wind_offshore_share":           {"geo_type": "country", "param_style": "rolling_snapshot", "param_name": "country"},
    "wind_offshore_share_daily_avg": {"geo_type": "country", "param_style": "year", "param_name": "country"},
    "ren_share_daily_avg":           {"geo_type": "country", "param_style": "year", "param_name": "country"},
    "frequency":                     {"geo_type": "region", "param_style": "chunk3day", "param_name": "region",
                                       "fixed_geo_key": "DE-Freiburg"},
    "price":                         {"geo_type": "bzn", "param_style": "start_end", "param_name": "bzn"},
    "price_current":                 {"geo_type": "bzn", "param_style": "rolling_snapshot", "param_name": "bzn"},
    "price_next_day":                {"geo_type": "bzn", "param_style": "rolling_snapshot", "param_name": "bzn"},
}

EXCLUDED_ENDPOINTS = ["total_power", "ren_share_forecast"]

# Manually built 2026-09-24 from the LIVE /v2/price `bzn` enum (not guessed,
# not from stale docs) cross-referenced against the `country` enum. Countries
# present in `country=` with no corresponding bzn are commented below —
# fetch_price* for those must be skipped ("no bzn mapping"), not treated as
# an error.
COUNTRY_TO_BZN = {
    "at": ["AT"],
    "be": ["BE"],
    "bg": ["BG"],
    "ch": ["CH"],
    "cz": ["CZ"],
    "de": ["DE-LU"],
    "dk": ["DK1", "DK2"],
    "ee": ["EE"],
    "es": ["ES"],
    "fi": ["FI"],
    "fr": ["FR"],
    "gr": ["GR"],
    "hr": ["HR"],
    "hu": ["HU"],
    "ie": ["IE(SEM)"],
    "it": ["IT-North", "IT-Centre-North", "IT-Centre-South", "IT-South",
           "IT-Sicily", "IT-Sardinia", "IT-Calabria", "IT-Brindisi",
           "IT-Foggia", "IT-GR", "IT-North-AT", "IT-North-CH", "IT-North-SI",
           "IT-North-FR", "IT-Priolo", "IT-Rossano", "IT-SACOAC", "IT-SACODC"],
    "lt": ["LT"],
    "lv": ["LV"],
    "me": ["ME"],
    "nl": ["NL"],
    "no": ["NO1", "NO2", "NO2NSL", "NO3", "NO4", "NO5"],
    "pl": ["PL"],
    "pt": ["PT"],
    "ro": ["RO"],
    "rs": ["RS"],
    "se": ["SE1", "SE2", "SE3", "SE4"],
    "si": ["SI"],
    "sk": ["SK"],
    "ua": ["UA-IPS", "UA-BEI"],
    # no bzn mapping (present in country= enum, no matching zone in the live
    # bzn enum): ba (Bosnia), cy (Cyprus), ge (Georgia), lu (Luxembourg —
    # merged into DE-LU, no standalone zone), md (Moldova), mk (North
    # Macedonia), uk (Britain), xk (Kosovo).
}

DEFAULT_PACE_S = 1.0


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


def _fetch_year_param(endpoint: str, param_name: str, geo_value: str, geo_key: str,
                       geo_type: str, start_year: int, end_year: int):
    for year in range(start_year, end_year + 1):
        if year > date.today().year:
            break
        status, body = _get(f"/v2/{endpoint}", {param_name: geo_value, "year": year})
        if status == 200:
            yield year, _rows_from_body(body, endpoint, geo_key, geo_type), None
        elif status in (400, 404):
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


def _fetch_rolling_snapshot(endpoint: str, param_name: str, geo_value: str, geo_key: str,
                             geo_type: str):
    status, body = _get(f"/v2/{endpoint}", {param_name: geo_value})
    if status == 200:
        yield "snapshot", _rows_from_body(body, endpoint, geo_key, geo_type), None
    elif status == 404:
        yield "snapshot", [], None
    else:
        yield "snapshot", [], f"HTTP {status}: {str(body)[:300]}"


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
            yield label, _rows_from_body(body, endpoint, geo_key, geo_type), None
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


# ── storage merge ───────────────────────────────────────────────────────────

def _merge_and_write(new_rows: list) -> int:
    """Append new_rows into DATA_PATH, deduping on the natural key. Safe to
    call repeatedly with overlapping data (resumed runs re-fetch already-
    saved chunks) — dedup keeps the row idempotent."""
    if not new_rows:
        return _current_row_count()
    new_df = pd.DataFrame.from_records(new_rows, columns=TIDY_COLUMNS)
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
    return len(combined)


def _current_row_count() -> int:
    if not os.path.exists(DATA_PATH):
        return 0
    return len(pd.read_parquet(DATA_PATH, columns=["endpoint"]))


# ── public backfill entry point ─────────────────────────────────────────────

def run_backfill(countries: list, start_date: str = "2018-01-01",
                  end_date: str = None, frequency_start_date: str = None,
                  log=print) -> dict:
    """Resumable backfill across ENDPOINTS x countries. Skips (endpoint,
    geo_key) combos already marked done=True in progress.json. Safe to
    interrupt (Ctrl+C) and re-run — already-done combos are skipped, and
    rows from a combo that was only partially fetched before interruption
    are already durably written (see _fetch_*'s per-chunk yield + write).

    frequency_start_date: because `frequency` has a hard 3-day window and
    1-second resolution (see module docstring), its real start (2022-05-01)
    would mean 500+ chunked requests per run — pass a short recent window
    here for a quick validation pass; omit to use frequency's real
    available_from (2022-05-01) for a genuine full backfill.
    """
    end = pd.Timestamp(end_date).date() if end_date else date.today()
    start_year = pd.Timestamp(start_date).year
    end_year = end.year
    progress = _load_progress()

    results = {"done": [], "skipped_no_bzn": [], "skipped_not_historical": [], "failed": []}

    for endpoint, cfg in ENDPOINTS.items():
        geo_type = cfg["geo_type"]
        param_name = cfg["param_name"]
        param_style = cfg["param_style"]

        if geo_type == "region":
            geo_values = [cfg["fixed_geo_key"]]
        elif geo_type == "bzn":
            geo_values = []
            for c in countries:
                bzns = COUNTRY_TO_BZN.get(c)
                if not bzns:
                    log(f"[skip] {endpoint}/{c}: no bzn mapping")
                    results["skipped_no_bzn"].append((endpoint, c))
                    continue
                geo_values.extend(bzns)
        else:
            geo_values = list(countries)

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
                elif param_style == "year":
                    gen = _fetch_year_param(endpoint, param_name, geo_value,
                                             geo_value, geo_type, start_year, end_year)
                elif param_style == "single_call":
                    gen = _fetch_single_call(endpoint, param_name, geo_value,
                                              geo_value, geo_type, extra_params)
                elif param_style == "rolling_snapshot":
                    gen = _fetch_rolling_snapshot(endpoint, param_name, geo_value,
                                                   geo_value, geo_type)
                elif param_style == "chunk3day":
                    f_start = pd.Timestamp(frequency_start_date or "2022-05-01").date()
                    gen = _fetch_chunk3day(endpoint, param_name, geo_value,
                                            geo_value, geo_type, f_start, end)
                else:
                    raise ValueError(f"unknown param_style {param_style!r}")

                for chunk_label, chunk_rows, chunk_error in gen:
                    if chunk_error:
                        error = chunk_error
                        log(f"  [error] {pkey} @ {chunk_label}: {chunk_error}")
                        break
                    if chunk_rows:
                        _merge_and_write(chunk_rows)
                        rows_total += len(chunk_rows)
            except Exception as e:
                error = f"{type(e).__name__}: {e}"

            entry = {
                "done": error is None,
                "last_attempt": datetime.now().isoformat(),
                "rows_fetched": rows_total,
                "error": error,
                "not_historical": param_style == "rolling_snapshot",
            }
            progress[pkey] = entry
            _save_progress(progress)

            if error:
                results["failed"].append((pkey, error))
            elif param_style == "rolling_snapshot":
                results["skipped_not_historical"].append(pkey)
            else:
                results["done"].append((pkey, rows_total))

    return results


# ── public read API ─────────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def load_energy_charts(endpoint: str = None, geo_key: str = None) -> pd.DataFrame:
    if not os.path.exists(DATA_PATH):
        return pd.DataFrame(columns=TIDY_COLUMNS)
    df = pd.read_parquet(DATA_PATH)
    if endpoint is not None:
        df = df[df["endpoint"] == endpoint]
    if geo_key is not None:
        df = df[df["geo_key"] == geo_key]
    return df.reset_index(drop=True)
