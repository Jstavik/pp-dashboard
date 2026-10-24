"""Stránka /stav — stáří dat, poslední běhy úloh a jejich chyby (pro uživatele:
„vidím, že všechno běží“). Výpočet stáří dat čte konce datasetů → cache 2 min."""
import json
import os
import time

import pandas as pd

from web.util import fmt_value

TZ = "Europe/Prague"
_cache = {"t": 0.0, "rows": None}

TASK_LABELS = {
    "ceps": "ČEPS (živě)", "gassco_realtime": "GASSCO realtime", "gassco_umm": "GASSCO UMM",
    "entsog": "ENTSO-G toky", "entsog_allocation": "ENTSO-G alokace", "entsog_operational": "ENTSO-G operational",
    "gie": "GIE zásobníky", "lng": "GIE LNG", "gassco": "GASSCO nominace", "hydro": "Hydro ENTSO-E",
    "dap_europe": "DAP Evropa", "generation": "Výroba ENTSO-E", "outages": "Odstávky ENTSO-E",
    "energy_charts": "Energy-Charts", "cot": "ICE CoT", "nve": "NVE Norsko", "fingrid": "Fingrid",
    "energinet": "Energinet", "weather": "Počasí Open-Meteo", "entsoe_prices": "ENTSO-E ceny",
    "entsoe_load": "ENTSO-E spotřeba", "entsoe_ws": "ENTSO-E prognóza OZE", "entsoe_flows": "ENTSO-E toky",
    "maintenance": "Údržba historie",
}
_sizes = {"t": 0.0, "g": None}


def _mb(b):
    return "—" if b is None else fmt_value(b / 2**20, ",.1f")


def _growth():
    from core.maintenance import growth
    if _sizes["g"] is None or time.time() - _sizes["t"] > 600:
        _sizes["g"], _sizes["t"] = growth(), time.time()
    return _sizes["g"]


def maintenance_context() -> dict:
    from core.maintenance import POLICY, last_maintenance
    m = last_maintenance()
    g = _growth()
    total = sum(v["bytes"] for v in g.values())
    per_year = [v["per_year"] for v in g.values() if v["per_year"] is not None]
    out = {"total_mb": _mb(total), "files": fmt_value(sum(v["files"] for v in g.values()), ",.0f"),
           "per_year_mb": _mb(sum(per_year)) if per_year else None,
           "keep_days": next(iter(POLICY["snapshots"].values())), "last": None}
    if m:
        out["last"] = {"t": _local(m["t"]), "secs": fmt_value(m.get("duration_s"), ",.0f"),
                       "files": f"{fmt_value(m.get('files_before'), ',.0f')} → {fmt_value(m.get('files_after'), ',.0f')}",
                       "mb": f"{_mb(m.get('bytes_before'))} → {_mb(m.get('bytes_after'))}",
                       "errors": m.get("errors", 0), "skipped": m.get("skipped", 0),
                       "actions": [f"{a['dataset']}: {a['action']}" +
                                   (f" — {a['skipped']}" if a.get("skipped") else "") +
                                   (f" — {len(a['errors'])} chyb: {a['errors'][0]}" if a.get("errors") else "")
                                   for a in m.get("actions", [])]}
    return out


def _age(h):
    if h is None:
        return "—"
    if h < 1:
        return f"{h * 60:.0f} min"
    if h < 48:
        return f"{h:.1f} h".replace(".", ",")
    return f"{h / 24:.1f} dne".replace(".", ",")


def _local(ts):
    if ts is None or (isinstance(ts, float) and pd.isna(ts)):
        return "—"
    return pd.Timestamp(ts).tz_convert(TZ).strftime("%d.%m.%Y %H:%M")


def context() -> dict:
    from core.datasets import dataset_status
    from core.runlog import summary
    from core.settings import history_root
    if _cache["rows"] is None or time.time() - _cache["t"] > 120:
        _cache["rows"], _cache["t"] = dataset_status(), time.time()
    from core.datasets import task_schedule
    from core.jobs import LIVE
    try:
        growth_by = _growth()
    except Exception:
        growth_by = {}
    ds = []
    for r in _cache["rows"]:
        if r["task"] == "-":
            continue
        last = r["last_data"] if r["last_data"] is not None else r["modified"]
        gr = growth_by.get(r["name"], {})
        ds.append({"name": r["name"], "task": TASK_LABELS.get(r["task"], r["task"]), "ok": r["ok"],
                   "task_key": r["task"] if r["task"] not in LIVE else None,
                   "schedule": task_schedule(r["task"]),
                   "missing": r["files"] == 0, "last": _local(last), "age": _age(r["age_h"]),
                   "limit": _age(r["max_age_h"]) if r["max_age_h"] != float("inf") else "—",
                   "size": _mb(gr.get("bytes")) if gr else fmt_value(r["size_mb"], ",.1f"),
                   "nfiles": fmt_value(gr.get("files"), ",.0f") if gr else str(r["files"]),
                   "growth": _mb(gr["per_year"]) if gr.get("per_year") is not None else "—"})
    runs = []
    for r in summary():
        err = r.get("last_error")
        runs.append({**r, "label": TASK_LABELS.get(r["task"], r["task"]), "last_local": _local(r["last"]),
                     "secs_txt": fmt_value(r["secs"], ",.0f") if r.get("secs") is not None else "—",
                     "err_txt": f"dřívější chyba {_local(err['t'])}: {err['detail']}" if err and err["t"] != r["last"] else ""})
    quota = None
    qp = history_root() / ".openmeteo_quota.json"
    if os.path.exists(qp):
        try:
            quota = json.loads(qp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            quota = None
    ov = {}
    try:
        from core.overview import load_overview
        o = load_overview()
        ov = {"built": _local(o.get("built")) if o.get("built") else "—",
              "full": _local(o.get("full_built")) if o.get("full_built") else "—", "errors": o.get("errors", [])}
    except Exception:
        pass
    n_bad = sum(1 for d in ds if not d["ok"])
    n_fail = sum(1 for r in runs if r["status"] == "FAIL")
    try:
        maint = maintenance_context()
    except Exception as e:
        maint = {"error": str(e)}
    return {"datasets": sorted(ds, key=lambda d: (d["ok"], d["name"])), "runs": runs, "quota": quota, "ov": ov,
            "maint": maint,
            "n_bad": n_bad, "n_ok": len(ds) - n_bad, "n_fail": n_fail,
            "state": "bad" if n_fail else ("warn" if n_bad else "ok"),
            "now": pd.Timestamp.now(tz=TZ).strftime("%d.%m.%Y %H:%M")}
