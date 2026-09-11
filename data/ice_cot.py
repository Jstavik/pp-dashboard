"""ICE MiFID II Commitments of Traders (IFEU_FUT + NDEX_FUT) — tidy storage.

Source: weekly CSV published at
https://www.ice.com/marketdata/publicdocs/mifid/commitment_of_traders/{venue}_FUT_{YYYYMMDD}.csv
for venue in (IFEU, NDEX). Each file already carries NEWT (this week) +
AMND (republished/restated older weeks) rows — see backfill script for how
that embedded history gets combined with fresh weekly fetches.

Storage: ONE flat tidy parquet (data/history/ice_cot.parquet), no monthly
partitioning — this is small weekly data (~30 commodities × 2 venues ×
~30 tidy rows/commodity/week), years of it comfortably fit in one file
unlike the daily/high-cardinality sources that use partitioned_store.py.

Tidy schema — one row per (venue, product, report week, category, side,
scope):
    venue           IFEU | NDEX
    product_code    Venue_Product_Code (e.g. "B", "TFM", "C")
    product_name    Name_of_Commodity_Contract_or_Emission_Allowance
    report_date     date the weekly report refers to
    report_status   NEWT | AMND
    rpttp           FUTR | COMB | None (NDEX only, and only once ICE added
                     the column — see backfill script notes)
    category        one of the 5 MiFID II Art. 58 trader categories
    side            Long | Short
    scope           Risk_Reducing | Other | Total (source's own NOPS_
                     breakdown — Total is ICE's own sum, not recomputed
                     here; IFEU's extra "MAR10 exemption" sub-breakdown is
                     intentionally not extracted — Total already includes
                     it, and it's not part of the schema this module was
                     asked to expose)
    notation_unit   NotationofthePositionQuantity (LOTS, MWHO, ...) — kept
                     per-row so a MWh product (TTF) can never get silently
                     compared against a LOTS product (Brent/EUA)
    nops            the NOPS_{category}_{side}_{scope} value
    published_at    Date_and_time_of_the_publication — NOT in the schema
                     as originally specified, added because it's the only
                     way to correctly implement "newest published version
                     wins" on merge (see _merge_and_write)
"""
import csv
import io
import json
import os
import re
import time
from datetime import date

import pandas as pd
import requests
import streamlit as st

COT_PATH = "data/history/ice_cot.parquet"
STATUS_PATH = "data/history/ice_cot_status.json"
STATUS_MAX_ENTRIES = 500

BASE_URL = "https://www.ice.com/marketdata/publicdocs/mifid/commitment_of_traders"
VENUES = ["IFEU", "NDEX"]
HEADERS = {"User-Agent": "pp-dashboard-ice-cot/1 (+data pipeline)"}

SIDES = ["Long", "Short"]
SCOPES = ["Risk_Reducing", "Other", "Total"]

# 5 MiFID II (Art. 58 / RTS 83) trader categories, identified by a
# normalized substring of the NOPS_..._{side}_{scope} column name — source
# header naming is inconsistent between IFEU/NDEX and even within IFEU
# itself (e.g. "NOPS_Commercial_Undertakings_Long_Total" vs
# "NOPS_CommercialUndertakings_Short_Total"), so we match on a normalized
# alnum-only substring instead of the exact string.
CATEGORY_KEYWORDS = [
    ("Investment Firms or Credit Institutions", "investmentfirmsorcreditinstitutions"),
    ("Investment Funds", "investmentfunds"),
    ("Other Financial Institutions", "otherfinancialinstitutions"),
    ("Commercial Undertakings", "commercialundertakings"),
    ("ETS Compliance Operators", "directive200387ec"),
]

TIDY_COLUMNS = [
    "venue", "product_code", "product_name", "report_date", "report_status",
    "rpttp", "category", "side", "scope", "notation_unit", "nops", "published_at",
]

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _find_nops_column(fieldnames: list[str], keyword_norm: str, side: str, scope: str) -> str:
    suffix = f"_{side}_{scope}"
    candidates = [c for c in fieldnames if c.startswith("NOPS_") and c.endswith(suffix)]
    matches = [c for c in candidates if keyword_norm in _normalize(c)]
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly 1 NOPS column matching category={keyword_norm!r} "
            f"side={side!r} scope={scope!r}, got {matches} (candidates: {candidates})"
        )
    return matches[0]


# ── CSV parsing + tidy melt ──────────────────────────────────────────────

def _parse_csv_rows(text: str) -> list[dict]:
    """Real data rows only — drops the trailing disclaimer row (its
    Name_of_Commodity_Contract_or_Emission_Allowance cell is empty)."""
    reader = csv.DictReader(io.StringIO(text))
    return [row for row in reader if row.get("Name_of_Commodity_Contract_or_Emission_Allowance")]


def _row_to_tidy_records(row: dict) -> list[dict]:
    fieldnames = list(row.keys())
    base = {
        "venue": row["Trading_Venue_Identifier"],
        "product_code": row["Venue_Product_Code"],
        "product_name": row["Name_of_Commodity_Contract_or_Emission_Allowance"],
        "report_date": row["Date_of_which_the_weekly_report_refers"],
        "report_status": row.get("Report_Status"),
        "rpttp": row.get("RptTp") or None,
        "notation_unit": row.get("NotationofthePositionQuantity"),
        "published_at": row.get("Date_and_time_of_the_publication"),
    }
    records = []
    for label, keyword in CATEGORY_KEYWORDS:
        for side in SIDES:
            for scope in SCOPES:
                col = _find_nops_column(fieldnames, keyword, side, scope)
                records.append({
                    **base,
                    "category": label,
                    "side": side,
                    "scope": scope,
                    "nops": pd.to_numeric(row.get(col), errors="coerce"),
                })
    return records


