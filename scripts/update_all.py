#!/usr/bin/env python3
"""Jeden vstupní bod pro všechny aktualizace historie (systemd timer).

Nahrazuje scripts/update_gas_history.py (2× denně) a scripts/update_ice_cot.py
(so/út) — logika úloh žije v core/updaters.py a u dat (data/*.py).

Použití:
    python scripts/update_all.py                  # 2× denně: vše kromě live a COT
    python scripts/update_all.py --only live -q   # každou minutu: ČEPS + GASSCO realtime/UMM
    python scripts/update_all.py --only cot       # jen ICE CoT (so/út)
    python scripts/update_all.py --only gas,dap_europe
    python scripts/update_all.py --skip outages --list

--only/--skip berou názvy úloh i skupin (čárkou oddělené, opakovatelné):
    gas    = entsog, entsog_allocation, entsog_operational, gie, lng, gassco
    power  = hydro, dap_europe, generation, outages
    live   = ceps, gassco_realtime, gassco_umm (minutový timer — ve výchozím běhu NE)
    entsoe = entsoe_prices, entsoe_load, entsoe_ws, entsoe_flows (vlastní timer, 3× denně — ve výchozím běhu NE)
    cot    = cot (ICE MiFID II, týdenní — ve výchozím běhu se NEspouští)
    energy_charts
    all    = všechno včetně live a cot

Idempotentní: všechny zápisy jsou upsert s dedupem / write-once snapshoty,
opakované spuštění nic nezdvojí. Souběžný běh brání zámek
<PPDASH_DATA_DIR>/history/.update.lock; běh jen live úloh má vlastní zámek
.live.lock (minutový timer tak neblokuje hodiny trvající denní běh a naopak
— zapisují do jiných datasetů).

Exit kódy:
    0  všechny úlohy OK (případně s varováním — částečná chyba, např. 1 země)
    1  aspoň jedna úloha selhala
    2  špatné argumenty
    3  jiná aktualizace právě běží (zámek)
"""
import argparse
import fcntl
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

log = logging.getLogger("ppdash.update")

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_LOCKED = 0, 1, 2, 3


def _tasks() -> dict:
    """name -> (popis, callable). Importy líně, ať --help/--list nic netahá."""
    from config import COUNTRIES, GENERATION_COUNTRIES
    from core import updaters as u

    def entsog_operational():
        from data.entsog_operational import update_eu_operational
        update_eu_operational()

    def lng():
        from data.lng import update_lng
        update_lng()

    def gassco():
        from data.gassco import update_gassco
        update_gassco()

    def gassco_umm():
        from data.gassco import update_gassco_umm
        return update_gassco_umm()

    def gassco_realtime():
        from data.gassco import update_gassco_realtime
        return update_gassco_realtime()

    def ceps():
        from data.ceps import update_ceps_live
        return update_ceps_live()

    def per_country(fn, label, countries=COUNTRIES):
        def run():
            errors, failed = 0, []
            for c in countries:
                try:
                    res = fn(c) or {}
                    errors += int(res.get("errors", 0) or 0)
                except Exception as e:
                    log.error(f"{label} {c}: {type(e).__name__}: {e}")
                    failed.append(c)
            if failed and len(failed) == len(countries):
                raise u.UpdateError(f"{label}: selhaly všechny země")
            return {"errors": errors + len(failed)}
        return run

    def energy_charts():
        from data.energy_charts import update_energy_charts
        res = update_energy_charts(countries=["cz"], log=log.info)
        if res["failed"] and not res["done"]:
            raise u.UpdateError(f"energy_charts: všechna komba selhala ({res['failed'][0]})")
        return {"errors": len(res["failed"])}

    def entsoe(name):
        def run():
            from data import entsoe_history as eh
            return getattr(eh, name)()
        return run

    return {
        "entsog":             ("ENTSO-G fyzické toky (všechny země)", u.update_entsog),
        "entsog_allocation":  ("ENTSO-G Allocation (všechny země)", u.update_entsog_allocation),
        "gie":                ("GIE AGSI+ zásobníky — všechny země", u.update_gie_all),
        "entsog_operational": ("ENTSOG operational (nominace/kapacity/kvalita)", entsog_operational),
        "lng":                ("LNG terminály (GIE ALSI)", lng),
        "gassco":             ("GASSCO nominace", gassco),
        "gassco_umm":         ("GASSCO UMM (odstávky polí)", gassco_umm),
        "gassco_realtime":    ("GASSCO realtime nominace (realTimeAtom)", gassco_realtime),
        "ceps":               ("ČEPS minutová data (odchylka, SVR, zatížení, výroba, toky, cena)", ceps),
        "hydro":              ("Vodní zásobníky (ENTSO-E 16.1.D)", u.update_hydro),
        "dap_europe":         ("DAP Europe (DAP mapa)", u.update_dap_europe),
        "generation":         ("Výroba podle zdroje (ENTSO-E, hlavní země EU + Nordic)",
                               per_country(u.update_generation, "Generation", GENERATION_COUNTRIES)),
        "outages":            ("Odstávky (ENTSO-E, všechny země)", per_country(u.update_outages, "Outages")),
        "energy_charts":      ("Energy-Charts (public_power, forecast, cbet, cbpf, installed, frequency)", energy_charts),
        "cot":                ("ICE MiFID II Commitments of Traders", u.update_cot),
        "entsoe_prices":      ("ENTSO-E day-ahead ceny všech bidding zón", entsoe("update_prices")),
        "entsoe_load":        ("ENTSO-E spotřeba skutečná + prognóza D-1", entsoe("update_load")),
        "entsoe_ws":          ("ENTSO-E prognóza větru a slunce (day-ahead)", entsoe("update_ws_forecast")),
        "entsoe_flows":       ("ENTSO-E fyzické toky + plánované výměny (CZ, sousedé, Nordic)", entsoe("update_flows")),
    }


