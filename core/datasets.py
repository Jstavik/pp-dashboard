"""Registr datasetů v historii (<PPDASH_DATA_DIR>/history) — jediný zdroj
pravdy pro: kdo dataset zapisuje (úloha scripts/update_all.py), jak starý
smí být, co je mrtvé (nenahrává se do PPDASH_DATA_DIR) a stav dat
(scripts/data_status.py, deploy/status.sh, /api/data-status).

Přehled kdo čte / kde se zobrazuje je v REBUILD_NOTES.md (audit historie).
"""
import glob
import os
from dataclasses import dataclass

import pandas as pd

from core.settings import history_root


@dataclass(frozen=True)
class Dataset:
    name: str            # cesta relativně k history/ (adresář nebo soubor)
    task: str            # úloha update_all.py, která ho zapisuje
    max_age_h: float     # po kolika hodinách bez nových dat je to "staré"
    date_col: str = None  # sloupec s časem dat (None = jen čas úpravy souboru)
    fmt: str = "parquet"
    subdirs: bool = False  # dataset = adresář podadresářů (generation/<CC>, energy_charts/<ep>, ceps/<ds>)


# Pozn. k max_age_h: zdroje s denní granularitou publikují se zpožděním
# (ENTSO-G/GIE D+1, hydro týdně, ENTSOG operational měsíční periody).
DATASETS = [
    # živé (timer ppdash-live, každou minutu)
    Dataset("ceps", "ceps", 0.25, "time", subdirs=True),
    Dataset("gassco_realtime", "gassco_realtime", 2, "updated"),
    Dataset("gassco_umm.parquet", "gassco_umm", 1),
    Dataset("gassco_umm_snapshots", "gassco_umm", 26),
    # 2× denně (timer ppdash-update)
    Dataset("entsog_flows", "entsog", 72, "date"),
    Dataset("entsog_operational", "entsog_operational", 24 * 35),
    Dataset("gie_all_storage", "gie", 72, "gasDayStart", fmt="csv"),
    Dataset("lng_storage", "lng", 72, "gasDayStart", fmt="csv"),
    Dataset("gassco_nominations", "gassco", 72, "date", fmt="csv"),
    Dataset("hydro_reservoirs", "hydro", 24 * 14, "date", fmt="csv"),
    Dataset("dap_europe", "dap_europe", 48, "date"),
    Dataset("generation", "generation", 24, "date", subdirs=True),
    Dataset("outages.parquet", "outages", 26),
    Dataset("outages_snapshots", "outages", 26),
    Dataset("energy_charts", "energy_charts", 36, "timestamp", subdirs=True),
    Dataset("energy_charts.parquet", "energy_charts", 36, "timestamp"),
    Dataset("energy_charts_progress.json", "-", float("inf")),  # jen ruční run_backfill
    # ENTSO-E (timer ppdash-update-entsoe, 3× denně, data/entsoe_history.py)
    Dataset("entsoe_prices", "entsoe_prices", 36, "time", subdirs=True),
    Dataset("entsoe_load", "entsoe_load", 36, "time", subdirs=True),
    Dataset("entsoe_ws_forecast", "entsoe_ws", 36, "time", subdirs=True),
    Dataset("entsoe_flows", "entsoe_flows", 36, "time", subdirs=True),
    # Nordic (data/nordic.py, ve výchozím běhu 2× denně)
    Dataset("nve_reservoirs.parquet", "nve", 24 * 10, "date"),
    Dataset("nve_reservoirs_stats.parquet", "nve", float("inf")),
    Dataset("fingrid", "fingrid", 36, "time"),
    Dataset("energinet", "energinet", 72, "time", subdirs=True),
    # týdně (timer ppdash-update-cot, st + záloha čt/so)
    Dataset("ice_cot.parquet", "cot", 24 * 12, "report_date"),
    Dataset("ice_cot_status.json", "cot", 24 * 11),
]

# Mrtvá data — nikdo je nezapisuje ani nečte. V gitu na main zůstávají
# (workflow tam běží dál), do PPDASH_DATA_DIR se NEnahrávají
# (deploy/install_user.sh je vynechá z rsync).
DEAD = {
    "hu_generation": "stará kopie, nahrazeno generation/HU (konec 2026-08)",
    "nuclear_fr_generation": "stará kopie, nahrazeno generation/FR (konec 2026-08)",
    "entsog_capacity": "nahrazeno entsog_operational (kapacity/interrupce), nikdo nečte ani nezapisuje",
    "hydro_reservoirs.csv": "stará jednosouborová verze, nahrazeno hydro_reservoirs/",
}


