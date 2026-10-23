"""Úvodní stránka „Přehled dne“ — jen čte předpočítaný history/overview.json
(core/overview.py, přepočítává scripts/update_all.py po každém běhu)."""
import logging
import os
import threading

import pandas as pd

from config import sparkline_svg
from web.util import fmt_value

log = logging.getLogger("ppdash.web")
TZ = "Europe/Prague"
_lock = threading.Lock()
_cache = {"mtime": None, "data": None}

GROUP_COLORS = {"power": "#f59e0b", "gas": "#1565c0", "weather": "#0891b2", "nordic": "#0d9488", "market": "#7c3aed"}


def load() -> dict:
    """overview.json s cache podle mtime; když chybí (první start), spočítá
    se jednou synchronně (pak už ho udržují aktualizace)."""
    from core.overview import build_overview, load_overview, overview_path
    path = overview_path()
    with _lock:
        try:
            mt = (path, os.stat(path).st_mtime_ns)
        except OSError:
            mt = None
        if mt is None:
            try:
                build_overview()
                mt = (path, os.stat(path).st_mtime_ns)
            except Exception as e:
                log.warning("přehled dne nelze spočítat: %s", e)
                return {}
        if mt != _cache["mtime"]:
            _cache["data"], _cache["mtime"] = load_overview(), mt
        return _cache["data"] or {}


def _when(iso: str, now: pd.Timestamp) -> str:
    t = pd.Timestamp(iso).tz_convert(TZ)
    if t.date() == now.date():
        return f"dnes {t:%H:%M}"
    if t.date() == (now - pd.Timedelta(days=1)).date():
        return f"včera {t:%H:%M}"
    return f"{t:%d.%m.} {t:%H:%M}"


def _delta(v, fmt, unit):
    if v is None:
        return None
    cls = "flat" if abs(v) < 1e-9 else ("up" if v > 0 else "down")
    return {"text": f"{fmt_value(v, fmt)} {unit}".strip(), "cls": cls}


def context(limit_events: int = 80) -> dict:
    from core.overview import GROUPS
    data = load()
    now = pd.Timestamp.now(tz=TZ)
    groups = []
    tiles = data.get("tiles", [])
    for gkey, gtitle in GROUPS:
        items = []
        for t in (x for x in tiles if x["group"] == gkey):
            col = GROUP_COLORS.get(gkey, "#1565c0")
            items.append({**t, "value_txt": fmt_value(t["value"], t["fmt"]) if t["value"] is not None else "—",
                          "dd_obj": _delta(t["dd"], t["dfmt"], t["dd_unit"]),
                          "ww_obj": _delta(t["ww"], t["dfmt"], t["dd_unit"]),
                          "spark_svg": sparkline_svg(t["spark"], col) if t.get("spark") else "", "color": col})
        if items:
            groups.append({"key": gkey, "title": gtitle, "tiles": items})
    # malé skupiny (≤ 2 dlaždice) za sebou sloučit do jedné řady
    merged = []
    for g in groups:
        if merged and len(g["tiles"]) <= 2 and merged[-1].get("small") and len(merged[-1]["tiles"]) < 4:
            merged[-1]["title"] += " · " + g["title"]
            merged[-1]["tiles"] += g["tiles"]
        else:
            merged.append({**g, "small": len(g["tiles"]) <= 2})
    groups = merged
    events = [{**e, "when": _when(e["t"], now)} for e in data.get("events", [])[:limit_events]]
    cats = sorted({e["cat"] for e in events})
    built = pd.Timestamp(data["built"]).tz_convert(TZ) if data.get("built") else None
    full = pd.Timestamp(data["full_built"]).tz_convert(TZ) if data.get("full_built") else None
    return {"groups": groups, "events": events, "cats": cats, "errors": data.get("errors", []),
            "built": built.strftime("%d.%m.%Y %H:%M") if built is not None else None,
            "full_built": full.strftime("%d.%m.%Y %H:%M") if full is not None else None,
            "today": f"{['po', 'út', 'st', 'čt', 'pá', 'so', 'ne'][now.weekday()]} {now:%d.%m.%Y}"}
