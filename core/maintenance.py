"""Údržba historie (úloha `maintenance`, timer ppdash-maintenance týdně).

Všechna pravidla jsou v POLICY níž (jediné místo; dny jdou přepsat env
proměnnými). Co dělá:

1. **Kompakce malých souborů** — uzavřené měsíce → jeden soubor na rok
   (`{YYYY}.parquet|csv`), u minutových zdrojů uzavřené dny → měsíc.
   Např. entsoe_prices: 45 zón × ~90 měsíců ≈ 4 000 souborů → ~350.
   Čtení (read_partitioned, Průzkumník/DuckDB) i zápis (upsert_partitioned
   merguje revize do ročního souboru) roční soubory znají.
   Ověření před smazáním originálů: počet řádků a kontrolní součet obsahu
   (hash každého řádku nezávislý na typech, viz content_checksum) zapsaného
   a znovu přečteného souboru = originály. CSV se slučuje jako text
   (buňky beze změny zápisu, typy se mezi měsíci liší). Přesné duplicitní řádky (jen po
   přerušené kompakci) se odstraní a vypíšou.
2. **Retence snapshotů** (odstávky ENTSO-E, GASSCO UMM) — posledních
   N dní plné denní soubory, starší v archivu „jen změny“
   (partitioned_store.archive_snapshot_days): každý řádek jednou s intervalem
   dnů. Každý den se po archivaci zrekonstruuje a porovná s originálem;
   denní soubor se smaže jen při shodě. Web (Porovnat s …) čte archiv
   transparentně.
3. **Předpovědi počasí podle dne vydání** — po N dnech se ponechá jen
   předstih ≤ max_lead_days (dny 11–16 mají malou hodnotu).
4. **Velikosti** — snímek velikosti a počtu souborů každého datasetu do
   history/.sizes.json (růst na /stav); výsledek běhu do .maintenance.json.

Každý dataset se upravuje pod zámkem úlohy, která ho zapisuje
(core/locks.py) — když úloha právě běží (déle než 2 min), dataset se
přeskočí a příště se dohoní.
"""
import json
import logging
import os
import time

import pandas as pd

from core.datasets import DATASETS
from core.locks import Busy, task_lock
from core.settings import env, history_root

log = logging.getLogger("ppdash.maintenance")


def _days(name, default):
    return int(env(name, str(default)))


# ── POLICY: jediné místo s pravidly údržby ──────────────────────────────
POLICY = {
    # dataset → cílová granularita souborů: "year" (uzavřené roky) | "month" (uzavřené měsíce z denních)
    "compact": {
        "entsoe_prices": "year", "entsoe_load": "year", "entsoe_ws_forecast": "year", "entsoe_flows": "year",
        "generation": "year", "fingrid": "year", "energinet": "year", "weather_daily": "year",
        "gie_all_storage": "year", "lng_storage": "year", "gassco_nominations": "year", "hydro_reservoirs": "year",
        "ceps": "month", "gassco_realtime": "month",
    },
    # denní snapshoty: kolik posledních dní ponechat jako plné soubory
    "snapshots": {
        "outages_snapshots": _days("PPDASH_SNAPSHOT_KEEP_DAYS", 60),
        "gassco_umm_snapshots": _days("PPDASH_SNAPSHOT_KEEP_DAYS", 60),
    },
    # předpovědi podle dne vydání: po keep_days jen předstih ≤ max_lead_days
    "forecast_trim": {
        "weather_fc_daily": {"keep_days": _days("PPDASH_FC_KEEP_DAYS", 90),
                             "max_lead_days": _days("PPDASH_FC_MAX_LEAD_DAYS", 10)},
    },
    # denní soubory se kompaktují až po uzavření měsíce + tolik dní (ČEPS dohání výpadky max 7 dní)
    "day_grace_days": 8,
    "sizes_keep": 260,     # kolik snímků velikostí držet (~5 let týdně)
    "lock_wait_s": 120,
}

DS_BY_NAME = {d.name: d for d in DATASETS}


