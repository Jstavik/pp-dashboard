"""Nordic zdroje (data/nordic.py) s falešnými odpověďmi + stránka /nordic."""
import pandas as pd
import pytest

from data import nordic as nd
from tests.conftest import FakeResponse


def _nve_records(years=(2023, 2024, 2025, 2026)):
    out = []
    for y in years:
        for w in range(1, 53 if y < 2026 else 42):
            d = pd.Timestamp.fromisocalendar(y, w, 7)
            for t, nr in (("NO", 0), ("EL", 1), ("EL", 5), ("VASS", 3)):
                out.append({"dato_Id": d.strftime("%Y-%m-%d"), "omrType": t, "omrnr": nr, "iso_aar": y,
                            "iso_uke": w, "fyllingsgrad": 0.3 + 0.4 * (w / 52) + (y - 2023) * 0.01,
                            "kapasitet_TWh": 87.0, "fylling_TWh": 87 * (0.3 + 0.4 * w / 52)})
    return out


def _nve_stats():
    return [{"omrType": "NO", "omrnr": 0, "iso_uke": w, "minFyllingsgrad": 0.2, "medianFyllingsgrad": 0.5,
             "maxFyllingsgrad": 0.9, "minFyllingTWH": 17, "medianFylling_TWH": 43, "maxFyllingTWH": 78}
            for w in range(1, 54)]


class Sess:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params, headers))
        for k, fn in self.routes.items():
            if k in url:
                return fn(url, params)
        return FakeResponse(404)


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(nd, "NVE_PATH", str(tmp_path / "h" / "nve_reservoirs.parquet"))
    monkeypatch.setattr(nd, "NVE_STATS_PATH", str(tmp_path / "h" / "nve_reservoirs_stats.parquet"))
    monkeypatch.setattr(nd, "FINGRID_DIR", str(tmp_path / "h" / "fingrid"))
    monkeypatch.setattr(nd, "ENERGINET_DIR", str(tmp_path / "h" / "energinet"))
    monkeypatch.setattr(nd.time, "sleep", lambda s: None)
    monkeypatch.setenv("PPDASH_NORDIC_START", "2026-09-01")
    return tmp_path / "h"


def test_nve_parse_and_update(paths):
    df = nd.parse_nve(_nve_records())
    assert set(df["area"]) == {"NO", "NO1", "NO5"} and df["filling_pct"].between(0, 100).all()
    s = Sess({"MinMaxMedian": lambda u, p: FakeResponse(json_data=_nve_stats()),
              "HentOffentligData": lambda u, p: FakeResponse(json_data=_nve_records())})
    res = nd.update_nve(session=s)
    assert res["errors"] == 0 and len(nd.load_nve()) == len(df)
    st = nd.load_nve_stats()
    assert st["median_pct"].eq(50).all() and st["median_twh"].eq(43).all()
    mtime = (paths / "nve_reservoirs.parquet").stat().st_mtime_ns
    nd.update_nve(session=s)  # beze změny → nepřepisuje
    assert (paths / "nve_reservoirs.parquet").stat().st_mtime_ns == mtime


def test_fingrid_skip_without_key(paths, monkeypatch):
    monkeypatch.setattr(nd, "fingrid_api_key", lambda: None)
    assert nd.update_fingrid(session=Sess({}))["skipped"]


def test_fingrid_pagination_and_resume(paths, monkeypatch):
    monkeypatch.setattr(nd, "fingrid_api_key", lambda: "k")
    monkeypatch.setattr(nd, "FINGRID_DATASETS", {124: "spotřeba"})

    def route(url, p):
        a = pd.Timestamp(p["startTime"])
        idx = pd.date_range(a, pd.Timestamp(p["endTime"]), freq="15min", inclusive="left")
        half = len(idx) // 2
        part = idx[:half] if p["page"] == 1 else idx[half:]
        return FakeResponse(json_data={"data": [{"datasetId": 124, "startTime": t.isoformat(), "value": 9000.0}
                                                for t in part], "pagination": {"currentPage": p["page"], "lastPage": 2}})
    s = Sess({"data.fingrid.fi": route})
    res = nd.update_fingrid(session=s)
    assert res["errors"] == 0 and s.calls[0][2]["x-api-key"] == "k"
    df = pd.read_parquet(paths / "fingrid")
    assert df["series"].eq("spotřeba").all() and df["time"].is_unique
    s2 = Sess({"data.fingrid.fi": route})
    nd.update_fingrid(session=s2)
    assert pd.Timestamp(s2.calls[0][1]["startTime"]) > pd.Timestamp("2026-10-01", tz="UTC")


def test_energinet_melt_and_update(paths):
    recs = [{"HourUTC": "2026-10-01T00:00:00", "HourDK": "2026-10-01T02:00:00", "PriceArea": a,
             "GrossConsumptionMWh": 2000.0, "OnshoreWindGe50kW_MWh": 500.0, "ExchangeNO_MWh": None}
            for a in ("DK1", "DK2")]
    m = nd.melt_energinet(recs)
    assert set(m["series"]) == {"GrossConsumptionMWh", "OnshoreWindGe50kW_MWh"} and len(m) == 4
    fc = nd.melt_energinet([{"HourUTC": "2026-10-01T00:00:00", "PriceArea": "DK1", "ForecastType": "Solar",
                             "ForecastDayAhead": 12.0}])
    assert fc["series"].tolist() == ["Solar|ForecastDayAhead"]

    def route(url, p):
        return FakeResponse(json_data={"total": 2, "records": recs})
    res = nd.update_energinet(session=Sess({"energidataservice": route}))
    assert res["rows"] > 0 and (paths / "energinet" / "ProductionConsumptionSettlement").is_dir()


def test_nordic_hydro_page(paths, monkeypatch):
    from fastapi.testclient import TestClient
    from web.app import app
    s = Sess({"MinMaxMedian": lambda u, p: FakeResponse(json_data=_nve_stats()),
              "HentOffentligData": lambda u, p: FakeResponse(json_data=_nve_records())})
    nd.update_nve(session=s)
    cl = TestClient(app)
    r = cl.get("/frag/nordic/hydro?area=NO")
    assert r.status_code == 200 and "vs. medián" in r.text and "NO5" in r.text
    j = cl.get("/api/chart/nordic/hydro/nve?area=NO1").json()
    assert "figure" in j
    for t in ("ceny", "toky", "fidk"):
        assert cl.get(f"/frag/nordic/{t}").status_code == 200
