"""Aktualizace historie — jednotlivé úlohy pro scripts/update_all.py.

Logika přenesená beze změny chování ze scripts/update_gas_history.py
(ENTSO-G toky/Allocation, GIE, hydro, DAP Europe, výroba, odstávky)
a scripts/update_ice_cot.py (ICE CoT). Ostatní úlohy žijí u svých dat
(data/entsog_operational.py, data/lng.py, data/gassco.py,
data/energy_charts.py) a tady se jen registrují.

Konvence pro update_all.py:
- úloha při úplném selhání (nic se nestáhlo kvůli chybě) VYHODÍ výjimku,
- částečné selhání (např. jedna země z 20) zaloguje varování a vrátí
  slovník s klíčem "errors" (počet) — update_all ho vyhodnotí jako WARN,
- "žádná nová data" není chyba (data už jsou aktuální),
- všechny zápisy jsou idempotentní (upsert s dedupem / write-once
  snapshoty), opakované spuštění nic nerozbije.
"""
import logging
import os
import time
from datetime import date, timedelta

import pandas as pd
import requests

from config import (
    ENTSOE_OUTAGE_REVISION_WINDOW_DAYS,
    ENTSOE_OUTAGE_FORWARD_WINDOW_DAYS, ENTSOE_OUTAGE_FETCH_RETRIES,
    ENTSOE_REQUEST_TIMEOUT_S,
    GENERATION_CHUNK_DAYS, GENERATION_CHUNK_RETRIES,
    PSR_CODE_BY_SOURCE_TYPE, COUNTRY_TIMEZONES,
    GIE_ALSI_REVISION_WINDOW_DAYS,
)
from core.settings import entsoe_token, gie_api_key, history_path, history_root
from data.partitioned_store import upsert_partitioned, last_date_partitioned, write_daily_snapshot

log = logging.getLogger("ppdash.update")

# ENTSO-G aggregateddata API sahá do 2020, dříve není dostupné
HISTORY_START = date(2020, 1, 1)

ENTSOG_FLOWS_DIR = history_path("entsog_flows")
GIE_ALL_DIR = history_path("gie_all_storage")
HYDRO_DIR = history_path("hydro_reservoirs")
DAP_DIR = history_path("dap_europe")
OUTAGES_PATH = history_path("outages.parquet")
OUTAGES_SNAPSHOTS_DIR = history_path("outages_snapshots")

COUNTRIES_GIE = ["AT", "BE", "CZ", "DE", "FR", "HR", "HU", "IT", "LV", "NL", "PL", "PT", "RO", "RS", "SK", "ES", "UA"]
HYDRO_COUNTRIES = [
    "FR", "AT", "CH", "ES", "PT", "IT", "NO", "SE", "FI",
    "RO", "BG", "GR", "HR", "SI", "RS", "ME", "MK", "AL", "LT", "LV",
]

KEEP_COLS = [
    "periodFrom", "indicator", "countryKey", "countryLabel",
    "directionKey", "adjacentSystemsKey", "adjacentSystemsLabel",
    "pointsNames", "value", "unit", "flowStatus",
]
ENTSOG_KEY_COLS = ["date", "indicator", "countryKey", "directionKey",
                   "adjacentSystemsKey", "pointsNames"]


class UpdateError(RuntimeError):
    """Úloha selhala úplně (nic nezapsala kvůli chybě)."""


def _require_entsoe_token() -> str:
    token = entsoe_token()
    if not token:
        raise UpdateError("ENTSOE_TOKEN není nastavený (env / ~/.config/ppdash/env)")
    return token


def _entsoe_client(timeout: bool = False):
    from entsoe import EntsoePandasClient
    kwargs = {"timeout": ENTSOE_REQUEST_TIMEOUT_S} if timeout else {}
    return EntsoePandasClient(api_key=_require_entsoe_token(), **kwargs)


def _written_summary(touched: list) -> tuple:
    written = [(m, n) for m, n, w in touched if w]
    return sum(n for _, n in written), len(written), len(touched) - len(written)


# ── ENTSO-G /aggregateddata (Physical Flow, Allocation) ─────────────────

