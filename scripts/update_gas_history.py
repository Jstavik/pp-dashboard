import sys
sys.path.insert(0, ".")
import requests
import pandas as pd
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
import os
import time
from data.entsog_capacity import update_capacity
from data.entsog_operational import update_cz_operational
from data.lng import update_lng
from data.gassco import update_gassco, update_gassco_umm
from data.dap_europe import DAP_COUNTRIES
from data.partitioned_store import upsert_partitioned, last_date_partitioned, write_daily_snapshot
from config import (
    ENTSOE_TOKEN, ENTSOE_OUTAGE_REVISION_WINDOW_DAYS,
    GENERATION_CHUNK_DAYS, GENERATION_CHUNK_RETRIES,
    PSR_CODE_BY_SOURCE_TYPE, COUNTRIES, COUNTRY_TIMEZONES,
)

# ENTSO-G aggregateddata API sahá do 2020, dříve není dostupné
HISTORY_START  = date(2020, 1, 1)
# Měsíčně partitionované úložiště (viz data/partitioned_store.py) — staré
# monolitické soubory (entsog_all_flows.parquet, gie_all_storage.csv,
# hydro_reservoirs.csv) zůstávají v repu jako needitovaný fallback, appka
# i update funkce už čtou/píšou jen do těchto adresářů.
ENTSOG_FLOWS_DIR = "data/history/entsog_flows"
GIE_CSV_PATH   = "data/history/gie_cz_storage.csv"
GIE_ALL_DIR    = "data/history/gie_all_storage"
GIE_KEY        = "628043ec28b2f2395a95f5adad7ec983"
COUNTRIES_GIE  = ["AT", "BE", "CZ", "DE", "FR", "HR", "HU", "IT", "LV", "NL", "PL", "PT", "RO", "RS", "SK", "ES", "UA"]
EU_CODE        = "EU"

HYDRO_DIR      = "data/history/hydro_reservoirs"
HYDRO_COUNTRIES = [
    "FR", "AT", "CH", "ES", "PT", "IT", "NO", "SE", "FI",
    "RO", "BG", "GR", "HR", "SI", "RS", "ME", "MK", "AL", "LT", "LV",
]

KEEP_COLS = [
    "periodFrom", "countryKey", "countryLabel",
    "directionKey", "adjacentSystemsKey", "adjacentSystemsLabel",
    "pointsNames", "value", "unit", "flowStatus",
]


def fetch_all_pages(from_date: date, to_date: date) -> pd.DataFrame:
    """Stáhne všechny stránky pro dané období — všechny země."""
    all_rows = []
    offset   = 0
    limit    = 2000

    while True:
        url = (
            "https://transparency.entsog.eu/api/v1/aggregateddata"
            f"?from={from_date}&to={to_date}"
            "&indicator=Physical%20Flow&periodType=day"
            f"&timezone=CET&limit={limit}&offset={offset}&format=json"
        )
        try:
            resp = requests.get(url, timeout=60)
            if resp.status_code != 200:
                print(f"    HTTP {resp.status_code} pro {from_date}–{to_date}, přeskakuji")
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
            print(f"    Chyba: {e}")
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


def update_entsog():
    """ENTSO-G fyzické toky — měsíčně partitionované úložiště (viz
    data/partitioned_store.py). Přepíše jen měsíce dotčené novým stahovaným
    oknem, starší uzavřené měsíce zůstanou v gitu beze změny."""
    os.makedirs("data/history", exist_ok=True)

    last_date = last_date_partitioned(ENTSOG_FLOWS_DIR, "date", "parquet")
    if last_date is not None:
        start = last_date.date() - timedelta(days=7)
        print(f"Existující data do {last_date.date()}, stahuji od {start}")
    else:
        start = HISTORY_START
        print(f"Nový soubor, stahuji od {start}")

    frames  = []
    current = start
    today   = date.today()

    while current <= today:
        end = min(current + timedelta(days=6), today)
        print(f"  {current} → {end} ...")
        df_week = fetch_all_pages(current, end)
        if not df_week.empty:
            frames.append(df_week)
        current = end + timedelta(days=1)
        time.sleep(0.5)

    if not frames:
        print("Žádná nová data.")
        return

    new_data = pd.concat(frames, ignore_index=True)
    key_cols = ["date", "countryKey", "directionKey",
                "adjacentSystemsKey", "pointsNames"]
    key_cols = [c for c in key_cols if c in new_data.columns]

    touched = upsert_partitioned(new_data, ENTSOG_FLOWS_DIR, "date", key_cols, fmt="parquet")
    written = [(m, n) for m, n, w in touched if w]
    print(f"ENTSO-G: {sum(n for _, n in written)} řádků v {len(written)} přepsaných měsících "
          f"({len(touched) - len(written)} beze změny) → {ENTSOG_FLOWS_DIR}/")



