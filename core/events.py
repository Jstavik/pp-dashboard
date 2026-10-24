"""Události pro anotace grafů (nový vzhled).

Dva zdroje:
- MARKET_EVENTS — ručně udržovaný seznam milníků trhu (doplňovat sem),
- feed Přehledu dne (history/overview.json → „events“): odstávky ENTSO-E
  ≥ 100 MW, GASSCO UMM, cenové výkyvy — posledních 21 dní.
"""
import pandas as pd

# (datum, krátký popisek, témata) — témata: gas | power | nordic
MARKET_EVENTS = [
    ("2022-02-24", "invaze na Ukrajinu", {"gas", "power"}),
    ("2022-08-26", "TTF max. ~340 €/MWh", {"gas", "power"}),
    ("2022-09-26", "Nord Stream poškozen", {"gas"}),
    ("2025-01-01", "konec tranzitu přes UA", {"gas"}),
    ("2025-10-01", "SDAC 15min MTU", {"power", "nordic"}),
]


def market_events(topic: str, since=None) -> list:
    since = pd.Timestamp(since) if since is not None else None
    return [(pd.Timestamp(d), label) for d, label, topics in MARKET_EVENTS
            if topic in topics and (since is None or pd.Timestamp(d) >= since)]


def feed_events(categories=(), limit=6, match=None) -> list:
    """Poslední události z feedu Přehledu dne → [(čas Praha naivně, popisek)]."""
    try:
        from core.overview import load_overview
        ev = load_overview().get("events") or []
    except Exception:
        return []
    out = []
    for e in ev:
        if categories and e.get("cat") not in categories:
            continue
        if match and not match(e):
            continue
        t = pd.Timestamp(e["t"])
        t = t.tz_convert("Europe/Prague").tz_localize(None) if t.tzinfo else t
        out.append((t, (e.get("title") or "")[:40]))
        if len(out) >= limit:
            break
    return out
