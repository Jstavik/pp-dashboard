"""Počasí přes Open-Meteo — vážené body CZ, DE, Nordic.

Datasety (history/):
- weather_daily/<REGION>/<YYYY-MM>.parquet  date, region, var, value, src
    denní pozorování: ERA5 (Open-Meteo archive, zpoždění ~5 dní, src=era5)
    + poslední dny z forecast API past_days (src=recent; ERA5 je pak přepíše).
    var: temp [°C, denní průměr], wind100 [m/s, průměr], solar [kWh/m²/den],
    precip [mm/den]. Backfill od PPDASH_WEATHER_START (default 1991-01-01,
    kvůli normálu 1991–2020) po 2letých kusech s rozpočtem „volání“.
- weather_fc/<REGION>/<YYYY-MM>.parquet  time, region, var, model, value, issued
    hodinová deterministická předpověď (poslední běh přepisuje), modely
    PPDASH_WEATHER_MODELS (ECMWF IFS, AIFS, GFS, ICON).
- weather_fc_daily/<REGION>/<YYYY-MM>.parquet  issued, date, region, var, model, value
    denní agregace každého dne vydání (poslední běh dne) → „změna předpovědi
    proti včerejšku“.
- weather_ens/<REGION>.parquet  time, var, model, p10, p50, p90, mean, n
    ensemble rozptyl (ECMWF ENS 51 členů, GEFS) — jen poslední běh.

Vážení: každá oblast má pro každou veličinu vlastní body s vahami
(teplota ~ obyvatelstvo, vítr ~ instalovaný výkon větru, slunce ~ FVE).
Váhy jsou hrubý odhad (REGIONS níže) — upravit podle potřeby.

Licence: Open-Meteo data CC BY 4.0 („Weather data by Open-Meteo.com“);
FREE API jen pro NEKOMERČNÍ použití (< 10 000 volání/den). Pro komerční
použití OPENMETEO_API_KEY (customer-*.open-meteo.com) nebo vlastní instance
(OPENMETEO_BASE_URL). Viz REBUILD_NOTES (rešerše počasí).
"""
import logging
import os
import re
import time

import numpy as np
import pandas as pd
import requests

from core.settings import env, history_path
from data.partitioned_store import last_date_partitioned, upsert_partitioned

log = logging.getLogger("ppdash.update")

UA = {"User-Agent": "ppdash/1.0 (+energy dashboard)"}
VAR_API = {"temp": "temperature_2m", "wind100": "wind_speed_100m", "solar": "shortwave_radiation",
           "precip": "precipitation"}
VAR_LABELS = {"temp": "Teplota", "wind100": "Vítr 100 m", "solar": "Sluneční záření", "precip": "Srážky"}
VAR_UNITS = {"temp": "°C", "wind100": "m/s", "solar": "kWh/m²/den", "precip": "mm/den"}
VAR_UNITS_H = {"temp": "°C", "wind100": "m/s", "solar": "W/m²", "precip": "mm/h"}

# (lat, lon, váha) — hrubé odhady: teplota ~ obyvatelstvo, vítr/slunce ~ instalovaný výkon
REGIONS = {
    "CZ": {
        "label": "Česko",
        "temp": [(50.08, 14.43, .30), (49.20, 16.61, .17), (49.83, 18.28, .15), (49.75, 13.38, .10),
                 (48.97, 14.47, .08), (50.21, 15.83, .10), (50.66, 14.04, .10)],
        "wind100": [(50.60, 13.60, .35), (50.10, 17.20, .25), (49.50, 15.60, .25), (50.80, 15.10, .15)],
        "solar": [(50.10, 14.40, .30), (49.20, 16.60, .25), (49.00, 14.50, .20), (50.40, 13.40, .25)],
    },
    "DE": {
        "label": "Německo",
        "temp": [(52.52, 13.40, .20), (53.55, 10.00, .12), (48.14, 11.58, .15), (50.94, 6.96, .18),
                 (50.11, 8.68, .15), (48.78, 9.18, .12), (51.34, 12.37, .08)],
        "wind100": [(53.00, 8.00, .25), (54.30, 9.50, .20), (52.50, 13.50, .15), (53.80, 12.00, .15),
                    (51.80, 11.50, .10), (54.50, 6.50, .15)],
        "solar": [(48.80, 11.50, .30), (48.60, 9.00, .22), (51.50, 7.50, .18), (52.40, 13.30, .15),
                  (50.80, 12.50, .15)],
    },
    "NORDIC": {
        "label": "Nordic",
        "temp": [(59.91, 10.75, .25), (59.33, 18.07, .30), (60.17, 24.94, .25), (55.68, 12.57, .20)],
        "wind100": [(56.00, 8.40, .25), (62.50, 17.00, .25), (64.00, 25.50, .20), (58.80, 6.00, .15),
                    (56.30, 14.00, .15)],
        "solar": [(55.70, 12.50, .4), (59.30, 18.00, .3), (60.20, 24.90, .3)],
        "precip": [(60.40, 5.30, .30), (59.50, 8.00, .25), (61.80, 7.50, .25), (63.40, 10.40, .20)],
    },
}