def fetch_all_pages(from_date: date, to_date: date, indicator: str = "Physical Flow",
                    errors: list = None) -> pd.DataFrame:
    """Stáhne všechny stránky pro dané období — všechny země, JEDEN
    indikátor. KRITICKÉ: kombinace víc indikátorů v jednom
    "indicator=A,B" dotazu tenhle endpoint TICHÉ, BEZE CHYBY zredukuje
    na jeden — ověřeno naživo (Physical Flow+Allocation dohromady
    vrátilo jen Allocation). Physical Flow a Allocation se proto vždy
    fetchují ODDĚLENĚ (viz update_entsog/update_entsog_allocation),
    stejný bug jako u /operationaldata (viz data/entsog_operational.py).

    Druhé zjištění: endpoint na malý "limit" (zkoušeno limit=5) tiše
    vrací 404 "No result found" místo dat nebo chyby parametru — limit
    musí zůstat vysoký (2000), i pro krátká okna.

    errors (volitelný list) — sem se připíše popis chyby (HTTP != 200/404,
    výjimka), ať volající pozná částečné selhání; dřív se chyba jen
    vypsala a okno se tiše přeskočilo."""
    import urllib.parse
    ind_enc = urllib.parse.quote(indicator)
    all_rows = []
    offset   = 0
    limit    = 2000

    while True:
        url = (
            "https://transparency.entsog.eu/api/v1/aggregateddata"
            f"?from={from_date}&to={to_date}"
            f"&indicator={ind_enc}&periodType=day"
            f"&timezone=CET&limit={limit}&offset={offset}&format=json"
        )
        try:
            resp = requests.get(url, timeout=60)
            if resp.status_code == 404:
                log.info("ENTSO-G %s %s–%s: 404 (žádná data)", indicator, from_date, to_date)
                break
            if resp.status_code != 200:
                msg = f"HTTP {resp.status_code} pro {indicator} {from_date}–{to_date}"
                log.warning(msg)
                if errors is not None:
                    errors.append(msg)
                break
            data  = resp.json()
            rows  = data.get("aggregateddata", [])
            total = data.get("meta", {}).get("total", 0)
            all_rows.extend(rows)
            offset += len(rows)
            if offset >= total or len(rows) == 0:
                break
            time.sleep(0.3)
        except Exception as e:
            msg = f"{indicator} {from_date}–{to_date}: {type(e).__name__}: {e}"
            log.warning(msg)
            if errors is not None:
                errors.append(msg)
            break

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df = df[[c for c in KEEP_COLS if c in df.columns]]
    df["value_GWh"] = pd.to_numeric(df["value"], errors="coerce") / 1_000_000
    df["date"] = pd.to_datetime(
        df["periodFrom"], utc=True
    ).dt.tz_convert("Europe/Prague").dt.normalize()
    return df.drop(columns=["value", "periodFrom"], errors="ignore")


def _update_entsog_indicator(indicator: str, label: str) -> dict:
    last_date = last_date_partitioned(ENTSOG_FLOWS_DIR, "date", "parquet")
    if last_date is not None:
        start = last_date.date() - timedelta(days=7)
        log.info(f"{label}: existující data do {last_date.date()}, stahuji od {start}")
    else:
        start = HISTORY_START
        log.info(f"{label}: nový soubor, stahuji od {start}")

    frames, errors = [], []
    current = start
    today   = date.today()
    while current <= today:
        end = min(current + timedelta(days=6), today)
        log.debug(f"  {label} {current} → {end}")
        df_week = fetch_all_pages(current, end, indicator=indicator, errors=errors)
        if not df_week.empty:
            frames.append(df_week)
        current = end + timedelta(days=1)
        time.sleep(0.5)

    if not frames:
        if errors:
            raise UpdateError(f"{label}: nic nestaženo — {errors[0]}")
        log.info(f"{label}: žádná nová data")
        return {"rows": 0, "errors": 0}

    new_data = pd.concat(frames, ignore_index=True)
    key_cols = [c for c in ENTSOG_KEY_COLS if c in new_data.columns]
    touched = upsert_partitioned(new_data, ENTSOG_FLOWS_DIR, "date", key_cols, fmt="parquet")
    n, n_written, n_same = _written_summary(touched)
    log.info(f"{label}: {n} řádků v {n_written} přepsaných měsících ({n_same} beze změny) → {ENTSOG_FLOWS_DIR}/")
    return {"rows": n, "errors": len(errors)}


def update_entsog() -> dict:
    """ENTSO-G fyzické toky — měsíčně partitionované úložiště (viz
    data/partitioned_store.py). Přepíše jen měsíce dotčené novým stahovaným
    oknem, starší uzavřené měsíce zůstanou beze změny."""
    return _update_entsog_indicator("Physical Flow", "ENTSO-G flows")


def update_entsog_allocation() -> dict:
    """ENTSO-G Allocation — STEJNÉ úložiště jako fyzické toky
    (ENTSOG_FLOWS_DIR), rozlišeno sloupcem "indicator" (je v dedup klíči
    — bez něj by dedup smíchal Allocation a Physical Flow řádky pro
    stejnou zemi/směr/den do jednoho a jeden by tiše přepsal druhý)."""
    return _update_entsog_indicator("Allocation", "ENTSO-G Allocation")