def melt_csv_text(text: str) -> pd.DataFrame:
    records = []
    for row in _parse_csv_rows(text):
        records.extend(_row_to_tidy_records(row))
    if not records:
        return pd.DataFrame(columns=TIDY_COLUMNS)
    df = pd.DataFrame.from_records(records, columns=TIDY_COLUMNS)
    df["report_date"] = pd.to_datetime(df["report_date"]).dt.date
    return df


# ── merge + persist ──────────────────────────────────────────────────────

def _read_existing() -> pd.DataFrame:
    if not os.path.exists(COT_PATH):
        return pd.DataFrame(columns=TIDY_COLUMNS)
    return pd.read_parquet(COT_PATH)


def _merge_and_write(new_tidy: pd.DataFrame) -> int:
    """Merge new_tidy into COT_PATH. For each (venue, product_code,
    report_date), whichever source (existing file or newly fetched) has
    the latest published_at wins — its full set of category/side/scope
    rows is kept, the other's dropped. Idempotent: re-merging identical
    data is a no-op (drop_duplicates catches exact re-fetches; the
    published_at comparison catches re-fetches of an already-superseded
    version). Older report weeks already on disk that aren't present in
    new_tidy are left untouched — merge, never overwrite-whole-file."""
    existing = _read_existing()
    if new_tidy.empty and existing.empty:
        return 0
    combined = pd.concat([existing, new_tidy], ignore_index=True) if not existing.empty else new_tidy.copy()
    combined["published_at"] = combined["published_at"].fillna("")

    key_cols = ["venue", "product_code", "report_date"]
    max_pub = combined.groupby(key_cols)["published_at"].transform("max")
    combined = combined[combined["published_at"] == max_pub]
    combined = combined.drop_duplicates(subset=key_cols + ["category", "side", "scope"], keep="last")
    combined = combined.sort_values(key_cols + ["category", "side", "scope"]).reset_index(drop=True)

    os.makedirs(os.path.dirname(COT_PATH), exist_ok=True)
    combined.to_parquet(COT_PATH, index=False)
    return len(combined)


def ingest_local_csv(path: str) -> pd.DataFrame:
    """Melt + merge one already-downloaded CSV file (no network) — used by
    the backfill script to absorb scratch/cot_data/raw_cache/*.csv without
    re-fetching."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    tidy = melt_csv_text(text)
    _merge_and_write(tidy)
    return tidy


# ── live fetch (weekly use + backfill) ───────────────────────────────────

def probe_rate_limit(venue: str = "IFEU") -> dict:
    """Single lightweight HEAD request to check whether ice.com is
    currently rate-limiting us (Cloudflare 429 with a long Retry-After —
    observed live to be ~35 min) before attempting a real fetch/sweep."""
    url = f"{BASE_URL}/{venue}_FUT_{date.today():%Y%m%d}.csv"
    try:
        resp = SESSION.head(url, timeout=10)
    except requests.RequestException as e:
        return {"rate_limited": None, "error": str(e)}
    if resp.status_code == 429:
        return {"rate_limited": True, "retry_after_s": resp.headers.get("Retry-After")}
    return {"rate_limited": False, "status_code": resp.status_code}


def fetch_ice_cot(report_date, venues: list[str] = VENUES) -> dict:
    """Fetch + merge one report Friday for the given venues. Idempotent —
    safe to call repeatedly for the same date (re-fetching an unchanged
    week is a no-op merge, see _merge_and_write). Logs one status entry
    per venue per call, success or failure."""
    report_date = pd.Timestamp(report_date).date()
    results = {}
    for venue in venues:
        url = f"{BASE_URL}/{venue}_FUT_{report_date:%Y%m%d}.csv"
        entry = {
            "timestamp": pd.Timestamp.now(tz="UTC").isoformat(),
            "venue": venue,
            "report_date": report_date.isoformat(),
            "url": url,
        }
        try:
            resp = SESSION.get(url, timeout=30)
            entry["http_status"] = resp.status_code
            resp.raise_for_status()
            tidy = melt_csv_text(resp.text)
            n_stored = _merge_and_write(tidy)
            entry["success"] = True
            entry["rows_fetched"] = len(tidy)
            entry["rows_in_store"] = n_stored
        except Exception as e:
            entry["success"] = False
            entry["error"] = str(e)
        results[venue] = entry
        _append_status(entry)
    return results


# ── status log ────────────────────────────────────────────────────────────

def _load_status_raw() -> list[dict]:
    if not os.path.exists(STATUS_PATH):
        return []
    with open(STATUS_PATH, encoding="utf-8") as f:
        try:
            return json.load(f).get("attempts", [])
        except json.JSONDecodeError:
            return []


def _append_status(entry: dict) -> None:
    attempts = _load_status_raw()
    attempts.append(entry)
    attempts = attempts[-STATUS_MAX_ENTRIES:]
    os.makedirs(os.path.dirname(STATUS_PATH), exist_ok=True)
    with open(STATUS_PATH, "w", encoding="utf-8") as f:
        json.dump({"attempts": attempts}, f, indent=2, default=str)


# ── public read API ──────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def load_ice_cot() -> pd.DataFrame:
    return _read_existing()


@st.cache_data(ttl=300, show_spinner=False)
def load_ice_cot_status() -> pd.DataFrame:
    attempts = _load_status_raw()
    cols = ["timestamp", "venue", "report_date", "success", "http_status",
            "rows_fetched", "rows_in_store", "error", "url"]
    if not attempts:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(attempts).reindex(columns=cols)