DAILY_DIR = history_path("weather_daily")
FC_DIR = history_path("weather_fc")
FC_DAILY_DIR = history_path("weather_fc_daily")
ENS_DIR = history_path("weather_ens")


def _models() -> list:
    return [m for m in env("PPDASH_WEATHER_MODELS",
                           "ecmwf_ifs025,ecmwf_aifs025_single,gfs_seamless,icon_seamless").split(",") if m]


def _ens_models() -> list:
    return [m for m in env("PPDASH_WEATHER_ENS_MODELS", "ecmwf_ifs025,gfs025").split(",") if m]


def _url(kind: str) -> str:
    """kind ∈ forecast | ensemble | archive. Klíč → customer-* (komerční)."""
    base = env("OPENMETEO_BASE_URL")
    key = env("OPENMETEO_API_KEY")
    path = {"forecast": "/v1/forecast", "ensemble": "/v1/ensemble", "archive": "/v1/archive"}[kind]
    if base:
        return base.rstrip("/") + path
    host = {"forecast": "api.open-meteo.com", "ensemble": "ensemble-api.open-meteo.com",
            "archive": "archive-api.open-meteo.com"}[kind]
    return f"https://{'customer-' if key else ''}{host}{path}"


# ── kvóta free API ──────────────────────────────────────────────────────
# Free tier: < 10 000 „volání“/den, 5 000/h, 600/min. Volání je vážené
# (místa × délka období/14 dní × proměnné/10; počítáme konzervativně i ×
# počet modelů). Spotřeba za den (UTC) se drží v history/.openmeteo_quota.json,
# backfill ERA5 dostane jen zbytek denní kvóty (rozloží se do více běhů/dní).

def _free_tier() -> bool:
    return not (env("OPENMETEO_API_KEY") or env("OPENMETEO_BASE_URL"))


def _quota_path() -> str:
    return os.path.join(os.path.dirname(DAILY_DIR), ".openmeteo_quota.json")


def daily_limit() -> float:
    return float(env("PPDASH_OPENMETEO_DAILY_LIMIT", "9000"))  # rezerva pod 10 000


def _quota_load() -> dict:
    import json
    today = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
    try:
        with open(_quota_path(), encoding="utf-8") as f:
            q = json.load(f)
        if q.get("date") == today:
            return q
    except (OSError, ValueError):
        pass
    return {"date": today, "used": 0.0, "exhausted": False}


def _quota_save(q: dict):
    import json
    path = _quota_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(q, f)
    os.replace(tmp, path)


def quota_left() -> float:
    if not _free_tier():
        return float("inf")
    q = _quota_load()
    return 0.0 if q.get("exhausted") else max(0.0, daily_limit() - float(q.get("used", 0)))


def _quota_add(weight: float, exhausted: bool = False):
    if not _free_tier():
        return
    q = _quota_load()
    q["used"] = round(float(q.get("used", 0)) + weight, 2)
    q["exhausted"] = bool(q.get("exhausted")) or exhausted
    _quota_save(q)


def request_weight(kind: str, params: dict) -> float:
    n = len(str(params.get("latitude", "")).split(","))
    if kind == "archive":
        days = (pd.Timestamp(params["end_date"]) - pd.Timestamp(params["start_date"])).days + 1
    else:
        days = int(params.get("forecast_days", 7)) + int(params.get("past_days", 0))
    n_vars = len(str(params.get("hourly", "")).split(","))
    models = str(params.get("models", ""))
    n_models = 1 if models in ("", "best_match") else len(models.split(","))
    return _call_weight(n, days, n_vars) * n_models


class QuotaExceeded(requests.HTTPError):
    """Denní/hodinová kvóta Open-Meteo — dál se v tomto běhu nevolá."""


