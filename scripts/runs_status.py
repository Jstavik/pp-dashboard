#!/usr/bin/env python3
"""Souhrn posledních běhů úloh (history/.runs.json, zapisuje update_all.py).

    python scripts/runs_status.py          # tabulka: poslední stav, chyby za posledních N běhů
    python scripts/runs_status.py --errors # navíc text poslední chyby každé úlohy

Exit 0 = poslední běh žádné úlohy neskončil FAIL, 1 = aspoň jedna úloha FAIL.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--errors", action="store_true")
    args = ap.parse_args(argv)
    import pandas as pd
    from core.runlog import summary
    rows = summary()
    if not rows:
        print("Zatím žádný zaznamenaný běh (history/.runs.json vzniká po prvním běhu update_all.py).")
        return 0
    print(f"{'':2} {'úloha':20} {'poslední běh (Praha)':20} {'stav':5} {'trvání':>8}  {'FAIL/WARN z běhů':16} detail")
    for r in rows:
        t = pd.Timestamp(r["last"]).tz_convert("Europe/Prague").strftime("%Y-%m-%d %H:%M")
        mark = "!!" if r["status"] == "FAIL" else ("~" if r["status"] == "WARN" else "OK")
        stat = f"{r['fails']}/{r['warns']} z {r['runs']}"
        print(f"{mark:2} {r['task']:20} {t:20} {r['status']:5} {r['secs'] or 0:7.0f}s  {stat:16} {r['detail'][:90]}")
        if args.errors and r["last_error"] and r["status"] != "OK":
            e = r["last_error"]
            et = pd.Timestamp(e["t"]).tz_convert("Europe/Prague").strftime("%d.%m. %H:%M")
            print(f"     poslední chyba {et}: {e['detail'][:300]}")
    return 1 if any(r["status"] == "FAIL" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
