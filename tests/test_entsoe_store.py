"""Elektřina: ENTSO-E z úložiště (data/entsoe_store.py), živě jen když den chybí."""
import pandas as pd
import pytest

from data.partitioned_store import upsert_partitioned

TZ = "Europe/Prague"


@pytest.fixture
def store(tmp_path, monkeypatch, no_cache):
    import data.entsoe_history as eh
    for name in ("PRICES_DIR", "LOAD_DIR", "WS_DIR"):
        d = tmp_path / name
        d.mkdir()
        monkeypatch.setattr(eh, name, str(d))
    today = pd.Timestamp.now(tz=TZ).normalize()
    t = pd.date_range(today - pd.Timedelta(days=1), today + pd.Timedelta(days=1), freq="h", inclusive="left")
    upsert_partitioned(pd.DataFrame({"time": t.tz_convert("UTC"), "zone": "CZ", "price": range(len(t))}),
                       str(tmp_path / "PRICES_DIR" / "CZ"), "time", ["time"], fmt="parquet")
    t2 = pd.date_range(today, today + pd.Timedelta(days=2), freq="15min", inclusive="left")
    upsert_partitioned(pd.concat([
        pd.DataFrame({"time": t2.tz_convert("UTC"), "area": "CZ", "kind": "forecast", "mw": 7000.0}),
        pd.DataFrame({"time": t2.tz_convert("UTC"), "area": "CZ", "kind": "actual", "mw": 6000.0})]),
        str(tmp_path / "LOAD_DIR" / "CZ"), "time", ["time", "kind"], fmt="parquet")
    upsert_partitioned(pd.concat([
        pd.DataFrame({"time": t2.tz_convert("UTC"), "area": "CZ", "psr": psr, "mw": v})
        for psr, v in (("Solar", 500.0), ("Wind Onshore", 80.0))]),
        str(tmp_path / "WS_DIR" / "CZ"), "time", ["time", "psr"], fmt="parquet")
    return today


def test_dap_from_store_and_live_fallback(store, monkeypatch):
    import data.entsoe as live
    import data.entsoe_store as es
    called = []
    monkeypatch.setattr(live, "fetch_dap", lambda off=0: called.append(off) or pd.Series([1.0], name="dap_EUR_MWh"))
    d0 = es.dap(0)
    assert len(d0) == 96 and d0.name == "dap_EUR_MWh" and str(d0.index.tz) == TZ
    assert d0.index[0] == store and d0.iloc[0] == 24 and d0.iloc[3] == 24 and d0.iloc[4] == 25  # 15min ffill
    assert es.source("dap0") == "úložiště" and not called
    d1 = es.dap(1)  # zítřek v úložišti není → živě
    assert called == [1] and len(d1) == 1 and es.source("dap1") == "živě"


def test_load_and_ws_forecast_from_store(store, monkeypatch):
    import data.entsoe_store as es
    lf = es.load_forecast()
    assert lf.name == "forecast_MW" and len(lf) == 192 and (lf == 7000).all() and es.source("load_fc") == "úložiště"
    ws = es.wind_solar_forecast()
    assert list(ws.columns) == ["Solar", "Wind Onshore"] and len(ws) == 192 and str(ws.index.tz) == TZ
    assert es.source("ws") == "úložiště"


def test_snapshot_no_live_load_query(store, monkeypatch):
    """Web volá fetch_entsoe_data(with_load=False) a prognózu zatížení bere z úložiště."""
    import data.entsoe as live
    import web.services as sv
    seen = {}

    def fake(with_load=True):
        seen["with_load"] = with_load
        raise RuntimeError("bez tokenu")
    monkeypatch.setattr(live, "fetch_entsoe_data", fake)
    snap = sv.entsoe_snapshot()
    assert seen["with_load"] is False and len(snap["load_fc"]) == 192 and snap["error"]


def test_elektrina_refresh_target():
    from core.jobs import target_info
    from scripts.update_all import resolve
    assert target_info("elektrina")[1] == "elektrina"
    assert {"entsoe_prices", "entsoe_load", "entsoe_ws", "generation", "outages"} <= set(resolve(["elektrina"], []))