def _get(session, kind: str, params: dict) -> list:
    from core.http import retry_after_s
    key = env("OPENMETEO_API_KEY")
    weight = request_weight(kind, params)
    if _free_tier() and quota_left() < weight:
        raise QuotaExceeded(f"denní kvóta Open-Meteo vyčerpána (limit {daily_limit():.0f})")
    if key:
        params = {**params, "apikey": key}
    for attempt in range(4):
        r = session.get(_url(kind), params=params, headers=UA, timeout=120)
        if r.status_code == 429:
            try:
                reason = str(r.json().get("reason", ""))
            except Exception:
                reason = getattr(r, "text", "")[:200]
            low = reason.lower()
            if "daily" in low:
                _quota_add(0, exhausted=True)
                raise QuotaExceeded(f"HTTP 429: {reason}")
            if "hourly" in low:
                raise QuotaExceeded(f"HTTP 429: {reason}")
            # minutový limit (600/min) → Retry-After nebo 60 s, 120 s, …
            wait = retry_after_s(r) or 60 * 2 ** attempt
            log.info("Open-Meteo 429 (%s) — čekám %.0f s", reason or "limit", wait)
            time.sleep(min(wait, 300))
            continue
        if r.status_code in (502, 503, 504):
            time.sleep(min(300, retry_after_s(r) or 10 * 2 ** attempt))
            continue
        if r.status_code >= 400:
            try:
                reason = r.json().get("reason", "")
            except Exception:
                reason = r.text[:200] if hasattr(r, "text") else ""
            raise requests.HTTPError(f"HTTP {r.status_code}: {reason}")
        _quota_add(weight)
        j = r.json()
        return j if isinstance(j, list) else [j]
    raise requests.HTTPError(f"HTTP {r.status_code} (opakovaně)")


def _points(region: str, var: str):
    pts = REGIONS[region].get(var) or []
    lat = ",".join(f"{p[0]:.2f}" for p in pts)
    lon = ",".join(f"{p[1]:.2f}" for p in pts)
    w = np.array([p[2] for p in pts], dtype=float)
    return lat, lon, w / w.sum() if len(w) else w


def _weighted(frames: list, weights) -> pd.Series:
    """Vážený průměr řad z jednotlivých bodů (chybějící bod → převáží se ostatní)."""
    df = pd.concat(frames, axis=1)
    w = np.broadcast_to(np.asarray(weights), df.shape)
    vals = df.to_numpy(dtype=float)
    mask = ~np.isnan(vals)
    num = np.nansum(vals * w, axis=1)
    den = (w * mask).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return pd.Series(np.where(den > 0, num / den, np.nan), index=df.index)


def _split_key(key: str, base: str, models: list):
    """'temperature_2m_member03_ecmwf_ifs025' → (member03, ecmwf_ifs025)."""
    if not key.startswith(base):
        return None
    rest = key[len(base):].lstrip("_")
    m = re.match(r"^(member\d+)?_?(.*)$", rest)
    member, model = m.group(1), m.group(2)
    if not model:
        model = models[0] if len(models) == 1 else "default"
    return member, model


# ── pozorování (denní) ──────────────────────────────────────────────────

def _daily_from_hourly(times, values, var: str) -> pd.Series:
    s = pd.Series(values, index=pd.to_datetime(times, utc=True), dtype=float)
    local = s.tz_convert("Europe/Prague")
    g = local.groupby(local.index.date)
    if var == "precip":
        out = g.sum(min_count=18)
    elif var == "solar":
        out = g.mean() * 24 / 1000  # W/m² průměr → kWh/m²/den
        out[g.count() < 18] = np.nan
    else:
        out = g.mean()
        out[g.count() < 18] = np.nan
    out.index = pd.to_datetime(out.index)
    return out


def _fetch_daily(session, kind: str, region: str, var: str, start, end, past_days: int = None) -> pd.DataFrame:
    lat, lon, w = _points(region, var)
    params = {"latitude": lat, "longitude": lon, "hourly": VAR_API[var], "timezone": "GMT",
              "wind_speed_unit": "ms"}
    if kind == "archive":
        params.update({"start_date": str(start), "end_date": str(end)})
    else:
        params.update({"past_days": past_days or 7, "forecast_days": 1, "models": "best_match"})
    locs = _get(session, kind, params)
    frames = []
    for loc in locs:
        h = loc.get("hourly", {})
        key = next((k for k in h if k.startswith(VAR_API[var])), None)
        if key is None:
            frames.append(pd.Series(dtype=float))
            continue
        frames.append(_daily_from_hourly(h["time"], h[key], var))
    v = _weighted(frames, w).dropna()
    if v.empty:
        return pd.DataFrame(columns=["date", "region", "var", "value", "src"])
    return pd.DataFrame({"date": pd.to_datetime(v.index).tz_localize("Europe/Prague").tz_convert("UTC"),
                         "region": region, "var": var, "value": v.values,
                         "src": "era5" if kind == "archive" else "recent"})


