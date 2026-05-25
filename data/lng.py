import requests, os, time
import pandas as pd

ALSI_KEY = "628043ec28b2f2395a95f5adad7ec983"
LNG_CSV  = "data/history/lng_storage.csv"

# Přidej nové země sem pokud ALSI přidá nové
COUNTRIES_ALSI = [
    "BE","FR","NL","ES","IT","DE",
    "PT","GR","HR","FI","LT","PL"
]


def fetch_lng_all() -> pd.DataFrame:
    """Stáhne LNG zásobníky ze GIE ALSI API — všechny země + EU."""
    all_frames = []

    # Per země
    for cc in COUNTRIES_ALSI:
        for page in range(1, 30):
            for attempt in range(3):
                try:
                    r = requests.get(
                        f"https://alsi.gie.eu/api?country={cc}"
                        f"&size=300&page={page}",
                        headers={"x-key": ALSI_KEY},
                        timeout=20,
                    )
                    if r.status_code != 200:
                        break
                    data = r.json()
                    rows = data.get("data", [])
                    if not rows:
                        break
                    for row in rows:
                        row["country_code"] = cc
                    all_frames.extend(rows)
                    if page >= data.get("last_page", 1):
                        break
                    time.sleep(0.3)
                    break
                except Exception as e:
                    print(f"  ALSI {cc} page {page}: {e}")
                    time.sleep(2)

    # EU agregát
    for page in range(1, 30):
        for attempt in range(3):
            try:
                r = requests.get(
                    f"https://alsi.gie.eu/api?type=eu"
                    f"&size=300&page={page}",
                    headers={"x-key": ALSI_KEY},
                    timeout=20,
                )
                if r.status_code != 200:
                    break
                data = r.json()
                rows = data.get("data", [])
                if not rows:
                    break
                for row in rows:
                    row["country_code"] = "EU"
                all_frames.extend(rows)
                if page >= data.get("last_page", 1):
                    break
                time.sleep(0.3)
                break
            except Exception as e:
                print(f"  ALSI EU page {page}: {e}")
                time.sleep(2)

    if not all_frames:
        return pd.DataFrame()

    df = pd.DataFrame(all_frames)
    df["gasDayStart"] = pd.to_datetime(df["gasDayStart"])

    # Rozbal nested inventory a dtmi
    df["inventory_gwh"] = df["inventory"].apply(
        lambda x: float(x["gwh"])
        if isinstance(x, dict) and x.get("gwh") not in ["-", "", None]
        else None
    ) if "inventory" in df.columns else None

    df["dtmi_gwh"] = df["dtmi"].apply(
        lambda x: float(x["gwh"])
        if isinstance(x, dict) and x.get("gwh") not in ["-", "", None]
        else None
    ) if "dtmi" in df.columns else None

    df["full_pct"] = (
        df["inventory_gwh"] / df["dtmi_gwh"] * 100
    ).round(1)

    for col in ["sendOut", "dtrs", "contractedCapacity", "availableCapacity"]:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(",", "."),
                errors="coerce"
            )

    keep = ["gasDayStart", "country_code", "name", "inventory_gwh",
            "dtmi_gwh", "full_pct", "sendOut", "dtrs",
            "contractedCapacity", "availableCapacity", "status"]
    return df[[c for c in keep if c in df.columns]]


def update_lng():
    os.makedirs("data/history", exist_ok=True)

    if os.path.exists(LNG_CSV):
        existing  = pd.read_csv(LNG_CSV, parse_dates=["gasDayStart"])
        last_date = existing["gasDayStart"].max().date()
        cutoff    = pd.Timestamp(last_date) - pd.Timedelta(days=14)
        existing  = existing[existing["gasDayStart"] < cutoff]
        print(f"LNG ALSI: existující data do {last_date}, stahuji přírůstek")
    else:
        existing = pd.DataFrame()
        print("LNG ALSI: nový soubor, stahuji vše")

    new_data = fetch_lng_all()
    if new_data.empty:
        print("LNG ALSI: žádná data")
        return

    if not existing.empty:
        combined = pd.concat([existing, new_data], ignore_index=True)
        combined = combined.drop_duplicates(
            subset=["gasDayStart", "country_code"], keep="last"
        )
    else:
        combined = new_data

    combined = combined.sort_values(
        ["country_code", "gasDayStart"]
    ).reset_index(drop=True)
    combined.to_csv(LNG_CSV, index=False)
    size_kb = os.path.getsize(LNG_CSV) / 1024
    print(f"LNG ALSI: {len(combined)} řádků → {LNG_CSV} ({size_kb:.0f} KB)")


def load_lng() -> pd.DataFrame:
    try:
        import streamlit as st
        @st.cache_data(ttl=3600, show_spinner=False)
        def _load():
            if os.path.exists(LNG_CSV):
                return pd.read_csv(LNG_CSV, parse_dates=["gasDayStart"])
            return pd.DataFrame()
        return _load()
    except ImportError:
        if os.path.exists(LNG_CSV):
            return pd.read_csv(LNG_CSV, parse_dates=["gasDayStart"])
        return pd.DataFrame()
