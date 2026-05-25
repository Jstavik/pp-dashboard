import sys
sys.path.insert(0, ".")
import requests
import pandas as pd
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
import os
import time
from data.entsog_capacity import update_capacity
from data.lng import update_lng
from data.gassco import update_gassco

# ENTSO-G aggregateddata API sahá do 2020, dříve není dostupné
HISTORY_START  = date(2020, 1, 1)
PARQUET_PATH   = "data/history/entsog_all_flows.parquet"
GIE_CSV_PATH   = "data/history/gie_cz_storage.csv"
GIE_ALL_CSV    = "data/history/gie_all_storage.csv"
GIE_KEY        = "628043ec28b2f2395a95f5adad7ec983"
COUNTRIES_GIE  = ["AT", "BE", "CZ", "DE", "FR", "HR", "HU", "IT", "LV", "NL", "PL", "PT", "RO", "RS", "SK", "ES", "UA"]
EU_CODE        = "EU"

HYDRO_CSV      = "data/history/hydro_reservoirs.csv"
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
    os.makedirs("data/history", exist_ok=True)

    last_date = None
    if os.path.exists(PARQUET_PATH):
        _dates    = pd.read_parquet(PARQUET_PATH, columns=["date"])
        last_date = pd.to_datetime(_dates["date"]).max().date()
        start     = last_date - timedelta(days=7)
        print(f"Existující data do {last_date}, stahuji od {start}")
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

    if last_date is not None and os.path.exists(PARQUET_PATH):
        existing = pd.read_parquet(PARQUET_PATH)
        cutoff   = pd.Timestamp(last_date) - pd.Timedelta(days=8)
        cutoff   = cutoff.tz_localize("UTC") if cutoff.tzinfo is None else cutoff
        existing = existing[pd.to_datetime(existing["date"], utc=True) < cutoff]
        df_final = pd.concat([existing, new_data], ignore_index=True)
        key_cols = ["date", "countryKey", "directionKey",
                    "adjacentSystemsKey", "pointsNames"]
        key_cols = [c for c in key_cols if c in df_final.columns]
        df_final = df_final.drop_duplicates(subset=key_cols, keep="last")
    else:
        df_final = new_data

    df_final = df_final.sort_values("date").reset_index(drop=True)
    df_final.to_parquet(PARQUET_PATH, index=False)
    size_mb  = os.path.getsize(PARQUET_PATH) / 1024 / 1024
    print(f"Uloženo {len(df_final)} řádků → {PARQUET_PATH} ({size_mb:.1f} MB)")



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

    for col in ["full", "gasInStorage", "injection", "withdrawal",
                "netWithdrawal", "workingGasVolume", "trend",
                "injectionCapacity", "withdrawalCapacity"]:
        if col in combined.columns:
            combined[col] = pd.to_numeric(
                combined[col].astype(str).str.replace(",", "."),
                errors="coerce",
            )
    return combined.sort_values(["country_code", "gasDayStart"])


def update_gie_all():
    os.makedirs("data/history", exist_ok=True)

    if os.path.exists(GIE_ALL_CSV):
        existing = pd.read_csv(GIE_ALL_CSV, parse_dates=["gasDayStart"])
        last_date = existing["gasDayStart"].max().date()
        print(f"GIE all: existující data do {last_date}")
        cutoff = pd.Timestamp(last_date) - pd.Timedelta(days=14)
        existing = existing[existing["gasDayStart"] < cutoff]
    else:
        existing = pd.DataFrame()
        print("GIE all: nový soubor")

    new_data = fetch_gie_all_countries()
    if new_data.empty:
        print("GIE all: žádná data")
        return

    if not existing.empty:
        combined = pd.concat([existing, new_data], ignore_index=True)
        combined = combined.drop_duplicates(
            subset=["country_code", "gasDayStart"], keep="last"
        )
    else:
        combined = new_data

    combined = combined.sort_values(
        ["country_code", "gasDayStart"]
    ).reset_index(drop=True)
    combined.to_csv(GIE_ALL_CSV, index=False)

    size_kb = os.path.getsize(GIE_ALL_CSV) / 1024
    print(f"GIE all: uloženo {len(combined)} řádků ({size_kb:.0f} KB)")


def update_hydro():
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from entsoe import EntsoePandasClient

    os.makedirs("data/history", exist_ok=True)
    client = EntsoePandasClient(
        api_key="95fa8cc7-1438-455b-9060-795d7c44d389"
    )

    if os.path.exists(HYDRO_CSV):
        existing  = pd.read_csv(HYDRO_CSV, parse_dates=["date"])
        last_date = existing["date"].max()
        start     = last_date - timedelta(days=30)
        print(f"Hydro: existující data do {last_date.date()}")
    else:
        existing = pd.DataFrame()
        start    = date(2020, 1, 1)
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

    if not existing.empty:
        combined = pd.concat([existing, new_data], ignore_index=True)
        combined = combined.drop_duplicates(
            subset=["date", "country"], keep="last"
        )
    else:
        combined = new_data

    combined = combined.sort_values(
        ["country", "date"]
    ).reset_index(drop=True)
    combined.to_csv(HYDRO_CSV, index=False)
    size_kb = os.path.getsize(HYDRO_CSV) / 1024
    print(f"Hydro: uloženo {len(combined)} řádků ({size_kb:.0f} KB)")


if __name__ == "__main__":
    for label, fn in [
        ("ENTSO-G flows (všechny země)",      update_entsog),
        ("GIE storage — všechny země",        update_gie_all),
        ("Hydro reservoirs (ENTSO-E 16.1.D)", update_hydro),
        ("Kapacity ENTSO-G",                  update_capacity),
        ("LNG terminály (GIE ALSI)",          update_lng),
        ("GASSCO nominace",                   update_gassco),
    ]:
        print(f"\n=== {label} ===")
        try:
            fn()
        except Exception as e:
            print(f"  CHYBA: {e}")
