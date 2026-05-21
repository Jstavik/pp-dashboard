import requests, time, os
import pandas as pd

CAPACITY_PARQUET = "data/history/entsog_capacity.parquet"

GAS_KEY_POINTS = {
    "VIP Brandov":       ["VIP Brandov", "VIP Brandov-GASPOOL"],
    "Brandov STEGAL":    ["Brandov STEGAL (CZ) / Stegal (DE)"],
    "Deutschneudorf":    ["Deutschneudorf EUGAL Brandov"],
    "VIP Waidhaus":      ["VIP Waidhaus", "VIP Waidhaus NCG"],
    "Waidhaus OGE":      ["Waidhaus (OGE)"],
    "Waidhaus GRTgaz":   ["Waidhaus (GRTgaz D)"],
    "VIP Oberkappel":    ["VIP Oberkappel"],
    "Oberkappel OGE":    ["Oberkappel (OGE)"],
    "Überackern ABG":    ["Überackern ABG (AT) / Überackern (DE)"],
    "Überackern SUDAL":  ["Überackern SUDAL (AT) / Überackern 2 (DE)"],
    "Baumgarten":        ["Baumgarten"],
    "Baumgarten TAG":    ["Baumgarten (TAG)"],
    "Baumgarten GCA":    ["Baumgarten (Gas Connect Austria)"],
    "Baumgarten WAG":    ["Baumgarten (WAG)"],
    "Ellund":            ["Ellund", "Ellund (OGE)", "Ellund (GUD)"],
    "Mallnow":           ["Mallnow"],
    "Emden EPT1 OGE":    ["Emden (EPT1) (OGE)"],
    "Emden NPT OGE":     ["Emden (NPT) (OGE)"],
    "Dornum NETRA OGE":  ["Dornum / NETRA (OGE)"],
    "Dornum NETRA GUD":  ["Dornum / NETRA (GUD)"],
    "Kiefersfelden":     ["Kiefersfelden", "VIP Kiefersfelden-Pfronten"],
    "Wallbach":          ["Wallbach"],
    "Tarvisio":          ["Tarvisio (IT) / Arnoldstein (AT)"],
    "Griespass":         ["Griespass (CH) / Passo Gries (IT)"],
    "Uzhhorod":          ["Uzhhorod (UA) - Velké Kapušany (SK)"],
    "Budince":           ["Budince"],
    "Cieszyn":           ["Cieszyn (PL) / Český Těšín (CZ)"],
    "Lanžhot":           ["Lanžhot"],
    "Petrzalka":         ["Petrzalka"],
    "Mosonmagyarovar":   ["Mosonmagyarovar"],
    "Arnoldstein":       ["Tarvisio (IT) / Arnoldstein (AT)"],
    "Gorizia":           ["Gorizia (IT) /Šempeter (SI)"],
    "Murfeld":           ["Murfeld (AT) / Ceršak (SI)"],
    "Balassagyarmat":    ["Balassagyarmat (HU) / Velké Zlievce (SK)"],
    "Kiskundorozsma":    ["Kiskundorozsma (HU>RS)"],
    "Beregdaroc":        ["Beregdaróc 800 (HU) - Beregovo (UA) (HU>UA)"],
    "Dravaszerdahely":   ["Dravaszerdahely"],
    "Rogatec":           ["Rogatec"],
    "Nybro":             ["Nybro"],
    "Dragør":            ["Dragør"],
    "Zeebrugge ZPT":     ["Zeebrugge ZPT"],
    "Zeebrugge IZT":     ["Zeebrugge IZT"],
    "Eynatten":          ["Eynatten 1 (BE) // Lichtenbusch / Raeren (DE)"],
    "Blaregnies":        ["Blaregnies (BE) / Taisnières (H) (FR) (Segeo)"],
    "Obergailbach":      ["Obergailbach (FR) / Medelsheim (DE)"],
}

