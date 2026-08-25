import requests, time, os
from datetime import date
import pandas as pd

from data.partitioned_store import read_partitioned, upsert_partitioned

OPERATIONAL_DIR = "data/history/entsog_operational"

# ENTSO-G /operationaldata BEZ pointKey/pointLabel filtru vrací najednou
# všechny body Evropy. Konsolidovaná vrstva pro VŠECHNY /operationaldata
# indikátory (Nomination/Renomination + kapacita + interrupce + kvalita
# plynu) — jeden dataset, sloupec "indicator" rozlišuje typ. Physical
# Flow a Allocation NEJSOU tady — jiný endpoint (/aggregateddata), jiný
# tvar dat (viz scripts/update_gas_history.py::update_entsog).
#
# Dva zásadně odlišné režimy chování, ověřeno naživo (2026-08-25):
#
# HISTORY_INDICATORS — periodFrom u nich odpovídá KONKRÉTNÍMU dni
# dotazovaného okna (dotaz na červen 2024 vrátí přesně 30 unikátních
# dat). Skutečná denní historie, backfilluje se po měsících 2021→dnes.
#
# OPEN_ENDED_INDICATORS — periodFrom/periodTo nesou VALIDITY OKNO
# (klidně roky dozadu i dopředu, ověřeno: dotaz na 2021-06 i 2026-07
# vrátil částečně STEJNÉ záznamy s periodFrom z roku 2007-2013).
# Žádná měsíční historie neexistuje, jen aktuální (+budoucí) platnost
# — backfilluje se JEDNÍM dotazem na aktuální měsíc, ne smyčkou přes
# roky (viz backfill_open_ended). Stejné chování jako u starého
# entsog_capacity.py::fetch_point_capacity (per-point, bez date
# parametrů) — tady jen bulk přes celou Evropu najednou.
#
# KRITICKÉ (ověřeno naživo, 2026-08-25): API kombinaci 3+ indikátorů
# v jednom "indicator=A,B,C" dotazu TICHÉ, BEZE CHYBY zredukuje na
# 1-2 "dominantní" — zbytek zmizí beze stopy (ověřeno na 4 i na 9
# kombinovaných indikátorech, vždy přežily jen 2). Kombinace přesně 2
# indikátorů (Nomination+Renomination) funguje spolehlivě a je už
# takhle plně zabackfillovaná — ale VŠECHNO NOVÉ se fetchuje PO JEDNOM
# indikátoru, nikdy kombinovaně, viz fetch_operational_month.
HISTORY_INDICATORS = ["Nomination", "Renomination", "GCV", "Wobbe Index"]

# Reálné (server-side canonical) názvy ověřené naživo pojedno — uživatelův
# původní seznam měl 12 položek, ale:
#   - "Interruptible Technical" neexistuje (404, capacity endpoint ho
#     evidentně nenabízí, jen Firm Technical)
#   - "Firm Interruption Planned/Unplanned - Interrupted" jsou jen
#     ALIASY (jiný string, stejná data) pro "Planned/Unplanned
#     interruption of firm capacity" — použit jen jeden z každé dvojice
#   - "Unplanned interruption of interruptible capacity" a "Actual
#     interruption of firm capacity" neexistují (404, obě zkoušené
#     varianty názvu) — ENTSOG tyhle kombinace zjevně netrackuje
# → 9 skutečně distinct indikátorů, ne 12.
OPEN_ENDED_INDICATORS = [
    "Firm Technical", "Firm Booked", "Firm Available",
    "Interruptible Booked", "Interruptible Available",
    "Actual interruption of interruptible capacity",
    "Planned interruption of firm capacity",
    "Unplanned interruption of firm capacity",
    "Interruptible Interruption Planned - Interrupted",
]

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

# Kolik posledních měsíců scheduled běh vždy přefetchuje znovu pro
# HISTORY_INDICATORS — hodnoty se v revizích mění (lastUpdateDateTime),
# ne jen append. 2 = aktuální + předchozí měsíc. OPEN_ENDED_INDICATORS
# se refreshují VŽDY celé (jeden aktuální měsíc), viz update_eu_operational.
REVISION_WINDOW_MONTHS = 2


def _month_bounds(year: int, month: int) -> tuple:
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    from datetime import timedelta
    return start, end - timedelta(days=1)


