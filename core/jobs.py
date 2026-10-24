"""Ruční „Aktualizovat teď“ z webu — spuštění úlohy na pozadí a její průběh.

Spouští se `scripts/update_all.py --manual --only <cíl> --job <cíl>`:
- přednostně jako jednorázová uživatelská služba
  `systemctl --user start ppdash-run@<cíl>.service` (deploy/systemd-user,
  vlastní cgroup a limity paměti, log v journald, systemd sám nespustí
  druhou instanci téhož cíle),
- jinak (bez uživatelského systemd, vývoj) jako odpojený podproces
  s logem v cache/jobs/<cíl>.log.
Volba: PPDASH_REFRESH_MODE=auto|systemd|subprocess|off.

Souběh hlídají zámky úloh (core/locks.py) — ruční běh počká, až úlohu
dokončí plánovaný běh, a když ji ten mezitím úspěšně dokončil, nic znovu
nestahuje. Ochrana proti spamu: cíl, který běží, se nespustí znovu;
po dokončení cooldown (COOLDOWN_S); nejvýš MAX_PARALLEL ručních běhů.
Průběh: history/.jobs/<cíl>.json (stav, aktuální úloha, výsledky).
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from core.settings import cache_root, env, history_root

ROOT = Path(__file__).resolve().parent.parent

# cíl → (popis, úlohy/skupiny pro --only, cooldown s)
TARGETS = {
    "gas":           ("Plyn (ENTSO-G, GIE, LNG, GASSCO)", "gas", 600),
    "power":         ("Elektřina ČR/EU (výroba, odstávky, DAP, hydro)", "power", 900),
    "entsoe":        ("ENTSO-E (ceny zón, spotřeba, prognóza OZE, toky)", "entsoe", 900),
    "entsoe_prices": ("ENTSO-E day-ahead ceny", "entsoe_prices", 600),
    "nordic":        ("Nordic (NVE, Fingrid, Energinet)", "nordic", 900),
    "weather":       ("Počasí (Open-Meteo)", "weather", 1800),
    "energy_charts": ("Energy-Charts", "energy_charts", 600),
    "cot":           ("ICE CoT", "cot", 1800),
    "maintenance":   ("Údržba historie", "maintenance", 3600),
}
# jednotlivé úlohy (tlačítko u datasetu na /stav)
TASK_COOLDOWN_S = 600
LIVE = {"ceps", "gassco_realtime", "gassco_umm"}  # běží každou minutu, tlačítko nedává smysl
MAX_PARALLEL = 2
# kratší rozpočty backfillu, ať ruční běh netrvá hodinu (pokračuje plánovaný běh)
MANUAL_ENV = {"PPDASH_GENERATION_BUDGET_S": "300", "PPDASH_ENTSOE_BUDGET_S": "600",
              "PPDASH_WEATHER_BUDGET_CALLS": "300", "PPDASH_ENERGINET_BUDGET_S": "300"}


def target_info(target: str):
    """(popis, --only, cooldown) nebo None pro neznámý cíl."""
    if target in TARGETS:
        return TARGETS[target]
    from scripts.update_all import TASK_ORDER
    if target in TASK_ORDER and target not in LIVE:
        from web.status import TASK_LABELS
        return (TASK_LABELS.get(target, target), target, TASK_COOLDOWN_S)
    return None


def jobs_dir(create: bool = False) -> Path:
    d = history_root() / ".jobs"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


class JobFile:
    """Stav jednoho ručního běhu (zapisuje update_all.py --job)."""

    def __init__(self, job_id: str):
        self.path = jobs_dir() / f"{job_id}.json"

    def read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def update(self, **kw) -> dict:
        data = self.read()
        data.update(kw)
        data["updated"] = time.time()
        if kw.get("state") == "done":
            data["finished"] = time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(tmp, self.path)
        return data


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def status(target: str) -> dict:
    """{state: idle|queued|starting|waiting|running|done|stale, …} + cooldown_left."""
    data = JobFile(target).read()
    if not data:
        return {"state": "idle", "target": target}
    st = data.get("state")
    now = time.time()
    if st in ("starting", "waiting", "running"):
        if data.get("pid") and not _alive(data["pid"]):
            st = "stale"  # proces skončil bez zápisu výsledku (pád, OOM kill)
    elif st == "queued" and now - data.get("requested", now) > 120:
        st = "stale"      # služba/podproces se vůbec nespustil
    data["state"] = st
    info = target_info(target)
    cd = info[2] if info else TASK_COOLDOWN_S
    if st == "done" and data.get("finished"):
        data["cooldown_left"] = max(0, int(data["finished"] + cd - now))
    else:
        data["cooldown_left"] = 0
    data["target"] = target
    return data


def active() -> list:
    out = []
    d = jobs_dir()
    for p in (d.glob("*.json") if d.is_dir() else []):
        s = status(p.stem)
        if s["state"] in ("queued", "starting", "waiting", "running"):
            out.append(s)
    return out


def _mode() -> str:
    m = env("PPDASH_REFRESH_MODE", "auto")
    if m != "auto":
        return m
    unit = Path(os.path.expanduser("~/.config/systemd/user/ppdash-run@.service"))
    return "systemd" if unit.exists() and shutil.which("systemctl") else "subprocess"


def start(target: str) -> dict:
    """Spustí cíl na pozadí. Vrací {ok, message, state}."""
    info = target_info(target)
    if info is None:
        return {"ok": False, "message": "neznámá úloha"}
    mode = _mode()
    if mode == "off":
        return {"ok": False, "message": "ruční aktualizace je vypnutá (PPDASH_REFRESH_MODE=off)"}
    cur = status(target)
    if cur["state"] in ("queued", "starting", "waiting", "running"):
        return {**cur, "ok": False, "message": "už běží"}
    if cur.get("cooldown_left"):
        m, s = divmod(cur["cooldown_left"], 60)
        return {**cur, "ok": False, "message": f"aktualizováno před chvílí — znovu za {m} min {s} s"}
    if len(active()) >= MAX_PARALLEL:
        return {"ok": False, "message": f"běží už {MAX_PARALLEL} ruční aktualizace — zkuste za chvíli"}
    job = JobFile(target)
    job.path.unlink(missing_ok=True)
    job.update(state="queued", requested=time.time(), label=info[0], mode=mode)
    try:
        if mode == "systemd":
            r = subprocess.run(["systemctl", "--user", "start", "--no-block", f"ppdash-run@{target}.service"],
                               capture_output=True, text=True, timeout=15)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip() or f"systemctl exit {r.returncode}")
        else:
            log_dir = cache_root() / "jobs"
            log_dir.mkdir(parents=True, exist_ok=True)
            with open(log_dir / f"{target}.log", "w") as lf:
                subprocess.Popen(
                    [sys.executable, str(ROOT / "scripts" / "update_all.py"), "--manual", "--only", info[1],
                     "--job", target],
                    cwd=str(ROOT), stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    start_new_session=True, env={**os.environ, **MANUAL_ENV})
    except Exception as e:
        job.update(state="done", ok=False, message=f"nepodařilo se spustit: {e}")
        return {"ok": False, "message": f"nepodařilo se spustit: {e}"}
    return {"ok": True, "message": "spuštěno", "state": "queued"}
