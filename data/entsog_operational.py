import requests, time, os
from datetime import date
import pandas as pd

from data.partitioned_store import read_partitioned, upsert_partitioned, last_date_partitioned

OPERATIONAL_DIR = "data/history/entsog_cz_operational"

# ENTSO-G /operationaldata BEZ pointKey/pointLabel filtru vrací najednou
# všechny body Evropy (ověřeno: 415 bodů, 23050 řádků za jeden měsíc jen
# pro Nomination). Filtrujeme až po stažení na operatorLabel == NET4GAS
# (CZ TSO) — dává přesně hraniční body (Cieszyn, VIP Brandov, Lanžhot,
# VIP Waidhaus) + CZ zásobníky (VGS ...), bez fragilního fuzzy matchování
# jako u GAS_KEY_POINTS (ten je pro capacity endpoint, jeho labely se s
# touhle datovou sadou vůbec nepotkávají — ověřeno).
INDICATORS = "Nomination,Renomination"
CZ_OPERATOR_LABEL = "NET4GAS"

# ENTSO-G aggregateddata/operationaldata API sahá do 2020, dříve není dostupné.
HISTORY_START = date(2020, 1, 1)

# Kolik posledních měsíců scheduled běh vždy přefetchuje znovu — Nomination/
# Renomination hodnoty se v revizích mění (lastUpdateDateTime), ne jen
# append. 2 = aktuální + předchozí měsíc.
REVISION_WINDOW_MONTHS = 2


def _month_bounds(year: int, month: int) -> tuple:
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    from datetime import timedelta
    return start, end - timedelta(days=1)


def fetch_operational_month(from_date: date, to_date: date) -> list:
    """Stáhne VŠECHNY body Evropy pro daný měsíc a oba indikátory najednou.

    KRITICKÉ: stránkování se NESMÍ řídit meta.total — API ho vrací
    nespolehlivě (echo zpět ~limit bez ohledu na skutečný zbytek dat,
    ověřeno). Jediný bezpečný stop podmínka je počet vrácených řádků <
    limit. Stejně tak dotaz NESMÍ pokrývat širší okno než jeden měsíc —
    server na širších oknech (ověřeno na kvartálu) TICHE, BEZ CHYBY vrátí
    jen zlomek dat (Q2 2024 → 3960 řádků místo ~23000+ za samotný červen),
    offset/limit stránkování to nijak nenaznačí.

    Síťovou/HTTP chybu NEPOLYKÁME — musí probublat ven a odlišit se od
    legitimně prázdného měsíce (2020, kde NET4GAS skutečně nic nehlásí).
    Tichým "return []" na chybu by se selhání v logu nerozeznalo od
    opravdové nuly (ověřeno na prvním běhu: lokální SSL chyba prošla jako
    "0 řádků" pro všech 80 měsíců včetně těch s daty).
    """
    all_rows, offset, limit = [], 0, 2000
    while True:
        url = (
            "https://transparency.entsog.eu/api/v1/operationaldata"
            f"?indicator={INDICATORS}"
            f"&periodType=day&from={from_date}&to={to_date}"
            f"&limit={limit}&offset={offset}&format=json"
        )
        resp = requests.get(url, timeout=60)
        if resp.status_code == 404 and "archived" in resp.text.lower():
            # TP archivuje data starší než ~5 let na rolling bázi — vrací
            # 404 s vysvětlující zprávou, ne prázdný výsledek. Očekávané
            # pro rok 2020 (a část 2021), ne chyba — bereme jako "žádná
            # data" a jedeme dál.
            break
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("operationaldata", [])
        all_rows.extend(rows)
        n = len(rows)
        offset += n
        if n < limit:
            break
        time.sleep(0.2)
    return all_rows


def _process(rows: list) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df[df["operatorLabel"] == CZ_OPERATOR_LABEL].copy()
    if df.empty:
        return df

    keep_cols = [
        "id", "indicator", "periodType", "periodFrom", "periodTo",
        "operatorKey", "operatorLabel", "pointKey", "pointLabel",
        "directionKey", "unit", "value", "flowStatus",
        "lastUpdateDateTime", "pointType",
    ]
    df = df[[c for c in keep_cols if c in df.columns]]

    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["value_GWh"] = df["value"] / 1_000_000
    df["periodFrom_dt"] = pd.to_datetime(df["periodFrom"], utc=True).dt.date
    df["periodTo_dt"] = pd.to_datetime(df["periodTo"], utc=True).dt.date
    return df


