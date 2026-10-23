"""Zpětně kompatibilní obal pro .github/workflows/update_ice_cot.yml (na
main) — logika je v core/updaters.py::update_cot, v provozu ji spouští
scripts/update_all.py --only cot (systemd timer so/út).

Computes the most recent Friday relative to today, fetches IFEU + NDEX
for that date, never raises on a per-venue fetch failure (logged to
ice_cot_status.json), writes report_date/<venue>_status to GITHUB_OUTPUT
when running in GitHub Actions.

Run: python3 scripts/update_ice_cot.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.updaters import last_friday, update_cot  # noqa: E402,F401


def _write_github_output(data: dict) -> None:
    """No-op outside GitHub Actions (GITHUB_OUTPUT unset)."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        for k, v in data.items():
            f.write(f"{k}={v}\n")


def main() -> int:
    from data.ice_cot import VENUES
    try:
        res = update_cot()
    except Exception as e:  # obě venues selhaly — dřív taky exit 0 (status v JSON)
        print(f"ICE CoT: {e}")
        res = {"report_date": "", "venues": {v: "FAILED" for v in VENUES}}
    _write_github_output({"report_date": res.get("report_date", ""),
                          **{f"{v.lower()}_status": s for v, s in res.get("venues", {}).items()}})
    return 0


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        sys.exit(main())
    except Exception as e:
        print(f"CHYBA (neočekávaná): {type(e).__name__}: {e}")
        sys.exit(1)
