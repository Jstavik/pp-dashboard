import json
import logging
import requests, time, os
from datetime import date, timedelta
import pandas as pd

from data.partitioned_store import read_partitioned, upsert_partitioned
from config import ENTSOG_OPERATIONAL_CACHE_TTL_SECONDS
from core.cache import ttl_cache
from core.settings import history_path

log = logging.getLogger(__name__)

OPERATIONAL_DIR = history_path("entsog_operational")

# Sloupce, které UI (Nominace, Kapacity) skutečně čte — id/periodFrom/
# periodTo/value/periodType/pointKey/pointType/flowStatus se nepoužívají.
# Načítat jen tyhle + category dtype = zlomek paměti (celý dataset má
# ~4.6M řádků, jako object sloupce ~4.8 GB).
UI_COLUMNS = [
    "indicator", "operatorKey", "operatorLabel", "pointLabel", "directionKey",
    "unit", "value_GWh", "periodFrom_dt", "periodTo_dt", "lastUpdateDateTime",
]
UI_CATEGORY_COLUMNS = ["indicator", "operatorKey", "operatorLabel", "pointLabel",
                       "directionKey", "unit"]

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
NOMINATION_QUALITY_INDICATORS = ["Nomination", "Renomination", "GCV", "Wobbe Index"]
# Physical Flow a Allocation PO BODECH (kolo 7, stránka Analýza toků
# plynu) — /operationaldata je vrací po bodech a operátorech stejně jako
# nominace (periodFrom = konkrétní den). Na úrovni zemí zůstává zdroj
# /aggregateddata (history/entsog_flows); po bodech je jediným zdrojem
# tahle vrstva.
POINT_FLOW_INDICATORS = ["Physical Flow", "Allocation"]
HISTORY_INDICATORS = NOMINATION_QUALITY_INDICATORS + POINT_FLOW_INDICATORS

# Podmnožiny HISTORY_INDICATORS pro panel Nominace (app.py, tab_nom):
# fig_cz_operational je navržená jen pro denní Entry/Exit graf, což sedí
# na Nomination/Renomination — GCV/Wobbe Index mají vlastní sekci
# "Kvalita plynu" (stejný graf, jiné umístění v UI, ne kapacitní tabulka).
NOMINATION_CHART_INDICATORS = ["Nomination", "Renomination"]
QUALITY_INDICATORS = ["GCV", "Wobbe Index"]

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
# Backfill pro Analýzu toků plynu (toky po bodech, kapacity, přerušení) —
# zkouší se od 2019; měsíce, které TP archivoval, vrátí 404 "archived" = 0 řádků.
POINT_HISTORY_START = date(2019, 1, 1)
# Kapacity a přerušení, které stránka Analýza toků plynu rozepisuje na dny —
# backfillují se PO MĚSÍCÍCH (dotaz na měsíc vrátí období platná v tom měsíci,
# tj. i dávno skončená), ne jen aktuální měsíc jako backfill_open_ended.
PERIOD_INDICATORS = [
    "Firm Technical", "Firm Booked", "Interruptible Booked",
    "Planned interruption of firm capacity", "Unplanned interruption of firm capacity",
    "Actual interruption of interruptible capacity",
]
# Starý loader kapacit (záložky Kapacity/Nominace) čte jen období platná
# v posledních N dnech — po měsíčním backfillu od 2019 by jinak do pandas
# šly miliony historických segmentů (RAM webu).
OPEN_ENDED_UI_DAYS = 800
# Pauza mezi požadavky na ENTSO-G (s) a opakování při 429/5xx/síťové chybě.
REQUEST_SLEEP = float(os.environ.get("PPDASH_ENTSOG_SLEEP", "1.0"))
MAX_RETRIES = 4

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
        resp = _get(url)
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
        time.sleep(REQUEST_SLEEP)
    return all_rows


def _get(url: str):
    """GET s rate-limitem: pauza REQUEST_SLEEP před každým požadavkem,
    při 429/5xx nebo síťové chybě opakování s rostoucí pauzou (Retry-After
    má přednost). Po MAX_RETRIES vrátí poslední odpověď / vyhodí chybu."""
    delay = max(REQUEST_SLEEP, 1.0)
    for attempt in range(MAX_RETRIES + 1):
        time.sleep(REQUEST_SLEEP if attempt == 0 else delay)
        try:
            resp = requests.get(url, timeout=90)
        except requests.RequestException:
            if attempt == MAX_RETRIES:
                raise
            delay *= 2
            continue
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
            try:
                delay = max(delay * 2, float(resp.headers.get("Retry-After", 0)))
            except (TypeError, ValueError):
                delay *= 2
            log.warning("ENTSO-G HTTP %s, opakuji za %.0f s", resp.status_code, delay)
            continue
        return resp
    return resp


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
    log.info(line)
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