def _files(path: str, ds: Dataset) -> list:
    if os.path.isfile(path):
        return [path]
    pattern = os.path.join(path, "*", "*.*") if ds.subdirs else os.path.join(path, "*.*")
    return [f for f in glob.glob(pattern) if not os.path.basename(f).startswith(".")]


def _last_data_time(path: str, ds: Dataset):
    """Nejnovější čas v datech — čte jen poslední soubor (u subdirs
    poslední soubor každého podadresáře) a jen sloupec s časem."""
    if not ds.date_col or not os.path.exists(path):
        return None
    if os.path.isfile(path):
        groups = [[path]]
    else:
        dirs = sorted(glob.glob(os.path.join(path, "*"))) if ds.subdirs else [path]
        groups = [sorted(glob.glob(os.path.join(d, f"*.{ds.fmt}"))) for d in dirs]
    best = None
    for files in groups:
        if not files:
            continue
        try:
            if ds.fmt == "parquet":
                col = pd.read_parquet(files[-1], columns=[ds.date_col])[ds.date_col]
            else:
                col = pd.read_csv(files[-1], usecols=[ds.date_col])[ds.date_col]
            ts = pd.to_datetime(col, utc=True).max()
        except Exception:
            continue
        if pd.notna(ts) and (best is None or ts > best):
            best = ts
    return best


def dataset_status(now: pd.Timestamp = None) -> list:
    """[{name, task, files, size_mb, modified, last_data, age_h, ok}] —
    age_h = stáří posledních dat (nebo poslední úpravy souborů, když
    dataset nemá časový sloupec)."""
    now = now or pd.Timestamp.now(tz="UTC")
    root = history_root()
    out = []
    for ds in DATASETS:
        path = str(root / ds.name)
        files = _files(path, ds) if os.path.exists(path) else []
        modified = (pd.Timestamp(max(os.path.getmtime(f) for f in files), unit="s", tz="UTC")
                    if files else None)
        last_data = _last_data_time(path, ds)
        # budoucí časy (forecast) neříkají nic o čerstvosti — bere se čas úpravy
        ref = last_data if last_data is not None and last_data <= now else modified
        age_h = (now - ref).total_seconds() / 3600 if ref is not None else None
        out.append({
            "name": ds.name, "task": ds.task, "files": len(files),
            "size_mb": round(sum(os.path.getsize(f) for f in files) / 2**20, 1),
            "modified": modified, "last_data": last_data,
            "age_h": None if age_h is None else round(age_h, 1),
            "ok": age_h is not None and age_h <= ds.max_age_h,
            "max_age_h": ds.max_age_h,
        })
    return out


def dead_present() -> list:
    """Mrtvé datasety, které v historii leží (např. po ručním rsync)."""
    root = history_root()
    return [n for n in DEAD if (root / n).exists()]


# ── Katalog pro Průzkumník (web /pruzkumnik, core/explore.py) ───────────
#
# Každý dataset = SQL "zdroj" nad soubory v history/ (DuckDB read_parquet/
# read_csv) popsaný sémanticky: časový sloupec, míry (measures) a dimenze.
# Výrazy jsou SQL nad řádkem souboru. Agregace probíhá na serveru (DuckDB),
# do prohlížeče jde jen výsledek.

@dataclass(frozen=True)
class Measure:
    id: str
    label: str
    expr: str              # SQL výraz nad řádkem zdroje
    unit: str = ""
    cat_agg: str = "sum"   # sloučení přes dimenze, které nejsou kategorií (sum|avg|min|max)
    time_agg: str = "avg"  # výchozí agregace v čase (sum|avg|min|max|last|energy)
    power: bool = False    # výkon (MW/GW) → nabídnout "energie = průměr × hodiny"


@dataclass(frozen=True)
class Dim:
    id: str
    label: str
    expr: str = None        # SQL výraz (default = id)
    partition: bool = False  # hodnota = název podadresáře (generation/<CC>/…) — prořezání souborů


