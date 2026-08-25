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


_XLEXPORT_COLUMNS = {
    ("Message ID", "Unnamed: 0_level_1"):              "messageId",
    ("Affected Asset or Unit", "Unnamed: 1_level_1"):   "affectedAsset",
    ("Event Status", "Unnamed: 2_level_1"):             "eventStatus",
    ("Type of unavailability", "Unnamed: 3_level_1"):   "unavailabilityType",
    ("Type of event", "Unnamed: 4_level_1"):            "eventType",
    ("Publication date/time", "Unnamed: 5_level_1"):    "publicationDateTime",
    ("Event", "Start"):                                 "eventStart",
    ("Event", "Stop"):                                  "eventStop",
    ("Unit of Meassurement", "Unnamed: 8_level_1"):     "unitMeasure",
    ("Capacity", "Technical"):                          "technicalCapacity",
    ("Capacity", "Available"):                          "availableCapacity",
    ("Capacity", "Unavailable"):                        "unavailableCapacity",
    ("Reason for the unavailability", "Unnamed: 12_level_1"): "unavailabilityReason",
    ("Remarks", "Unnamed: 13_level_1"):                 "remarks",
    ("Balancing Zone", "Unnamed: 14_level_1"):          "balancingZone",
    ("Market Participant", "Unnamed: 15_level_1"):      "marketParticipant",
    ("Market Participant Code", "Unnamed: 16_level_1"): "marketParticipantCode",
    ("Affected Asset or Unit EIC Code", "Unnamed: 17_level_1"): "affectedAssetEicCode",
}