def fetch_operational_month(from_date: date, to_date: date, indicator: str) -> list:
    """Stáhne VŠECHNY body Evropy pro daný měsíc a JEDEN indikátor.

    KRITICKÉ #1: vždy JEDEN indikátor za dotaz. Kombinace 3+ indikátorů
    v "indicator=A,B,C" API tiše zredukuje na 1-2 dominantní, zbytek
    zmizí beze stopy (ověřeno naživo, viz komentář u HISTORY_INDICATORS
    výš). Volající (backfill_history/backfill_open_ended) smyčkují přes
    indikátory samy a fetch_operational_month volají po jednom.

    KRITICKÉ #2: stránkování se NESMÍ řídit meta.total — API ho vrací
    nespolehlivě (echo zpět ~limit bez ohledu na skutečný zbytek dat,
    ověřeno). Jediný bezpečný stop podmínka je počet vrácených řádků <
    limit. Stejně tak dotaz NESMÍ pokrývat širší okno než jeden měsíc —
    server na širších oknech (ověřeno na kvartálu) TICHE, BEZ CHYBY vrátí
    jen zlomek dat (Q2 2024 → 3960 řádků místo ~23000+ za samotný červen),
    offset/limit stránkování to nijak nenaznačí. Platí i pro
    OPEN_ENDED_INDICATORS — proto backfill_open_ended taky používá
    měsíční okno, i když sémanticky jde o "aktuální stav", ne historii.

    Síťovou/HTTP chybu NEPOLYKÁME — musí probublat ven a odlišit se od
    legitimně prázdného měsíce (2020, kde archiv skutečně nic nevrací).
    Tichým "return []" na chybu by se selhání v logu nerozeznalo od
    opravdové nuly (ověřeno na prvním běhu: lokální SSL chyba prošla jako
    "0 řádků" pro všech 80 měsíců včetně těch s daty).
    """
    import urllib.parse
    ind_enc = urllib.parse.quote(indicator)
    all_rows, offset, limit = [], 0, 2000
    while True:
        url = (
            "https://transparency.entsog.eu/api/v1/operationaldata"
            f"?indicator={ind_enc}"
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
        if resp.status_code == 404 and "no result found" in resp.text.lower():
            # Legitimně prázdný výsledek pro danou kombinaci indikátor+
            # okno (ne archivní, jen nic k vrácení) — narozdíl od
            # "archived" zprávy tahle nenese žádný specifický důvod.
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


def backfill_history(indicators: list = None, log_path: str = None, start: date = None, end: date = None):
    """Jednorázový (resumable) backfill pro HISTORY_INDICATORS (skutečná
    denní historie) přes VŠECHNY body Evropy, po měsících od start
    (default HISTORY_START) do end (default dnešek). Přeskočí měsíce,
    které už mají partitioned soubor (idempotentní resume po přerušení)
    — KROMĚ posledního (aktuálního, ještě otevřeného) měsíce, ten se
    přefetchuje vždy. Uvnitř každého měsíce se fetchuje PO JEDNOM
    indikátoru (viz fetch_operational_month), výsledky se spojí a
    zapíšou jedním upsertem za měsíc."""
    import time as _time
    indicators = indicators or HISTORY_INDICATORS
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
    _log(log_path, f"=== Backfill (history) start {start} → {end}, {total} měsíců, indikátory={indicators} ===")

    for i, (year, month) in enumerate(months, 1):
        month_str = f"{year:04d}-{month:02d}"
        existing_path = os.path.join(OPERATIONAL_DIR, f"{month_str}.parquet")

        # Resume je PER INDIKÁTOR, ne per soubor — víc indikátorů teď
        # sdílí stejný měsíční soubor (konsolidovaná vrstva), takže
        # "soubor existuje" už dávno platí pro každý měsíc (díky dřív
        # zabackfillovaným Nomination/Renomination). Bez tyhle kontroly
        # by backfill NOVÉHO indikátoru (GCV, Wobbe Index, ...) přeskočil
        # úplně všechno — čte se jen sloupec "indicator" (levné, parquet
        # column projection), ne celý soubor.
        needed = indicators
        if os.path.exists(existing_path) and (year, month) != current_month:
            try:
                existing_ind = set(pd.read_parquet(existing_path, columns=["indicator"])["indicator"].unique())
            except Exception:
                existing_ind = set()
            needed = [ind for ind in indicators if ind not in existing_ind]
            if not needed:
                _log(log_path, f"[{i}/{total}] {month_str}: přeskočeno, všechny indikátory už v souboru")
                continue

        t0 = _time.time()
        from_date, to_date = _month_bounds(year, month)
        frames, ind_errors = [], []
        for indicator in needed:
            try:
                rows = fetch_operational_month(from_date, to_date, indicator)
                if rows:
                    frames.append(rows)
            except Exception as e:
                ind_errors.append(f"{indicator}: {e}")

        if ind_errors:
            _log(log_path, f"    ⚠ {month_str}: chyby u indikátorů — {'; '.join(ind_errors)}")

        elapsed = _time.time() - t0
        if not frames:
            _log(log_path, f"[{i}/{total}] {month_str}: 0 řádků ({elapsed:.1f}s)")
            continue

        df = _process([row for group in frames for row in group])
        if df.empty:
            _log(log_path, f"[{i}/{total}] {month_str}: 0 řádků po isNA filtru ({elapsed:.1f}s)")
            continue

        n_raw = len(df)
        n_unique = df["id"].nunique()
        if n_unique != n_raw:
            # Ojediněle pozorovaná přechodná nekonzistence živého API mezi
            # stránkami. id je deterministický klíč ze všech polí
            # (indicator+period+operator+point+direction+unit) —
            # duplicitní id tedy nemůže nést jinou hodnotu,
            # drop_duplicates(keep="last") v upsert_partitioned to řeší
            # bezpečně. Loguje se jen jako viditelné varování.
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
            f"[{i}/{total}] {month_str}: {n_total} řádků (soubor po mergi), {n_points} bodů, "
            f"{dict(by_ind)} {size_kb:.0f} KB ({elapsed:.1f}s)",
        )

    _log(log_path, "=== Backfill (history) hotovo ===")