def fetch_gie_all_countries() -> pd.DataFrame:
    """Stáhne GIE historii pro všechny země + EU agregát."""
    import time
    all_frames = []

    targets = [(cc, f"country={cc}") for cc in COUNTRIES_GIE]
    targets.append(("EU", "type=eu"))

    for cc, param in targets:
        print(f"  GIE {cc}...")
        frames = []
        for page in range(1, 50):
            url = f"https://agsi.gie.eu/api?{param}&size=300&page={page}"
            try:
                resp = requests.get(
                    url,
                    headers={"x-key": GIE_KEY},
                    timeout=30,
                )
            except Exception as e:
                print(f"    timeout/chyba str. {page}: {e}")
                break
            if resp.status_code != 200:
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
    # reprezentační odchylka (ne skutečná změna dat) rozbíjela detekci
    # "obsah beze změny" v upsert_partitioned a odstávky by se v gitu
    # zbytečně přepisovaly při každém běhu.
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
    # round-trip se nestabilně mění mezi listem a stringem "[]", stejný
    # problém jako výše. Nikde v appce se nečte, zahazujeme.
    combined = combined.drop(columns=["info"], errors="ignore")

    return combined.sort_values(["country_code", "gasDayStart"])


def update_gie_all():
    """GIE storage všechny země — měsíčně partitionované úložiště.

    fetch_gie_all_countries() bohužel vždy stahuje CELOU historii (GIE AGSI
    API nemá parametr pro časové okno) — stará uzavřená data se tedy
    přeposílají znovu při každém běhu. upsert_partitioned ale díky kontrole
    "obsah beze změny → nepřepisovat" nechá uzavřené měsíce netknuté."""
    os.makedirs("data/history", exist_ok=True)
    new_data = fetch_gie_all_countries()
    if new_data.empty:
        print("GIE all: žádná data")
        return

    touched = upsert_partitioned(new_data, GIE_ALL_DIR, "gasDayStart",
                                   ["country_code", "gasDayStart"], fmt="csv")
    written = [(m, n) for m, n, w in touched if w]
    print(f"GIE all: {sum(n for _, n in written)} řádků v {len(written)} přepsaných měsících "
          f"({len(touched) - len(written)} beze změny) → {GIE_ALL_DIR}/")


def update_hydro():
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from entsoe import EntsoePandasClient

    os.makedirs("data/history", exist_ok=True)
    client = EntsoePandasClient(api_key=ENTSOE_TOKEN)

    last_date = last_date_partitioned(HYDRO_DIR, "date", "csv")
    if last_date is not None:
        start = last_date - timedelta(days=30)
        print(f"Hydro: existující data do {last_date.date()}")
    else:
        start = date(2020, 1, 1)
        print("Hydro: nový soubor")

    start_raw = pd.Timestamp(start)
    start_ts  = (start_raw.tz_localize("UTC")
                 if start_raw.tzinfo is None
                 else start_raw.tz_convert("UTC"))
    end_ts    = pd.Timestamp.now(tz="UTC")

    frames = []
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
            print(f"  ✓ {cc}: {len(df)} týdnů")
        except Exception as e:
            print(f"  ✗ {cc}: {str(e)[:60]}")

    if not frames:
        print("Hydro: žádná data")
        return

    new_data = pd.concat(frames, ignore_index=True)
    touched = upsert_partitioned(new_data, HYDRO_DIR, "date", ["date", "country"], fmt="csv")
    written = [(m, n) for m, n, w in touched if w]
    print(f"Hydro: {sum(n for _, n in written)} řádků v {len(written)} přepsaných měsících "
          f"({len(touched) - len(written)} beze změny) → {HYDRO_DIR}/")