@dataclass(frozen=True)
class ExploreSpec:
    key: str
    label: str
    group: str               # Elektřina | Plyn | Nordic | Počasí | Trh
    path: str                # glob relativně k history/ (…/*.parquet nebo …/*.csv)
    time_expr: str           # SQL → TIMESTAMPTZ
    measures: tuple
    dims: tuple
    source: str              # odkud data jsou
    dataset: str = None      # Dataset.name v DATASETS (čerstvost, úloha)
    where: str = None        # pevný filtr zdroje (SQL)
    native: str = "1h"       # nativní rozlišení (15min|1h|1D|1W)
    align: str = None        # zarovnání sérií před sloučením kategorií (např. "hour")
    default_days: int = 30
    default_cat: str = None
    default_filters: tuple = ()  # ((dim, (hodnoty,)), …)
    description: str = ""

    @property
    def fmt(self) -> str:
        return "csv" if self.path.endswith(".csv") else "parquet"

    def measure(self, mid):
        return next((m for m in self.measures if m.id == mid), self.measures[0])

    def dim(self, did):
        return next((d for d in self.dims if d.id == did), None)


_EC_TS = "CAST(\"timestamp\" AS TIMESTAMPTZ)"
_EC_DIMS = (Dim("geo_key", "Země", partition=False), Dim("series", "Série", "series_name"),
            Dim("series_id", "ID série"))