def _progress_path() -> str:
    return os.path.join(OPERATIONAL_DIR, ".backfill_periods.json")


def backfill_periods(indicators: list = None, log_path: str = None, start: date = None, end: date = None):
    """Jednorázový (navazující) backfill kapacit a přerušení PO MĚSÍCÍCH
    od start (default POINT_HISTORY_START) do end (default dnes): dotaz na
    měsíc vrátí všechna období platná v tom měsíci, takže se zachytí i
    historická (už skončená) přerušení a kapacity. Záznamy se ukládají do
    měsíce podle periodFrom_dt (upsert podle id = revize se nezdvojí).

    Navazování: hotové (měsíc, indikátor) se zapisují do
    entsog_operational/.backfill_periods.json — přerušený běh pokračuje
    tam, kde skončil (soubor podle periodFrom by to neuměl poznat, záznamy
    padají do jiných měsíců, než se stahovaly). Aktuální měsíc se stahuje
    vždy. Rate-limit: _get (PPDASH_ENTSOG_SLEEP, opakování při 429/5xx)."""
    indicators = indicators or PERIOD_INDICATORS
    start = start or POINT_HISTORY_START
    end = end or date.today()
    os.makedirs(OPERATIONAL_DIR, exist_ok=True)
    try:
        with open(_progress_path(), encoding="utf-8") as f:
            done = set(json.load(f).get("done", []))
    except (OSError, ValueError):
        done = set()
    months = pd.period_range(pd.Timestamp(start), pd.Timestamp(end), freq="M")
    current = pd.Period(pd.Timestamp(end), freq="M")
    _log(log_path, f"=== Backfill (periods) {start} → {end}, {len(months)} měsíců, indikátory={indicators} ===")
    for i, per in enumerate(months, 1):
        from_date, to_date = _month_bounds(per.year, per.month)
        todo = [ind for ind in indicators if per == current or f"{per}|{ind}" not in done]
        if not todo:
            continue
        t0 = time.time()
        for ind in todo:
            try:
                rows = fetch_operational_month(from_date, to_date, ind)
            except Exception as e:
                _log(log_path, f"    ⚠ {per} {ind}: {e} — zkusí se při dalším běhu")
                continue
            df = _process(rows) if rows else pd.DataFrame()
            if not df.empty:
                upsert_partitioned(df, OPERATIONAL_DIR, "periodFrom_dt", ["id"], fmt="parquet")
            if per != current:
                done.add(f"{per}|{ind}")
                tmp = _progress_path() + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"done": sorted(done)}, f)
                os.replace(tmp, _progress_path())
            _log(log_path, f"[{i}/{len(months)}] {per} {ind}: {len(df)} řádků")
        _log(log_path, f"[{i}/{len(months)}] {per}: hotovo ({time.time() - t0:.0f} s)")
    _log(log_path, "=== Backfill (periods) hotovo ===")


def backfill_point_flows(log_path: str = None, start: date = None, end: date = None):
    """Physical Flow + Allocation po bodech od POINT_HISTORY_START (navazuje
    po měsících a indikátorech, viz backfill_history)."""
    backfill_history(POINT_FLOW_INDICATORS, log_path=log_path, start=start or POINT_HISTORY_START, end=end)


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
        log.info(f"ENTSOG operational (history): {sum(n for _, n in written)} řádků v {len(written)} přepsaných "
                 f"měsících ({len(touched) - len(written)} beze změny) → {OPERATIONAL_DIR}/")
    else:
        log.warning("ENTSOG operational (history): žádná data")

    backfill_open_ended(OPEN_ENDED_INDICATORS)


_CATEGORY_COLS = [
    "indicator", "periodType", "operatorKey", "operatorLabel",
    "pointKey", "pointLabel", "directionKey", "unit", "pointType",
    "flowStatus", "country",
]


def _country_series(operator_key: pd.Series) -> pd.Series:
    """country_from_operator_key přes celý sloupec — u category dtype se
    funkce volá jen jednou na kategorii (rychlé i na milionech řádků)."""
    if isinstance(operator_key.dtype, pd.CategoricalDtype):
        mapping = {c: country_from_operator_key(c) for c in operator_key.cat.categories}
        return operator_key.map(mapping).astype(object).fillna("??")
    return operator_key.astype(object).map(country_from_operator_key, na_action="ignore").fillna("??")