def _base_dirs(name: str) -> list:
    root = history_root() / name
    if not root.is_dir():
        return []
    ds = DS_BY_NAME.get(name)
    if ds is not None and ds.subdirs:
        return sorted(str(p) for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    return [str(root)]


def _fmt(name: str) -> str:
    ds = DS_BY_NAME.get(name)
    return ds.fmt if ds else "parquet"


# ── 1. kompakce ─────────────────────────────────────────────────────────

def _groups(base_dir: str, fmt: str, target: str, now: pd.Timestamp) -> dict:
    """{cílový stem: [soubory]} — jen uzavřená období, jen kde je co slučovat."""
    groups = {}
    cur_year = now.strftime("%Y")
    cutoff_month = (now - pd.Timedelta(days=POLICY["day_grace_days"])).strftime("%Y-%m")
    for f in sorted(os.listdir(base_dir)):
        if not f.endswith(f".{fmt}") or f.startswith((".", "_")):
            continue
        stem = f[: -len(fmt) - 1]
        if target == "year" and len(stem) == 7 and stem[4] == "-" and stem[:4] < cur_year:
            groups.setdefault(stem[:4], []).append(os.path.join(base_dir, f))
        elif target == "month" and len(stem) == 10 and stem[:7] < cutoff_month:
            groups.setdefault(stem[:7], []).append(os.path.join(base_dir, f))
    for stem, files in groups.items():
        cont = os.path.join(base_dir, f"{stem}.{fmt}")
        if os.path.exists(cont):
            files.insert(0, cont)
    return groups


def _read(path: str, fmt: str) -> pd.DataFrame:
    # CSV se slučuje jako text (každá buňka přesně jak byla v souboru): odvozené
    # typy se mezi měsíci liší (sloupec s "-" je jednou text, jindy float, prázdné
    # buňky NaN vs. "") a round-trip přes typy by měnil zápis čísel i obsah.
    if fmt == "parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path, dtype=str, keep_default_na=False, na_filter=False)


def compact_group(files: list, target_path: str, fmt: str) -> dict:
    """Sloučí soubory do target_path s ověřením. Vrací {rows, files, dups}.
    Při neshodě vyhodí RuntimeError a originály nechá být."""
    from data.partitioned_store import canonical_rows, content_checksum
    frames = [_read(f, fmt) for f in files]
    n_in = sum(len(x) for x in frames)
    raw = pd.concat(frames, ignore_index=True)
    del frames
    if fmt != "parquet":
        raw = raw.fillna("")  # sloupec chybějící v některém měsíci = prázdná buňka, jak ji zapíše to_csv
    if len(raw) != n_in:
        raise RuntimeError(f"{target_path}: concat ztratil řádky ({len(raw)} ≠ {n_in})")
    h = canonical_rows(raw)
    dup = h.duplicated().to_numpy()
    expected = (int((~dup).sum()), int(h[~dup].sum()))
    if dup.any():
        raw = raw[~dup].reset_index(drop=True)
    tmp = os.path.join(os.path.dirname(target_path), "." + os.path.basename(target_path) + ".tmp")
    if fmt == "parquet":
        raw.to_parquet(tmp, index=False)
    else:
        raw.to_csv(tmp, index=False)
    del raw
    got = content_checksum(_read(tmp, fmt))
    if got != expected:
        os.unlink(tmp)
        raise RuntimeError(f"{target_path}: kontrolní součet nesedí (zapsáno {got}, čekáno {expected})")
    os.replace(tmp, target_path)
    for f in files:
        if os.path.abspath(f) != os.path.abspath(target_path):
            os.unlink(f)
    return {"rows": expected[0], "files": len(files), "dups": int(dup.sum())}


def compact_dataset(name: str, target: str, now: pd.Timestamp = None) -> dict:
    now = now or pd.Timestamp.now(tz="UTC")
    fmt = _fmt(name)
    out = {"groups": 0, "files_removed": 0, "rows": 0, "dups": 0, "errors": []}
    for base in _base_dirs(name):
        for stem, files in sorted(_groups(base, fmt, target, now).items()):
            try:
                r = compact_group(files, os.path.join(base, f"{stem}.{fmt}"), fmt)
            except Exception as e:
                log.error("kompakce %s/%s: %s", os.path.relpath(base, history_root()), stem, e)
                out["errors"].append(f"{os.path.basename(base)}/{stem}: {e}")
                continue
            out["groups"] += 1
            out["files_removed"] += r["files"] - 1
            out["rows"] += r["rows"]
            out["dups"] += r["dups"]
    return out


# ── 2. retence snapshotů ────────────────────────────────────────────────

def archive_snapshots(name: str, keep_days: int, now: pd.Timestamp = None) -> dict:
    from data.partitioned_store import _archive_meta, archive_snapshot_days, canonical_rows, read_archived_snapshot
    now = now or pd.Timestamp.now(tz="UTC")
    base = str(history_root() / name)
    out = {"archived": 0, "removed": 0, "errors": []}
    if not os.path.isdir(base):
        return out
    cutoff = (now.normalize() - pd.DateOffset(days=keep_days)).strftime("%Y-%m-%d")
    last = (_archive_meta(base).get("days") or [""])[-1]
    days = sorted(f[:10] for f in os.listdir(base)
                  if f.endswith(".parquet") and len(f) == 18 and f[:10] < cutoff and f[:10] > last)
    if not days:
        return out
    res = archive_snapshot_days(base, days)
    out["archived"] = res["added"]
    for day in days:
        path = os.path.join(base, f"{day}.parquet")
        orig = pd.read_parquet(path)
        rec = read_archived_snapshot(base, day)
        a, b = canonical_rows(orig).sort_values().to_numpy(), canonical_rows(rec).sort_values().to_numpy()
        if len(a) == len(b) and (a == b).all() and list(rec.columns) == list(orig.columns):
            os.unlink(path)
            out["removed"] += 1
        else:
            out["errors"].append(f"{day}: rekonstrukce ≠ originál ({len(b)} vs {len(a)} řádků) — soubor ponechán")
            log.error("archiv %s %s: rekonstrukce nesedí, soubor ponechán", name, day)
    return out


# ── 3. předpovědi podle dne vydání ──────────────────────────────────────

def trim_forecasts(name: str, keep_days: int, max_lead_days: int, now: pd.Timestamp = None) -> dict:
    from data.partitioned_store import content_checksum
    now = now or pd.Timestamp.now(tz="UTC")
    cutoff = now.normalize() - pd.DateOffset(days=keep_days)
    out = {"rows_removed": 0, "files": 0, "errors": []}
    for base in _base_dirs(name):
        for f in sorted(os.listdir(base)):
            if not f.endswith(".parquet") or f.startswith((".", "_")) or f[:7] > cutoff.strftime("%Y-%m"):
                continue
            path = os.path.join(base, f)
            df = pd.read_parquet(path)
            issued = pd.to_datetime(df["issued"], utc=True)
            lead = (pd.to_datetime(df["date"], utc=True) - issued).dt.days
            drop = ((issued < cutoff) & (lead > max_lead_days)).to_numpy()
            if not drop.any():
                continue
            kept = df[~drop].reset_index(drop=True)
            expected = content_checksum(kept)
            tmp = os.path.join(base, f".{f}.tmp")
            kept.to_parquet(tmp, index=False)
            if content_checksum(pd.read_parquet(tmp)) != expected:
                os.unlink(tmp)
                out["errors"].append(f"{f}: kontrolní součet nesedí")
                continue
            os.replace(tmp, path)
            out["rows_removed"] += int(drop.sum())
            out["files"] += 1
    return out


# ── 4. velikosti ────────────────────────────────────────────────────────

def dataset_sizes() -> dict:
    """{dataset: (bajty, soubory)} — vč. archivů a podadresářů, bez skrytých."""
    root = history_root()
    out = {}
    for ds in DATASETS:
        p = root / ds.name
        if p.is_file():
            out[ds.name] = (p.stat().st_size, 1)
        elif p.is_dir():
            size = n = 0
            for dirpath, dirnames, filenames in os.walk(p):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for fn in filenames:
                    if not fn.startswith("."):
                        size += os.path.getsize(os.path.join(dirpath, fn))
                        n += 1
            out[ds.name] = (size, n)
    return out


def _sizes_path():
    return history_root() / ".sizes.json"


def load_sizes_history() -> list:
    try:
        with open(_sizes_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def record_sizes(now: pd.Timestamp = None) -> dict:
    now = now or pd.Timestamp.now(tz="UTC")
    sizes = dataset_sizes()
    hist = load_sizes_history()
    hist.append({"t": now.isoformat(), "ds": {k: list(v) for k, v in sizes.items()}})
    hist = hist[-POLICY["sizes_keep"]:]
    _atomic_json(_sizes_path(), hist)
    return sizes


def growth(sizes: dict = None, now: pd.Timestamp = None, min_days: float = 6) -> dict:
    """{dataset: {"bytes", "files", "per_year"}} — per_year = lineární růst
    od nejstaršího snímku za posledních ~90 dní (aspoň min_days zpět)."""
    now = now or pd.Timestamp.now(tz="UTC")
    sizes = sizes if sizes is not None else dataset_sizes()
    hist = load_sizes_history()
    ref = None
    for h in hist:
        age = (now - pd.Timestamp(h["t"])).total_seconds() / 86400
        if min_days <= age <= 95:
            ref = (h, age)
            break
    out = {}
    for name, (b, n) in sizes.items():
        per_year = None
        if ref and name in ref[0]["ds"]:
            per_year = (b - ref[0]["ds"][name][0]) / ref[1] * 365
        out[name] = {"bytes": b, "files": n, "per_year": per_year}
    return out


def _atomic_json(path, data):
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=str)
    os.replace(tmp, path)


def last_maintenance() -> dict:
    try:
        with open(history_root() / ".maintenance.json", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# ── běh ─────────────────────────────────────────────────────────────────

def _locked(name: str, fn):
    task = DS_BY_NAME[name].task if name in DS_BY_NAME else name
    try:
        with task_lock(task, wait_s=POLICY["lock_wait_s"]):
            return fn()
    except Busy:
        log.warning("údržba %s: úloha %s právě běží — přeskočeno, příště", name, task)
        return {"skipped": f"běží úloha {task}"}


def run_maintenance(now: pd.Timestamp = None) -> dict:
    """Celá údržba. Vrací {"errors": n, "pending": n} pro update_all."""
    t0 = time.monotonic()
    now = now or pd.Timestamp.now(tz="UTC")
    before = dataset_sizes()
    actions = []
    for name, target in POLICY["compact"].items():
        r = _locked(name, lambda: compact_dataset(name, target, now))
        if r.get("groups") or r.get("errors") or r.get("skipped"):
            actions.append({"dataset": name, "action": f"kompakce → {target}", **r})
        if r.get("groups"):
            log.info("kompakce %s: %d skupin, −%d souborů, %d řádků ověřeno, %d duplicit",
                     name, r["groups"], r["files_removed"], r["rows"], r["dups"])
    for name, keep in POLICY["snapshots"].items():
        r = _locked(name, lambda: archive_snapshots(name, keep, now))
        if r.get("archived") or r.get("errors") or r.get("skipped"):
            actions.append({"dataset": name, "action": f"archiv snapshotů starších {keep} dní", **r})
            log.info("snapshoty %s: archivováno %s, smazáno %s", name, r.get("archived"), r.get("removed"))
    for name, cfg in POLICY["forecast_trim"].items():
        r = _locked(name, lambda: trim_forecasts(name, cfg["keep_days"], cfg["max_lead_days"], now))
        if r.get("rows_removed") or r.get("errors") or r.get("skipped"):
            actions.append({"dataset": name, "action": f"předstih ≤ {cfg['max_lead_days']} d po {cfg['keep_days']} dnech",
                            **r})
    after = record_sizes(now)
    errors = sum(len(a.get("errors") or []) for a in actions)
    skipped = sum(1 for a in actions if a.get("skipped"))
    summary = {
        "t": now.isoformat(), "duration_s": round(time.monotonic() - t0, 1),
        "bytes_before": sum(b for b, _ in before.values()), "bytes_after": sum(b for b, _ in after.values()),
        "files_before": sum(n for _, n in before.values()), "files_after": sum(n for _, n in after.values()),
        "errors": errors, "skipped": skipped, "actions": actions,
    }
    _atomic_json(history_root() / ".maintenance.json", summary)
    log.info("údržba: soubory %d → %d, %.1f → %.1f MB, %d chyb, %d přeskočeno",
             summary["files_before"], summary["files_after"], summary["bytes_before"] / 2**20,
             summary["bytes_after"] / 2**20, errors, skipped)
    return {"errors": errors, "pending": skipped}