def update_dap_europe():
    from entsoe import EntsoePandasClient
    from concurrent.futures import ThreadPoolExecutor, as_completed
    print("\n=== DAP Europe ===")

    DAP_DIR = "data/history/dap_europe"

    client = EntsoePandasClient(api_key=ENTSOE_TOKEN)

    # +1 den: den-ahead ceny na dnešek jsou publikované už včera, takže
    # dnešek je kompletní den dat a musí být celý zahrnutý v dotazu.
    end = pd.Timestamp.now(tz="Europe/Prague").normalize() + pd.Timedelta(days=1)

    last_existing = last_date_partitioned(DAP_DIR, "date", "parquet")
    if last_existing is not None:
        start = pd.Timestamp(last_existing.date(), tz="Europe/Prague") - pd.Timedelta(days=1)
    else:
        start = end - pd.Timedelta(days=3)

    def fetch_one(args):
        cc, code = args
        try:
            d = client.query_day_ahead_prices(code, start=start, end=end)
            d = d.tz_convert("Europe/Prague").resample("1h").mean()

            # Vezmi jen kompletní dny (>= 20 hodinových bodů) — poslední den
            # v odpovědi ENTSO-E může být jen okrajový bod (např. 00:00),
            # což by se mylně vzalo jako "dnešek" a peak (8-20h) by vyšel NaN.
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
            print(f"  {cc}: CHYBA — {e}")
            return None

    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(fetch_one, item): item
                   for item in DAP_COUNTRIES.items()}
        results = [f.result() for f in as_completed(futures)
                   if f.result() is not None]

    if not results:
        print("DAP Europe: žádná data")
        return

    df_new = pd.DataFrame(results)
    touched = upsert_partitioned(df_new, DAP_DIR, "date", ["date", "cc"], fmt="parquet")
    written = [(m, n) for m, n, w in touched if w]
    if not written:
        print("DAP Europe: data už aktuální, beze změny")
        return
    print(f"DAP Europe: {len(df_new)} zemí, {len(written)} přepsaný měsíc → {DAP_DIR}/")


def update_nuclear_fr_generation():
    """Jaderná výroba FR (ENTSO-E query_generation, psr_type=B14) →
    měsíčně partitionované úložiště (krok 0 vzor) —
    data/history/nuclear_fr_generation/{YYYY-MM}.parquet."""
    from entsoe import EntsoePandasClient
    import pandas as pd
    GEN_DIR = "data/history/nuclear_fr_generation"
    client = EntsoePandasClient(api_key=ENTSOE_TOKEN)
    tz = "Europe/Paris"

    last_date = last_date_partitioned(GEN_DIR, "date", "parquet")
    if last_date is not None:
        start = last_date.tz_convert(tz).normalize()
        if start.date() >= pd.Timestamp.now(tz=tz).date():
            print("Nuclear FR gen: aktuální, skip")
            return
    else:
        start = pd.Timestamp("2020-01-01", tz=tz)
    end = pd.Timestamp.now(tz=tz) + pd.Timedelta(days=1)

    df_new = client.query_generation("FR", start=start, end=end, psr_type="B14")
    df_new.columns = ["nuclear_mw"]
    df_new.index.name = "date"
    df_new = df_new.reset_index()
    local = df_new["date"].dt.tz_convert(tz)
    df_new["year"] = local.year
    df_new["day_of_year"] = local.day_of_year

    touched = upsert_partitioned(df_new, GEN_DIR, "date", ["date"], fmt="parquet")
    written = [(m, n) for m, n, w in touched if w]
    print(f"Nuclear FR gen: {sum(n for _, n in written)} řádků v {len(written)} přepsaných měsících "
          f"({len(touched) - len(written)} beze změny) → {GEN_DIR}/")