def _call_weight(n_points: int, days: int, n_vars: int = 1) -> float:
    """Open-Meteo počítá „volání“: 1 = ≤10 proměnných, ≤2 týdny, 1 místo."""
    return n_points * max(1.0, days / 14) * max(1.0, n_vars / 10)


def update_weather_history(session=None, budget_calls: float = None) -> dict:
    """ERA5 denní řady (inkrementálně, backfill po 2 letech) + posledních
    7 dní z forecast API (dokud ERA5 nedožene)."""
    s = session or requests.Session()
    budget = float(budget_calls if budget_calls is not None else env("PPDASH_WEATHER_BUDGET_CALLS", "2000"))
    # rezerva na předpověď dalších běhů dne (~350 volání/běh, 4 běhy/den)
    budget = min(budget, quota_left() - float(env("PPDASH_WEATHER_FC_RESERVE", "1100")))
    start0 = pd.Timestamp(env("PPDASH_WEATHER_START", "1991-01-01")).date()
    today = pd.Timestamp.now(tz="Europe/Prague").normalize().date()
    era5_end = today - pd.Timedelta(days=6)
    used, rows, errors, pending, quota_hit = 0.0, 0, 0, 0, False
    for region, cfg in REGIONS.items():
        base = os.path.join(DAILY_DIR, region)
        for var in VAR_API:
            if var not in cfg:
                continue
            last = _last_era5(base, var)
            cur = (last - pd.Timedelta(days=3)) if last is not None else start0
            while cur <= era5_end:
                b = min(cur + pd.Timedelta(days=730), era5_end)
                w = _call_weight(len(cfg[var]), (b - cur).days + 1)
                if quota_hit or used + w > budget:
                    pending += 1
                    break
                try:
                    df = _fetch_daily(s, "archive", region, var, cur, b)
                    used += w
                    if not df.empty:
                        touched = upsert_partitioned(df, base, "date", ["date", "var"], fmt="parquet")
                        rows += sum(n for _, n, wr in touched if wr)
                except QuotaExceeded as e:
                    log.warning("Počasí archiv: %s — backfill pokračuje příští běh", e)
                    quota_hit = True
                    pending += 1
                    break
                except Exception as e:
                    log.warning("Počasí archiv %s/%s %s: %s", region, var, cur, e)
                    errors += 1
                    break
                cur = b + pd.Timedelta(days=1)
                time.sleep(0.5)
            # poslední dny (ERA5 ještě není) — jen dny po konci ERA5
            if quota_hit:
                continue
            try:
                rec = _fetch_daily(s, "forecast", region, var, None, None, past_days=8)
                used += _call_weight(len(cfg[var]), 9)
                last = _last_era5(base, var)
                if last is not None:
                    rec = rec[rec["date"].dt.tz_convert("Europe/Prague").dt.date > last]
                rec = rec[rec["date"].dt.tz_convert("Europe/Prague").dt.date < today]
                if not rec.empty:
                    upsert_partitioned(rec, base, "date", ["date", "var"], fmt="parquet")
            except QuotaExceeded as e:
                log.warning("Počasí recent: %s", e)
                quota_hit = True
            except Exception as e:
                log.warning("Počasí recent %s/%s: %s", region, var, e)
                errors += 1
    log.info("Počasí historie: %d řádků, ~%.0f volání, %d řad čeká na další běh, %d chyb, zbývá kvóty dnes %s",
             rows, used, pending, errors, "∞" if not _free_tier() else f"{quota_left():.0f}")
    if errors and not rows and errors >= sum(len([v for v in VAR_API if v in c]) for c in REGIONS.values()):
        from core.updaters import UpdateError
        raise UpdateError("Počasí: všechna volání selhala")
    return {"rows": rows, "errors": errors, "pending": pending}


def _last_era5(base: str, var: str):
    import glob
    for f in sorted(glob.glob(os.path.join(base, "*.parquet")), reverse=True):
        d = pd.read_parquet(f, columns=["date", "var", "src"])
        d = d[(d["var"] == var) & (d["src"] == "era5")]
        if not d.empty:
            return pd.to_datetime(d["date"], utc=True).max().tz_convert("Europe/Prague").date()
    return None