TASK_ORDER = [
    "ceps", "gassco_realtime", "gassco_umm",
    "entsog", "entsog_allocation", "gie", "hydro", "entsog_operational", "lng",
    "gassco", "dap_europe", "generation", "outages", "energy_charts", "cot",
    "entsoe_prices", "entsoe_load", "entsoe_ws", "entsoe_flows",
]
LIVE_TASKS = ["ceps", "gassco_realtime", "gassco_umm"]
ENTSOE_TASKS = ["entsoe_prices", "entsoe_load", "entsoe_ws", "entsoe_flows"]
GROUPS = {
    "gas":   ["entsog", "entsog_allocation", "entsog_operational", "gie", "lng", "gassco"],
    "power": ["hydro", "dap_europe", "generation", "outages"],
    "live":  list(LIVE_TASKS),
    "entsoe": list(ENTSOE_TASKS),
    "all":   list(TASK_ORDER),
}
DEFAULT_EXCLUDE = {"cot", *LIVE_TASKS, *ENTSOE_TASKS}


def resolve(only: list, skip: list) -> list:
    def expand(items):
        names = []
        for item in items:
            for part in item.split(","):
                part = part.strip()
                if not part:
                    continue
                if part in GROUPS:
                    names.extend(GROUPS[part])
                elif part in TASK_ORDER:
                    names.append(part)
                else:
                    raise ValueError(f"neznámá úloha/skupina: {part!r} "
                                     f"(úlohy: {', '.join(TASK_ORDER)}; skupiny: {', '.join(GROUPS)})")
        return names

    selected = set(expand(only)) if only else set(TASK_ORDER) - DEFAULT_EXCLUDE
    selected -= set(expand(skip or []))
    return [t for t in TASK_ORDER if t in selected]


