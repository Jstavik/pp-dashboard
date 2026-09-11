"""Ad-hoc smoke test for data/ice_cot.py — not part of the app, run manually.

Checks load_ice_cot() returns a sane tidy DataFrame: non-empty, no NaN in
the identifying columns, nops present for the numeric column, sensible
report_date range, and that load_ice_cot_status() at least doesn't crash.

Run: python3 scripts/smoke_test_ice_cot.py
"""
import sys

sys.path.insert(0, ".")

from data.ice_cot import load_ice_cot, load_ice_cot_status, TIDY_COLUMNS

FAILURES = []


def check(condition: bool, message: str) -> None:
    status = "OK " if condition else "FAIL"
    print(f"  [{status}] {message}")
    if not condition:
        FAILURES.append(message)


def main():
    print("=" * 70)
    print("smoke test: data/ice_cot.py")
    print("=" * 70)

    df = load_ice_cot()
    check(not df.empty, f"load_ice_cot() non-empty ({len(df)} rows)")
    check(list(df.columns) == TIDY_COLUMNS, f"columns match TIDY_COLUMNS ({list(df.columns)})")

    id_cols = ["venue", "product_code", "product_name", "report_date",
               "report_status", "category", "side", "scope", "notation_unit"]
    for col in id_cols:
        check(df[col].notna().all(), f"no NaN in {col!r}")

    check(df["nops"].notna().any(), "at least some non-NaN 'nops' values")
    check(set(df["side"].unique()) == {"Long", "Short"}, f"side values = {sorted(df['side'].unique())}")
    check(set(df["scope"].unique()) == {"Risk_Reducing", "Other", "Total"},
          f"scope values = {sorted(df['scope'].unique())}")
    check(df["category"].nunique() == 5, f"exactly 5 categories ({df['category'].nunique()})")

    lo, hi = df["report_date"].min(), df["report_date"].max()
    print(f"  report_date range: {lo} .. {hi}")
    check(lo <= hi, "report_date range is well-formed")

    n_venues = df["venue"].nunique()
    n_products = df.groupby(["venue", "product_code"]).ngroups
    print(f"  {n_venues} venue(s), {n_products} distinct (venue, product) combos")

    status_df = load_ice_cot_status()
    check(list(status_df.columns) == [
        "timestamp", "venue", "report_date", "success", "http_status",
        "rows_fetched", "rows_in_store", "error", "url",
    ], "load_ice_cot_status() has expected columns")
    print(f"  status log: {len(status_df)} attempt(s) recorded")

    print()
    if FAILURES:
        print(f"SMOKE TEST FAILED — {len(FAILURES)} check(s) failed:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
