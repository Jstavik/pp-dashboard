#!/usr/bin/env python3
"""Stáří dat per dataset (pro deploy/status.sh a ruční kontrolu).

    python scripts/data_status.py          # tabulka
    python scripts/data_status.py --json   # strojově
    python scripts/data_status.py --dead   # vypíše jen mrtvé datasety (pro rsync --exclude)

Exit 0 = vše čerstvé, 1 = aspoň jeden dataset starý/chybí.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _fmt_age(h):
    if h is None:
        return "—"
    if h < 1:
        return f"{h * 60:.0f} min"
    if h < 48:
        return f"{h:.1f} h"
    return f"{h / 24:.1f} d"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dead", action="store_true")
    args = ap.parse_args(argv)

    from core.datasets import DEAD, dataset_status, dead_present
    from core.settings import history_root

    if args.dead:
        print("\n".join(DEAD))
        return 0

    rows = dataset_status()
    if args.json:
        print(json.dumps([{**r, "modified": str(r["modified"]), "last_data": str(r["last_data"])}
                          for r in rows], ensure_ascii=False, indent=1, default=str))
    else:
        print(f"Historie: {history_root()}")
        print(f"{'':2} {'dataset':28} {'úloha':18} {'souborů':>7} {'MB':>7}  {'poslední data (Praha)':22} {'stáří':>9}")
        for r in rows:
            last = r["last_data"] if r["last_data"] is not None else r["modified"]
            last_s = last.tz_convert("Europe/Prague").strftime("%Y-%m-%d %H:%M") if last is not None else "—"
            mark = "OK" if r["ok"] else "!!"
            print(f"{mark:2} {r['name']:28} {r['task']:18} {r['files']:7} {r['size_mb']:7.1f}  "
                  f"{last_s:22} {_fmt_age(r['age_h']):>9}")
        dead = dead_present()
        if dead:
            print(f"\nMrtvá data v historii (nikdo je nečte, lze smazat): {', '.join(dead)}")
    return 0 if all(r["ok"] or r["task"] == "-" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