def _log(log_path: str, line: str):
    print(line)
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def backfill_cz_operational(log_path: str = None, start: date = None, end: date = None):
    """Jednorázový (resumable) backfill ENTSOG Nomination/Renomination pro
    NET4GAS body, po měsících od start (default HISTORY_START) do end
    (default dnešek). Přeskočí měsíce, které už mají partitioned soubor
    (idempotentní resume po přerušení) — KROMĚ posledního (aktuálního,
    ještě otevřeného) měsíce, ten se přefetchuje vždy."""
    import time as _time
    start = start or HISTORY_START
    end = end or date.today()
    os.makedirs(OPERATIONAL_DIR, exist_ok=True)

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
    _log(log_path, f"=== Backfill start {start} → {end}, {total} měsíců ===")

    for i, (year, month) in enumerate(months, 1):
        month_str = f"{year:04d}-{month:02d}"
        existing_path = os.path.join(OPERATIONAL_DIR, f"{month_str}.parquet")
        if os.path.exists(existing_path) and (year, month) != current_month:
            _log(log_path, f"[{i}/{total}] {month_str}: přeskočeno, soubor už existuje")
            continue

        t0 = _time.time()
        from_date, to_date = _month_bounds(year, month)
        try:
            rows = fetch_operational_month(from_date, to_date)
        except Exception as e:
            elapsed = _time.time() - t0
            _log(log_path, f"[{i}/{total}] {month_str}: CHYBA — {e} ({elapsed:.1f}s)")
            continue
        df = _process(rows)
        elapsed = _time.time() - t0

        if df.empty:
            _log(log_path, f"[{i}/{total}] {month_str}: 0 řádků NET4GAS ({elapsed:.1f}s)")
            continue

        touched = upsert_partitioned(df, OPERATIONAL_DIR, "periodFrom_dt", ["id"], fmt="parquet")
        written = [(mo, n) for mo, n, w in touched if w]
        n_total = sum(n for _, n in written) if written else len(df)
        by_ind = df["indicator"].value_counts().to_dict()
        _log(
            log_path,
            f"[{i}/{total}] {month_str}: {n_total} řádků "
            f"(Nomination={by_ind.get('Nomination', 0)}, Renomination={by_ind.get('Renomination', 0)}) "
            f"({elapsed:.1f}s)",
        )

    _log(log_path, "=== Backfill hotovo ===")


def update_cz_operational():
    """Scheduled běh: přefetchuje jen posledních REVISION_WINDOW_MONTHS
    měsíců (Nomination/Renomination se v revizích mění, ne jen append) —
    ne celou historii. Plný backfill viz scripts/backfill_entsog_cz_operational.py."""
    os.makedirs(OPERATIONAL_DIR, exist_ok=True)
    today = date.today()
    y, m = today.year, today.month
    months = [(y, m)]
    for _ in range(REVISION_WINDOW_MONTHS - 1):
        m -= 1
        if m < 1:
            m = 12
            y -= 1
        months.append((y, m))
    months = sorted(set(months))

    frames = []
    for year, month in months:
        from_date, to_date = _month_bounds(year, month)
        rows = fetch_operational_month(from_date, to_date)
        df = _process(rows)
        if not df.empty:
            frames.append(df)

    if not frames:
        print("ENTSOG CZ operational: žádná data")
        return

    new_data = pd.concat(frames, ignore_index=True)
    touched = upsert_partitioned(new_data, OPERATIONAL_DIR, "periodFrom_dt", ["id"], fmt="parquet")
    written = [(mo, n) for mo, n, w in touched if w]
    print(f"ENTSOG CZ operational: {sum(n for _, n in written)} řádků v {len(written)} přepsaných měsících "
          f"({len(touched) - len(written)} beze změny) → {OPERATIONAL_DIR}/")


def load_cz_operational() -> pd.DataFrame:
    def _load():
        return read_partitioned(OPERATIONAL_DIR, fmt="parquet")
    try:
        import streamlit as st
        return st.cache_data(ttl=3600, show_spinner=False)(_load)()
    except ImportError:
        return _load()
