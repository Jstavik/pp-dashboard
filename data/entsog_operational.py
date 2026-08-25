import requests, time, os
from datetime import date
import pandas as pd

from data.partitioned_store import read_partitioned, upsert_partitioned, last_date_partitioned

OPERATIONAL_DIR = "data/history/entsog_eu_operational"

# ENTSO-G /operationaldata BEZ pointKey/pointLabel filtru vrací najednou
# všechny body Evropy (ověřeno: 415 bodů, 23050 řádků za jeden měsíc jen
# pro Nomination). Ukládáme VŠECHNY body beze filtru — CZ/NET4GAS filtr
# byl původně myšlený jen jako dočasné zúžení pro opravu pár anomálních
# kvartálů a omylem se stal trvalým rozhodnutím ve finální vrstvě.
# operatorKey (např. "CZ-TSO-0001") nese ISO kód země jako prefix — country
# se z dat odvozuje downstream (UI), netřeba ho tady ukládat zvlášť.
INDICATORS = "Nomination,Renomination"

# Zobrazovací jména pro ISO prefix z operatorKey — jen kosmetika pro UI,
# NEOVLIVŇUJE které země/body se nabízí (ty se vždy odvozují z reálných
# dat, viz country_from_operator_key). Kód, co v mapě chybí, se zobrazí
# jako holý ISO prefix — nikdy nezmizí z výběru jen proto, že tu není.
EU_COUNTRY_NAMES = {
    "AT": "🇦🇹 Rakousko", "BE": "🇧🇪 Belgie", "BG": "🇧🇬 Bulharsko",
    "CZ": "🇨🇿 Česko", "DE": "🇩🇪 Německo", "DK": "🇩🇰 Dánsko",
    "EE": "🇪🇪 Estonsko", "ES": "🇪🇸 Španělsko", "FI": "🇫🇮 Finsko",
    "FR": "🇫🇷 Francie", "GR": "🇬🇷 Řecko", "HR": "🇭🇷 Chorvatsko",
    "HU": "🇭🇺 Maďarsko", "IE": "🇮🇪 Irsko", "IT": "🇮🇹 Itálie",
    "LT": "🇱🇹 Litva", "LU": "🇱🇺 Lucembursko", "LV": "🇱🇻 Lotyšsko",
    "NL": "🇳🇱 Nizozemsko", "PL": "🇵🇱 Polsko", "PT": "🇵🇹 Portugalsko",
    "RO": "🇷🇴 Rumunsko", "SI": "🇸🇮 Slovinsko", "SK": "🇸🇰 Slovensko",
    "UA": "🇺🇦 Ukrajina", "UK": "🇬🇧 Velká Británie", "AL": "🇦🇱 Albánie",
}


def country_from_operator_key(operator_key) -> str:
    """ISO prefix z operatorKey (např. 'CZ-TSO-0001' -> 'CZ'). '??' pro
    chybějící/nevalidní operatorKey (ojediněle se v datech vyskytne
    ~20 řádků z 1.5M s operatorKey=None — bez smysluplného obsahu vůbec,
    stejné řádky mají i pointLabel=None, takže je UI dropne beztak)."""
    if operator_key is None or (isinstance(operator_key, float) and pd.isna(operator_key)):
        return "??"
    return str(operator_key)[:2]


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
    legitimně prázdného měsíce (2020, kde archiv skutečně nic nevrací).
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

    # isNA==1 řádky (odstavené/nerelevantní body, vždy value=None) mají
    # "id" navázané na jejich (statické) reportovací období, NE na
    # periodFrom konkrétního dne — API tak vrací STEJNÉ id pro tenhle
    # placeholder v každém měsíci, kdy se bod dotázal. Bez odfiltrování
    # by "id" dedup v upsert_partitioned nerozeznal dva různé měsíce od
    # sebe (ověřeno naživo: 146-770 kolidujících id mezi sousedními
    # měsíci, ~38k napříč celým EU backfillem). Řádky s reálnou (i
    # nulovou/None) denní hodnotou mají id vždy per-den unikátní — tohle
    # se týká jen skutečných "not applicable" placeholderů.
    if "isNA" in df.columns:
        df = df[df["isNA"] != 1].copy()
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


