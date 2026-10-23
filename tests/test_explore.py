"""Průzkumník: DuckDB engine (core/explore.py) nad syntetickou historií +
endpointy (pivot export, uložené pohledy, cross-filter parametry)."""
from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from core import explore as ex
from core.cache import clear_all
from core.datasets import EXPLORE, EXPLORE_BY_KEY


@pytest.fixture
def hist(tmp_path, monkeypatch):
    root = tmp_path / "history"
    root.mkdir()
    monkeypatch.setattr(ex, "history_root", lambda: root)
    monkeypatch.setattr(ex, "cache_root", lambda: tmp_path / "cache")
    monkeypatch.setattr(ex, "_con", None)
    clear_all()
    yield root
    clear_all()


def _gen(root, country, start, periods, freq, values):
    idx = pd.date_range(start, periods=periods, freq=freq, tz="UTC")
    rows = []
    for st, v in values.items():
        rows.append(pd.DataFrame({"date": idx, "country": country, "source_type": st,
                                  "psr_code": "B14", "mw": float(v)}))
    df = pd.concat(rows, ignore_index=True)
    d = root / "generation" / country
    d.mkdir(parents=True, exist_ok=True)
    for month, g in df.groupby(df["date"].dt.strftime("%Y-%m")):
        g.to_parquet(d / f"{month}.parquet", index=False)


def test_catalog_consistent():
    keys = [e.key for e in EXPLORE]
    assert len(keys) == len(set(keys))
    for e in EXPLORE:
        assert e.measures and e.dims
        assert e.default_cat is None or e.dim(e.default_cat) is not None
        for d, _ in e.default_filters:
            assert e.dim(d) is not None, (e.key, d)


def test_energy_over_dst_and_two_step_agg(hist):
    # CZ hodinově, DE čtvrthodinově: součet zemí se musí zarovnat na hodinu
    _gen(hist, "CZ", "2026-03-28 23:00", 24 * 3, "h", {"Nuclear": 100, "Solar": 50})
    _gen(hist, "DE_LU", "2026-03-28 23:00", 96 * 3, "15min", {"Nuclear": 10})
    q = ex.Query("generation", "mw", date(2026, 3, 29), date(2026, 3, 29), grain="day", time_agg="energy")
    r = ex.run(q)
    assert r.unit == "MWh"
    # 29.3. (změna času) má 23 hodin → (100 + 50 + 10) × 23
    assert r.df["v"].tolist() == [pytest.approx(160 * 23)]
    q = ex.Query("generation", "mw", date(2026, 3, 29), date(2026, 3, 29), grain="hour", cat="country")
    r = ex.run(q)
    assert set(r.df["cat"]) == {"CZ", "DE_LU"} and len(r.df) == 46
    assert r.df.loc[r.df["cat"] == "CZ", "v"].eq(150).all()
    # průměr při slučování ostatních dimenzí
    q = ex.Query("generation", "mw", date(2026, 3, 29), date(2026, 3, 29), grain="day", cat="country",
                 cat_agg="avg", filters=(("country", ("CZ",)),))
    assert ex.run(q).df["v"].tolist() == [75.0]


def test_breakdown_pivot_and_partition_pruning(hist):
    _gen(hist, "CZ", "2025-12-31 23:00", 24 * 60, "h", {"Nuclear": 100, "Solar": 10})
    _gen(hist, "AT", "2025-12-31 23:00", 24 * 60, "h", {"Nuclear": 1})
    spec = EXPLORE_BY_KEY["generation"]
    files = ex.files_for(spec, {"country": ("CZ",)}, date(2026, 2, 10), date(2026, 2, 20))
    assert files and all("/CZ/" in f for f in files) and not any("2026-03" in f for f in files)
    q = ex.Query("generation", "mw", date(2026, 1, 1), date(2026, 2, 28), grain="month", cat="source_type",
                 time_agg="energy")
    bd = ex.breakdown(q)
    assert bd["cat"].tolist() == ["Nuclear", "Solar"]
    assert bd["v"].iloc[0] == pytest.approx(101 * 24 * (31 + 28))
    pv = ex.pivot(ex.run(q), "time", "cat")
    assert list(pv.index) == ["2026-01", "2026-02"] and "Σ" in pv.columns
    assert pv.loc["2026-01", "Solar"] == pytest.approx(10 * 24 * 31)
    assert ex.dim_values("generation", "country") == ["AT", "CZ"]
    assert ex.dim_values("generation", "source_type", (("country", ("AT",)),)) == ["Nuclear"]


