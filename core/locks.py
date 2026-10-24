"""Zámky úloh (fcntl, history/.locks/<úloha>.lock).

Skupinové zámky v scripts/update_all.py (.update.lock, .entsoe.lock, …)
brání dvěma během TÉHOŽ timeru. Zámek úlohy navíc brání tomu, aby do
stejného datasetu psaly dva procesy: plánovaný běh, ruční „Aktualizovat
teď“ z webu a týdenní údržba (core/maintenance.py) — každý drží zámek
úlohy, jen dokud ji provádí.
"""
import fcntl
import json
import os
import time
from contextlib import contextmanager

from core.settings import history_root


def lock_dir():
    d = history_root() / ".locks"
    d.mkdir(parents=True, exist_ok=True)
    return d


class Busy(Exception):
    """Úlohu právě provádí jiný proces."""


@contextmanager
def task_lock(name: str, wait_s: float = 0, on_wait=None):
    """Exkluzivní zámek úlohy. wait_s=0 → hned Busy, jinak čeká (po 2 s)
    nejvýš wait_s; on_wait() se zavolá jednou, když se začne čekat."""
    path = lock_dir() / f"{name}.lock"
    fh = open(path, "a+")
    deadline = time.monotonic() + wait_s
    waited = False
    try:
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise Busy(name)
                if not waited and on_wait:
                    on_wait()
                waited = True
                time.sleep(min(2.0, max(0.05, deadline - time.monotonic())))
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps({"pid": os.getpid(), "since": time.time()}))
        fh.flush()
        yield
    finally:
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
        finally:
            fh.close()


def is_locked(name: str) -> bool:
    path = lock_dir() / f"{name}.lock"
    if not path.exists():
        return False
    with open(path, "a+") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(fh, fcntl.LOCK_UN)
    return False