EXPLORE = [
    ExploreSpec(
        "generation", "Výroba podle zdroje (ENTSO-E)", "Elektřina", "generation/*/*.parquet",
        "CAST(date AS TIMESTAMPTZ)",
        (Measure("mw", "Výroba", "mw", "MW", "sum", "avg", power=True),),
        (Dim("country", "Země", partition=True), Dim("source_type", "Zdroj"), Dim("psr_code", "PSR kód")),
        "ENTSO-E 16.1.B&C Actual Generation per Production Type", "generation",
        native="15min", align="hour", default_days=30, default_cat="source_type",
        default_filters=(("country", ("CZ",)),),
        description="Skutečná výroba podle typu zdroje, hodinově/čtvrthodinově, od 2020."),
    ExploreSpec(
        "ec_public_power", "Výroba a zatížení (Energy-Charts)", "Elektřina",
        "energy_charts/public_power/*.parquet", _EC_TS,
        (Measure("v", "Výkon", "value", "MW", "sum", "avg", power=True),),
        _EC_DIMS, "Fraunhofer ISE Energy-Charts /public_power", "energy_charts",
        native="15min", align="hour", default_cat="series",
        description="Veřejná výroba podle zdroje + zatížení, reziduální zatížení, obchod."),
    ExploreSpec(
        "ec_forecast", "Prognóza výroby/zatížení (Energy-Charts)", "Elektřina",
        "energy_charts/public_power_forecast/*.parquet", _EC_TS,
        (Measure("v", "Výkon", "value", "MW", "sum", "avg", power=True),),
        _EC_DIMS, "Energy-Charts /public_power_forecast (day-ahead)", "energy_charts",
        native="15min", align="hour", default_days=7, default_cat="series"),
    ExploreSpec(
        "ec_cbet", "Přeshraniční obchod (Energy-Charts cbet)", "Elektřina",
        "energy_charts/cbet/*.parquet", _EC_TS,
        (Measure("v", "Saldo (kladné = import)", "value", "GW", "sum", "avg", power=True),),
        _EC_DIMS, "Energy-Charts /cbet", "energy_charts", native="15min", align="hour",
        default_cat="series"),
    ExploreSpec(
        "ec_cbpf", "Fyzické přeshraniční toky (Energy-Charts cbpf)", "Elektřina",
        "energy_charts/cbpf/*.parquet", _EC_TS,
        (Measure("v", "Saldo (kladné = import)", "value", "GW", "sum", "avg", power=True),),
        _EC_DIMS, "Energy-Charts /cbpf", "energy_charts", native="15min", align="hour",
        default_cat="series"),
    ExploreSpec(
        "ec_frequency", "Frekvence sítě (Energy-Charts, 5 min)", "Elektřina", "energy_charts.parquet",
        _EC_TS,
        (Measure("hz", "Frekvence", "value", "Hz", "avg", "avg"),
         Measure("dev", "Odchylka od 50 Hz", "(value - 50) * 1000", "mHz", "avg", "avg")),
        (Dim("geo_key", "Měřicí bod"),), "Energy-Charts /frequency", "energy_charts.parquet",
        where="endpoint = 'frequency'", native="5min", default_days=7),
    ExploreSpec(
        "ceps", "ČEPS minutová data", "Elektřina", "ceps/*/*.parquet", "CAST(\"time\" AS TIMESTAMPTZ)",
        (Measure("v", "Hodnota", "value", "", "sum", "avg"),),
        (Dim("ds", "Datová sada", partition=True), Dim("series", "Série")),
        "ČEPS SOAP (živě, každou minutu)", "ceps", native="1min", default_days=2,
        default_cat="series", default_filters=(("ds", ("load",)),)),
    ExploreSpec(
        "entsoe_prices", "Day-ahead ceny — bidding zóny EU (ENTSO-E)", "Elektřina", "entsoe_prices/*/*.parquet",
        "CAST(\"time\" AS TIMESTAMPTZ)",
        (Measure("price", "Cena", "price", "EUR/MWh", "avg", "avg"),),
        (Dim("zone", "Zóna", "zone", partition=True),),
        "ENTSO-E 12.1.D Day-ahead Prices (SDAC)", "entsoe_prices", native="15min", align="hour",
        default_days=30, default_cat="zone", default_filters=(("zone", ("CZ", "DE_LU", "AT", "SK", "PL", "HU")),),
        description="Hodinové (od 10/2025 čtvrthodinové) ceny všech bidding zón vč. NO1–5, SE1–4, FI, DK1–2, od 2019."),
    ExploreSpec(
        "entsoe_load", "Spotřeba skutečná + prognóza (ENTSO-E)", "Elektřina", "entsoe_load/*/*.parquet",
        "CAST(\"time\" AS TIMESTAMPTZ)",
        (Measure("mw", "Spotřeba", "mw", "MW", "sum", "avg", power=True),),
        (Dim("area", "Oblast", "area", partition=True), Dim("kind", "Druh")),
        "ENTSO-E 6.1.A Actual Total Load, 6.1.B Day-ahead Total Load Forecast", "entsoe_load",
        native="15min", align="hour", default_days=14, default_cat="kind", default_filters=(("area", ("CZ",)),),
        description="kind: actual = skutečnost, forecast = prognóza D-1. Země i Nordic zóny."),
    ExploreSpec(
        "entsoe_ws", "Prognóza větru a slunce D-1 (ENTSO-E)", "Elektřina", "entsoe_ws_forecast/*/*.parquet",
        "CAST(\"time\" AS TIMESTAMPTZ)",
        (Measure("mw", "Prognóza výroby", "mw", "MW", "sum", "avg", power=True),),
        (Dim("area", "Oblast", "area", partition=True), Dim("psr", "Zdroj")),
        "ENTSO-E 14.1.D Day-ahead Generation Forecasts for Wind and Solar", "entsoe_ws_forecast",
        native="15min", align="hour", default_days=14, default_cat="psr", default_filters=(("area", ("DE_LU",)),)),
    ExploreSpec(
        "entsoe_flows", "Přeshraniční toky a plánované výměny (ENTSO-E)", "Elektřina", "entsoe_flows/*/*.parquet",
        "CAST(\"time\" AS TIMESTAMPTZ)",
        (Measure("mw", "Tok (směr od → do)", "mw", "MW", "sum", "avg", power=True),),
        (Dim("pair", "Hranice (směr)", "src || '_' || dst", partition=True), Dim("src", "Od"), Dim("dst", "Do"),
         Dim("kind", "Druh")),
        "ENTSO-E 12.1.G Physical Flows, 12.1.F Scheduled Commercial Exchanges", "entsoe_flows",
        native="15min", align="hour", default_days=30, default_cat="dst",
        default_filters=(("src", ("CZ",)), ("kind", ("physical",))),
        description="Směrové řady (od → do); export oblasti = filtr Od, import = filtr Do. "
                    "kind: physical = fyzický tok, scheduled = plánovaná výměna."),
    ExploreSpec(
        "dap_europe", "DAP base/peak — EU (denní)", "Elektřina", "dap_europe/*.parquet",
        "CAST(\"date\" AS TIMESTAMP) AT TIME ZONE 'Europe/Prague'",
        (Measure("base", "Base", "base", "EUR/MWh", "avg", "avg"),
         Measure("peak", "Peak", "peak", "EUR/MWh", "avg", "avg"),
         Measure("dod_base", "Změna base D/D", "dod_base", "EUR/MWh", "avg", "avg")),
        (Dim("cc", "Země"),), "ENTSO-E day-ahead (agregace base/peak)", "dap_europe",
        native="1D", default_days=90, default_cat="cc", default_filters=(("cc", ("CZ", "DE", "AT")),)),
    ExploreSpec(
        "hydro", "Vodní nádrže (ENTSO-E 16.1.D)", "Elektřina", "hydro_reservoirs/*.csv",
        "CAST(\"date\" AS TIMESTAMPTZ)",
        (Measure("gwh", "Zásoba energie", "value_GWh", "GWh", "sum", "avg"),),
        (Dim("country", "Země"),), "ENTSO-E Aggregated Filling Rate of Water Reservoirs", "hydro_reservoirs",
        native="1W", default_days=3 * 365, default_cat="country",
        default_filters=(("country", ("NO", "SE", "FI")),)),
    ExploreSpec(
        "nve", "Nádrže Norsko — NVE (týdně)", "Nordic", "nve_reservoirs.parquet", "CAST(\"date\" AS TIMESTAMPTZ)",
        (Measure("pct", "Plnění", "filling_pct", "%", "avg", "avg"),
         Measure("twh", "Zásoba", "filling_twh", "TWh", "sum", "avg"),
         Measure("cap", "Kapacita", "capacity_twh", "TWh", "sum", "avg")),
        (Dim("area", "Oblast"),), "NVE Magasinstatistikk", "nve_reservoirs.parquet", native="1W",
        default_days=5 * 365, default_cat="area", default_filters=(("area", ("NO",)),),
        description="Plnění nádrží celé Norsko (NO) a elspot oblasti NO1–NO5, týdně od 1995."),
    ExploreSpec(
        "fingrid", "Fingrid — Finsko", "Nordic", "fingrid/*.parquet", "CAST(\"time\" AS TIMESTAMPTZ)",
        (Measure("mw", "Výkon", "value", "MW", "sum", "avg", power=True),),
        (Dim("series", "Série"),), "Fingrid open data (API klíč)", "fingrid", native="15min", align="hour",
        default_days=14, default_cat="series", default_filters=(("series", ("spotřeba", "výroba", "vítr")),)),
    ExploreSpec(
        "energinet", "Energinet — Dánsko (DK1/DK2)", "Nordic", "energinet/*/*.parquet",
        "CAST(\"time\" AS TIMESTAMPTZ)",
        (Measure("v", "Hodnota", "value", "MWh/h", "sum", "avg", power=True),),
        (Dim("dataset", "Dataset", partition=True), Dim("area", "Oblast"), Dim("series", "Série")),
        "Energinet Energi Data Service", "energinet", native="1h", default_days=14, default_cat="series",
        default_filters=(("dataset", ("ProductionConsumptionSettlement",)),),
        description="Obecně převedené sloupce datasetů (série = název sloupce, u prognóz typ|sloupec)."),
    ExploreSpec(
        "entsog_flows", "Toky plynu (ENTSO-G, denní)", "Plyn", "entsog_flows/*.parquet",
        "CAST(\"date\" AS TIMESTAMPTZ)",
        (Measure("gwh", "Tok", "value_GWh", "GWh/d", "sum", "avg"),),
        (Dim("indicator", "Indikátor"), Dim("country", "Země", "countryKey"),
         Dim("direction", "Směr", "directionKey"), Dim("adjacent", "Sousední systém", "adjacentSystemsLabel"),
         Dim("points", "Body", "pointsNames")),
        "ENTSO-G Transparency /aggregateddata", "entsog_flows", native="1D", default_days=365,
        default_cat="direction",
        default_filters=(("indicator", ("Physical Flow",)), ("country", ("CZ",)))),
    ExploreSpec(
        "entsog_operational", "ENTSOG operational (body, nominace, kapacity)", "Plyn",
        "entsog_operational/*.parquet", "CAST(periodFrom AS TIMESTAMPTZ)",
        (Measure("gwh", "Hodnota", "value_GWh", "GWh/d", "sum", "avg"),),
        (Dim("indicator", "Indikátor"), Dim("point", "Bod", "pointLabel"), Dim("operator", "Operátor", "operatorLabel"),
         Dim("direction", "Směr", "directionKey"), Dim("point_type", "Typ bodu", "pointType")),
        "ENTSO-G Transparency /operationaldata", "entsog_operational", native="1D", default_days=90,
        default_cat="point", where="periodType = 'day'",
        default_filters=(("indicator", ("Nomination",)), ("operator", ("NET4GAS",))),
        description="Denní nominace/renominace, kvalita (GCV, Wobbe) a kapacity po bodech. "
                    "Kapacity jsou období (periodFrom–periodTo) — čas = začátek období."),
    ExploreSpec(
        "gie", "Zásobníky plynu (GIE AGSI+)", "Plyn", "gie_all_storage/*.csv", "CAST(gasDayStart AS TIMESTAMPTZ)",
        (Measure("full", "Naplnění", "\"full\"", "%", "avg", "avg"),
         Measure("gas", "Zásoba", "gasInStorage", "TWh", "sum", "avg"),
         Measure("inj", "Vtláčení", "injection", "GWh/d", "sum", "avg"),
         Measure("wd", "Těžba", "withdrawal", "GWh/d", "sum", "avg"),
         Measure("net", "Čistá těžba", "netWithdrawal", "GWh/d", "sum", "avg")),
        (Dim("country", "Země", "country_code"),), "GIE AGSI+", "gie_all_storage", native="1D",
        default_days=3 * 365, default_cat="country", default_filters=(("country", ("CZ", "DE", "AT", "EU")),)),
    ExploreSpec(
        "lng", "LNG terminály (GIE ALSI)", "Plyn", "lng_storage/*.csv", "CAST(gasDayStart AS TIMESTAMPTZ)",
        (Measure("inv", "Zásoba", "inventory_gwh", "GWh", "sum", "avg"),
         Measure("sendout", "Send-out", "sendOut", "GWh/d", "sum", "avg"),
         Measure("full", "Naplnění", "full_pct", "%", "avg", "avg")),
        (Dim("country", "Země", "country_code"), Dim("terminal", "Terminál", "name")),
        "GIE ALSI", "lng_storage", native="1D", default_days=365, default_cat="country"),
    ExploreSpec(
        "gassco", "GASSCO nominace (denní)", "Plyn", "gassco_nominations/*.csv", "CAST(\"date\" AS TIMESTAMPTZ)",
        (Measure("msm3", "Nominace", "value_MSm3", "MSm³/d", "sum", "avg"),
         Measure("gwh", "Nominace", "value_GWh", "GWh/d", "sum", "avg")),
        (Dim("point", "Bod"),), "GASSCO", "gassco_nominations", native="1D", default_days=365,
        default_cat="point"),
    ExploreSpec(
        "ice_cot", "ICE Commitments of Traders", "Trh", "ice_cot.parquet",
        "CAST(report_date AS TIMESTAMP) AT TIME ZONE 'Europe/Prague'",
        (Measure("nops", "Pozice", "nops", "", "sum", "last"),),
        (Dim("venue", "Burza"), Dim("product", "Produkt", "product_code"), Dim("category", "Kategorie"),
         Dim("side", "Strana"), Dim("scope", "Rozsah")),
        "ICE MiFID II CoT", "ice_cot.parquet", native="1W", default_days=3 * 365, default_cat="category",
        default_filters=(("venue", ("NDEX",)), ("product", ("TFM",)), ("side", ("Long",)),
                         ("scope", ("Total",)))),
]

EXPLORE_BY_KEY = {e.key: e for e in EXPLORE}

# kdy úlohy běží (deploy/systemd-user/*.timer) — pro katalog
TASK_SCHEDULE = {
    "live": "každou minutu",
    "default": "2× denně (06:00, 17:00)",
    "cot": "týdně (st 14:00/19:00, záloha čt+so)",
}


def schedule_for(spec: "ExploreSpec") -> str:
    ds = next((d for d in DATASETS if d.name == spec.dataset), None)
    if ds is None:
        return "—"
    if ds.task in ("ceps", "gassco_realtime", "gassco_umm"):
        return TASK_SCHEDULE["live"]
    if ds.task == "cot":
        return TASK_SCHEDULE["cot"]
    return TASK_SCHEDULE.get(ds.task, TASK_SCHEDULE["default"])