def backfill_open_ended(indicators: list = None, log_path: str = None):
    """Jednorázový fetch pro OPEN_ENDED_INDICATORS (kapacita/interrupce)
    — ŽÁDNÁ měsíční smyčka přes roky, tyhle záznamy nesou validity okno
    (periodFrom/periodTo) samy o sobě, dotaz na "aktuální měsíc" zachytí
    všechno aktuálně platné. Fetchuje se PO JEDNOM indikátoru (viz
    fetch_operational_month). Zápis přes stejný upsert_partitioned jako
    historické indikátory — záznamy se starým periodFrom (klidně 2007)
    přirozeně skončí ve svém vlastním starém měsíčním souboru, to je OK,
    partitioning se řídí periodFrom_dt, ne datem fetchování."""
    import time as _time
    indicators = indicators or OPEN_ENDED_INDICATORS
    os.makedirs(OPERATIONAL_DIR, exist_ok=True)
    today = date.today()
    from_date, to_date = _month_bounds(today.year, today.month)

    t0 = _time.time()
    frames, ind_errors = [], []
    for indicator in indicators:
        try:
            rows = fetch_operational_month(from_date, to_date, indicator)
            if rows:
                frames.append(rows)
        except Exception as e:
            ind_errors.append(f"{indicator}: {e}")

    if ind_errors:
        _log(log_path, f"  ⚠ open-ended: chyby u indikátorů — {'; '.join(ind_errors)}")

    elapsed = _time.time() - t0
    if not frames:
        _log(log_path, f"open-ended ({from_date}→{to_date}): 0 řádků ({elapsed:.1f}s)")
        return

    df = _process([row for group in frames for row in group])
    if df.empty:
        _log(log_path, f"open-ended ({from_date}→{to_date}): 0 řádků po isNA filtru ({elapsed:.1f}s)")
        return

    n_raw = len(df)
    n_unique = df["id"].nunique()
    if n_unique != n_raw:
        _log(
            log_path,
            f"  ⚠ open-ended: {n_raw - n_unique} duplicitních id v raw fetchi "
            f"({n_raw} → {n_unique} unikátních) — dedup bezpečně vyřešeno",
        )

    touched = upsert_partitioned(df, OPERATIONAL_DIR, "periodFrom_dt", ["id"], fmt="parquet")
    written = [(mo, n) for mo, n, w in touched if w]
    by_ind = df["indicator"].value_counts().to_dict()
    n_points = df["pointLabel"].nunique()
    months_touched = sorted({mo for mo, n, w in touched})
    _log(
        log_path,
        f"open-ended: {len(df)} řádků raw, {n_points} bodů, rozprostřeno do "
        f"{len(written)}/{len(touched)} přepsaných měsíčních souborů "
        f"(rozsah {months_touched[0] if months_touched else '-'}"
        f"→{months_touched[-1] if months_touched else '-'}) {dict(by_ind)} ({elapsed:.1f}s)",
    )


