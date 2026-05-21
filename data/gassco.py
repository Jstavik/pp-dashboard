import requests
import pandas as pd
import os
from datetime import timedelta

GASSCO_CSV  = "data/history/gassco_nominations.csv"
MSMM3_TO_GWH = 10.55


def _get_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })
    session.get("https://umm.gassco.no/", timeout=10)
    session.get("https://umm.gassco.no/disclaimer/acceptDisclaimer", timeout=10)
    return session


def fetch_gassco_nominations() -> pd.DataFrame:
    session = _get_session()

    r = session.get(
        "https://umm.gassco.no/ch/points",
        headers={"Accept": "application/json",
                 "X-Requested-With": "XMLHttpRequest"},
        timeout=10,
    )
    if r.status_code != 200:
        return pd.DataFrame()

    points = r.json()
    frames = []
    for pt in points:
        pt_id   = pt["id"]
        pt_name = pt["name"]
        r2 = session.get(
            f"https://umm.gassco.no/ch/2Y/{pt_id}",
            headers={"Accept": "application/json",
                     "X-Requested-With": "XMLHttpRequest"},
            timeout=15,
        )
        if r2.status_code == 200 and r2.text.startswith("{"):
            data = r2.json()
            rows = data.get("data", [])
            if rows:
                df_p = pd.DataFrame(rows)
                df_p["date"]       = pd.to_datetime(df_p["x"], unit="ms", utc=True)
                df_p["point"]      = pt_name
                df_p["value_MSm3"] = df_p["y"]
                df_p["value_GWh"]  = df_p["value_MSm3"] * MSMM3_TO_GWH
                frames.append(df_p[["date", "point", "value_MSm3", "value_GWh"]])

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    return df.sort_values(["point", "date"]).reset_index(drop=True)


def fetch_gassco_umm() -> pd.DataFrame:
    import xml.etree.ElementTree as ET

    try:
        resp = requests.get(
            "https://umm.gassco.no/atom.xml",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        if resp.status_code != 200:
            return pd.DataFrame()

        root = ET.fromstring(resp.text)
        ns       = {"atom": "http://www.w3.org/2005/Atom"}
        remit_ns = "http://www.acer.europa.eu/REMIT/REMITUMMGasSchema_V2.xsd"

        records = []
        for entry in root.findall("atom:entry", ns):
            title   = entry.findtext("atom:title", "", ns)
            updated = entry.findtext("atom:updated", "", ns)
            link    = entry.find("atom:link", ns)
            href    = link.get("href") if link is not None else ""
            summary = entry.findtext("atom:summary", "", ns)

            rec = {"title": title, "updated": updated, "link": href}
            if summary:
                try:
                    clean = summary.strip()
                    if "<?xml" in clean:
                        root_s = ET.fromstring(clean)
                        umm_el = root_s.find(f"{{{remit_ns}}}UMM")
                        if umm_el is not None:
                            ev = umm_el.find(f"{{{remit_ns}}}event")
                            if ev is not None:
                                rec["eventStatus"]     = ev.findtext(f"{{{remit_ns}}}eventStatus", "")
                                rec["eventType"]       = ev.findtext(f"{{{remit_ns}}}eventType", "")
                                rec["eventStart"]      = ev.findtext(f"{{{remit_ns}}}eventStart", "")
                                rec["eventStop"]       = ev.findtext(f"{{{remit_ns}}}eventStop", "")
                                rec["affectedAsset"]   = ev.findtext(f"{{{remit_ns}}}affectedAsset", "")
                                rec["techCapacity"]    = ev.findtext(f"{{{remit_ns}}}technicalCapacity", "")
                                rec["availCapacity"]   = ev.findtext(f"{{{remit_ns}}}availableCapacity", "")
                                rec["unavailCapacity"] = ev.findtext(f"{{{remit_ns}}}unavailableCapacity", "")
                                rec["unit"]            = ev.findtext(f"{{{remit_ns}}}unitOfMeasure", "")
                                rec["reason"]          = ev.findtext(f"{{{remit_ns}}}reasonForUnavailability", "")
                except Exception:
                    pass
            records.append(rec)

        return pd.DataFrame(records)
    except Exception:
        return pd.DataFrame()


def update_gassco():
    os.makedirs("data/history", exist_ok=True)
    print("  GASSCO nominace...")
    df = fetch_gassco_nominations()
    if df.empty:
        print("  GASSCO: žádná data")
        return
    df["date"] = df["date"].astype(str)
    df.to_csv(GASSCO_CSV, index=False)
    print(f"  GASSCO: {len(df)} řádků → {GASSCO_CSV}")


def load_gassco() -> pd.DataFrame:
    try:
        import streamlit as st
        @st.cache_data(ttl=1800, show_spinner=False)
        def _load():
            if os.path.exists(GASSCO_CSV):
                df = pd.read_csv(GASSCO_CSV, parse_dates=["date"])
                df["date"] = pd.to_datetime(df["date"], utc=True)
                return df
            return pd.DataFrame()
        return _load()
    except ImportError:
        if os.path.exists(GASSCO_CSV):
            df = pd.read_csv(GASSCO_CSV, parse_dates=["date"])
            df["date"] = pd.to_datetime(df["date"], utc=True)
            return df
        return pd.DataFrame()
