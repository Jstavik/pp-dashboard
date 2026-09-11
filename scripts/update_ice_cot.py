"""Weekly update for data/history/ice_cot.parquet — GitHub Actions entry
point (see .github/workflows/update_ice_cot.yml).

Computes the most recent Friday relative to today (the report_date ICE's
weekly MiFID II CoT files are keyed by), fetches IFEU + NDEX for that
date via data/ice_cot.py::fetch_ice_cot() (which already merges into the
parquet and logs every attempt — success or failure — to
data/history/ice_cot_status.json), and never raises on a per-venue fetch
failure (429, 404 from a bank-holiday-delayed publication, etc.) — that's
expected/recoverable, logged, and the script continues. The two cron
schedules (Saturday primary, Tuesday retry) both resolve to the SAME
report_date automatically, since "most recent Friday" as of either day
is the same Friday — no special-casing needed between the two runs.

Run: python3 scripts/update_ice_cot.py
"""
import os
import sys

sys.path.insert(0, ".")

import pandas as pd

from data.ice_cot import fetch_ice_cot, probe_rate_limit, VENUES


def last_friday(today: pd.Timestamp):
    # Monday=0 ... Friday=4, Saturday=5, Sunday=6
    offset = (today.weekday() - 4) % 7
    return (today - pd.Timedelta(days=offset)).date()


def _write_github_output(data: dict) -> None:
    """No-op outside GitHub Actions (GITHUB_OUTPUT unset) — safe to run
    locally without special-casing."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        for k, v in data.items():
            f.write(f"{k}={v}\n")


def main() -> int:
    today = pd.Timestamp.now(tz="UTC")
    report_date = last_friday(today)
    print(f"=== ICE MiFID II CoT update — report_date {report_date} (today {today.date()}) ===")

    probe = probe_rate_limit()
    if probe.get("rate_limited"):
        print(f"  RATE LIMITED (429, Retry-After={probe.get('retry_after_s')}s) — "
              f"skipping fetch entirely this run, nothing to commit.")
        _write_github_output({
            "report_date": report_date,
            **{f"{v.lower()}_status": "SKIPPED (rate limited)" for v in VENUES},
        })
        return 0

    results = fetch_ice_cot(report_date)

    summary = {}
    for venue in VENUES:
        entry = results.get(venue, {})
        if entry.get("success"):
            summary[venue] = "OK"
        else:
            http_status = entry.get("http_status")
            summary[venue] = f"FAILED {http_status}" if http_status else "FAILED"

    print()
    print("=== SUMMARY ===")
    for venue in VENUES:
        entry = results.get(venue, {})
        if entry.get("success"):
            print(f"  {venue}: OK — report_date {report_date}, "
                  f"{entry.get('rows_fetched')} rows fetched, "
                  f"{entry.get('rows_in_store')} rows now in store")
        else:
            print(f"  {venue}: FAILED — report_date {report_date}, "
                  f"http_status={entry.get('http_status')}, error={entry.get('error')}")

    _write_github_output({"report_date": report_date, **{f"{v.lower()}_status": s for v, s in summary.items()}})
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        # Per-venue fetch failures are already caught inside
        # fetch_ice_cot() and logged to status.json — this is only for
        # genuinely unexpected errors (bug, disk full, ...), which SHOULD
        # surface loudly in the GH Actions log rather than pass silently.
        print(f"CHYBA (neočekávaná): {type(e).__name__}: {e}")
        sys.exit(1)
