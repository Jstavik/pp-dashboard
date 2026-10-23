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
    # týdně (timer ppdash-update-cot, so/út)
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