def test_coarsening_limits_rows(hist, monkeypatch):
    _gen(hist, "CZ", "2026-01-01", 24 * 40, "h", {"Nuclear": 1})
    monkeypatch.setattr(ex, "MAX_ROWS", 100)
    r = ex.run(ex.Query("generation", "mw", date(2026, 1, 1), date(2026, 2, 9), grain="hour"))
    assert len(r.df) <= 100 and r.query.grain in ("day", "week") and r.warnings


def test_csv_mirror_and_seasonal(hist):
    d = hist / "hydro_reservoirs"
    d.mkdir()
    for y in (2024, 2025, 2026):
        idx = pd.date_range(f"{y}-01-07", periods=20, freq="7D", tz="UTC")
        df = pd.DataFrame({"date": idx, "country": "NO", "value_GWh": [y - 2000.0] * 20})
        for month, g in df.groupby(df["date"].dt.strftime("%Y-%m")):
            g.to_csv(d / f"{month}.csv", index=False)
    q = ex.Query("hydro", "gwh", date(2024, 1, 1), date(2026, 12, 31), grain="week")
    r = ex.run(q)
    assert len(r.df) == 60
    assert (hist.parent / "cache" / "explore_mirror" / "hydro_reservoirs").is_dir()
    s = ex.seasonal(r)
    assert sorted(s["year"].unique()) == [2024, 2025, 2026]
    assert s[s["year"] == 2026]["v"].eq(26).all()


def test_empty_dataset(hist):
    assert ex.bounds("generation") is None
    r = ex.run(ex.Query("generation", "mw", date(2026, 1, 1), date(2026, 1, 2)))
    assert r.empty and r.warnings


# ── web ─────────────────────────────────────────────────────────────────

@pytest.fixture
def client(hist, tmp_path, monkeypatch):
    _gen(hist, "CZ", "2025-12-31 23:00", 24 * 60, "h", {"Nuclear": 100, "Solar": 10})
    from web.app import app
    import web.tabs.explorer as te
    monkeypatch.setattr(te, "data_dir", lambda: tmp_path)
    return TestClient(app)


def test_explorer_pages(client):
    r = client.get("/frag/pruzkumnik/pohled?ds=generation&range=max&cat=source_type")
    assert r.status_code == 200 and "Kontingenční tabulka" in r.text and "Jaderná" in r.text
    for chart in ("line", "bar", "area", "season", "heat_year", "heat_hour"):
        j = client.get(f"/api/chart/pruzkumnik/pohled/main?ds=generation&range=max&chart={chart}").json()
        assert "figure" in j, chart
    j = client.get("/api/chart/pruzkumnik/pohled/breakdown?ds=generation&range=max&cat=source_type"
                   "&xf_from=2026-01-10&xf_to=2026-01-11").json()
    assert "11.01.2026" in j["figure"]["layout"]["title"]["text"]
    r = client.get("/api/explorer/pivot?fmt=csv&ds=generation&range=max&cat=source_type&pr=cat&pc=month")
    assert r.status_code == 200 and r.content.startswith("﻿".encode()) and b"Jadern" in r.content
    r = client.get("/api/explorer/pivot?fmt=xlsx&ds=generation&range=max")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    r = client.get("/api/chart-data/pruzkumnik/pohled/main?fmt=csv&ds=generation&range=max")
    assert r.status_code == 200
    assert client.get("/frag/pruzkumnik/katalog").status_code == 200


def test_dataset_switch_resets_params(client):
    # filtr země z jiného datasetu se po změně ds nepoužije
    r = client.get("/frag/pruzkumnik/pohled?ds=hydro&ds_prev=generation&f_country=CZ&f_country__set=1&m=mw")
    assert r.status_code == 200
    r = client.get("/frag/pruzkumnik/pohled?ds=generation&ds_prev=generation&f_country__set=1&cat=")
    assert "— (celkem)" in r.text


def test_saved_views(client, tmp_path):
    r = client.post("/api/explorer/views", json={"name": "CZ jádro", "query": "ds=generation&f_country=CZ"})
    assert r.status_code == 200 and r.json()["views"][0]["name"] == "CZ jádro"
    assert "CZ jádro" in client.get("/frag/pruzkumnik/pohled").text
    assert client.post("/api/explorer/views", json={"name": " "}).status_code == 400
    r = client.delete("/api/explorer/views", params={"name": "CZ jádro"})
    assert r.json()["views"] == []