def backfill_entsog_allocation(start: date = None, end: date = None, log_path: str = None):
    """Jednorázový PLNÝ backfill Allocation přes celou historii (2020 →
    dnes), týdenní chunking (stejný jako fetch_all_pages/update_entsog —
    /aggregateddata pagination to vyžaduje). NENÍ totéž co
    update_entsog_allocation() výš — ta pro scheduled běhy spoléhá na
    last_date_partitioned(ENTSOG_FLOWS_DIR, "date", ...), což je
    INDIKÁTOR-NEVĚDOMÉ: vidí, že Physical Flow už má data do dneška, a
    myslela by si, že Allocation je taky hotové — přefetchovala by jen
    posledních 7 dní, ne celou historii. Backfill proto resume řeší
    sám, PER MĚSÍC (čte jen sloupec "indicator" existujícího souboru),
    stejný vzor jako data/entsog_operational.py::backfill_history."""
    def _log(line):
        log.info(line)
        if log_path:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    os.makedirs(history_root(), exist_ok=True)
    start = start or HISTORY_START
    end = end or date.today()

    months = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    current_month = (end.year, end.month)
    total = len(months)
    _log(f"=== Backfill Allocation start {start} → {end}, {total} měsíců ===")

    for i, (year, month) in enumerate(months, 1):
        month_str = f"{year:04d}-{month:02d}"
        month_path = os.path.join(ENTSOG_FLOWS_DIR, f"{month_str}.parquet")

        if os.path.exists(month_path) and (year, month) != current_month:
            try:
                existing_ind = set(
                    pd.read_parquet(month_path, columns=["indicator"])["indicator"].dropna().unique()
                )
            except Exception:
                existing_ind = set()
            if "Allocation" in existing_ind:
                _log(f"[{i}/{total}] {month_str}: přeskočeno, Allocation už v souboru")
                continue

        month_start = date(year, month, 1)
        month_end = (date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)) - timedelta(days=1)
        month_end = min(month_end, end)

        t0 = time.time()
        frames = []
        cur = month_start
        while cur <= month_end:
            week_end = min(cur + timedelta(days=6), month_end)
            df_week = fetch_all_pages(cur, week_end, indicator="Allocation")
            if not df_week.empty:
                frames.append(df_week)
            cur = week_end + timedelta(days=1)
            time.sleep(0.3)

        elapsed = time.time() - t0
        if not frames:
            _log(f"[{i}/{total}] {month_str}: 0 řádků ({elapsed:.1f}s)")
            continue

        new_data = pd.concat(frames, ignore_index=True)
        n_raw = len(new_data)
        key_cols = [c for c in ENTSOG_KEY_COLS if c in new_data.columns]
        n_unique = len(new_data.drop_duplicates(subset=key_cols))
        if n_unique != n_raw:
            _log(f"    ⚠ {month_str}: {n_raw - n_unique} duplicitních klíčů v raw fetchi "
                 f"({n_raw} → {n_unique} unikátních) — dedup bezpečně vyřešeno")

        touched = upsert_partitioned(new_data, ENTSOG_FLOWS_DIR, "date", key_cols, fmt="parquet")
        written = [(mo, n) for mo, n, w in touched if w]
        n_total = sum(n for _, n in written) if written else len(new_data)
        size_kb = os.path.getsize(month_path) / 1024 if os.path.exists(month_path) else 0
        _log(f"[{i}/{total}] {month_str}: {n_total} řádků (soubor po mergi), "
             f"{n_raw} nových Allocation řádků, {size_kb:.0f} KB ({elapsed:.1f}s)")

    _log("=== Backfill Allocation hotovo ===")


# ── GIE AGSI+ (zásobníky) ────────────────────────────────────────────────