TSO_COUNTRY = {
    "NET4GAS":              "CZ",
    "GASCADE Gastransport": "DE",
    "Open Grid Europe":     "DE",
    "GRTgaz Deutschland":   "DE",
    "NaTran Deutschland":   "DE",
    "Gasunie Deutschland":  "DE",
    "Fluxys Deutschland":   "DE",
    "ONTRAS":               "DE",
    "Gas Connect Austria":  "AT",
    "TAG":                  "AT",
    "eustream":             "SK",
    "GRTgaz":               "FR",
    "Fluxys":               "BE",
    "GTS":                  "NL",
    "Energinet":            "DK",
    "Snam":                 "IT",
}

INDICATORS = (
    "Firm Technical,Firm Booked,Firm Available,"
    "Interruptible Total,Interruptible Booked,Interruptible Available"
)


def fetch_point_capacity(point_label: str) -> list:
    all_rows, offset, limit = [], 0, 500
    while True:
        url = (
            "https://transparency.entsog.eu/api/v1/operationaldata"
            f"?indicator={INDICATORS}"
            f"&pointLabel={requests.utils.quote(point_label)}"
            f"&limit={limit}&offset={offset}&format=json"
        )
        try:
            resp = requests.get(url, timeout=30)
            data = resp.json()
            rows  = data.get("operationaldata", [])
            total = data.get("meta", {}).get("total", 0)
            all_rows.extend(rows)
            offset += len(rows)
            if offset >= total or len(rows) == 0:
                break
            time.sleep(0.2)
        except Exception as e:
            print(f"  Chyba {point_label}: {e}")
            break
    return all_rows


def update_capacity():
    os.makedirs("data/history", exist_ok=True)
    all_records = []
    for group_name, labels in GAS_KEY_POINTS.items():
        for label in labels:
            rows = fetch_point_capacity(label)
            for r in rows:
                r["group_name"] = group_name
            all_records.extend(rows)
            time.sleep(0.3)
        print(f"  ✓ {group_name}")

    if not all_records:
        return

    df = pd.DataFrame(all_records)
    df["value"] = (
        df["value"].astype(str)
          .str.strip()
          .replace({"": None, "nan": None, "None": None})
    )
    df["value_GWh"]     = pd.to_numeric(df["value"], errors="coerce") / 1_000_000
    df["periodFrom_dt"] = pd.to_datetime(df["periodFrom"], utc=True).dt.date
    df["periodTo_dt"]   = pd.to_datetime(df["periodTo"],   utc=True).dt.date
    df["tso_country"]   = df["operatorLabel"].apply(
        lambda x: next(
            (c for tso, c in TSO_COUNTRY.items()
             if tso.lower() in str(x).lower()), "??")
    )
    df.to_parquet(CAPACITY_PARQUET, index=False)
    print(f"Kapacity: {len(df)} řádků → {CAPACITY_PARQUET}")


def load_capacity() -> pd.DataFrame:
    try:
        import streamlit as st
        @st.cache_data(ttl=3600, show_spinner=False)
        def _load():
            if os.path.exists(CAPACITY_PARQUET):
                return pd.read_parquet(CAPACITY_PARQUET)
            return pd.DataFrame()
        return _load()
    except ImportError:
        if os.path.exists(CAPACITY_PARQUET):
            return pd.read_parquet(CAPACITY_PARQUET)
        return pd.DataFrame()


def expand_capacity(df_raw: pd.DataFrame, target_dates: list) -> pd.DataFrame:
    df_raw = df_raw.copy()
    if "periodFrom_dt" not in df_raw.columns:
        df_raw["periodFrom_dt"] = pd.to_datetime(
            df_raw["periodFrom"], utc=True).dt.date
    if "periodTo_dt" not in df_raw.columns:
        df_raw["periodTo_dt"] = pd.to_datetime(
            df_raw["periodTo"], utc=True).dt.date
    records = []
    for d in target_dates:
        mask = (df_raw["periodFrom_dt"] <= d) & (df_raw["periodTo_dt"] >= d)
        sub  = df_raw[mask].copy()
        if not sub.empty:
            sub["date"] = d
            records.append(sub)
    return pd.concat(records, ignore_index=True) if records else pd.DataFrame()
