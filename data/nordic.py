"""Nordic zdroje zdarma (doplněk k ENTSO-E):

- NVE Magasinstatistikk (NO, bez klíče) — týdenní plnění nádrží za celé
  Norsko a elspot oblasti NO1–NO5 (+ statistika min/medián/max po týdnech):
  history/nve_reservoirs.parquet, history/nve_reservoirs_stats.parquet
- Fingrid open data (FI, free API klíč FINGRID_API_KEY; bez klíče se úloha
  přeskočí): spotřeba, výroba, vítr, jádro, voda, přenosy SE1/SE3/EE/NO,
  prognózy → history/fingrid/<YYYY-MM>.parquet (time, series, value)
- Energinet Energi Data Service (DK, bez klíče): hodinová výroba/spotřeba/
  výměny po oblastech DK1/DK2 a prognózy OZE → history/energinet/<dataset>/
  (time, area, series, value) — sloupce se melt-ují obecně, takže změna
  schématu datasetu nic nerozbije (jen přibudou/zmizí série).

Švédsko: volně strojově čitelná data o nádržích nejsou (Energiföretagen
publikuje týdenní PDF/Excel „Kraftläget“, Nord Pool hydro je placený) — SE
nádrže bere web z ENTSO-E 16.1.D (history/hydro_reservoirs, úloha hydro).

Vše inkrementálně, upsert s dedupem. Formáty API NEOVĚŘENY živě (síť ke
zdrojům v prostředí vývoje nebyla) — parsování je proto tolerantní.
"""
import logging
import os
import time

import pandas as pd
import requests

from core.http import RateLimited, get_with_backoff
from core.settings import env, history_path
from data.partitioned_store import last_date_partitioned, upsert_partitioned

log = logging.getLogger("ppdash.update")

TZ = "Europe/Prague"
UA = {"User-Agent": "ppdash/1.0 (+energy dashboard)"}

# ── NVE ─────────────────────────────────────────────────────────────────

NVE_BASE = "https://biapi.nve.no/magasinstatistikk/api/Magasinstatistikk"
NVE_PATH = history_path("nve_reservoirs.parquet")
NVE_STATS_PATH = history_path("nve_reservoirs_stats.parquet")


def _lower(rec: dict) -> dict:
    return {str(k).lower(): v for k, v in rec.items()}


def _nve_area(omr_type, omr_nr) -> str:
    t = str(omr_type or "").upper()
    if t == "NO":
        return "NO"
    if t == "EL":
        return f"NO{int(omr_nr)}"
    return f"{t}{omr_nr}"


def parse_nve(records: list) -> pd.DataFrame:
    """HentOffentligData → date, area, iso_year, iso_week, filling_pct,
    filling_twh, capacity_twh (jen celé Norsko a elspot oblasti NO1–5)."""
    rows = []
    for r in records or []:
        r = _lower(r)
        t = str(r.get("omrtype", "")).upper()
        if t not in ("NO", "EL"):
            continue
        try:
            fill = float(r.get("fyllingsgrad"))
        except (TypeError, ValueError):
            continue
        rows.append({
            "date": pd.Timestamp(r.get("dato_id")).tz_localize(TZ) if r.get("dato_id") else pd.NaT,
            "area": _nve_area(t, r.get("omrnr")),
            "iso_year": int(r.get("iso_aar")), "iso_week": int(r.get("iso_uke")),
            "filling_pct": fill * 100 if fill <= 1.5 else fill,
            "filling_twh": pd.to_numeric(r.get("fylling_twh"), errors="coerce"),
            "capacity_twh": pd.to_numeric(r.get("kapasitet_twh"), errors="coerce"),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], utc=True)
    return df


def parse_nve_stats(records: list) -> pd.DataFrame:
    """HentOffentligDataMinMaxMedian → area, iso_week, min/median/max (%, TWh)."""
    rows = []
    for r in records or []:
        r = _lower(r)
        t = str(r.get("omrtype", "")).upper()
        if t not in ("NO", "EL"):
            continue

        def pct(k):
            v = pd.to_numeric(r.get(k), errors="coerce")
            return v * 100 if pd.notna(v) and v <= 1.5 else v
        rows.append({"area": _nve_area(t, r.get("omrnr")), "iso_week": int(r.get("iso_uke")),
                     "min_pct": pct("minfyllingsgrad"), "median_pct": pct("medianfyllingsgrad"),
                     "max_pct": pct("maxfyllingsgrad"),
                     "min_twh": pd.to_numeric(r.get("minfyllingtwh"), errors="coerce"),
                     "median_twh": pd.to_numeric(r.get("medianfylling_twh", r.get("medianfyllingtwh")),
                                                 errors="coerce"),
                     "max_twh": pd.to_numeric(r.get("maxfyllingtwh"), errors="coerce")})
    return pd.DataFrame(rows)