def update_nuclear_fr_outages():
    """Stáhne jaderné odstávky FR (ENTSO-E unavailability) jen za posledních
    ENTSOE_OUTAGE_REVISION_WINDOW_DAYS dní zpět → next_year_end a
    zmerguje s existujícím parquetem.

    Odstávky se v revizích mění (docstatus, avail_qty), ale jen ty
    "aktivní/blízké" — revize starší než pár týdnů se prakticky nedějí
    (předpoklad, needěláno na historických datech). Proto na rozdíl od
    update_nuclear_fr_generation() nejde o čistý append: záznamy, jejichž
    odstávka skončila před stahovaným oknem, zůstávají z parquetu beze
    změny; záznamy uvnitř okna (mohly dostat novou revizi) se z API
    stažení přepíšou celé.
    """
    from entsoe import EntsoePandasClient
    import pandas as pd, os
    PATH = "data/history/nuclear_fr_outages.parquet"
    client = EntsoePandasClient(api_key=ENTSOE_TOKEN)

    now = pd.Timestamp.now(tz="Europe/Paris")
    fetch_start = now - pd.Timedelta(days=ENTSOE_OUTAGE_REVISION_WINDOW_DAYS)
    next_year_end = pd.Timestamp(year=now.year + 1, month=12, day=31, tz="Europe/Paris")

    df_out = client.query_unavailability_of_generation_units(
        "FR", start=fetch_start, end=next_year_end, docstatus=None
    )
    nuclear = df_out[df_out["plant_type"] == "Nuclear"].copy()
    nuclear["nominal_power"] = pd.to_numeric(nuclear["nominal_power"], errors="coerce").fillna(0)
    nuclear["avail_qty"] = pd.to_numeric(nuclear["avail_qty"], errors="coerce").fillna(0)

    # Nejnovější revision pro každý blok+interval
    latest = nuclear.sort_values("revision").groupby(
        ["production_resource_name", "start", "end"]
    ).last().reset_index()
    latest = latest[latest["docstatus"] != "Cancelled"]
    latest["unavail_mw"] = latest["nominal_power"] - latest["avail_qty"]

    cols = ["production_resource_name", "start", "end", "nominal_power",
            "avail_qty", "unavail_mw", "docstatus", "businesstype"]
    latest = latest[cols].reset_index(drop=True)

    if os.path.exists(PATH):
        existing = pd.read_parquet(PATH)
        frozen = existing[existing["end"] < fetch_start]
        combined = pd.concat([frozen, latest], ignore_index=True)
    else:
        frozen = latest.iloc[0:0]
        combined = latest

    combined = combined.sort_values(["production_resource_name", "start"]).reset_index(drop=True)
    combined.to_parquet(PATH, index=False)
    print(f"Nuclear FR outages: {len(latest)} z API (okno {fetch_start.date()}→{next_year_end.date()}) "
          f"+ {len(frozen)} beze změny z parquetu → {len(combined)} celkem → {PATH}")


def _melt_hu_generation(df_wide: pd.DataFrame) -> pd.DataFrame:
    """Wide/MultiIndex df z query_generation("HU", ..., psr_type=None) →
    long formát (date, source_type, psr_code, mw). Zachovává jen sloupce
    'Actual Aggregated' (zahazuje 'Actual Consumption', např. u přečerpávání)."""
    cols = ["date", "source_type", "psr_code", "mw"]
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
        print(f"    VAROVÁNÍ: nenamapované source_type v PSR_CODE_BY_SOURCE_TYPE: {unmapped}")

    long["psr_code"] = long["source_type"].map(PSR_CODE_BY_SOURCE_TYPE)
    return long[cols]


def update_hu_generation():
    """Výroba HU podle zdroje (ENTSO-E query_generation, psr_type=None) →
    long formát parquet (date, source_type, psr_code, mw).

    Stahuje po GENERATION_CHUNK_DAYS-denních chuncích s
    GENERATION_CHUNK_RETRIES pokusy na chunk — ENTSO-E vrací 503/504 na
    širokých rozsazích. Měsíčně partitionované úložiště (krok 0 vzor) —
    při existujících datech stahuje jen od last_date (s pár dny přesahu
    pro jistotu), jinak celý backfill od HISTORY_START."""
    from entsoe import EntsoePandasClient
    import pandas as pd, time
    GEN_DIR = "data/history/hu_generation"
    client = EntsoePandasClient(api_key=ENTSOE_TOKEN)
    tz = "Europe/Budapest"

    last_date = last_date_partitioned(GEN_DIR, "date", "parquet")
    if last_date is not None:
        start = last_date.tz_convert(tz) - pd.Timedelta(days=3)
        print(f"HU gen: existující data do {last_date.date()}, stahuji od {start.date()}")
    else:
        start = pd.Timestamp(HISTORY_START, tz=tz)
        print(f"HU gen: nový soubor, plný backfill od {start.date()}")

    end = pd.Timestamp.now(tz=tz) + pd.Timedelta(days=1)

    frames = []
    cur = start
    while cur < end:
        chunk_end = min(cur + pd.Timedelta(days=GENERATION_CHUNK_DAYS), end)
        for attempt in range(1, GENERATION_CHUNK_RETRIES + 1):
            try:
                raw = client.query_generation("HU", start=cur, end=chunk_end, psr_type=None)
                frames.append(_melt_hu_generation(raw))
                print(f"  {cur.date()} → {chunk_end.date()}: OK")
                break
            except Exception as e:
                print(f"  {cur.date()} → {chunk_end.date()}: pokus {attempt}/{GENERATION_CHUNK_RETRIES} "
                      f"selhal — {str(e)[:80]}")
                if attempt < GENERATION_CHUNK_RETRIES:
                    time.sleep(5)
        cur = chunk_end
        time.sleep(1)

    if not frames:
        print("HU gen: žádná nová data")
        return

    new_data = pd.concat(frames, ignore_index=True)
    if new_data.empty:
        print("HU gen: žádná nová data")
        return

    touched = upsert_partitioned(new_data, GEN_DIR, "date", ["date", "source_type"], fmt="parquet")
    written = [(m, n) for m, n, w in touched if w]
    print(f"HU gen: {sum(n for _, n in written)} řádků v {len(written)} přepsaných měsících "
          f"({len(touched) - len(written)} beze změny) → {GEN_DIR}/")


