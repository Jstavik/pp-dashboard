import logging
import threading

import pandas as pd

from core.cache import ttl_cache

log = logging.getLogger(__name__)

CEPS_WSDL = "https://www.ceps.cz/_layouts/CepsData.asmx?WSDL"
CEPS_NS   = "https://www.ceps.cz/CepsData/StructuredData/1.0"

# Mapování value-id → název pro AktivaceSVRvCR (stejné jako dřív ve
# fetch_ceps_svr) — charts.imbalance.fig_ceps_svr čte právě tyhle sloupce.
SVR_SERIES = {
    "value1": "aFRR+ [MW]",
    "value2": "aFRR- [MW]",
    "value3": "mFRR+ [MW]",
    "value4": "mFRR- [MW]",
    "value7": "mFRR5 [MW]",
}

_client = None
_client_lock = threading.Lock()


def _get_ceps_client():
    """SOAP klient se vytváří LÍNĚ při prvním volání (dřív při importu
    modulu — import data.ceps tak stahoval WSDL po síti a při výpadku ČEPS
    zdržoval start celé aplikace). Neúspěšná inicializace se zkusí znovu
    při dalším volání."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                from zeep import Client as SoapClient
                from zeep.transports import Transport
                _client = SoapClient(wsdl=CEPS_WSDL, transport=Transport(timeout=20, operation_timeout=30))
    return _client


def _parse_ceps(result) -> pd.DataFrame:
    series = {}
    for s in result.findall(f"{{{CEPS_NS}}}series/{{{CEPS_NS}}}serie"):
        series[s.get("id")] = s.get("name")
    rows = []
    for item in result.findall(f"{{{CEPS_NS}}}data/{{{CEPS_NS}}}item"):
        row = {"time": pd.Timestamp(item.get("date")).tz_convert("Europe/Prague")}
        for vid, name in series.items():
            val = item.get(vid)
            row[name] = float(val) if val is not None else 0.0
        rows.append(row)
    return pd.DataFrame(rows).set_index("time") if rows else pd.DataFrame()


def _parse_ceps_ids(result, mapping: dict) -> pd.DataFrame:
    """Jako _parse_ceps, ale sloupce podle pevného value-id mapování."""
    rows = []
    for item in result.findall(f"{{{CEPS_NS}}}data/{{{CEPS_NS}}}item"):
        row = {"time": pd.Timestamp(item.get("date")).tz_convert("Europe/Prague")}
        for vid, name in mapping.items():
            row[name] = float(item.get(vid, 0) or 0)
        rows.append(row)
    return pd.DataFrame(rows).set_index("time") if rows else pd.DataFrame()


def _parse_price(result, start: pd.Timestamp, col: str) -> pd.DataFrame:
    rows = []
    for item in result.findall(f"{{{CEPS_NS}}}data/{{{CEPS_NS}}}item"):
        interval = item.get("value15", "")
        price    = float(item.get("value2", 0) or 0)
        if not interval or price == 0:
            continue
        hh, mm = interval.split("-")[0].split(":")
        ts = start + pd.Timedelta(hours=int(hh), minutes=int(mm))
        rows.append({"time": ts, col: price})
    return pd.DataFrame(rows).set_index("time") if rows else pd.DataFrame()


def _empty_all(now) -> dict:
    empty = pd.DataFrame()
    return {
        "imbal": empty, "svr": empty, "load": empty,
        "gen": empty, "res": empty, "freq": empty,
        "cb": empty, "cena": empty, "now": now,
        "svr_named": empty, "imbal_mw": empty, "price": empty,
    }


@ttl_cache(ttl=60)
def fetch_ceps_all():
    """Stáhne všechna ČEPS data najednou — jeden cache entry (TTL 60 s).

    Vrací i odvozené klíče pro dřívější samostatné funkce (svr_named,
    imbal_mw, price) — fetch_ceps_imbalance/svr/imbalance_price dřív volaly
    STEJNÉ SOAP metody ještě jednou zvlášť (OPTIMIZATION_REPORT.md: dva
    requesty na AktualniSystemovaOdchylkaCR), teď jen přebírají výsledek
    odsud."""
    now   = pd.Timestamp.now(tz="Europe/Prague")
    start = now.normalize()

    try:
        ceps = _get_ceps_client()
    except Exception as e:
        log.warning("ČEPS SOAP init selhal: %s", e)
        return _empty_all(now)

    def call(method, **kw):
        fn = getattr(ceps.service, method)
        return fn(
            dateFrom=start.to_pydatetime().replace(tzinfo=None),
            dateTo  =now.to_pydatetime().replace(tzinfo=None),
            **kw
        )

    raw = {}

    def safe(key, method, **kw):
        try:
            raw[key] = call(method, **kw)
            return _parse_ceps(raw[key])
        except Exception as e:
            log.info("ČEPS %s selhalo: %s", method, e)
            return pd.DataFrame()

    df_imbal = safe("imbal", "AktualniSystemovaOdchylkaCR", agregation="MI", function="AVG")
    df_svr   = safe("svr", "AktivaceSVRvCR", agregation="MI", function="AVG", param1="all")
    df_load  = safe("load", "Load", agregation="MI", function="AVG", version="RT")
    df_gen   = safe("gen", "Generation", agregation="QH", function="AVG", version="RT", para1="all")
    df_res   = safe("res", "GenerationRES", agregation="MI", function="AVG", version="RT", para1="all")
    df_freq  = safe("freq", "Frekvence")
    df_cb    = safe("cb", "CrossborderPowerFlows", agregation="MI", function="AVG", version="RT")

    df_cena = pd.DataFrame()
    try:
        r_cena = call("OdhadovanaCenaOdchylky")
        df_cena = _parse_price(r_cena, start, "Cena odchylky (CZK/MWh)")
    except Exception as e:
        log.info("ČEPS OdhadovanaCenaOdchylky selhalo: %s", e)

    if not df_cb.empty:
        actual_cols = [c for c in df_cb.columns if "Actual" in c]
        if actual_cols:
            df_cb["Net Export (MW)"] = df_cb[actual_cols].sum(axis=1)

    svr_named = _parse_ceps_ids(raw["svr"], SVR_SERIES) if "svr" in raw else pd.DataFrame()
    imbal_mw = (df_imbal.iloc[:, [0]].rename(columns={df_imbal.columns[0]: "odchylka_MW"})
                if not df_imbal.empty else pd.DataFrame())
    price = (df_cena.rename(columns={"Cena odchylky (CZK/MWh)": "cena_CZK_MWh"})
             if not df_cena.empty else pd.DataFrame())

    return {
        "imbal": df_imbal, "svr": df_svr, "load": df_load,
        "gen": df_gen, "res": df_res, "freq": df_freq,
        "cb": df_cb, "cena": df_cena, "now": now,
        "svr_named": svr_named, "imbal_mw": imbal_mw, "price": price,
    }


def fetch_ceps_imbalance():
    """Systémová odchylka ČR z ČEPS — minutová, ~5min zpoždění.
    Vrací (DataFrame se sloupcem odchylka_MW, now) jako dřív."""
    d = fetch_ceps_all()
    return d["imbal_mw"], d["now"]


def fetch_ceps_svr():
    """Aktivace SVR v ČR z ČEPS — minutová, sloupce podle SVR_SERIES."""
    return fetch_ceps_all()["svr_named"]


def fetch_ceps_imbalance_price():
    """Odhadovaná cena odchylky z ČEPS — 15min, sloupec cena_CZK_MWh."""
    return fetch_ceps_all()["price"]