def fetch_gie_all_countries(from_date: date = None, to_date: date = None, errors: list = None) -> pd.DataFrame:
    """Stáhne GIE historii pro všechny země + EU agregát.

    from_date/to_date (volitelné) — AGSI API from/to param PODPORUJE
    (ověřeno živě 2026-09-09: dotaz s from/to vrátil přesně dny v okně,
    1 stránka místo 18-20 bez okna). Bez zadání stahuje celou historii."""
    key = gie_api_key()
    if not key:
        raise UpdateError("GIE_API_KEY není nastavený (env / ~/.config/ppdash/env)")

    all_frames = []
    date_param = ""
    if from_date is not None:
        date_param += f"&from={from_date}"
    if to_date is not None:
        date_param += f"&to={to_date}"

    targets = [(cc, f"country={cc}") for cc in COUNTRIES_GIE]
    targets.append(("EU", "type=eu"))

    for cc, param in targets:
        log.debug(f"  GIE {cc}...")
        frames = []
        for page in range(1, 50):
            url = f"https://agsi.gie.eu/api?{param}{date_param}&size=300&page={page}"
            try:
                resp = requests.get(url, headers={"x-key": key}, timeout=30)
            except Exception as e:
                msg = f"GIE {cc} str. {page}: {type(e).__name__}: {e}"
                log.warning(msg)
                if errors is not None:
                    errors.append(msg)
                break
            if resp.status_code != 200:
                if resp.status_code != 404 and errors is not None:
                    errors.append(f"GIE {cc}: HTTP {resp.status_code}")
                break
            data = resp.json()
            records = data.get("data", [])
            if not records:
                break
            frames.extend(records)
            if page >= data.get("last_page", 1):
                break
            time.sleep(0.3)

        if frames:
            df = pd.DataFrame(frames)
            df["country_code"] = cc
            all_frames.append(df)
        time.sleep(0.5)

    if not all_frames:
        return pd.DataFrame()

    combined = pd.concat(all_frames, ignore_index=True)
    combined["gasDayStart"] = pd.to_datetime(combined["gasDayStart"])

    # GIE AGSI API vrací číselná pole nekonzistentně jako JSON číslo nebo
    # string (např. 50.0 vs "50.0000") — beze sjednocení na float by tahle
    # reprezentační odchylka rozbíjela detekci "obsah beze změny" v
    # upsert_partitioned a soubory by se zbytečně přepisovaly při každém běhu.
    for col in ["full", "gasInStorage", "injection", "withdrawal",
                "netWithdrawal", "workingGasVolume", "trend",
                "injectionCapacity", "withdrawalCapacity",
                "consumption", "consumptionFull"]:
        if col in combined.columns:
            combined[col] = pd.to_numeric(
                combined[col].astype(str).str.replace(",", "."),
                errors="coerce",
            )

    # "info" je nepoužívaný list/blob sloupec (vždy [] v praxi) — přes CSV
    # round-trip se nestabilně mění mezi listem a stringem "[]".
    combined = combined.drop(columns=["info"], errors="ignore")
    return combined.sort_values(["country_code", "gasDayStart"])


def update_gie_all() -> dict:
    """GIE storage všechny země — měsíčně partitionované úložiště, stahuje
    se jen okno [last_date - GIE_ALSI_REVISION_WINDOW_DAYS, dnešek]."""
    last_date = last_date_partitioned(GIE_ALL_DIR, "gasDayStart", "csv")
    if last_date is not None:
        from_date = (last_date - timedelta(days=GIE_ALSI_REVISION_WINDOW_DAYS)).date()
        log.info(f"GIE all: existující data do {last_date.date()}, stahuji od {from_date}")
    else:
        from_date = None
        log.info("GIE all: nový soubor, plný backfill")

    errors = []
    new_data = fetch_gie_all_countries(from_date=from_date, to_date=date.today(), errors=errors)
    if new_data.empty:
        raise UpdateError(f"GIE all: žádná data{' — ' + errors[0] if errors else ''}")

    touched = upsert_partitioned(new_data, GIE_ALL_DIR, "gasDayStart",
                                 ["country_code", "gasDayStart"], fmt="csv")
    n, n_written, n_same = _written_summary(touched)
    log.info(f"GIE all: {n} řádků v {n_written} přepsaných měsících ({n_same} beze změny) → {GIE_ALL_DIR}/")
    return {"rows": n, "errors": len(errors)}


# ── ENTSO-E ──────────────────────────────────────────────────────────────