# ── předpověď ───────────────────────────────────────────────────────────

def parse_forecast(locs: list, var: str, weights, models: list) -> pd.DataFrame:
    """Odpověď forecast API (víc bodů × víc modelů) → time, model, value (vážený průměr)."""
    per_model = {}
    for loc in locs:
        h = loc.get("hourly", {})
        times = pd.to_datetime(h.get("time", []), utc=True)
        for k, vals in h.items():
            if k == "time":
                continue
            sk = _split_key(k, VAR_API[var], models)
            if sk is None or sk[0] is not None:
                continue
            per_model.setdefault(sk[1], []).append(pd.Series(vals, index=times, dtype=float))
    out = []
    for model, frames in per_model.items():
        if len(frames) != len(locs):
            continue
        v = _weighted(frames, weights).dropna()
        out.append(pd.DataFrame({"time": v.index, "model": model, "value": v.values}))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=["time", "model", "value"])


def parse_ensemble(locs: list, var: str, weights, models: list) -> pd.DataFrame:
    """Členové ensemblu → kvantily váženého průměru přes body."""
    members = {}
    for loc_i, loc in enumerate(locs):
        h = loc.get("hourly", {})
        times = pd.to_datetime(h.get("time", []), utc=True)
        for k, vals in h.items():
            if k == "time":
                continue
            sk = _split_key(k, VAR_API[var], models)
            if sk is None:
                continue
            members.setdefault((sk[1], sk[0] or "member00"), {})[loc_i] = pd.Series(vals, index=times, dtype=float)
    by_model = {}
    for (model, _m), locmap in members.items():
        if len(locmap) != len(locs):
            continue
        v = _weighted([locmap[i] for i in range(len(locs))], weights)
        by_model.setdefault(model, []).append(v)
    out = []
    for model, series in by_model.items():
        m = pd.concat(series, axis=1)
        q = m.quantile([0.1, 0.5, 0.9], axis=1).T
        out.append(pd.DataFrame({"time": m.index, "var": var, "model": model, "p10": q[0.1].values,
                                 "p50": q[0.5].values, "p90": q[0.9].values, "mean": m.mean(axis=1).values,
                                 "n": m.notna().sum(axis=1).values}))
    return pd.concat(out, ignore_index=True).dropna(subset=["p50"]) if out else pd.DataFrame()


def _daily_fc(fc: pd.DataFrame, var: str) -> pd.DataFrame:
    local = fc.assign(date=fc["time"].dt.tz_convert("Europe/Prague").dt.date)
    g = local.groupby(["model", "date"])["value"]
    if var == "precip":
        d = g.sum(min_count=18)
    elif var == "solar":
        d = g.mean() * 24 / 1000
    else:
        d = g.mean()
    d = d[g.count() >= 18].rename("value").reset_index()
    d["date"] = pd.to_datetime(d["date"]).dt.tz_localize("Europe/Prague").dt.tz_convert("UTC")
    return d


def update_weather_forecast(session=None) -> dict:
    """Deterministická předpověď (všechny modely v 1 requestu na veličinu
    a oblast) + ensemble. Neznámý model (400) → zkusí modely jednotlivě."""
    s = session or requests.Session()
    models, ens_models = _models(), _ens_models()
    issued = pd.Timestamp.now(tz="UTC").floor("h")
    rows, errors, total = 0, 0, 0
    for region, cfg in REGIONS.items():
        for var in VAR_API:
            if var not in cfg:
                continue
            total += 1
            lat, lon, w = _points(region, var)
            base = {"latitude": lat, "longitude": lon, "hourly": VAR_API[var], "timezone": "GMT",
                    "wind_speed_unit": "ms", "forecast_days": 16}
            try:
                fc = _fetch_models(s, "forecast", base, models, lambda locs, ms: parse_forecast(locs, var, w, ms))
                if not fc.empty:
                    fc = fc.assign(region=region, var=var, issued=issued)
                    touched = upsert_partitioned(fc, os.path.join(FC_DIR, region), "time",
                                                 ["time", "var", "model"], fmt="parquet")
                    rows += sum(n for _, n, wr in touched if wr)
                    daily = _daily_fc(fc, var).assign(region=region, var=var,
                                                      issued=issued.tz_convert("Europe/Prague").normalize()
                                                      .tz_convert("UTC"))
                    upsert_partitioned(daily, os.path.join(FC_DAILY_DIR, region), "issued",
                                       ["issued", "date", "var", "model"], fmt="parquet")
            except QuotaExceeded as e:
                log.warning("Počasí předpověď: %s", e)
                errors += 1
                continue
            except Exception as e:
                log.warning("Počasí předpověď %s/%s: %s", region, var, e)
                errors += 1
            try:
                ens = _fetch_models(s, "ensemble", {**base, "forecast_days": 15}, ens_models,
                                    lambda locs, ms: parse_ensemble(locs, var, w, ms))
                if not ens.empty:
                    _replace_ens(region, var, ens)
            except Exception as e:
                log.warning("Počasí ensemble %s/%s: %s", region, var, e)
            time.sleep(0.3)
    if errors == total:
        from core.updaters import UpdateError
        raise UpdateError("Počasí: předpověď selhala pro všechny oblasti")
    log.info("Počasí předpověď: %d řádků, %d chyb", rows, errors)
    return {"rows": rows, "errors": errors}