def backfill_eu_operational(log_path: str = None, start: date = None, end: date = None):
    """Jednorázový (resumable) backfill ENTSOG Nomination/Renomination pro
    VŠECHNY body Evropy (~415), po měsících od start (default HISTORY_START)
    do end (default dnešek). Přeskočí měsíce, které už mají partitioned
    soubor (idempotentní resume po přerušení) — KROMĚ posledního (aktuálního,
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
            _log(log_path, f"[{i}/{total}] {month_str}: 0 řádků ({elapsed:.1f}s)")
            continue

        n_raw = len(df)
        n_unique = df["id"].nunique()
        if n_unique != n_raw:
            # Ojediněle pozorovaná přechodná nekonzistence živého API mezi
            # stránkami (ověřeno: 1 z 4 opakovaných dotazů na stejný
            # uzavřený měsíc vrátil ~7 % duplicitních id, ostatní 3 čistě).
            # id je deterministický klíč ze všech polí (indicator+period+
            # operator+point+direction+unit) — duplicitní id tedy nemůže
            # nést jinou hodnotu, drop_duplicates(keep="last") v
            # upsert_partitioned to řeší bezpečně. Loguje se jen jako
            # viditelné varování pro zpětnou kontrolu.
            _log(
                log_path,
                f"    ⚠ {month_str}: {n_raw - n_unique} duplicitních id v raw fetchi "
                f"({n_raw} → {n_unique} unikátních) — API pagination nekonzistence, "
                f"dedup bezpečně vyřešeno",
            )

        touched = upsert_partitioned(df, OPERATIONAL_DIR, "periodFrom_dt", ["id"], fmt="parquet")
        written = [(mo, n) for mo, n, w in touched if w]
        n_total = sum(n for _, n in written) if written else len(df)
        by_ind = df["indicator"].value_counts().to_dict()
        n_points = df["pointLabel"].nunique()
        file_path = os.path.join(OPERATIONAL_DIR, f"{month_str}.parquet")
        size_kb = os.path.getsize(file_path) / 1024 if os.path.exists(file_path) else 0
        _log(
            log_path,
            f"[{i}/{total}] {month_str}: {n_total} řádků, {n_points} bodů "
            f"(Nomination={by_ind.get('Nomination', 0)}, Renomination={by_ind.get('Renomination', 0)}) "
            f"{size_kb:.0f} KB ({elapsed:.1f}s)",
        )

    _log(log_path, "=== Backfill hotovo ===")


def update_eu_operational():
    """Scheduled běh: přefetchuje jen posledních REVISION_WINDOW_MONTHS
    měsíců (Nomination/Renomination se v revizích mění, ne jen append) —
    ne celou historii. Plný backfill viz scripts/backfill_entsog_eu_operational.py."""
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
        print("ENTSOG EU operational: žádná data")
        return

    new_data = pd.concat(frames, ignore_index=True)
    touched = upsert_partitioned(new_data, OPERATIONAL_DIR, "periodFrom_dt", ["id"], fmt="parquet")
    written = [(mo, n) for mo, n, w in touched if w]
    print(f"ENTSOG EU operational: {sum(n for _, n in written)} řádků v {len(written)} přepsaných měsících "
          f"({len(touched) - len(written)} beze změny) → {OPERATIONAL_DIR}/")


def load_eu_operational() -> pd.DataFrame:
    def _load():
        return read_partitioned(OPERATIONAL_DIR, fmt="parquet")
    try:
        import streamlit as st
        return st.cache_data(ttl=3600, show_spinner=False)(_load)()
    except ImportError:
        return _load()


# Dočasný alias — app.py zatím importuje load_cz_operational beze změny,
# dokud UI (výběr země/bodu pro celou Evropu) není potvrzené a upravené.
load_cz_operational = load_eu_operational