def update_hydro() -> dict:
    """Vodní zásobníky (ENTSO-E 16.1.D), týdenní data, 20 zemí.

    Překryv zpětného stahování: max(30 dní, mezera od posledních dat + 7)
    — dřív pevných 30 dní, takže výpadek aktualizací delší než měsíc by
    v datech nechal díru (OPTIMIZATION_REPORT.md, update_hydro)."""
    client = _entsoe_client()

    last_date = last_date_partitioned(HYDRO_DIR, "date", "csv")
    if last_date is not None:
        gap_days = (pd.Timestamp.now(tz="UTC") - last_date).days
        overlap = max(30, gap_days + 7)
        start = last_date - timedelta(days=overlap)
        log.info(f"Hydro: existující data do {last_date.date()}, překryv {overlap} dní")
    else:
        start = date(2020, 1, 1)
        log.info("Hydro: nový soubor")

    start_raw = pd.Timestamp(start)
    start_ts  = (start_raw.tz_localize("UTC") if start_raw.tzinfo is None else start_raw.tz_convert("UTC"))
    end_ts    = pd.Timestamp.now(tz="UTC")

    frames, errors = [], []
    for cc in HYDRO_COUNTRIES:
        try:
            raw = client.query_aggregate_water_reservoirs_and_hydro_storage(
                country_code=cc, start=start_ts, end=end_ts
            )
            if raw is None or len(raw) == 0:
                continue
            df = raw.reset_index()
            df.columns = ["date", "value_MWh"]
            df["country"]   = cc
            df["value_GWh"] = df["value_MWh"] / 1000
            df["date"]      = pd.to_datetime(df["date"], utc=True)
            frames.append(df[["date", "country", "value_GWh"]])
            log.debug(f"  ✓ {cc}: {len(df)} týdnů")
        except Exception as e:
            # NoMatchingDataError pro zemi bez nových dat není chyba
            if type(e).__name__ == "NoMatchingDataError":
                continue
            log.warning(f"  ✗ Hydro {cc}: {str(e)[:120]}")
            errors.append(cc)

    if not frames:
        if errors:
            raise UpdateError(f"Hydro: žádná data, selhalo {len(errors)} zemí")
        log.info("Hydro: žádná nová data")
        return {"rows": 0, "errors": 0}

    new_data = pd.concat(frames, ignore_index=True)
    touched = upsert_partitioned(new_data, HYDRO_DIR, "date", ["date", "country"], fmt="csv")
    n, n_written, n_same = _written_summary(touched)
    log.info(f"Hydro: {n} řádků v {n_written} přepsaných měsících ({n_same} beze změny) → {HYDRO_DIR}/")
    return {"rows": n, "errors": len(errors)}


def update_dap_europe() -> dict:
    """DAP base/peak + DoD změna pro ~23 zemí (choropleth DAP Mapa)."""
    from concurrent.futures import ThreadPoolExecutor
    from data.dap_europe import DAP_COUNTRIES

    client = _entsoe_client()

    # +2 dny: den-ahead ceny na ZÍTŘEK bývají publikované už dnes kolem
    # 13:00 CET — odpolední běh je tak může zachytit rovnou. Když zítřek
    # ještě publikovaný není (ranní běh), cnt>=20 filtr níž ho vyřadí.
    end = pd.Timestamp.now(tz="Europe/Prague").normalize() + pd.Timedelta(days=2)

    last_existing = last_date_partitioned(DAP_DIR, "date", "parquet")
    if last_existing is not None:
        start = pd.Timestamp(last_existing.date(), tz="Europe/Prague") - pd.Timedelta(days=1)
    else:
        start = end - pd.Timedelta(days=3)

    errors = []

    def fetch_one(args):
        cc, code = args
        try:
            d = client.query_day_ahead_prices(code, start=start, end=end)
            d = d.tz_convert("Europe/Prague").resample("1h").mean()

            # Jen kompletní dny (>= 20 hodinových bodů) — poslední den v
            # odpovědi může být jen okrajový bod (např. 00:00).
            day_counts = pd.Series(d.index.date).value_counts()
            full_dates = sorted(dt for dt, cnt in day_counts.items() if cnt >= 20)
            if len(full_dates) < 2:
                return None
            today_date = full_dates[-1]
            yest_date  = full_dates[-2]

            today = d[d.index.date == today_date]
            yest  = d[d.index.date == yest_date]
            if today.empty or yest.empty:
                return None
            base_t = today.mean()
            peak_t = today[today.index.hour.isin(range(8, 20))].mean()
            base_y = yest.mean()
            peak_y = yest[yest.index.hour.isin(range(8, 20))].mean()
            return {
                "date":     today_date,
                "cc":       cc,
                "base":     round(float(base_t), 2),
                "peak":     round(float(peak_t), 2),
                "dod_base": round(float(base_t - base_y), 2),
                "dod_peak": round(float(peak_t - peak_y), 2),
            }
        except Exception as e:
            log.warning(f"DAP {cc}: CHYBA — {e}")
            errors.append(cc)
            return None

    with ThreadPoolExecutor(max_workers=6) as ex:
        results = [r for r in ex.map(fetch_one, DAP_COUNTRIES.items()) if r is not None]

    if not results:
        if errors:
            raise UpdateError(f"DAP Europe: žádná data, selhalo {len(errors)} zemí")
        log.info("DAP Europe: žádná nová data")
        return {"rows": 0, "errors": 0}

    df_new = pd.DataFrame(results)
    touched = upsert_partitioned(df_new, DAP_DIR, "date", ["date", "cc"], fmt="parquet")
    n, n_written, _ = _written_summary(touched)
    if not n_written:
        log.info("DAP Europe: data už aktuální, beze změny")
    else:
        log.info(f"DAP Europe: {len(df_new)} zemí, {n_written} přepsaný měsíc → {DAP_DIR}/")
    return {"rows": n, "errors": len(errors)}


