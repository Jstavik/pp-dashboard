"""One-time backfill for data/history/ice_cot.parquet.

1. Ingests the raw CSVs already fetched during prototyping
   (scratch/cot_data/raw_cache/*.csv) — no network needed for this part.
2. Probes ice.com once to see whether the Cloudflare rate limit hit
   during prototyping has expired; if clear, opportunistically fetches
   today's report too (best-effort, not required for the backfill to
   count as done).
3. Prints row/commodity/week counts and a Long vs Short sanity check for
   TFM (TTF), C (NDEX EUA) and B (IFEU Brent) — same check used to
   validate the prototype; should still land well under ~1%.

Run: python3 scripts/backfill_ice_cot.py
"""
import glob
import sys

sys.path.insert(0, ".")

import pandas as pd

from data.ice_cot import (
    ingest_local_csv, probe_rate_limit, fetch_ice_cot, load_ice_cot,
)

RAW_CACHE_GLOB = "scratch/cot_data/raw_cache/*.csv"


def sanity_check(df: pd.DataFrame, venue: str, code: str, label: str) -> None:
    sub = df[(df["venue"] == venue) & (df["product_code"] == code) & (df["scope"] == "Total")]
    if sub.empty:
        print(f"  {label}: NO DATA")
        return
    totals = (
        sub.pivot_table(index="report_date", columns="side", values="nops", aggfunc="sum")
        .rename(columns={"Long": "total_long", "Short": "total_short"})
    )
    totals["diff_pct"] = ((totals["total_long"] - totals["total_short"]).abs() / totals["total_long"] * 100)
    print(f"  {label}: {len(totals)} weeks, {totals.index.min()} .. {totals.index.max()}, "
          f"mean |Long-Short| diff = {totals['diff_pct'].mean():.4f}%, max = {totals['diff_pct'].max():.4f}%")


def main():
    print("=" * 70)
    print("STEP 1 — ingest raw_cache CSVs (no network)")
    print("=" * 70)
    paths = sorted(glob.glob(RAW_CACHE_GLOB))
    if not paths:
        print(f"  No files found at {RAW_CACHE_GLOB} — nothing to backfill from cache.")
    for path in paths:
        tidy = ingest_local_csv(path)
        print(f"  {path}: {len(tidy)} tidy rows merged")

    print()
    print("=" * 70)
    print("STEP 2 — rate-limit probe + opportunistic fresh fetch")
    print("=" * 70)
    probe = probe_rate_limit()
    print(f"  probe result: {probe}")
    if probe.get("rate_limited") is False:
        today = pd.Timestamp.today().date()
        print(f"  rate limit clear — attempting fresh fetch for {today}...")
        result = fetch_ice_cot(today)
        for venue, entry in result.items():
            print(f"    {venue}: {entry}")
    else:
        print("  still rate-limited (or probe failed) — skipping live fetch, "
              "backfill relies on raw_cache only for this run.")

    print()
    print("=" * 70)
    print("STEP 3 — summary + sanity check")
    print("=" * 70)
    df = load_ice_cot()
    print(f"  Total tidy rows: {len(df)}")
    for venue in df["venue"].unique():
        sub = df[df["venue"] == venue]
        n_commodities = sub["product_code"].nunique()
        n_weeks = sub["report_date"].nunique()
        print(f"  {venue}: {n_commodities} commodities, {n_weeks} distinct report weeks, "
              f"{sub['report_date'].min()} .. {sub['report_date'].max()}")

    print()
    print("  Long vs Short sanity check (scope=Total, summed across categories):")
    sanity_check(df, "NDEX", "TFM", "TFM (TTF)")
    sanity_check(df, "NDEX", "C", "EUA")
    sanity_check(df, "IFEU", "B", "Brent")


if __name__ == "__main__":
    main()
