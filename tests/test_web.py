"""Smoke testy webu — každá stránka, záložka (fragment) a graf vrací 200.

Síť je zakázaná a tokeny nenastavené: grafy závislé na živých API (ENTSO-E,
ČEPS) musí vrátit JSON s chybou/prázdnem, ne HTTP 500.
Data: data/history v repu, pokud existuje (viz conftest)."""
import re

import pytest
from fastapi.testclient import TestClient

from web.app import app
from web import registry

client = TestClient(app)

TAB_KEYS = list(registry.TABS.keys())
CHART_KEYS = list(registry.CHARTS.keys())


def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"


@pytest.mark.parametrize("page", list(registry.PAGES))
def test_page(page):
    r = client.get(f"/{page}")
    assert r.status_code == 200
    for tab in registry.PAGES[page].tabs:
        assert f'data-tab="{tab}"' in r.text


def test_index_redirect_and_404():
    assert client.get("/", follow_redirects=False).status_code == 307
    assert client.get("/neexistuje").status_code == 404
    assert client.get("/frag/plyn/neexistuje").status_code == 404


@pytest.mark.parametrize("page,tab", TAB_KEYS, ids=[f"{p}/{t}" for p, t in TAB_KEYS])
def test_fragment(page, tab):
    r = client.get(f"/frag/{page}/{tab}")
    assert r.status_code == 200
    assert "Záložku se nepodařilo načíst" not in r.text, re.sub(r"\s+", " ", r.text)[:500]


@pytest.mark.parametrize("page,tab,cid", CHART_KEYS, ids=[f"{p}/{t}/{c}" for p, t, c in CHART_KEYS])
def test_chart(page, tab, cid):
    r = client.get(f"/api/chart/{page}/{tab}/{cid}")
    assert r.status_code == 200
    body = r.json()
    assert "figure" in body or body.get("empty") or "error" in body
    if "figure" in body:
        assert "data" in body["figure"] and "layout" in body["figure"]


def test_electricity_header():
    r = client.get("/frag-header/elektrina")
    assert r.status_code == 200 and "kpi" in r.text


def test_static_and_plotly():
    assert client.get("/static/app.js").status_code == 200
    r = client.get("/vendor/plotly.min.js")
    assert r.status_code == 200 and len(r.content) > 1_000_000


def test_filters_change_fragment():
    """Parametry se propisují do ovládacích prvků (kaskáda) i data-query."""
    r = client.get("/frag/plyn/toky?countries=Germany&chart_type=Plocha&range=30")
    assert r.status_code == 200
    if "Historická data ENTSO-G nejsou dostupná" not in r.text:
        assert 'value="Plocha" checked' in r.text
        assert "range=" not in re.search(r'data-query="([^"]*)"', r.text).group(1)


def test_csv_exports():
    for url in ("/api/csv/imbalance", "/api/csv/generation", "/api/csv/outages?level=PU",
                "/api/csv/reserves?key=afrr_d_amt"):
        r = client.get(url)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")


def test_cache_clear():
    assert client.post("/api/cache/clear").json() == {"cleared": True}