def _melt_generation(df_wide: pd.DataFrame, country: str) -> pd.DataFrame:
    """Wide/MultiIndex df z query_generation(country, ..., psr_type=None) →
    long formát (date, country, source_type, psr_code, mw). Zachovává jen
    sloupce 'Actual Aggregated' (zahazuje 'Actual Consumption')."""
    cols = ["date", "country", "source_type", "psr_code", "mw"]
    if df_wide.empty:
        return pd.DataFrame(columns=cols)

    agg_cols = [c for c in df_wide.columns if c[1] == "Actual Aggregated"]
    df = df_wide[agg_cols].copy()
    df.columns = [c[0] for c in agg_cols]
    df.index.name = "date"

    long = df.reset_index().melt(id_vars="date", var_name="source_type", value_name="mw")
    long = long.dropna(subset=["mw"])

    unmapped = sorted(set(long["source_type"]) - set(PSR_CODE_BY_SOURCE_TYPE))
    if unmapped:
        log.warning(f"nenamapované source_type v PSR_CODE_BY_SOURCE_TYPE ({country}): {unmapped}")

    long["country"] = country
    long["psr_code"] = long["source_type"].map(PSR_CODE_BY_SOURCE_TYPE)
    return long[cols]


def update_generation(country: str) -> dict:
    """Výroba podle zdroje (ENTSO-E query_generation, psr_type=None) →
    <history>/generation/<country>/{YYYY-MM}.parquet. Stahuje po
    GENERATION_CHUNK_DAYS s GENERATION_CHUNK_RETRIES pokusy na chunk
    (ENTSO-E padá na 503/504 na širokých rozsazích)."""
    client = _entsoe_client()
    tz = COUNTRY_TIMEZONES[country]
    base_dir = history_path("generation", country)

    last_date = last_date_partitioned(base_dir, "date", "parquet")
    if last_date is not None:
        start = last_date.tz_convert(tz) - pd.Timedelta(days=3)
        log.info(f"Generation {country}: existující data do {last_date.date()}, stahuji od {start.date()}")
    else:
        start = pd.Timestamp(HISTORY_START, tz=tz)
        log.info(f"Generation {country}: nový soubor, plný backfill od {start.date()}")

    end = pd.Timestamp.now(tz=tz) + pd.Timedelta(days=1)

    # každý chunk se zapíše hned — přerušený backfill nové země pokračuje
    # příště od konce uložených dat; časový rozpočet drží běh v mezích
    budget = float(os.environ.get("PPDASH_GENERATION_BUDGET_S", "1800"))
    deadline = time.monotonic() + budget
    rows, n_written, failed_chunks, n_chunks = 0, 0, 0, 0
    cur = start
    while cur < end:
        if time.monotonic() > deadline:
            log.info(f"Generation {country}: rozpočet {budget:.0f}s vyčerpán u {cur.date()} — pokračuje příští běh")
            break
        n_chunks += 1
        chunk_end = min(cur + pd.Timedelta(days=GENERATION_CHUNK_DAYS), end)
        for attempt in range(1, GENERATION_CHUNK_RETRIES + 1):
            try:
                raw = client.query_generation(country, start=cur, end=chunk_end, psr_type=None)
                part = _melt_generation(raw, country)
                if not part.empty:
                    touched = upsert_partitioned(part, base_dir, "date", ["date", "source_type"], fmt="parquet")
                    rows += sum(n for _, n, w in touched if w)
                    n_written += sum(1 for _, _, w in touched if w)
                break
            except Exception as e:
                if type(e).__name__ == "NoMatchingDataError":
                    break
                log.warning(f"  {country} {cur.date()} → {chunk_end.date()}: pokus {attempt}/"
                            f"{GENERATION_CHUNK_RETRIES} selhal — {str(e)[:120]}")
                if attempt < GENERATION_CHUNK_RETRIES:
                    time.sleep(5)
                else:
                    failed_chunks += 1
        cur = chunk_end
        time.sleep(1)

    if failed_chunks and failed_chunks == n_chunks:
        raise UpdateError(f"Generation {country}: všechny chunky selhaly")
    log.info(f"Generation {country}: {rows} řádků v {n_written} přepsaných měsících")
    return {"rows": rows, "errors": failed_chunks}