def _write_if_changed(df: pd.DataFrame, path: str, keys: list) -> bool:
    df = df.drop_duplicates(keys, keep="last").sort_values(keys).reset_index(drop=True)
    if os.path.exists(path):
        old = pd.read_parquet(path)
        merged = pd.concat([old, df], ignore_index=True).drop_duplicates(keys, keep="last")
        merged = merged.sort_values(keys).reset_index(drop=True)
        if merged.equals(old.sort_values(keys).reset_index(drop=True)):
            return False
        df = merged
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)
    return True


def update_nve(session=None) -> dict:
    """Celá historie je jeden request (~několik MB JSON) — týdenní data,
    publikace ve středu. Zapisuje se jen při změně."""
    from core.updaters import UpdateError
    s = session or requests.Session()
    try:
        r = s.get(f"{NVE_BASE}/HentOffentligData", headers=UA, timeout=60)
        r.raise_for_status()
        df = parse_nve(r.json())
    except Exception as e:
        raise UpdateError(f"NVE: {type(e).__name__}: {e}")
    if df.empty:
        raise UpdateError("NVE: odpověď bez dat NO/EL")
    written = _write_if_changed(df, NVE_PATH, ["area", "iso_year", "iso_week"])
    errors = 0
    try:
        r = s.get(f"{NVE_BASE}/HentOffentligDataMinMaxMedian", headers=UA, timeout=60)
        r.raise_for_status()
        st = parse_nve_stats(r.json())
        if not st.empty:
            _write_if_changed(st, NVE_STATS_PATH, ["area", "iso_week"])
    except Exception as e:
        log.warning("NVE min/max/medián: %s", e)
        errors = 1
    log.info("NVE: %d řádků (%s), poslední týden %s", len(df), "zapsáno" if written else "beze změny",
             df["date"].max().date())
    return {"rows": len(df), "errors": errors}


def load_nve() -> pd.DataFrame:
    return pd.read_parquet(NVE_PATH) if os.path.exists(NVE_PATH) else pd.DataFrame()


def load_nve_stats() -> pd.DataFrame:
    return pd.read_parquet(NVE_STATS_PATH) if os.path.exists(NVE_STATS_PATH) else pd.DataFrame()


# ── Fingrid ─────────────────────────────────────────────────────────────

FINGRID_BASE = "https://data.fingrid.fi/api/datasets/{id}/data"
FINGRID_DIR = history_path("fingrid")
# id datasetu → název série (id ověřit v kontejneru, viz REBUILD_NOTES)
FINGRID_DATASETS = {
    124: "spotřeba",
    74: "výroba",
    75: "vítr",
    188: "jádro",
    191: "voda",
    165: "prognóza spotřeby",
    245: "prognóza větru",
    248: "prognóza slunce",
    87: "přenos FI–SE1",
    89: "přenos FI–SE3",
    180: "přenos FI–EE",
    187: "přenos FI–NO",
}


def fingrid_api_key():
    return env("FINGRID_API_KEY")


def _fingrid_fetch(session, ds_id: int, start: pd.Timestamp, end: pd.Timestamp, key: str) -> pd.DataFrame:
    rows, page = [], 1
    while True:
        params = {"startTime": start.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "endTime": end.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "format": "json", "pageSize": 20000, "page": page, "sortBy": "startTime", "sortOrder": "asc"}
        # limit ~10 req/min na klíč: 429 → Retry-After / backoff 15, 30, 60, 120 s
        r = get_with_backoff(session, FINGRID_BASE.format(id=ds_id), params=params,
                             headers={**UA, "x-api-key": key}, timeout=60, tries=5, base_s=15,
                             cap_s=300, max_wait_s=900, sleep=time.sleep, label=f"Fingrid {ds_id}")
        r.raise_for_status()
        j = r.json()
        data = j.get("data", []) if isinstance(j, dict) else j
        rows.extend(data)
        pag = j.get("pagination", {}) if isinstance(j, dict) else {}
        last = pag.get("lastPage") or 1
        if page >= last or not data:
            break
        page += 1
        time.sleep(6.5)
    if not rows:
        return pd.DataFrame(columns=["time", "series", "value"])
    df = pd.DataFrame(rows)
    out = pd.DataFrame({"time": pd.to_datetime(df["startTime"], utc=True), "series": FINGRID_DATASETS[ds_id],
                        "value": pd.to_numeric(df["value"], errors="coerce")})
    return out.dropna(subset=["value"])