def update_eu_operational():
    """Scheduled běh:
    - HISTORY_INDICATORS: přefetchuje jen posledních REVISION_WINDOW_MONTHS
      měsíců (hodnoty se v revizích mění, ne jen append).
    - OPEN_ENDED_INDICATORS: přefetchuje se VŽDY celé (jeden aktuální
      měsíc) — nové/zrušené kapacitní booking a interrupce se objevují
      kdykoliv, ne jen jako revize existujícího záznamu.
    Plný jednorázový backfill viz scripts/backfill_entsog_operational.py."""
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
        for indicator in HISTORY_INDICATORS:
            rows = fetch_operational_month(from_date, to_date, indicator)
            df = _process(rows)
            if not df.empty:
                frames.append(df)

    if frames:
        new_data = pd.concat(frames, ignore_index=True)
        touched = upsert_partitioned(new_data, OPERATIONAL_DIR, "periodFrom_dt", ["id"], fmt="parquet")
        written = [(mo, n) for mo, n, w in touched if w]
        print(f"ENTSOG operational (history): {sum(n for _, n in written)} řádků v {len(written)} přepsaných "
              f"měsících ({len(touched) - len(written)} beze změny) → {OPERATIONAL_DIR}/")
    else:
        print("ENTSOG operational (history): žádná data")

    backfill_open_ended(OPEN_ENDED_INDICATORS)


_CATEGORY_COLS = [
    "indicator", "periodType", "operatorKey", "operatorLabel",
    "pointKey", "pointLabel", "directionKey", "unit", "pointType",
    "flowStatus", "country",
]


def load_eu_operational() -> pd.DataFrame:
    """Načte celý konsolidovaný dataset (partitioned soubory, všechny
    HISTORY_ i OPEN_ENDED_ indikátory pohromadě) a přidá 'country' (viz
    country_from_operator_key).

    Sloupce s malým počtem unikátních hodnot (indicator, unit,
    directionKey, operatorKey/pointLabel, ...) se převádí na category
    dtype PŘÍMO TADY, uvnitř cachovaného _load() — ne až v app.py při
    každém rerunu. Důvod: bez tohohle měl obyčejný df.copy() (na
    přidání country sloupce) v app.py ArrayMemoryError na konsolidaci
    ~190MB object-dtype bloku (ověřeno naživo) — plain string sloupce
    opakované přes 1.5M+ řádků jsou v paměti řádově dražší než jejich
    pár set unikátních hodnot. category dtype tohle řeší už při jediném
    (cachovaném) načtení, downstream filtrování (==, .unique(),
    groupby) funguje na category stejně jako na str. id a
    periodFrom_dt/periodTo_dt zůstávají beze změny — id je skoro
    unikátní na řádek (kategorizace by nepomohla) a date sloupce se
    porovnávají (>=, <=) v grafech, což na category dtype není bezpečné."""
    def _load():
        df = read_partitioned(OPERATIONAL_DIR, fmt="parquet")
        if df.empty:
            return df
        df["country"] = df["operatorKey"].apply(country_from_operator_key)
        for col in _CATEGORY_COLS:
            if col in df.columns:
                df[col] = df[col].astype("category")
        return df
    try:
        import streamlit as st
        return st.cache_data(ttl=3600, show_spinner=False)(_load)()
    except ImportError:
        return _load()


# Dočasný alias — app.py zatím importuje load_cz_operational beze změny,
# dokud UI (výběr země/bodu pro celou Evropu) není potvrzené a upravené.
load_cz_operational = load_eu_operational