def update_outages(country: str) -> dict:
    """Odstávky pro danou zemi (ENTSO-E unavailability) — všechny typy
    zdrojů. Rolling window [now - ENTSOE_OUTAGE_REVISION_WINDOW_DAYS,
    now + ENTSOE_OUTAGE_FORWARD_WINDOW_DAYS], merge do JEDNOHO sdíleného
    outages.parquet (odstávky se revidují, měsíční split nedává smysl).
    REVISION+FORWARD < 365 dní, aby entsoe-py (@year_limited) neděl dotaz
    na 2 roční bloky. ENTSOE_OUTAGE_FETCH_RETRIES — vlastní retry, protože
    entsoe-py @retry nechytá ReadTimeout."""
    client = _entsoe_client(timeout=True)
    tz = COUNTRY_TIMEZONES[country]

    now = pd.Timestamp.now(tz=tz)
    fetch_start = now - pd.Timedelta(days=ENTSOE_OUTAGE_REVISION_WINDOW_DAYS)
    fetch_end = now + pd.Timedelta(days=ENTSOE_OUTAGE_FORWARD_WINDOW_DAYS)

    df_out = None
    last_err = None
    for attempt in range(1, ENTSOE_OUTAGE_FETCH_RETRIES + 1):
        try:
            df_out = client.query_unavailability_of_generation_units(
                country, start=fetch_start, end=fetch_end, docstatus=None
            ).copy()
            break
        except Exception as e:
            last_err = e
            log.warning(f"  Outages {country}: pokus {attempt}/{ENTSOE_OUTAGE_FETCH_RETRIES} "
                        f"selhal — {str(e)[:120]}")
            if attempt < ENTSOE_OUTAGE_FETCH_RETRIES:
                time.sleep(5)
    if df_out is None:
        raise UpdateError(f"Outages {country}: všechny pokusy selhaly — {last_err}")
    df_out["nominal_power"] = pd.to_numeric(df_out["nominal_power"], errors="coerce").fillna(0)
    df_out["avail_qty"] = pd.to_numeric(df_out["avail_qty"], errors="coerce").fillna(0)

    latest = df_out.sort_values("revision").groupby(
        ["production_resource_name", "start", "end"]
    ).last().reset_index()
    latest = latest[latest["docstatus"] != "Cancelled"]
    latest["unavail_mw"] = latest["nominal_power"] - latest["avail_qty"]
    latest["country"] = country
    latest["start"] = pd.to_datetime(latest["start"], utc=True)
    latest["end"] = pd.to_datetime(latest["end"], utc=True)

    cols = ["country", "plant_type", "production_resource_name", "start", "end",
            "unavail_mw", "nominal_power", "avail_qty", "docstatus", "businesstype"]
    latest = latest[cols].reset_index(drop=True)

    fetch_start_utc = fetch_start.tz_convert("UTC")
    fetch_end_utc   = fetch_end.tz_convert("UTC")

    if os.path.exists(OUTAGES_PATH):
        existing = pd.read_parquet(OUTAGES_PATH)
        # zamrzlé: záznamy jiných zemí + záznamy téhle země MIMO stahované
        # okno (skončily před fetch_start, nebo začínají za fetch_end)
        frozen = existing[
            (existing["country"] != country)
            | (existing["end"] < fetch_start_utc)
            | (existing["start"] > fetch_end_utc)
        ]
    else:
        frozen = latest.iloc[0:0]
    combined = pd.concat([frozen, latest], ignore_index=True)
    # Pojistka: ENTSO-E občas vrací i dávno uzavřené odstávky (end hluboko
    # před fetch_start) — bez dedupu by se hromadily o kopii při každém běhu.
    combined = combined.drop_duplicates(
        subset=["country", "production_resource_name", "start", "end"], keep="last")

    combined = combined.sort_values(["country", "production_resource_name", "start"]).reset_index(drop=True)
    os.makedirs(os.path.dirname(OUTAGES_PATH), exist_ok=True)
    combined.to_parquet(OUTAGES_PATH, index=False)
    n_frozen_country = int((frozen["country"] == country).sum()) if not frozen.empty else 0
    log.info(f"Outages {country}: {len(latest)} z API (okno {fetch_start.date()}→{fetch_end.date()}) "
             f"+ {n_frozen_country} beze změny z {country} + {len(frozen) - n_frozen_country} ostatní země "
             f"→ {len(combined)} celkem")

    # Denní snapshot celého outages.parquet pro fig_outages_delta — write-once.
    snap_written = write_daily_snapshot(combined, OUTAGES_SNAPSHOTS_DIR, fmt="parquet")
    log.info(f"Outages snapshot: {'zapsán nový den' if snap_written else 'dnešní už existuje'}")
    return {"rows": len(latest), "errors": 0}


# ── ICE MiFID II CoT ─────────────────────────────────────────────────────

def last_friday(today: pd.Timestamp):
    # Monday=0 ... Friday=4, Saturday=5, Sunday=6
    offset = (today.weekday() - 4) % 7
    return (today - pd.Timedelta(days=offset)).date()