def update_hu_outages():
    """Odstávky HU (ENTSO-E unavailability) — stejná architektura jako
    update_nuclear_fr_outages(), ale BEZ filtru na plant_type (u FR šlo jen
    o jádro, u HU chceme rozpad podle všech typů zdrojů). Rolling window
    [now - ENTSOE_OUTAGE_REVISION_WINDOW_DAYS, next_year_end] + merge se
    starým parquetem, protože odstávky se v revizích mění."""
    from entsoe import EntsoePandasClient
    import pandas as pd, os
    PATH = "data/history/hu_outages.parquet"
    client = EntsoePandasClient(api_key=ENTSOE_TOKEN)

    now = pd.Timestamp.now(tz="Europe/Budapest")
    fetch_start = now - pd.Timedelta(days=ENTSOE_OUTAGE_REVISION_WINDOW_DAYS)
    next_year_end = pd.Timestamp(year=now.year + 1, month=12, day=31, tz="Europe/Budapest")

    df_out = client.query_unavailability_of_generation_units(
        "HU", start=fetch_start, end=next_year_end, docstatus=None
    ).copy()
    df_out["nominal_power"] = pd.to_numeric(df_out["nominal_power"], errors="coerce").fillna(0)
    df_out["avail_qty"] = pd.to_numeric(df_out["avail_qty"], errors="coerce").fillna(0)

    # Nejnovější revision pro každý blok+interval
    latest = df_out.sort_values("revision").groupby(
        ["production_resource_name", "start", "end"]
    ).last().reset_index()
    latest = latest[latest["docstatus"] != "Cancelled"]
    latest["unavail_mw"] = latest["nominal_power"] - latest["avail_qty"]

    cols = ["production_resource_name", "plant_type", "start", "end", "nominal_power",
            "avail_qty", "unavail_mw", "docstatus", "businesstype"]
    latest = latest[cols].reset_index(drop=True)

    if os.path.exists(PATH):
        existing = pd.read_parquet(PATH)
        frozen = existing[existing["end"] < fetch_start]
        combined = pd.concat([frozen, latest], ignore_index=True)
    else:
        frozen = latest.iloc[0:0]
        combined = latest

    combined = combined.sort_values(["production_resource_name", "start"]).reset_index(drop=True)
    combined.to_parquet(PATH, index=False)
    print(f"HU outages: {len(latest)} z API (okno {fetch_start.date()}→{next_year_end.date()}) "
          f"+ {len(frozen)} beze změny z parquetu → {len(combined)} celkem → {PATH}")


def _melt_generation(df_wide: pd.DataFrame, country: str) -> pd.DataFrame:
    """Wide/MultiIndex df z query_generation(country, ..., psr_type=None) →
    long formát (date, country, source_type, psr_code, mw). Zachovává jen
    sloupce 'Actual Aggregated' (zahazuje 'Actual Consumption', např. u
    přečerpávání). Obecná verze data/hu_generation.py::_melt_hu_generation
    z kroku 1 — krok 2 nahrazuje per-country funkce touhle jednou."""
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
        print(f"    VAROVÁNÍ: nenamapované source_type v PSR_CODE_BY_SOURCE_TYPE ({country}): {unmapped}")

    long["country"] = country
    long["psr_code"] = long["source_type"].map(PSR_CODE_BY_SOURCE_TYPE)
    return long[cols]


