import requests, os, time
import pandas as pd

ALSI_KEY  = "628043ec28b2f2395a95f5adad7ec983"
LNG_CSV   = "data/history/lng_storage.csv"

LNG_TERMINALS = {
    "Gate Terminal (NL)":    "NL",
    "Zeebrugge (BE)":        "BE",
    "Dunkerque (FR)":        "FR",
    "Montoir (FR)":          "FR",
    "Fos (FR)":              "FR",
    "Eemshaven (NL)":        "NL",
    "Isle of Grain (GB)":    "GB",
    "Milford Haven (GB)":    "GB",
    "Panigaglia (IT)":       "IT",
    "Adriatic LNG (IT)":     "IT",
    "Barcelona (ES)":        "ES",
    "Bilbao (ES)":           "ES",
    "Cartagena (ES)":        "ES",
    "Huelva (ES)":           "ES",
    "Sagunto (ES)":          "ES",
    "Sines (PT)":            "PT",
    "Revithoussa (GR)":      "GR",
}


def fetch_lng_all() -> pd.DataFrame:
    all_frames = []
    for page in range(1, 20):
        url = f"https://alsi.gie.eu/api?type=eu&size=300&page={page}"
        try:
            resp = requests.get(
                url, headers={"x-key": ALSI_KEY}, timeout=30)
        except Exception as e:
            print(f"  LNG timeout str. {page}: {e}")
            break
        if resp.status_code != 200:
            break
        data = resp.json()
        records = data.get("data", [])
        if not records:
            break
        all_frames.extend(records)
        if page >= data.get("last_page", 1):
            break
        time.sleep(0.3)

    if not all_frames:
        return pd.DataFrame()

    df = pd.DataFrame(all_frames)
    df["gasDayStart"] = pd.to_datetime(df["gasDayStart"])

    if "inventory" in df.columns:
        df["inventory_gwh"] = df["inventory"].apply(
            lambda x: float(x.get("gwh", 0)) if isinstance(x, dict) else 0
        )
        df["inventory_lng"] = df["inventory"].apply(
            lambda x: float(x.get("lng", 0)) if isinstance(x, dict) else 0
        )
    if "dtmi" in df.columns:
        df["dtmi_gwh"] = df["dtmi"].apply(
            lambda x: float(x.get("gwh", 0)) if isinstance(x, dict) else 0
        )

    if "inventory_gwh" in df.columns and "dtmi_gwh" in df.columns:
        df["full"] = (
            df["inventory_gwh"] / df["dtmi_gwh"].replace(0, float("nan")) * 100
        ).round(2)

    for col in ["sendOut", "dtrs", "contractedCapacity", "availableCapacity"]:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(",", "."),
                errors="coerce")

    return df.sort_values("gasDayStart")


def update_lng():
    os.makedirs("data/history", exist_ok=True)
    print("  LNG terminály...")
    df = fetch_lng_all()
    if df.empty:
        print("  LNG: žádná data")
        return
    df.to_csv(LNG_CSV, index=False)
    print(f"  LNG: {len(df)} řádků → {LNG_CSV}")


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
