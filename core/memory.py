"""Vracení uvolněné paměti operačnímu systému (glibc malloc_trim).

Python/pandas po velkém dočasném výpočtu (čtení historie, groupby) paměť
uvolní jen do alokátoru — RSS procesu zůstává vysoko. Na 2 GB RAM se
vyplatí po pomalých (= velkých) načteních zavolat malloc_trim(0). Na
systémech bez glibc je to no-op."""
import ctypes
import ctypes.util

_libc = None


def trim() -> None:
    global _libc
    try:
        if _libc is None:
            _libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6")
        _libc.malloc_trim(0)
    except Exception:
        _libc = False if _libc is None else _libc
