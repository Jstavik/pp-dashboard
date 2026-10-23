"""Počasí (data/weather.py) s falešným Open-Meteo + stránka /pocasi."""
import re

import numpy as np
import pandas as pd
import pytest

from data import weather as wx
from tests.conftest import FakeResponse


def _hours(start, end):
    return pd.date_range(start, end, freq="h", inclusive="left")


class FakeOM:
    """Odpovědi ve tvaru Open-Meteo: seznam míst, klíče var[_memberNN][_model]."""

    def __init__(self, bad_models=()):
        self.calls = []
        self.bad = set(bad_models)

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, dict(params)))
        n = len(params["latitude"].split(","))
        var = params["hourly"]
        models = params.get("models", "").split(",") if params.get("models") else []
        if self.bad & set(models):
            return FakeResponse(400, json_data={"error": True, "reason": "Cannot initialize model"})
        if "archive" in url:
            t = _hours(params["start_date"], pd.Timestamp(params["end_date"]) + pd.Timedelta(days=1))
        else:
            now = pd.Timestamp.now(tz="UTC").floor("D").tz_localize(None)
            t = _hours(now - pd.Timedelta(days=params.get("past_days", 0)),
                       now + pd.Timedelta(days=params.get("forecast_days", 1)))
        base = 5 + 10 * np.sin(np.arange(len(t)) / 24 / 365 * 2 * np.pi)
        locs = []
        for i in range(n):
            h = {"time": [x.strftime("%Y-%m-%dT%H:%M") for x in t]}
            if "ensemble" in url:
                for m in models:
                    for k in range(5):
                        h[f"{var}_member{k:02d}_{m}" if k else f"{var}_{m}"] = list(base + k + i)
            elif len(models) > 1:
                for j, m in enumerate(models):
                    h[f"{var}_{m}"] = list(base + j)
            else:
                h[var] = list(base + i)
            locs.append({"latitude": 0, "longitude": 0, "hourly": h})
        return FakeResponse(json_data=locs if n > 1 else locs[0])


@pytest.fixture
def wdirs(tmp_path, monkeypatch):
    for k, d in (("DAILY_DIR", "weather_daily"), ("FC_DIR", "weather_fc"), ("FC_DAILY_DIR", "weather_fc_daily"),
                 ("ENS_DIR", "weather_ens")):
        monkeypatch.setattr(wx, k, str(tmp_path / "history" / d))
    monkeypatch.setattr(wx.time, "sleep", lambda s: None)
    monkeypatch.setattr(wx, "REGIONS", {"CZ": wx.REGIONS["CZ"]})
    monkeypatch.setenv("PPDASH_WEATHER_START", "2024-01-01")
    monkeypatch.setenv("PPDASH_WEATHER_MODELS", "ecmwf_ifs025,gfs_seamless,bad_model")
    monkeypatch.setenv("PPDASH_WEATHER_ENS_MODELS", "ecmwf_ifs025")
    return tmp_path / "history"


def test_split_key():
    assert wx._split_key("temperature_2m_member03_ecmwf_ifs025", "temperature_2m", []) == ("member03", "ecmwf_ifs025")
    assert wx._split_key("temperature_2m", "temperature_2m", ["icon"]) == (None, "icon")
    assert wx._split_key("wind_speed_100m_gfs_seamless", "temperature_2m", []) is None


def test_weighted_handles_missing():
    a = pd.Series([1.0, np.nan]); b = pd.Series([3.0, 5.0])
    assert wx._weighted([a, b], [0.5, 0.5]).tolist() == [2.0, 5.0]


def test_forecast_models_fallback_and_ensemble(wdirs):
    om = FakeOM(bad_models={"bad_model"})
    res = wx.update_weather_forecast(session=om)
    assert res["errors"] == 0
    fc = pd.read_parquet(wdirs / "weather_fc" / "CZ")
    assert set(fc["model"]) == {"ecmwf_ifs025", "gfs_seamless"} and set(fc["var"]) == {"temp", "wind100", "solar"}
    d = pd.read_parquet(wdirs / "weather_fc_daily" / "CZ")
    assert d["issued"].nunique() == 1 and (d["date"] >= d["issued"] - pd.Timedelta(days=1)).all()
    ens = wx.load_ens("CZ")
    assert set(ens["var"]) == {"temp", "wind100", "solar"} and (ens["p10"] <= ens["p90"]).all()
    assert ens["n"].max() == 5
    assert all("apikey" not in p for _, p in om.calls)


def test_history_backfill_budget_and_recent(wdirs):
    om = FakeOM()
    res = wx.update_weather_history(session=om, budget_calls=50)
    assert res["pending"] > 0  # rozpočet nestačí na celou historii
    res = wx.update_weather_history(session=FakeOM(), budget_calls=10_000)
    d = pd.read_parquet(wdirs / "weather_daily" / "CZ")
    assert set(d["src"]) == {"era5", "recent"} and d.groupby(["date", "var"]).size().max() == 1
    loc = d["date"].dt.tz_convert("Europe/Prague").dt.date
    assert loc.min() == pd.Timestamp("2024-01-01").date()
    assert d[d["src"] == "recent"]["date"].min() > d[d["src"] == "era5"]["date"].max()
    nrm = wx.normals(d, "temp")
    assert len(nrm) == 366 and nrm["mean"].notna().all()


def test_commercial_key_url(monkeypatch):
    monkeypatch.setenv("OPENMETEO_API_KEY", "x")
    assert wx._url("forecast").startswith("https://customer-api.open-meteo.com")
    monkeypatch.setenv("OPENMETEO_BASE_URL", "http://meteo.lan:8080")
    assert wx._url("archive") == "http://meteo.lan:8080/v1/archive"


def test_hdd_cdd():
    assert wx.hdd([10, 16, -2]).tolist() == [8, 0, 20]
    assert wx.cdd([25, 23]).tolist() == [4, 0]


def test_weather_page(wdirs, monkeypatch):
    from fastapi.testclient import TestClient
    import web.tabs.weather as tw
    from core.cache import clear_all
    wx.update_weather_forecast(session=FakeOM(bad_models={"bad_model"}))
    wx.update_weather_history(session=FakeOM(), budget_calls=10_000)
    wx.update_weather_forecast(session=FakeOM(bad_models={"bad_model"}))
    monkeypatch.setattr(tw, "history_path", lambda name: str(wdirs / name))
    clear_all()
    from web.app import app
    cl = TestClient(app)
    for t in ("teplota", "vitr", "slunce", "mapy"):
        r = cl.get(f"/frag/pocasi/{t}")
        assert r.status_code == 200 and "alert-bad" not in r.text, t
    r = cl.get("/frag/pocasi/teplota")
    assert "HDD normál" in r.text and "Open-Meteo" in r.text
    for cid in ("outlook", "hourly", "hdd"):
        assert "figure" in cl.get(f"/api/chart/pocasi/teplota/{cid}").json(), cid
    clear_all()