def _last_series_time(base_dir: str, series: str, col="series"):
    import glob
    for f in sorted(glob.glob(os.path.join(base_dir, "*.parquet")), reverse=True):
        d = pd.read_parquet(f, columns=["time", col])
        d = d[d[col] == series]
        if not d.empty:
            return pd.to_datetime(d["time"], utc=True).max()
    return None


def update_fingrid(session=None, budget_s: float = None) -> dict:
    """Bez FINGRID_API_KEY se přeskočí (není chyba). Chunky po 90 dnech,
    ≥ 6.5 s mezi requesty (limit ~10/min), časový rozpočet."""
    key = fingrid_api_key()
    if not key:
        log.info("Fingrid: FINGRID_API_KEY není nastavený — přeskakuji")
        return {"skipped": True, "errors": 0}
    s = session or requests.Session()
    budget = float(budget_s if budget_s is not None else env("PPDASH_FINGRID_BUDGET_S", "900"))
    deadline = time.monotonic() + budget
    start0 = pd.Timestamp(env("PPDASH_NORDIC_START", "2019-01-01"), tz=TZ)
    now = pd.Timestamp.now(tz=TZ)
    rows, errors, pending = 0, 0, 0
    for ds_id, name in FINGRID_DATASETS.items():
        last = _last_series_time(FINGRID_DIR, name)
        cur = max(start0, last.tz_convert(TZ) - pd.Timedelta(days=2)) if last is not None else start0
        end = now + (pd.Timedelta(days=2) if "prognóza" in name else pd.Timedelta(0))
        while cur < end and time.monotonic() < deadline:
            b = min(cur + pd.Timedelta(days=90), end)
            try:
                df = _fingrid_fetch(s, ds_id, cur, b, key)
                if not df.empty:
                    touched = upsert_partitioned(df, FINGRID_DIR, "time", ["time", "series"], fmt="parquet")
                    rows += sum(n for _, n, w in touched if w)
            except RateLimited as e:
                log.warning("Fingrid %s (%s) %s: %s — pokračuje příští běh", ds_id, name, cur.date(), e)
                break
            except Exception as e:
                log.warning("Fingrid %s (%s) %s: %s", ds_id, name, cur.date(), e)
                errors += 1
                cur = None
                break
            cur = b
            time.sleep(6.5)
        if cur is not None and cur < end:  # 429 nebo vypršel rozpočet → navazuje příští běh
            pending += 1
    if errors == len(FINGRID_DATASETS):
        from core.updaters import UpdateError
        raise UpdateError("Fingrid: všechny datasety selhaly")
    return {"rows": rows, "errors": errors, "pending": pending}


# ── Energinet ───────────────────────────────────────────────────────────

ENERGINET_BASE = "https://api.energidataservice.dk/dataset/{name}"
ENERGINET_DIR = history_path("energinet")
ENERGINET_DATASETS = ["ProductionConsumptionSettlement", "Forecasts_Hour"]
_TIME_COLS = ("TimeUTC", "HourUTC", "Minutes15UTC", "Minutes5UTC")
_SKIP_COLS = {"HourDK", "TimeDK", "Minutes15DK", "Minutes5DK"}


def melt_energinet(records: list) -> pd.DataFrame:
    """Obecný převod záznamů Energi Data Service → time, area, series, value.
    Časový sloupec = první z *_UTC, oblast = PriceArea, ostatní textové
    sloupce (např. ForecastType) se přidají do názvu série."""
    cols = ["time", "area", "series", "value"]
    if not records:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(records)
    tcol = next((c for c in _TIME_COLS if c in df.columns), None)
    if tcol is None:
        return pd.DataFrame(columns=cols)
    df = df.drop(columns=[c for c in _SKIP_COLS if c in df.columns])
    area = df["PriceArea"] if "PriceArea" in df.columns else pd.Series("DK", index=df.index)
    text_cols = [c for c in df.columns if c not in (tcol, "PriceArea") and df[c].dtype == object
                 and df[c].notna().any() and pd.to_numeric(df[c], errors="coerce").isna().all()]
    prefix = df[text_cols].astype(str).agg("|".join, axis=1) if text_cols else None
    num_cols = [c for c in df.columns if c not in (tcol, "PriceArea", *text_cols)]
    out = []
    for c in num_cols:
        v = pd.to_numeric(df[c], errors="coerce")
        series = (prefix + "|" + c) if prefix is not None else pd.Series(c, index=df.index)
        out.append(pd.DataFrame({"time": pd.to_datetime(df[tcol], utc=True), "area": area.astype(str),
                                 "series": series, "value": v}))
    res = pd.concat(out, ignore_index=True).dropna(subset=["value"])
    return res[cols]


