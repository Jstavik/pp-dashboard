import pandas as pd
import requests

from config import DG_BASE


def fetch_deltagreen(api_key: str):
    headers = {"x-api-key": api_key, "accept": "application/json"}
    r1 = requests.get(f"{DG_BASE}/copilot/portfolio-state",
                      headers=headers, params={"granularity": "15s"}, timeout=15)
    r2 = requests.get(f"{DG_BASE}/copilot/available-flexibility",
                      headers=headers, timeout=15)
    r1.raise_for_status()
    r2.raise_for_status()
    df1 = pd.DataFrame(r1.json()["records"])
    df1["time"] = pd.to_datetime(df1["time"]).dt.tz_convert("Europe/Prague")
    for col in ["batteryPowerKW","gridPowerKW","consumptionPowerKW","photovoltaicPowerKW"]:
        if col not in df1.columns:
            df1[col] = None
    df2 = pd.DataFrame(r2.json()["records"])
    df2["time"] = pd.to_datetime(df2["time"]).dt.tz_convert("Europe/Prague")
    for col in ["upPowerKW","downBatteryPowerKW","downSolarCurtailmentPowerKW"]:
        if col not in df2.columns:
            df2[col] = None
    return df1, df2
