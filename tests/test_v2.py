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
    import re
    tabs = lambda html: re.findall(r'data-tab="([^"]+)" data-page="([^"]+)"', html)  # noqa: E731
    # nový vzhled je výchozí: přehledové záložky (v2-…) + všechny původní záložky
    r = cl.get("/plyn")
    assert r.status_code == 200 and "přepnout na starý" in r.text
    t = tabs(r.text)
    assert t[0] == ("prehled", "v2-plyn") and ("lng", "plyn") in t and ("gassco", "plyn") in t and ("mapa", "plyn") in t
    r = cl.get("/plyn?vzhled=stary")
    assert r.cookies.get("ppdash_vzhled") == "stary" and "přepnout na nový" in r.text
    assert all(pg == "plyn" for _, pg in tabs(r.text))
    assert all(pg == "plyn" for _, pg in tabs(cl.get("/plyn").text))          # zapamatováno
    r = cl.get("/plyn?vzhled=novy&tab=lng")
    assert r.cookies.get("ppdash_vzhled") == "novy" and 'class="tab active" data-tab="lng"' in r.text
    for page, v2 in (("elektrina", "v2-elektrina"), ("nordic", "v2-nordic"), ("trh-eu", "v2-trh-eu"),
                     ("pocasi", "v2-pocasi")):
        t = tabs(cl.get(f"/{page}").text)
        assert t[0][1] == v2 and any(pg == page for _, pg in t), page
    assert ("odstavky", "v2-elektrina") in tabs(cl.get("/elektrina").text)
    # stránky bez nového vzhledu beze změny
    assert all(pg == "cot" for _, pg in tabs(cl.get("/cot").text))


@pytest.mark.parametrize("page,charts", [
    ("v2-plyn", ["eu", "multi", "net"]),
    ("v2-elektrina", ["base", "recent", "spread", "profile", "gen"]),
    ("v2-nordic", ["hydro", "hydro_multi", "spreads"]),
    ("v2-trh-eu", ["multi", "spreads", "load"]),
    ("v2-pocasi", ["temp", "anom", "wind", "solar"]),
])
def test_v2_pages_render(cl, page, charts):
    r = cl.get(f"/frag/{page}/prehled")
    assert r.status_code == 200 and "alert-bad" not in r.text
    for ch in charts:
        assert cl.get(f"/api/chart/{page}/prehled/{ch}").status_code == 200


def test_v2_outages_tab(cl):
    r = cl.get("/frag/v2-elektrina/odstavky")
    assert r.status_code == 200 and "alert-bad" not in r.text
    assert cl.get("/api/chart/v2-elektrina/odstavky/unavail").status_code == 200


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


def test_v2_new_pages_synthetic(tmp_path, monkeypatch, no_cache):
    """Trh EU a Počasí v novém vzhledu nad syntetickými daty (v repu ta data nejsou)."""
    import json
    import web.tabs.v2 as v2
    import web.tabs.weather as wt
    from data.partitioned_store import upsert_partitioned
    rng = np.random.default_rng(0)
    d = pd.date_range("2020-01-01", "2026-10-20", freq="D")
    pr = pd.DataFrame({z: 60 + 20 * np.cos(2 * np.pi * d.dayofyear / 365) + i + rng.normal(0, 5, len(d))
                       for i, z in enumerate(v2.EU_ZONES)}, index=d)
    monkeypatch.setattr(v2, "_eu_prices", lambda: pr)
    monkeypatch.setattr(v2, "_eu_load", lambda area="CZ": pr["CZ"] / 10)
    for reg in v2.WX_REGIONS:
        base = tmp_path / reg
        rows = pd.concat([pd.DataFrame({"date": d.tz_localize("UTC"), "region": reg, "var": var,
                                        "value": 10 - 10 * np.cos(2 * np.pi * d.dayofyear / 365) + rng.normal(0, 2, len(d)),
                                        "src": "era5"}) for var in ("temp", "wind100", "solar")])
        upsert_partitioned(rows, str(base), "date", ["date", "var"], fmt="parquet")
    monkeypatch.setattr(wt, "_daily", lambda region: pd.read_parquet(tmp_path / region).assign(
        date=lambda x: pd.to_datetime(x["date"], utc=True)))
    fd = pd.date_range("2026-10-21", periods=10, freq="D", tz="UTC")
    monkeypatch.setattr(wt, "_latest_daily_fc", lambda region, var: pd.DataFrame(
        {"issued": fd[0], "date": fd, "var": var, "model": "ecmwf_ifs025", "value": 12.0}))
    cl = TestClient(app)
    r = cl.get("/frag/v2-trh-eu/prehled")
    assert r.status_code == 200 and "Ø 7 dní" in r.text and "Spread vs. DE" in r.text
    for ch in ("multi", "spreads", "load"):
        j = json.loads(cl.get(f"/api/chart/v2-trh-eu/prehled/{ch}").text)
        assert "figure" in j and j["figure"]["data"], ch
    r = cl.get("/frag/v2-pocasi/prehled")
    assert r.status_code == 200 and "HDD 14 dní" in r.text and "příštích 7 dní" in r.text
    for ch in ("temp", "anom", "wind", "solar"):
        j = json.loads(cl.get(f"/api/chart/v2-pocasi/prehled/{ch}").text)
        assert "figure" in j, ch
    names = [t.get("name") for t in json.loads(cl.get("/api/chart/v2-pocasi/prehled/temp").text)["figure"]["data"]]
    assert "předpověď ECMWF" in names
