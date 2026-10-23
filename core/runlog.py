"""Záznam výsledků běhů úloh (scripts/update_all.py) — history/.runs.json.

Pro každou úlohu posledních KEEP běhů: čas konce (UTC ISO), stav OK/WARN/FAIL,
trvání, detail (chyba / dílčí chyby / „pokračuje příští běh“). Čte stránka
/stav a deploy/status.sh. Zápis pod zámkem souboru (live běh každou minutu
a dlouhé aktualizace běží souběžně).
"""
import fcntl
import json
import os
import time

from core.settings import history_root

KEEP = 20


def path() -> str:
    return str(history_root() / ".runs.json")


def load() -> dict:
    try:
        with open(path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def record(results: list, now: float = None) -> None:
    """results = [(name, status, secs, detail)]."""
    now = now or time.time()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    p = path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        data = load()
        for name, status, secs, detail in results:
            runs = data.get(name, [])
            runs.append({"t": stamp, "status": status, "secs": round(float(secs), 1), "detail": str(detail)[:500]})
            data[name] = runs[-KEEP:]
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, p)


def summary(data: dict = None) -> list:
    """[{task, last, status, detail, fails, last_ok}] seřazeno: FAIL, WARN, OK."""
    data = load() if data is None else data
    out = []
    for name, runs in data.items():
        if not runs:
            continue
        last = runs[-1]
        last_ok = next((r["t"] for r in reversed(runs) if r["status"] != "FAIL"), None)
        last_err = next((r for r in reversed(runs) if r["status"] != "OK" and r.get("detail")), None)
        out.append({"task": name, "last": last["t"], "status": last["status"], "detail": last.get("detail", ""),
                    "secs": last.get("secs"), "fails": sum(r["status"] == "FAIL" for r in runs),
                    "warns": sum(r["status"] == "WARN" for r in runs), "runs": len(runs), "last_ok": last_ok,
                    "last_error": last_err})
    order = {"FAIL": 0, "WARN": 1, "OK": 2}
    return sorted(out, key=lambda r: (order.get(r["status"], 3), r["task"]))