# ICE publikuje report za pátek (název souboru …_YYYYMMDD = pátek) až
# následující STŘEDU — dřívější sobotní/úterní běh chtěl pátek, který ještě
# nevyšel (odtud 404 v ice_cot_status.json).
COT_PUBLICATION_LAG_DAYS = 5


def last_published_friday(today: pd.Timestamp):
    """Nejnovější pátek, jehož report už měl vyjít (pátek + 5 dní ≤ dnes)."""
    return last_friday(today - pd.Timedelta(days=COT_PUBLICATION_LAG_DAYS))


COT_MAX_CATCHUP_WEEKS = 8


def _cot_pending_fridays(report_date, stored_max) -> list:
    """Pátky, které v úložišti chybí: od posledního uloženého report_date
    (+7 dní) do report_date, nejvýš COT_MAX_CATCHUP_WEEKS zpět. Bez dat
    jen report_date."""
    if stored_max is None:
        return [report_date]
    fridays = []
    d = report_date
    while d > stored_max and len(fridays) < COT_MAX_CATCHUP_WEEKS:
        fridays.append(d)
        d = d - timedelta(days=7)
    return sorted(fridays) or [report_date]


def update_cot(today: pd.Timestamp = None) -> dict:
    """Týdenní CoT: nejnovější už publikovaný pátek (report_date, vychází
    ve středu), IFEU + NDEX přes data/ice_cot.py::fetch_ice_cot (merge +
    status log) — a k tomu všechny dřívější pátky, které v úložišti chybí
    (inkrementálně od posledního uloženého report_date, max
    COT_MAX_CATCHUP_WEEKS), takže týden, kdy ICE soubor nevydalo včas, se
    dožene příští běh. Středeční i záložní běhy (čt, so) se trefí do
    STEJNÉHO report_date (už uložený týden se znovu nestahuje). 404 v den
    publikace (středa 14:00 může být dřív než ICE) = WARN, ne chyba.
    Při rate-limitu (429) se fetch přeskočí (není to chyba — zkusí se
    příští běh). Výsledek se hodnotí podle posledního pátku: selhání
    jednoho venue = WARN, obou = chyba."""
    from data.ice_cot import fetch_ice_cot, load_ice_cot, probe_rate_limit, VENUES

    today = today or pd.Timestamp.now(tz="UTC")
    report_date = last_published_friday(today)
    try:
        stored = load_ice_cot()
        stored_max = pd.to_datetime(stored["report_date"]).max().date() if not stored.empty else None
    except Exception:
        stored_max = None
    pending = _cot_pending_fridays(report_date, stored_max)
    log.info(f"ICE CoT: report_date {report_date} (dnes {today.date()}, uloženo do {stored_max}, "
             f"ke stažení {', '.join(map(str, pending))})")

    probe = probe_rate_limit()
    if probe.get("rate_limited"):
        log.warning(f"ICE CoT: RATE LIMITED (429, Retry-After={probe.get('retry_after_s')}s) — přeskakuji")
        return {"report_date": report_date, "skipped": True, "errors": 0,
                "venues": {v: "SKIPPED (rate limited)" for v in VENUES}}

    for older in pending[:-1]:
        res = fetch_ice_cot(older)
        ok = [v for v in VENUES if res.get(v, {}).get("success")]
        log.info(f"  dohánění {older}: {'OK ' + ','.join(ok) if ok else 'nedostupné'}")

    results = fetch_ice_cot(pending[-1])
    summary, failed = {}, 0
    for venue in VENUES:
        entry = results.get(venue, {})
        if entry.get("success"):
            summary[venue] = "OK"
            log.info(f"  {venue}: OK — {entry.get('rows_fetched')} řádků staženo, "
                     f"{entry.get('rows_in_store')} v úložišti")
        else:
            failed += 1
            http_status = entry.get("http_status")
            summary[venue] = f"FAILED {http_status}" if http_status else "FAILED"
            log.warning(f"  {venue}: SELHALO — http_status={http_status}, error={entry.get('error')}")
    publication_day = report_date + timedelta(days=COT_PUBLICATION_LAG_DAYS)
    if failed == len(VENUES) and today.date() <= publication_day and all(
            results.get(v, {}).get("http_status") == 404 for v in VENUES):
        log.warning(f"ICE CoT {report_date}: zatím nepublikováno (404 v den publikace) — zkusí další běh")
        return {"report_date": report_date, "venues": summary, "errors": failed}
    if failed == len(VENUES):
        raise UpdateError(f"ICE CoT {report_date}: všechny venues selhaly ({summary})")
    return {"report_date": report_date, "venues": summary, "errors": failed}