def setup_logging(verbose: bool, log_file: str = None, quiet: bool = False) -> None:
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    # Pod systemd jde stdout do journald, který si čas přidá sám.
    fmt = "%(levelname)s %(name)s: %(message)s" if os.environ.get("INVOCATION_ID") \
        else "%(asctime)s %(levelname)s %(name)s: %(message)s"
    level = logging.DEBUG if verbose else logging.WARNING if quiet else logging.INFO
    logging.basicConfig(level=level, format=fmt, handlers=handlers, force=True)
    for noisy in ("urllib3", "zeep", "entsoe", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class _Lock:
    def __init__(self, path: Path):
        self.path = path
        self.fh = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "w")
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fh.close()
            self.fh = None
            return False
        self.fh.write(f"{os.getpid()}\n")
        self.fh.flush()
        return True

    def release(self) -> None:
        if self.fh:
            fcntl.flock(self.fh, fcntl.LOCK_UN)
            self.fh.close()
            self.fh = None


def run_tasks(names: list) -> list:
    """Spustí úlohy postupně (chyba jedné nezastaví další). Vrací
    [(name, status, sekundy, detail)], status ∈ OK | WARN | FAIL."""
    tasks = _tasks()
    results = []
    for name in names:
        label, fn = tasks[name]
        log.info(f"=== {name}: {label} ===")
        t0 = time.monotonic()
        try:
            res = fn() or {}
            n_err = int(res.get("errors", 0) or 0) if isinstance(res, dict) else 0
            status = "WARN" if n_err else "OK"
            detail = f"{n_err} dílčích chyb" if n_err else ""
        except Exception as e:
            from core.updaters import UpdateError
            if isinstance(e, UpdateError):  # očekávané selhání — bez tracebacku
                log.error(f"{name}: SELHALO — {e}")
            else:
                log.exception(f"{name}: SELHALO — {type(e).__name__}: {e}")
            status, detail = "FAIL", f"{type(e).__name__}: {e}"
        results.append((name, status, time.monotonic() - t0, detail))
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Aktualizace historie PP Dashboard",
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog=__doc__.split("Exit kódy:")[0].split("Použití:")[1])
    parser.add_argument("--only", action="append", default=[], metavar="ÚLOHY",
                        help="spustit jen tyto úlohy/skupiny (čárkou oddělené)")
    parser.add_argument("--skip", action="append", default=[], metavar="ÚLOHY",
                        help="vynechat tyto úlohy/skupiny")
    parser.add_argument("--list", action="store_true", help="jen vypsat vybrané úlohy a skončit")
    parser.add_argument("--no-lock", action="store_true", help="nebrat zámek (jen pro ladění)")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="jen varování/chyby + jednořádkový souhrn (minutový timer)")
    parser.add_argument("--log-file", help="logovat navíc do souboru")
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return EXIT_USAGE if e.code else EXIT_OK

    setup_logging(args.verbose, args.log_file, args.quiet)
    try:
        names = resolve(args.only, args.skip)
    except ValueError as e:
        log.error(str(e))
        return EXIT_USAGE
    if not names:
        log.error("žádná úloha k spuštění")
        return EXIT_USAGE
    if args.list:
        for n in names:
            print(n)
        return EXIT_OK

    from core.settings import history_root
    log.info(f"Data: {history_root()} | úlohy: {', '.join(names)}")

    # vlastní zámky: minutový live běh a ENTSO-E backfill (hodiny) nesmí
    # blokovat denní běh ani sebe navzájem — zapisují do jiných datasetů
    lock_name = (".live.lock" if set(names) <= set(LIVE_TASKS)
                 else ".entsoe.lock" if set(names) <= set(ENTSOE_TASKS) else ".update.lock")
    lock = _Lock(history_root() / lock_name)
    if not args.no_lock and not lock.acquire():
        log.error(f"jiná aktualizace právě běží (zámek {lock.path})")
        return EXIT_LOCKED
    try:
        results = run_tasks(names)
    finally:
        lock.release()

    failed = [r for r in results if r[1] == "FAIL"]
    if args.quiet:
        # jeden řádek i v tichém režimu (journald: je vidět, že timer běží)
        print(" ".join(f"{n}={st}({secs:.1f}s)" for n, st, secs, _ in results), flush=True)
    else:
        log.info("=== SOUHRN ===")
        for name, status, secs, detail in results:
            log.info(f"  {status:4} {name:20} {secs:7.1f}s {detail}")
    return EXIT_FAILED if failed else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
