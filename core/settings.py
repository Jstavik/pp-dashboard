"""Konfigurace z prostředí (env) — žádné tajné hodnoty v repu.

Pořadí zdrojů pro každou proměnnou:
1. skutečné prostředí procesu (systemd EnvironmentFile, export v shellu),
2. soubor ~/.config/ppdash/env (KEY=VALUE řádky, # komentáře) — načte se
   jen pro klíče, které v prostředí ještě nejsou, takže ruční spuštění
   skriptu z shellu se chová stejně jako služba,
3. (jen kvůli zpětné kompatibilitě staré app.py) st.secrets, pokud je
   Streamlit nainstalovaný a běží — viz secret().

Cesty k datům:
- PPDASH_DATA_DIR (default /var/lib/ppdash) — kořen dat mimo git:
    <DATA_DIR>/history/   historie (stejná struktura jako dřív data/history/)
    <DATA_DIR>/cache/     disková TTL cache (core/cache.py)
- Vývojový fallback: když PPDASH_DATA_DIR není nastavené a /var/lib/ppdash
  neexistuje, použije se data/history/ v repu (a .cache/ v repu) — stará
  app.py i GitHub Actions workflow tak dál fungují beze změny.
"""
import logging
import os
import sys
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = Path("/var/lib/ppdash")
ENV_FILE = Path(os.path.expanduser("~/.config/ppdash/env"))


def _parse_env_file(path: Path) -> dict:
    values = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        values[key] = val
    return values


@lru_cache(maxsize=1)
def _env_file_values() -> dict:
    path = Path(os.environ.get("PPDASH_ENV_FILE", ENV_FILE))
    return _parse_env_file(path)


def env(key: str, default=None):
    """Hodnota z prostředí, jinak z ~/.config/ppdash/env, jinak default."""
    val = os.environ.get(key)
    if val not in (None, ""):
        return val
    val = _env_file_values().get(key)
    if val not in (None, ""):
        return val
    return default


def secret(key: str, default=None):
    """Jako env(), navíc fallback na st.secrets (jen stará Streamlit app)."""
    val = env(key)
    if val is not None:
        return val
    # st.secrets jen pokud Streamlit UŽ běží (streamlit run app.py) — web
    # ani skripty ho nikdy neimportují.
    st = sys.modules.get("streamlit")
    if st is None:
        return default
    try:  # pragma: no cover — závisí na běžící Streamlit app
        return st.secrets.get(key, default)
    except Exception:
        return default


@lru_cache(maxsize=1)
def data_dir() -> Path:
    configured = env("PPDASH_DATA_DIR")
    if configured:
        return Path(configured)
    if DEFAULT_DATA_DIR.exists():
        return DEFAULT_DATA_DIR
    return REPO_ROOT / "data"


@lru_cache(maxsize=1)
def history_root() -> Path:
    """Kořen historie. Pro DATA_DIR=<repo>/data je to <repo>/data/history,
    tj. přesně původní umístění (zpětná kompatibilita)."""
    return data_dir() / "history"


@lru_cache(maxsize=1)
def cache_root() -> Path:
    configured = env("PPDASH_CACHE_DIR")
    if configured:
        return Path(configured)
    if data_dir() == REPO_ROOT / "data":
        return REPO_ROOT / ".cache"
    return data_dir() / "cache"


def history_path(*parts: str) -> str:
    """Absolutní cesta uvnitř historie, např. history_path("entsog_flows")."""
    return str(history_root().joinpath(*parts))


def reset_caches() -> None:
    """Pro testy — znovu načíst env/cesty po změně os.environ."""
    for fn in (_env_file_values, data_dir, history_root, cache_root):
        fn.cache_clear()


# ── tajné klíče / tokeny (jen z env) ────────────────────────────────────
def entsoe_token():
    return secret("ENTSOE_TOKEN")


def gie_api_key():
    """GIE AGSI+ / ALSI klíč (sdílený pro obě API)."""
    return secret("GIE_API_KEY")


def deltagreen_api_key():
    return secret("DELTAGREEN_API_KEY")
