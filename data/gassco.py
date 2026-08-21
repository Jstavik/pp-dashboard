import re
import glob
import requests
import pandas as pd
import os
from datetime import timedelta

from config import MSMM3_TO_GWH
from data.partitioned_store import write_daily_snapshot, read_snapshot

GASSCO_CSV      = "data/history/gassco_nominations.csv"
GASSCO_UMM_PATH = "data/history/gassco_umm.parquet"
GASSCO_UMM_SNAPSHOTS_DIR = "data/history/gassco_umm_snapshots"


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


def _remit_name(el, remit_ns: str, common_ns: str) -> str:
    """affectedAsset/affectedUnit/marketParticipant nesou název ve
    vnořeném <ns2:name> (common schema), ne v přímém textu elementu."""
    if el is None:
        return ""
    name_el = el.find(f"{{{common_ns}}}name")
    return (name_el.text or "").strip() if name_el is not None else ""


def fetch_gassco_umm() -> pd.DataFrame:
    """REMIT UMM zprávy (odstávky polí) z /atom.xml — feed vrací jen
    AKTUÁLNÍ stav (poslední revize + nedávno zrušené), ne plnou historii;
    tu si stavíme sami opakovaným voláním, viz update_gassco_umm().

    Pozor na skutečnou XML strukturu (ověřeno živě, dřívější verze měla
    špatné cesty): unavailabilityType/publicationDateTime/capacity/
    unavailabilityReason/affectedAsset/affectedUnit jsou SOUROZENCI
    <event>, ne jeho potomci — <event> obsahuje jen eventStatus/eventType/
    eventStart/eventStop. affectedAsset/affectedUnit navíc nesou jméno ve
    vnořeném <ns2:name>, ne v přímém textu elementu."""
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
        ns        = {"atom": "http://www.w3.org/2005/Atom"}
        remit_ns  = "http://www.acer.europa.eu/REMIT/REMITUMMGasSchema_V2.xsd"
        common_ns = "http://www.acer.europa.eu/REMIT/REMITUMMCommonSchema_V1.xsd"

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
                            ev  = umm_el.find(f"{{{remit_ns}}}event")
                            cap = umm_el.find(f"{{{remit_ns}}}capacity")
                            asset = umm_el.find(f"{{{remit_ns}}}affectedAsset")
                            unit_el = umm_el.find(f"{{{remit_ns}}}affectedUnit")

                            rec["messageId"] = umm_el.findtext(f"{{{remit_ns}}}messageId", "")
                            rec["unavailabilityType"]   = umm_el.findtext(f"{{{remit_ns}}}unavailabilityType", "")
                            rec["publicationDateTime"]  = umm_el.findtext(f"{{{remit_ns}}}publicationDateTime", "")
                            rec["unavailabilityReason"] = umm_el.findtext(f"{{{remit_ns}}}unavailabilityReason", "")
                            rec["affectedAsset"] = _remit_name(asset, remit_ns, common_ns)
                            rec["affectedUnit"]  = _remit_name(unit_el, remit_ns, common_ns)

                            if ev is not None:
                                rec["eventStatus"] = ev.findtext(f"{{{remit_ns}}}eventStatus", "")
                                rec["eventType"]    = ev.findtext(f"{{{remit_ns}}}eventType", "")
                                rec["eventStart"]   = ev.findtext(f"{{{remit_ns}}}eventStart", "")
                                rec["eventStop"]    = ev.findtext(f"{{{remit_ns}}}eventStop", "")
                            if cap is not None:
                                rec["unitMeasure"]         = cap.findtext(f"{{{remit_ns}}}unitMeasure", "")
                                rec["technicalCapacity"]   = cap.findtext(f"{{{remit_ns}}}technicalCapacity", "")
                                rec["availableCapacity"]   = cap.findtext(f"{{{remit_ns}}}availableCapacity", "")
                                rec["unavailableCapacity"] = cap.findtext(f"{{{remit_ns}}}unavailableCapacity", "")
                except Exception:
                    pass
            records.append(rec)

        df = pd.DataFrame(records)
        if df.empty or "messageId" not in df.columns:
            return df

        df["messageId"] = df["messageId"].fillna("")
        df["base_id"]   = df["messageId"].str.split("_").str[0]
        df["revision"]  = (
            df["messageId"].str.extract(r"(\d+)$")[0]
            .astype("Int64")
        )
        for col in ("technicalCapacity", "availableCapacity", "unavailableCapacity"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        is_mcm = df.get("unitMeasure", "") == "mcm/d"
        for col in ("technicalCapacity", "availableCapacity", "unavailableCapacity"):
            if col in df.columns:
                df[f"{col}_GWh"] = (df[col] * MSMM3_TO_GWH).where(is_mcm)
        for col in ("eventStart", "eventStop", "publicationDateTime", "updated"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
        return df
    except Exception:
        return pd.DataFrame()


def _latest_per_base_id(df: pd.DataFrame) -> pd.DataFrame:
    """Poslední revize na base_id — "aktuální stav" té UMM zprávy. Historie
    revizí (včetně starších/Dismissed) zůstává v rolling souboru samotném,
    tohle je jen pohled na čtení, nic se nemaže."""
    if df.empty:
        return df
    return (df.sort_values("revision")
              .groupby("base_id", as_index=False).last())


def compute_umm_delta(df_now: pd.DataFrame, df_past: pd.DataFrame) -> dict:
    """3 kategorie změn mezi dvěma rolling-souborovými stavy (teď vs. D-N
    dní zpět), přes base_id/revision/eventStatus — čistší signál než
    interval-overlap u elektřinových odstávek (viz fig_outages_delta),
    není potřeba žádná heuristika na překryv intervalů.

    - nové:      base_id aktivní teď, v minulém stavu vůbec neexistoval
    - zrušené:   base_id byl aktivní v minulém stavu, dnes má (jakoukoliv
                 revizi, i novější než tehdy) eventStatus == "Dismissed"
    - změněné:   base_id aktivní v obou, ale revision se liší (číslo
                 revize vyšší teď = něco se od tehdy upravilo)
    """
    now_latest  = _latest_per_base_id(df_now)
    past_latest = _latest_per_base_id(df_past)

    now_active  = now_latest[now_latest["eventStatus"]  != "Dismissed"] if not now_latest.empty  else now_latest
    past_active = past_latest[past_latest["eventStatus"] != "Dismissed"] if not past_latest.empty else past_latest

    now_ids  = set(now_active["base_id"])  if not now_active.empty  else set()
    past_ids = set(past_active["base_id"]) if not past_active.empty else set()

    new_ids = now_ids - past_ids
    novel   = now_active[now_active["base_id"].isin(new_ids)]

    was_active_ids = past_ids
    now_status_by_id = (now_latest.set_index("base_id")["eventStatus"]
                        if not now_latest.empty else pd.Series(dtype=object))
    cancelled_ids = {bid for bid in was_active_ids
                     if now_status_by_id.get(bid) == "Dismissed"}
    cancelled = now_latest[now_latest["base_id"].isin(cancelled_ids)]

    common_ids = now_ids & past_ids
    if common_ids and not now_active.empty and not past_active.empty:
        rev_now  = now_active.set_index("base_id")["revision"]
        rev_past = past_active.set_index("base_id")["revision"]
        changed_ids = {bid for bid in common_ids
                       if rev_now.get(bid) != rev_past.get(bid)}
        changed = now_active[now_active["base_id"].isin(changed_ids)]
    else:
        changed = now_active.iloc[0:0]

    return {"new": novel, "cancelled": cancelled, "changed": changed}


def update_gassco_umm():
    """REMIT UMM zprávy (odstávky polí GASSCO) — rolling soubor jako
    outages.parquet (revize se dějí přirozeně, ne měsíční partitioning).
    Dedup je jednodušší než u ENTSOG kapacity: messageId je jednou
    publikovaný a NEMĚNNÝ (nová revize = nový messageId), takže stačí
    drop_duplicates na messageId — žádné porovnávání lastUpdateDateTime.

    KRITICKÉ (ověřeno přes ACER REMIT Q&A): eventStatus="Dismissed"
    znamená skutečné zrušení odstávky (návrat kapacity), ne rutinní
    revizi — NIKDY se nezahazuje, ukládá se jako plnohodnotný záznam."""
    os.makedirs("data/history", exist_ok=True)
    new_data = fetch_gassco_umm()
    if new_data.empty:
        print("  GASSCO UMM: žádná data")
        return

    if os.path.exists(GASSCO_UMM_PATH):
        existing = pd.read_parquet(GASSCO_UMM_PATH)
        combined = pd.concat([existing, new_data], ignore_index=True)
    else:
        combined = new_data
    combined = combined.drop_duplicates(subset=["messageId"], keep="last")
    combined = combined.sort_values(["base_id", "revision"]).reset_index(drop=True)
    combined.to_parquet(GASSCO_UMM_PATH, index=False)
    print(f"  GASSCO UMM: {len(new_data)} z feedu → {len(combined)} celkem (revize) → {GASSCO_UMM_PATH}")

    snap_written = write_daily_snapshot(combined, GASSCO_UMM_SNAPSHOTS_DIR, fmt="parquet")
    print(f"  GASSCO UMM snapshot: {'zapsán nový den' if snap_written else 'dnešní už existuje'}")


def load_gassco_umm() -> pd.DataFrame:
    def _load():
        if not os.path.exists(GASSCO_UMM_PATH):
            return pd.DataFrame()
        return pd.read_parquet(GASSCO_UMM_PATH)
    try:
        import streamlit as st
        return st.cache_data(ttl=300, show_spinner=False)(_load)()
    except ImportError:
        return _load()


def load_gassco_umm_snapshot(days_back: int) -> pd.DataFrame:
    day = pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=days_back)
    return read_snapshot(GASSCO_UMM_SNAPSHOTS_DIR, day, fmt="parquet")


def list_available_umm_snapshot_dates() -> list:
    """Seřazený seznam dat (staré → nové), pro která existuje denní
    snapshot UMM zpráv — pro UI 'Porovnat s' selectbox, stejný vzor jako
    data/outages.py::list_available_snapshot_dates (jen bez country
    filtru, GASSCO UMM je jeden feed, ne per-country)."""
    files = sorted(glob.glob(os.path.join(GASSCO_UMM_SNAPSHOTS_DIR, "*.parquet")))
    dates = []
    for f in files:
        day_str = os.path.basename(f).removesuffix(".parquet")
        try:
            dates.append(pd.Timestamp(day_str, tz="UTC"))
        except ValueError:
            continue
    return dates


def fetch_realtime_nominations() -> pd.DataFrame:
    """Aktuální nominace z realTimeAtom.xml — bez session."""
    import xml.etree.ElementTree as ET
    try:
        resp = requests.get(
            "https://umm.gassco.no/realTimeAtom.xml",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        if resp.status_code != 200:
            return pd.DataFrame()

        root = ET.fromstring(resp.text)
        ns   = {"atom": "http://www.w3.org/2005/Atom"}

        updated_str = root.findtext("atom:updated", "", ns)
        updated = pd.to_datetime(updated_str, utc=True)
        today   = updated.normalize()

        records = []
        for entry in root.findall("atom:entry", ns):
            title   = entry.findtext("atom:title", "", ns)
            content = entry.findtext("atom:content", "0", ns)
            name = (title
                    .replace("Exit Nomination ", "")
                    .replace(" (MSm3)", "")
                    .strip())
            try:
                val_msm3 = float(content)
            except ValueError:
                val_msm3 = 0.0
            records.append({
                "date":       today,
                "point":      name,
                "value_MSm3": val_msm3,
                "value_GWh":  val_msm3 * MSMM3_TO_GWH,
                "realtime":   True,
            })

        return pd.DataFrame(records)
    except Exception as e:
        print(f"realTimeAtom chyba: {e}")
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
        @st.cache_data(ttl=300, show_spinner=False)
        def _load():
            if os.path.exists(GASSCO_CSV):
                df_hist = pd.read_csv(GASSCO_CSV, parse_dates=["date"])
                df_hist["date"] = pd.to_datetime(df_hist["date"], utc=True)
            else:
                df_hist = pd.DataFrame()

            df_live = fetch_realtime_nominations()

            if df_live.empty:
                return df_hist
            if df_hist.empty:
                return df_live

            live_date = df_live["date"].iloc[0]
            df_hist = df_hist[df_hist["date"].dt.date != live_date.date()]
            df_combined = pd.concat([df_hist, df_live], ignore_index=True)
            return df_combined.sort_values(["point", "date"]).reset_index(drop=True)

        return _load()
    except ImportError:
        if os.path.exists(GASSCO_CSV):
            df = pd.read_csv(GASSCO_CSV, parse_dates=["date"])
            df["date"] = pd.to_datetime(df["date"], utc=True)
            df_live = fetch_realtime_nominations()
            if not df_live.empty:
                live_date = df_live["date"].iloc[0]
                df = df[df["date"].dt.date != live_date.date()]
                df = pd.concat([df, df_live], ignore_index=True)
            return df
        return pd.DataFrame()