def update_generation(country: str):
    """Výroba podle zdroje pro danou zemi (ENTSO-E query_generation,
    psr_type=None) → sjednocené partitionované úložiště
    data/history/generation/<country>/{YYYY-MM}.parquet.

    Krok 2 obecné datové vrstvy — nahrazuje postupně
    update_nuclear_fr_generation/update_hu_generation (ty zatím běží dál
    a udržují staré soubory jako fallback, dokud nová appka není ověřená
    — krok 4). Stahuje po GENERATION_CHUNK_DAYS chuncích s
    GENERATION_CHUNK_RETRIES pokusy (ENTSO-E padá na 503/504 na širokých
    rozsazích) — vzor z update_hu_generation."""
    from entsoe import EntsoePandasClient
    import pandas as pd, time
    client = EntsoePandasClient(api_key=ENTSOE_TOKEN)
    tz = COUNTRY_TIMEZONES[country]
    base_dir = f"data/history/generation/{country}"

    last_date = last_date_partitioned(base_dir, "date", "parquet")
    if last_date is not None:
        start = last_date.tz_convert(tz) - pd.Timedelta(days=3)
        print(f"Generation {country}: existující data do {last_date.date()}, stahuji od {start.date()}")
    else:
        start = pd.Timestamp(HISTORY_START, tz=tz)
        print(f"Generation {country}: nový soubor, plný backfill od {start.date()}")

    end = pd.Timestamp.now(tz=tz) + pd.Timedelta(days=1)

    frames = []
    cur = start
    while cur < end:
        chunk_end = min(cur + pd.Timedelta(days=GENERATION_CHUNK_DAYS), end)
        for attempt in range(1, GENERATION_CHUNK_RETRIES + 1):
            try:
                raw = client.query_generation(country, start=cur, end=chunk_end, psr_type=None)
                frames.append(_melt_generation(raw, country))
                print(f"  {country} {cur.date()} → {chunk_end.date()}: OK")
                break
            except Exception as e:
                print(f"  {country} {cur.date()} → {chunk_end.date()}: pokus {attempt}/{GENERATION_CHUNK_RETRIES} "
                      f"selhal — {str(e)[:80]}")
                if attempt < GENERATION_CHUNK_RETRIES:
                    time.sleep(5)
        cur = chunk_end
        time.sleep(1)

    if not frames:
        print(f"Generation {country}: žádná nová data")
        return
    new_data = pd.concat(frames, ignore_index=True)
    if new_data.empty:
        print(f"Generation {country}: žádná nová data")
        return

    touched = upsert_partitioned(new_data, base_dir, "date", ["date", "source_type"], fmt="parquet")
    written = [(m, n) for m, n, w in touched if w]
    print(f"Generation {country}: {sum(n for _, n in written)} řádků v {len(written)} přepsaných měsících "
          f"({len(touched) - len(written)} beze změny) → {base_dir}/")


