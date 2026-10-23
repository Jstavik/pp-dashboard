"""Společné nastavení testů.

- Síť je zakázaná (requests.Session.send vyhodí chybu) — každý test
  zdroje si odpověď mockuje sám. Živá API se v testech NEVOLAJÍ.
- PPDASH_DATA_DIR: když existuje data/history v repu, testy stránek běží
  nad ní (realistická data); jinak nad prázdným adresářem (stránky musí
  vrátit 200 i bez dat). Nastavuje se PŘED importem aplikace, protože
  datové moduly si cesty počítají při importu.
- ~/.config/ppdash/env se nečte (PPDASH_ENV_FILE=/nonexistent), tokeny
  nejsou nastavené.
- PPDASH_TEST_EMPTY=1 vynutí prázdný datový adresář i když data v repu jsou.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["PPDASH_ENV_FILE"] = "/nonexistent"
for _k in ("ENTSOE_TOKEN", "GIE_API_KEY", "DELTAGREEN_API_KEY"):
    os.environ.pop(_k, None)
if (ROOT / "data" / "history").is_dir() and not os.environ.get("PPDASH_TEST_EMPTY"):
    os.environ["PPDASH_DATA_DIR"] = str(ROOT / "data")
    HAS_REPO_DATA = True
else:
    _tmp = tempfile.mkdtemp(prefix="ppdash-test-")
    os.makedirs(os.path.join(_tmp, "history"))
    os.environ["PPDASH_DATA_DIR"] = _tmp
    HAS_REPO_DATA = False
os.environ["PPDASH_CACHE_DIR"] = tempfile.mkdtemp(prefix="ppdash-cache-")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import requests

    def _blocked(self, request, **kwargs):
        raise requests.ConnectionError(f"síť je v testech zakázaná: {request.url}")
    monkeypatch.setattr(requests.Session, "send", _blocked)
    yield


@pytest.fixture
def no_cache(monkeypatch):
    monkeypatch.setenv("PPDASH_CACHE_DISABLE", "1")


class FakeResponse:
    def __init__(self, status=200, json_data=None, text=None, content=None, headers=None):
        self.status_code = status
        self._json = json_data
        self.text = text if text is not None else ("" if json_data is None else str(json_data))
        self.content = content if content is not None else self.text.encode()
        self.headers = headers or {}

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")