def _with_country(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df["country"] = _country_series(df["operatorKey"])
    for col in _CATEGORY_COLS:
        if col in df.columns and df[col].dtype != "category":
            df[col] = df[col].astype("category")
    return df


@ttl_cache(ttl=ENTSOG_OPERATIONAL_CACHE_TTL_SECONDS, maxsize=2)
def _load_operational(date_from=None, date_to=None) -> pd.DataFrame:
    df = read_partitioned(OPERATIONAL_DIR, fmt="parquet", date_from=date_from, date_to=date_to,
                          columns=UI_COLUMNS, categories=UI_CATEGORY_COLUMNS)
    return _with_country(df)


def load_eu_operational(date_from=None, date_to=None) -> pd.DataFrame:
    """Načte konsolidovaný dataset (partitioned soubory, všechny HISTORY_
    i OPEN_ENDED_ indikátory pohromadě) a přidá 'country' (viz
    country_from_operator_key).

    date_from/date_to (volitelné) omezí čtení na měsíční soubory v daném
    rozsahu (viz partitioned_store.read_partitioned) — bez nich se čte
    CELÁ historie od HISTORY_START (~4.6M řádků). Volající (typicky UI
    s výběrem konkrétního bodu/období) by měl date_from/date_to nastavit
    na rozumné okno, ne spoléhat na default = všechno. Web místo toho
    používá load_operational_point() (jen jeden bod, filtr při čtení).

    Čtou se jen UI_COLUMNS rovnou jako category (pyarrow) — dřív se
    nejdřív načetly všechny sloupce jako object a kategorizovaly až pak
    (špička paměti = plná object verze).

    POZOR pro OPEN_ENDED_INDICATORS (kapacita/interrupce): jejich
    periodFrom_dt nese VALIDITY okno, klidně roky staré (viz komentář u
    OPEN_ENDED_INDICATORS výš) — date-omezené volání je tak přirozeně
    NEBUDE zahrnovat celé, i když jsou aktuálně platné. Date-okno je
    bezpečné pro HISTORY_INDICATORS (Nomination/Renomination/GCV/Wobbe),
    kde periodFrom_dt odpovídá skutečnému dni. UI pro kapacitu/interrupce
    volá load_eu_operational_open_ended()."""
    return _load_operational(date_from, date_to)


@ttl_cache(ttl=ENTSOG_OPERATIONAL_CACHE_TTL_SECONDS, maxsize=4)
def operational_index(date_from=None) -> pd.DataFrame:
    """Malý přehled (country, pointLabel, indicator) — unikátní kombinace
    v HISTORY_INDICATORS datech od date_from. Pro výběrové seznamy v UI
    (země → bod → indikátor) bez načtení hodnot."""
    df = read_partitioned(OPERATIONAL_DIR, fmt="parquet", date_from=date_from,
                          columns=["operatorKey", "pointLabel", "indicator"],
                          categories=["operatorKey", "pointLabel", "indicator"])
    if df.empty:
        return pd.DataFrame(columns=["country", "pointLabel", "indicator"])
    df = df.dropna(subset=["pointLabel"]).drop_duplicates()
    df["country"] = _country_series(df["operatorKey"])
    out = df[["country", "pointLabel", "indicator"]].astype(str).drop_duplicates()
    return out[out["country"] != "??"].reset_index(drop=True)


@ttl_cache(ttl=ENTSOG_OPERATIONAL_CACHE_TTL_SECONDS, maxsize=16)
def load_operational_point(point_label: str, country: str = None, date_from=None, date_to=None) -> pd.DataFrame:
    """HISTORY_INDICATORS data jednoho bodu (Nomination/Renomination/GCV/
    Wobbe) — filtr na pointLabel a indikátory se aplikuje UŽ PŘI ČTENÍ
    (pyarrow pushdown), v paměti je jen ten jeden bod. country (ISO
    prefix operatorKey) rozliší body, které pod stejným pointLabel hlásí
    obě strany hranice (např. Cieszyn)."""
    df = read_partitioned(
        OPERATIONAL_DIR, fmt="parquet", date_from=date_from, date_to=date_to,
        columns=UI_COLUMNS,
        filters=[("pointLabel", "==", point_label), ("indicator", "in", NOMINATION_QUALITY_INDICATORS)],
    )
    df = _with_country(df)
    if not df.empty and country:
        df = df[df["country"] == country]
    return df.reset_index(drop=True)


@ttl_cache(ttl=ENTSOG_OPERATIONAL_CACHE_TTL_SECONDS, maxsize=1)
def _load_open_ended() -> pd.DataFrame:
    # Filtr VYLOUČENÍM HISTORY_INDICATORS (viz docstring níž) přímo při
    # čtení — dřív per-soubor pandas row_filter přes plné načtení.
    df = read_partitioned(
        OPERATIONAL_DIR, fmt="parquet",
        columns=UI_COLUMNS, categories=UI_CATEGORY_COLUMNS,
        filters=[("indicator", "not in", HISTORY_INDICATORS),
                 ("periodTo_dt", ">=", date.today() - timedelta(days=OPEN_ENDED_UI_DAYS))],
    )
    if df.empty:
        return df
    df["country"] = _country_series(df["operatorKey"]).astype("category")
    return df


def load_eu_operational_open_ended() -> pd.DataFrame:
    """Načte kapacitní/interrupční (OPEN_ENDED_INDICATORS-typu) záznamy
    napříč CELOU historií, BEZ date-okna — na rozdíl od load_eu_operational.

    Důvod: periodFrom_dt u těchhle indikátorů nese VALIDITY okno, klidně
    z roku 2013 (ověřeno naživo na VIP Brandov — Firm Booked periodFrom
    2013-10-16 → 2026-08-01, periodTo až 2050), zatímco jejich aktuální
    platnost (periodTo_dt >= dnes) je nezávislá na tom, jak starý je
    periodFrom_dt. Date-okno (viz load_eu_operational, řeší panel
    Nominace) by aktuálně platné, ale staré záznamy nesprávně vyřadilo.

    Filtruje se VYLOUČENÍM HISTORY_INDICATORS, ne přesným seznamem
    OPEN_ENDED_INDICATORS — v uložených datech se vyskytuje i varianta
    názvu mimo kanonický seznam ("Planned interruption of interruptible
    capacity", zjevně pozůstatek dřívější verze fetch configu, ověřeno
    naživo). Vyloučení je vůči tomu odolné, přesný seznam by tenhle
    záznam tiše ztratil.

    Paměťově bezpečné: filtr se aplikuje při čtení (pyarrow), OPEN_ENDED
    indikátory jsou řádově menší objem než HISTORY_ (~130k řádků)."""
    return _load_open_ended()


# Dočasný alias — app.py zatím importuje load_cz_operational beze změny,
# dokud UI (výběr země/bodu pro celou Evropu) není potvrzené a upravené.
load_cz_operational = load_eu_operational


def active_capacity_dedup(df_point: pd.DataFrame, target_dates: list) -> pd.Series:
    """Denní časová řada pro JEDEN bod+operátor+směr+indikátor (typicky
    Firm/Interruptible Technical/Available) z OPEN_ENDED_INDICATORS dat.

    KRITICKÉ (ověřeno naživo na VIP Brandov/GASCADE/Firm Technical, viz
    panel Nominace): tenhle typ indikátoru se v datech objevuje jako
    NEUSPOŘÁDANÉ PŘEKRÝVAJÍCÍ SE validity segmenty — pro jeden konkrétní
    den `d` může existovat 5-10 řádků, kde periodFrom_dt <= d <=
    periodTo_dt současně platí (revize/re-publikace se stejným nebo
    posunutým oknem). Naivní `.sum()` přes všechny takové řádky pro
    daný den DEN VÝSLEDEK 5-10× NADHODNOTÍ (ověřený bug, ne teoretický
    — stejná chyba jako u data/entsog_capacity.py::expand_capacity()).

    Správně: pro každý den `d` ze VŠECH řádků, co ho pokrývají, vzít JEN
    ten s nejnovějším lastUpdateDateTime (poslední publikovaná revize),
    ne sumu. Dny bez pokrývajícího řádku se do výsledku vůbec nezahrnou
    (ne 0) — volající tak může vykreslit čáru, co končí tam, kde končí
    skutečná data, místo aby spadla na nulu (viz panel Kapacita v
    app.py::tab_cap, "konec dostupných dat" značka)."""
    if df_point.empty:
        return pd.Series(dtype="float64")

    df_point = df_point.copy()
    df_point["_from"] = pd.to_datetime(df_point["periodFrom_dt"])
    df_point["_to"] = pd.to_datetime(df_point["periodTo_dt"])
    df_point["_upd"] = pd.to_datetime(df_point["lastUpdateDateTime"], utc=True)
    df_point = df_point.sort_values("_upd")

    values = {}
    for d in target_dates:
        # periodFrom_dt/periodTo_dt jsou tz-naive (obyčejné kalendářní dny) —
        # target_dates volající typicky staví z tz-aware "teď" (Europe/Prague),
        # takže d_ts musí přijít o tz, jinak pandas porovnání tvrdě spadne
        # (tz-naive vs tz-aware datetime64 nejde srovnat).
        d_ts = pd.Timestamp(d)
        if d_ts.tzinfo is not None:
            d_ts = d_ts.tz_localize(None)
        mask = (df_point["_from"] <= d_ts) & (df_point["_to"] >= d_ts)
        cand = df_point.loc[mask]
        if cand.empty:
            continue
        values[d_ts] = cand.iloc[-1]["value_GWh"]  # sorted by _upd asc → last = nejnovější revize

    return pd.Series(values).sort_index()