def update_energinet(session=None, budget_s: float = None, sleep=None) -> dict:
    """Energi Data Service: max **1 dotaz za minutu na IP a dataset**
    (energidataservice.dk/support-service; jinak 429 „Try again in X
    seconds“, při opakování blokace IP). Proto: datasety se střídají
    (každý má vlastní minutový rozestup PPDASH_ENERGINET_INTERVAL_S=65),
    backfill po velkých oknech (PPDASH_ENERGINET_WINDOW_DAYS=92 — 1 dotaz
    vrátí celé okno, limit=0), každé okno se hned zapíše, 429 respektuje
    Retry-After; když server chce čekat déle, dataset se odloží na další běh
    (pokračuje od posledního uloženého). 429 není chyba běhu."""
    from core.http import Pacer, RateLimited, get_with_backoff
    sleep = sleep or time.sleep
    s = session or requests.Session()
    budget = float(budget_s if budget_s is not None else env("PPDASH_ENERGINET_BUDGET_S", "900"))
    interval = float(env("PPDASH_ENERGINET_INTERVAL_S", "65"))
    window = pd.Timedelta(days=int(env("PPDASH_ENERGINET_WINDOW_DAYS", "92")))
    deadline = time.monotonic() + budget
    start0 = pd.Timestamp(env("PPDASH_NORDIC_START", "2019-01-01"), tz="UTC")
    now = pd.Timestamp.now(tz="UTC").ceil("h")
    pacer = Pacer(interval, sleep=sleep)
    state = {}
    for name in ENERGINET_DATASETS:
        base = os.path.join(ENERGINET_DIR, name)
        last = last_date_partitioned(base, "time", "parquet")
        cur = max(start0, last - pd.Timedelta(days=3)) if last is not None else start0
        end = now + (pd.Timedelta(days=2) if "Forecast" in name else pd.Timedelta(0))
        state[name] = {"base": base, "cur": cur, "end": end, "status": "run"}
    rows, errors, pending, requests_n = 0, 0, 0, 0
    while True:
        active = [n for n, st in state.items() if st["status"] == "run" and st["cur"] < st["end"]]
        if not active:
            break
        name = min(active, key=pacer.ready_in)
        st = state[name]
        if time.monotonic() + pacer.ready_in(name) > deadline:
            break
        pacer.wait(name)
        cur, b = st["cur"], min(st["cur"] + window, st["end"])
        try:
            r = get_with_backoff(s, ENERGINET_BASE.format(name=name), headers=UA, timeout=180,
                                 params={"start": cur.strftime("%Y-%m-%dT%H:%M"),
                                         "end": b.strftime("%Y-%m-%dT%H:%M"), "timezone": "utc", "limit": 0},
                                 tries=3, base_s=interval, cap_s=300, max_wait_s=600, sleep=sleep,
                                 label=f"Energinet {name}")
            requests_n += 1
            if r.status_code == 403:
                raise RuntimeError("HTTP 403 — IP možná zablokovaná kvůli počtu dotazů (počkat, kontaktovat Energinet)")
            r.raise_for_status()
            df = melt_energinet(r.json().get("records", []))
            if not df.empty:
                touched = upsert_partitioned(df, st["base"], "time", ["time", "area", "series"], fmt="parquet")
                rows += sum(n for _, n, w in touched if w)
            st["cur"] = b
        except RateLimited as e:
            log.warning("Energinet %s %s: %s — pokračuje příští běh", name, cur.date(), e)
            st["status"] = "pending"
        except Exception as e:
            log.warning("Energinet %s %s: %s", name, cur.date(), e)
            st["status"] = "error"
            errors += 1
    for st in state.values():
        if st["status"] == "run" and st["cur"] < st["end"]:
            st["status"] = "pending"
        if st["status"] == "pending":
            pending += 1
    log.info("Energinet: %d řádků, %d dotazů, %d datasetů pokračuje příští běh, %d chyb",
             rows, requests_n, pending, errors)
    if errors == len(ENERGINET_DATASETS):
        from core.updaters import UpdateError
        raise UpdateError("Energinet: všechny datasety selhaly")
    return {"rows": rows, "errors": errors, "pending": pending}
