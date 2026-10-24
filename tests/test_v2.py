"""Nový vzhled (prototyp): přepínač nový/starý, stránky a grafy, sezónní pásmo."""
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from web.app import app


@pytest.fixture
def cl():
    return TestClient(app)


def test_toggle_cookie_and_old_stays(cl):
    r = cl.get("/plyn")
    assert r.status_code == 200 and 'data-page="plyn"' in r.text and "Vyzkoušet nový vzhled" in r.text
    r = cl.get("/plyn?vzhled=novy")
    assert 'data-page="v2-plyn"' in r.text and r.cookies.get("ppdash_vzhled") == "novy"
    assert 'data-page="v2-plyn"' in cl.get("/plyn").text          # zapamatováno
    assert 'data-page="plyn"' in cl.get("/plyn?tab=lng").text      # odkaz na starou záložku
    r = cl.get("/plyn?vzhled=stary")
    assert 'data-page="plyn"' in r.text and r.cookies.get("ppdash_vzhled") == "stary"
    assert 'data-page="trh-eu"' in cl.get("/trh-eu?vzhled=novy").text  # bez prototypu beze změny


@pytest.mark.parametrize("page,charts", [
    ("v2-plyn", ["eu", "multi", "net"]),
    ("v2-elektrina", ["base", "recent", "spread", "profile", "gen"]),
    ("v2-nordic", ["hydro", "hydro_multi", "spreads"]),
])
def test_v2_pages_render(cl, page, charts):
    r = cl.get(f"/frag/{page}/prehled")
    assert r.status_code == 200 and "alert-bad" not in r.text
    for ch in charts:
        assert cl.get(f"/api/chart/{page}/prehled/{ch}").status_code == 200


def test_seasonal_band():
    from charts.v2 import fig_seasonal, fig_small_multiples, seasonal
    idx = pd.date_range("2019-01-01", "2026-06-30", freq="D")
    s = pd.Series(np.arange(len(idx), dtype=float) % 365, index=idx)
    s[s.index.year == 2026] += 100
    sz = seasonal(s)
    assert sz["year"] == 2026 and sz["years"] == [2021, 2022, 2023, 2024, 2025]
    d = pd.Timestamp("2026-03-01")
    assert sz["cur"][d] - sz["mean"][d] > 90 and sz["lo"][d] <= sz["mean"][d] <= sz["hi"][d]
    assert sz["cur"].dropna().index.max() == pd.Timestamp("2026-06-30")
    fig = fig_seasonal(sz, "t", "%", targets=[(90, "cíl")], events=[("2026-02-01", "x")])
    names = [t.name for t in fig.data]
    assert "2026" in names and "2025" in names and any(n and n.startswith("min–max") for n in names)
    assert len(fig_small_multiples([("a", sz), ("b", sz), ("c", {})], "%").data) == 2 * len(fig.data)
    # týdenní data → interpolace na dny
    w = s.resample("W").mean()
    assert seasonal(w, interpolate=True)["cur"].dropna().size > 100