def _fetch_models(session, kind, params, models, parse):
    try:
        return parse(_get(session, kind, {**params, "models": ",".join(models)}), models)
    except QuotaExceeded:
        raise
    except requests.HTTPError as e:
        if "400" not in str(e) or len(models) == 1:
            raise
    frames = []
    for m in models:
        try:
            frames.append(parse(_get(session, kind, {**params, "models": m}), [m]))
        except QuotaExceeded:
            raise
        except requests.HTTPError as e:
            log.warning("Open-Meteo %s model %s: %s", kind, m, e)
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _replace_ens(region: str, var: str, ens: pd.DataFrame):
    path = os.path.join(ENS_DIR, f"{region}.parquet")
    os.makedirs(ENS_DIR, exist_ok=True)
    old = pd.read_parquet(path) if os.path.exists(path) else pd.DataFrame()
    if not old.empty:
        old = old[old["var"] != var]
    df = pd.concat([old, ens], ignore_index=True)
    tmp = path + ".tmp"
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def update_weather(session=None) -> dict:
    a = update_weather_forecast(session)
    b = update_weather_history(session)
    return {"rows": a["rows"] + b["rows"], "errors": a["errors"] + b["errors"], "pending": b.get("pending", 0)}


# ── čtení pro web ───────────────────────────────────────────────────────

def load_ens(region: str) -> pd.DataFrame:
    path = os.path.join(ENS_DIR, f"{region}.parquet")
    return pd.read_parquet(path) if os.path.exists(path) else pd.DataFrame()


def hdd(t):
    """Eurostat: HDD = 18 − Tm, když Tm ≤ 15 °C, jinak 0."""
    t = np.asarray(t, dtype=float)
    return np.where(t <= 15, 18 - t, 0.0)


def cdd(t):
    """Eurostat: CDD = Tm − 21, když Tm ≥ 24 °C, jinak 0."""
    t = np.asarray(t, dtype=float)
    return np.where(t >= 24, t - 21, 0.0)


def normals(daily: pd.DataFrame, var: str, years=(1991, 2020)) -> pd.DataFrame:
    """Normál po dnech v roce (1–366): průměr a směrodatná odchylka za roky
    normálového období (když nejsou, všechny roky kromě aktuálního),
    vyhlazený klouzavým oknem ±7 dní (cyklicky)."""
    d = daily[daily["var"] == var]
    if d.empty:
        return pd.DataFrame(columns=["doy", "mean", "std", "years"])
    local = d["date"].dt.tz_convert("Europe/Prague")
    d = d.assign(year=local.dt.year, doy=local.dt.dayofyear)
    sel = d[(d["year"] >= years[0]) & (d["year"] <= years[1])]
    if sel["year"].nunique() < 5:
        sel = d[d["year"] < d["year"].max()]
    label = f"{sel['year'].min()}–{sel['year'].max()}" if not sel.empty else ""
    g = sel.groupby("doy")["value"]
    m = g.mean().reindex(range(1, 367)).interpolate(limit_direction="both")
    sd = g.std().reindex(range(1, 367)).interpolate(limit_direction="both")
    ext_m = pd.concat([m.iloc[-7:], m, m.iloc[:7]]).rolling(15, center=True).mean().iloc[7:-7]
    ext_s = pd.concat([sd.iloc[-7:], sd, sd.iloc[:7]]).rolling(15, center=True).mean().iloc[7:-7]
    return pd.DataFrame({"doy": range(1, 367), "mean": ext_m.values, "std": ext_s.values, "years": label})