def update_outages(country: str):
    """Odstávky pro danou zemi (ENTSO-E unavailability) — BEZ filtru na
    plant_type, všechny typy zdrojů (stejně jako HU v kroku 1, ne jen
    jádro jako u staré FR verze). Rolling window
    [now - ENTSOE_OUTAGE_REVISION_WINDOW_DAYS, next_year_end], merge do
    JEDNOHO sdíleného data/history/outages.parquet (víc zemí ve stejném
    souboru — na rozdíl od per-country partitioningu u výroby, odstávky
    se revidují, denní/country split nedává smysl, viz krok 0/krok 1).

    Krok 2 obecné datové vrstvy — nahrazuje postupně
    update_nuclear_fr_outages/update_hu_outages (ty zatím běží dál a
    udržují staré soubory jako fallback, dokud nová appka není ověřená
    — krok 4)."""
    from entsoe import EntsoePandasClient
    import pandas as pd, os
    PATH = "data/history/outages.parquet"
    client = EntsoePandasClient(api_key=ENTSOE_TOKEN)
    tz = COUNTRY_TIMEZONES[country]

    now = pd.Timestamp.now(tz=tz)
    fetch_start = now - pd.Timedelta(days=ENTSOE_OUTAGE_REVISION_WINDOW_DAYS)
    next_year_end = pd.Timestamp(year=now.year + 1, month=12, day=31, tz=tz)

    df_out = client.query_unavailability_of_generation_units(
        country, start=fetch_start, end=next_year_end, docstatus=None
    ).copy()
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

    if os.path.exists(PATH):
        existing = pd.read_parquet(PATH)
        # zamrzlé: záznamy jiných zemí (netýká se tohohle běhu) + záznamy
        # téhle země mimo stahované okno (odstávka skončila dřív)
        frozen = existing[(existing["country"] != country) | (existing["end"] < fetch_start_utc)]
    else:
        frozen = latest.iloc[0:0]
    combined = pd.concat([frozen, latest], ignore_index=True)
    # Pojistka proti reálně pozorovanému jevu: ENTSO-E občas vrací i
    # záznamy dávno uzavřených odstávek (end hluboko před fetch_start),
    # i když o ně query start/end okno vůbec nežádá. Takový záznam je
    # zároveň v "frozen" (end < fetch_start, correctly preserved) i v
    # "latest" (API ho znovu vrátilo) → beze slitování by se hromadil
    # o 1 kopii při každém běhu. V produkci takhle nabujel jeden řádek
    # (FR/PROVENCE 4) na 32 kopií za ~2 týdny dvou běhů denně, než jsme
    # na to přišli — vyčištěno + tahle pojistka to natrvalo drží na 1.
    combined = combined.drop_duplicates(
        subset=["country", "production_resource_name", "start", "end"], keep="last")

    combined = combined.sort_values(["country", "production_resource_name", "start"]).reset_index(drop=True)
    combined.to_parquet(PATH, index=False)
    n_frozen_country = int((frozen["country"] == country).sum()) if not frozen.empty else 0
    print(f"Outages {country}: {len(latest)} z API (okno {fetch_start.date()}→{next_year_end.date()}) "
          f"+ {n_frozen_country} beze změny z {country} + {len(frozen) - n_frozen_country} ostatní země "
          f"→ {len(combined)} celkem → {PATH}")

    # Denní snapshot celého (všechny země) outages.parquet pro fig_outages_delta
    # — write-once, no-op pokud dnešní snapshot už existuje (viz
    # data/partitioned_store.py::write_daily_snapshot).
    snap_written = write_daily_snapshot(combined, "data/history/outages_snapshots", fmt="parquet")
    print(f"Outages snapshot: {'zapsán nový den' if snap_written else 'dnešní už existuje, beze změny'}")


if __name__ == "__main__":
    runs = [
        ("ENTSO-G flows (všechny země)",      update_entsog),
        ("GIE storage — všechny země",        update_gie_all),
        ("Hydro reservoirs (ENTSO-E 16.1.D)", update_hydro),
        ("Kapacity ENTSO-G",                  update_capacity),
        ("ENTSOG CZ Nomination/Renomination", update_cz_operational),
        ("LNG terminály (GIE ALSI)",          update_lng),
        ("GASSCO nominace",                   update_gassco),
        ("GASSCO UMM (odstávky polí)",         update_gassco_umm),
        ("DAP Europe choropleth",             update_dap_europe),
        # Staré per-country zdroje (FR/HU) — dočasně běží dál souběžně
        # s obecnou vrstvou níž, dokud není nová appka ověřená (krok 4),
        # pak se smažou (staré update_* funkce i tenhle blok).
        ("Jaderná výroba FR (ENTSO-E)",        update_nuclear_fr_generation),
        ("Jaderné odstávky FR (ENTSO-E)",      update_nuclear_fr_outages),
        ("Výroba HU podle zdroje (ENTSO-E)",   update_hu_generation),
        ("Odstávky HU (ENTSO-E)",              update_hu_outages),
    ]
    # Obecná vrstva (krok 2) — přidání země = jeden řádek v config.COUNTRIES,
    # žádná kopie funkce.
    runs += [(f"Výroba {c} — obecná vrstva (ENTSO-E)", lambda c=c: update_generation(c)) for c in COUNTRIES]
    runs += [(f"Odstávky {c} — obecná vrstva (ENTSO-E)", lambda c=c: update_outages(c)) for c in COUNTRIES]

    for label, fn in runs:
        print(f"\n=== {label} ===")
        try:
            fn()
        except Exception as e:
            print(f"  CHYBA: {e}")
