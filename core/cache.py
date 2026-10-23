"""TTL cache v paměti (+ volitelně na disku) — náhrada za st.cache_data.

    @ttl_cache(ttl=60)                 # jen paměť
    @ttl_cache(ttl=900, disk=True)     # paměť + disk (přežije restart)

Chování odpovídá tomu, na co je zbytek kódu zvyklý ze Streamlitu:
- klíč = funkce + hodnoty argumentů (Timestamp/date/list se normalizují),
- výjimky se NEcachují (propadnou volajícímu, příští volání zkusí znovu),
- volající dostane MĚLKOU KOPII (DataFrame.copy(deep=False), u dict/list/
  tuple rekurzivně) — přidání/přepsání sloupce ve výsledku tak nerozbije
  hodnotu v cache. st.cache_data vracel dokonce hlubokou kopii (pickle);
  hluboká kopie velkých DataFrame by na 2 GB RAM byla drahá, a web
  v kombinaci s pandas copy-on-write (web/app.py) je chráněný i proti
  zápisu do existujících sloupců.
- single-flight: souběžné požadavky na stejný klíč počítají hodnotu jen
  jednou (ostatní čekají na zámek) — důležité pro pomalá API (ENTSO-E).

Disková vrstva (disk=True) je určená pro výsledky síťových volání: po
restartu služby se nemusí hned znovu volat všechna API. Pro data čtená
z parquet/CSV historie nemá smysl (už na disku jsou).

PPDASH_CACHE_DISABLE=1 vypne cache úplně (testy).
"""
import datetime as _dt
import functools
import hashlib
import logging
import os
import pickle
import threading
import time
from collections import OrderedDict
from pathlib import Path

log = logging.getLogger(__name__)

_REGISTRY: list = []


def _freeze(obj):
    """Hashovatelná, deterministická reprezentace argumentu pro klíč."""
    try:
        import pandas as pd
    except ImportError:  # pragma: no cover
        pd = None
    if pd is not None:
        if isinstance(obj, pd.Timestamp):
            return ("ts", obj.isoformat())
        if isinstance(obj, (pd.DataFrame, pd.Series)):
            # DataFrame jako argument — klíčem je obsah (jako u Streamlitu)
            return ("df", hashlib.sha1(pd.util.hash_pandas_object(obj, index=True).values.tobytes()).hexdigest())
    if isinstance(obj, (_dt.datetime, _dt.date)):
        return (type(obj).__name__, obj.isoformat())
    if isinstance(obj, (list, tuple)):
        return (type(obj).__name__, tuple(_freeze(x) for x in obj))
    if isinstance(obj, (set, frozenset)):
        return ("set", tuple(sorted(repr(_freeze(x)) for x in obj)))
    if isinstance(obj, dict):
        return ("dict", tuple(sorted((repr(k), _freeze(v)) for k, v in obj.items())))
    return obj


def make_key(args, kwargs) -> str:
    raw = repr((_freeze(args), _freeze(kwargs)))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def shallow_copy(value):
    try:
        import pandas as pd
    except ImportError:  # pragma: no cover
        return value
    if isinstance(value, (pd.DataFrame, pd.Series)):
        return value.copy(deep=False)
    if isinstance(value, dict):
        return {k: shallow_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [shallow_copy(v) for v in value]
    if isinstance(value, tuple):
        return tuple(shallow_copy(v) for v in value)
    return value


def _disabled() -> bool:
    return os.environ.get("PPDASH_CACHE_DISABLE", "") in ("1", "true", "yes")


class _TTLCache:
    def __init__(self, fn, ttl: float, disk: bool, maxsize: int, name: str):
        self.fn = fn
        self.ttl = float(ttl)
        self.disk = disk
        self.maxsize = maxsize
        self.name = name
        self._data: "OrderedDict[str, tuple[float, object]]" = OrderedDict()
        self._lock = threading.Lock()
        self._key_locks: dict = {}
        self.hits = 0
        self.misses = 0
        functools.update_wrapper(self, fn)

    # ── disk ─────────────────────────────────────────────────────────────
    def _disk_path(self, key: str) -> Path:
        from core.settings import cache_root
        return cache_root() / self.name / f"{key}.pkl"

    def _disk_get(self, key: str):
        if not self.disk:
            return None
        path = self._disk_path(key)
        try:
            with open(path, "rb") as f:
                expires, value = pickle.load(f)
        except FileNotFoundError:
            return None
        except Exception as e:  # poškozený/nekompatibilní soubor
            log.debug("cache %s: nelze číst %s (%s)", self.name, path, e)
            return None
        if expires < time.time():
            return None
        return expires, value

    def _disk_put(self, key: str, expires: float, value) -> None:
        if not self.disk:
            return
        path = self._disk_path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".tmp{os.getpid()}.{threading.get_ident()}")
            with open(tmp, "wb") as f:
                pickle.dump((expires, value), f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, path)
        except Exception as e:
            log.warning("cache %s: nelze zapsat %s (%s)", self.name, path, e)

    # ── memory ───────────────────────────────────────────────────────────
    def _mem_get(self, key: str):
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            if item[0] < time.time():
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return item

    def _mem_put(self, key: str, expires: float, value) -> None:
        with self._lock:
            self._data[key] = (expires, value)
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def _key_lock(self, key: str) -> threading.Lock:
        with self._lock:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = self._key_locks[key] = threading.Lock()
            return lock

    def __call__(self, *args, **kwargs):
        if _disabled():
            return self.fn(*args, **kwargs)
        key = make_key(args, kwargs)
        item = self._mem_get(key)
        if item is not None:
            self.hits += 1
            return shallow_copy(item[1])
        with self._key_lock(key):
            item = self._mem_get(key)  # mezitím spočítal jiný thread
            if item is None:
                item = self._disk_get(key)
                if item is not None:
                    self._mem_put(key, *item)
            if item is not None:
                self.hits += 1
                return shallow_copy(item[1])
            self.misses += 1
            value = self.fn(*args, **kwargs)
            expires = time.time() + self.ttl
            self._mem_put(key, expires, value)
            self._disk_put(key, expires, value)
            return shallow_copy(value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._key_locks.clear()
        if self.disk:
            from core.settings import cache_root
            d = cache_root() / self.name
            if d.is_dir():
                for p in d.glob("*.pkl"):
                    try:
                        p.unlink()
                    except OSError:
                        pass

    def cache_info(self) -> dict:
        with self._lock:
            return {"name": self.name, "ttl": self.ttl, "entries": len(self._data),
                    "hits": self.hits, "misses": self.misses, "disk": self.disk}


def ttl_cache(ttl: float, *, disk: bool = False, maxsize: int = 16, name: str = None):
    """Dekorátor — viz docstring modulu."""
    def deco(fn):
        cache = _TTLCache(fn, ttl=ttl, disk=disk, maxsize=maxsize,
                          name=name or f"{fn.__module__}.{fn.__qualname__}")
        _REGISTRY.append(cache)
        return cache
    return deco


def clear_all() -> None:
    for c in _REGISTRY:
        c.clear()


def cache_stats() -> list:
    return [c.cache_info() for c in _REGISTRY]