def fetch_gassco_umm() -> pd.DataFrame:
    """REMIT UMM zprávy (odstávky polí) — /xlexport (kompletní aktuální
    stav, ne přírůstek). Nahrazuje dřívější /atom.xml, který se ukázal
    být rolling feed posledně AKTUALIZOVANÝCH zpráv (9 záznamů), ne
    kompletní seznam platných odstávek — ověřeno živě: /xlexport dává
    129 záznamů vs. 9 z atom.xml, včetně právě probíhajících a
    nejbližších odstávek, co atom.xml chybělo úplně (0 v okně 30 dní).

    messageId formát je stejný jako u atom.xml (base_id/revision parsing
    beze změny).

    Chyby se NEPOLYKAJÍ potichu — dřív "except Exception: return
    pd.DataFrame()" bez jediného vypsaného řádku vedlo k tomu, že
    scheduled běh v GitHub Actions tiše nezapisoval žádná nová data
    (a žádný denní UMM snapshot) celé dny bez jakékoliv viditelné
    chyby v jobu — jen "GASSCO UMM: žádná data" v update_gassco_umm(),
    bez důvodu proč. Teď se skutečná příčina (HTTP status, výjimka)
    vždycky vypíše, + 1 retry pro přechodné síťové chyby."""
    import time as _time
    last_err = None
    for attempt in (1, 2):
        try:
            session = _get_session()
            resp = session.get("https://umm.gassco.no/xlexport", timeout=30)
            if resp.status_code != 200:
                last_err = f"HTTP {resp.status_code} z /xlexport"
                print(f"  GASSCO UMM: pokus {attempt}/2 selhal — {last_err}")
                if attempt < 2:
                    _time.sleep(5)
                    continue
                return pd.DataFrame()
            break
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            print(f"  GASSCO UMM: pokus {attempt}/2 selhal — {last_err}")
            if attempt < 2:
                _time.sleep(5)
                continue
            return pd.DataFrame()

    try:
        import io
        raw = pd.read_excel(io.BytesIO(resp.content), header=[1, 2])
        raw.columns = [_XLEXPORT_COLUMNS.get(tuple(c), c[0]) for c in raw.columns]
        df = raw.dropna(subset=["messageId"]).reset_index(drop=True)
        if df.empty:
            print("  GASSCO UMM: /xlexport OK, ale export neobsahuje žádné messageId řádky")
            return df

        df["messageId"] = df["messageId"].astype(str)
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
        for col in ("eventStart", "eventStop", "publicationDateTime"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
        return df
    except Exception as e:
        print(f"  GASSCO UMM: chyba při zpracování /xlexport — {type(e).__name__}: {e}")
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


def compute_price_relevant_changes(df_now: pd.DataFrame, df_previous: pd.DataFrame,
                                    capacity_change_threshold: float = 0.10) -> pd.DataFrame:
    """Základ pro budoucí alerting (zatím jen výpočet — žádné posílání
    zpráv, to přijde později). Staví na compute_umm_delta, ale filtruje
    "změněné" jen na skutečně tržně relevantní revize: drobná revize
    (posun data o den, zaokrouhlení publikačního času) není zajímavá,
    zajímavá je jen změna OBJEMU nedostupné kapacity.

    Práh: |Δ unavailableCapacity| / technicalCapacity > capacity_change_threshold
    (default 0.10 = 10 %, konzultováno s uživatelem, laditelné parametrem
    — ne natvrdo zabudované do logiky). Nové a zrušené odstávky se
    vždy berou celé (žádný práh — objevení/zmizení odstávky je vždy
    relevantní, na rozdíl od malé revize existující).

    Vrací DataFrame se sloupcem change_type (new/cancelled/revised) a
    capacity_change_pct (jen u revised, jinak NaN)."""
    delta = compute_umm_delta(df_now, df_previous)

    novel = delta["new"].copy()
    if not novel.empty:
        novel["change_type"] = "new"
        novel["capacity_change_pct"] = pd.NA

    cancelled = delta["cancelled"].copy()
    if not cancelled.empty:
        cancelled["change_type"] = "cancelled"
        cancelled["capacity_change_pct"] = pd.NA

    changed = delta["changed"]
    revised_rows = []
    if not changed.empty:
        past_latest = _latest_per_base_id(df_previous)
        past_by_id = past_latest.set_index("base_id") if not past_latest.empty else past_latest
        for _, row in changed.iterrows():
            bid = row["base_id"]
            if past_by_id.empty or bid not in past_by_id.index:
                continue
            past_row = past_by_id.loc[bid]
            tech = row.get("technicalCapacity") or 0
            if not tech:
                continue
            delta_unavail = (row.get("unavailableCapacity") or 0) - (past_row.get("unavailableCapacity") or 0)
            pct = abs(delta_unavail) / tech
            if pct > capacity_change_threshold:
                r = row.copy()
                r["change_type"] = "revised"
                r["capacity_change_pct"] = pct
                revised_rows.append(r)
    revised = pd.DataFrame(revised_rows) if revised_rows else changed.iloc[0:0].copy()

    parts = [d for d in (novel, cancelled, revised) if not d.empty]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def update_gassco_umm():
    """REMIT UMM zprávy (odstávky polí GASSCO) — /xlexport vrací VŽDY
    kompletní aktuální stav (ne přírůstek/rolling feed jako dřívější
    atom.xml), takže gassco_umm.parquet je teď prostý poslední snapshot,
    ne akumulovaný merge — přepisuje se celý při každém běhu, žádný
    merge s předchozí verzí není potřeba ani žádoucí (mohl by tam nechat
    navždy viset messageId, co Gassco už ze svého exportu odstranil).

    Historie napříč běhy (pro delta/alerting) se buduje přes denní
    snapshoty (write_daily_snapshot) níž, ne přes tenhle soubor.

    KRITICKÉ (ověřeno přes ACER REMIT Q&A): eventStatus="Dismissed"
    znamená skutečné zrušení odstávky (návrat kapacity), ne rutinní
    revizi — je to plnohodnotný záznam v exportu, ne něco, co bychom
    měli speciálně řešit při zápisu."""
    os.makedirs("data/history", exist_ok=True)
    new_data = fetch_gassco_umm()
    if new_data.empty:
        print("  GASSCO UMM: žádná data")
        return

    combined = new_data.sort_values(["base_id", "revision"]).reset_index(drop=True)
    combined.to_parquet(GASSCO_UMM_PATH, index=False)
    print(f"  GASSCO UMM: {len(combined)} řádků (kompletní stav) → {GASSCO_UMM_PATH}")

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
