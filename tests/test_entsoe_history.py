"""ENTSO-E do historie (data/entsoe_history.py): navazování, zápis po
chunkech, rozpočet, rate limiter, normalizace — s falešným klientem."""
import time

import pandas as pd
import pytest

from data import entsoe_history as eh


class FakeClient:
    def __init__(self):
        self.calls = []

    def query_day_ahead_prices(self, zone, start, end):
        self.calls.append(("price", zone, start, end))
        idx = pd.date_range(start, end, freq="1h", inclusive="left", tz=start.tz)
        return pd.Series(50.0, index=idx)

    def query_load(self, area, start, end):
        self.calls.append(("load", area, start, end))
        idx = pd.date_range(start, min(end, start + pd.Timedelta(days=40)), freq="15min", inclusive="left")
        return pd.DataFrame({"Actual Load": 1000.0}, index=idx)

    def query_load_forecast(self, area, start, end, process_type="A01"):
        self.calls.append(("fc", area, start, end))
        idx = pd.date_range(start, end, freq="1h", inclusive="left")
        return pd.DataFrame({"Forecasted Load": 900.0}, index=idx)

    def query_wind_and_solar_forecast(self, area, start, end, psr_type=None):
        idx = pd.date_range(start, end, freq="1h", inclusive="left")
        return pd.DataFrame({"Solar": 1.0, "Wind Onshore": 2.0}, index=idx)

    def query_crossborder_flows(self, a, b, start, end):
        if (a, b) == ("CZ", "AT"):
            from entsoe.exceptions import NoMatchingDataError
            raise NoMatchingDataError
        idx = pd.date_range(start, end, freq="1h", inclusive="left")
        return pd.Series(300.0, index=idx)

    def query_scheduled_exchanges(self, a, b, start, end, dayahead=False):
        idx = pd.date_range(start, end, freq="1h", inclusive="left")
        return pd.Series(250.0, index=idx)


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    for name in ("PRICES_DIR", "LOAD_DIR", "WS_DIR", "FLOWS_DIR"):
        monkeypatch.setattr(eh, name, str(tmp_path / name))
    monkeypatch.setenv("PPDASH_ENTSOE_START", "2026-09-01")
    monkeypatch.setenv("PPDASH_ENTSOE_FLOWS_START", "2026-10-01")
    return tmp_path


def test_prices_incremental(dirs):
    c = FakeClient()
    st = eh.run_items(eh.price_items(["CZ", "NO_2"]), client=c)
    assert st["done"] == 2 and st["errors"] == 0
    df = pd.read_parquet(dirs / "PRICES_DIR" / "CZ")
    assert df["time"].dt.tz is not None and df["price"].eq(50).all()
    n = len(df)
    c2 = FakeClient()
    eh.run_items(eh.price_items(["CZ"]), client=c2)
    # druhý běh navazuje od konce dat − 2 dny, nic nezdvojí
    assert c2.calls[0][2] > pd.Timestamp("2026-10-01", tz="Europe/Prague")
    assert len(pd.read_parquet(dirs / "PRICES_DIR" / "CZ")) == n


def test_load_actual_and_forecast_resume_separately(dirs):
    c = FakeClient()
    eh.run_items(eh.load_items(["CZ"]), client=c)
    df = pd.read_parquet(dirs / "LOAD_DIR" / "CZ")
    assert set(df["kind"]) == {"actual", "forecast"}
    # actual fake vrací max 40 dní na chunk → konec actual < konec forecast; každý druh navazuje zvlášť
    la = eh._last_time(str(dirs / "LOAD_DIR" / "CZ"), "kind", "actual")
    lf = eh._last_time(str(dirs / "LOAD_DIR" / "CZ"), "kind", "forecast")
    assert la < lf


def test_flows_nodata_and_ws(dirs):
    c = FakeClient()
    st = eh.run_items(eh.flow_items([("CZ", "AT")]), client=c)
    assert st["errors"] == 0
    assert not (dirs / "FLOWS_DIR" / "CZ_AT").exists() or \
        set(pd.read_parquet(dirs / "FLOWS_DIR" / "CZ_AT")["kind"]) == {"scheduled"}
    df = pd.read_parquet(dirs / "FLOWS_DIR" / "AT_CZ")
    assert set(df["kind"]) == {"physical", "scheduled"} and set(df["src"]) == {"AT"}
    eh.run_items(eh.ws_items(["CZ"]), client=c)
    ws = pd.read_parquet(dirs / "WS_DIR" / "CZ")
    assert set(ws["psr"]) == {"Solar", "Wind Onshore"}


def test_budget_stops_and_resumes(dirs, monkeypatch):
    c = FakeClient()
    st = eh.run_items(eh.price_items(["CZ"]), client=c, budget_s=-1)
    assert st["pending"] == 1 and not c.calls


def test_errors_counted(dirs, monkeypatch):
    class Bad(FakeClient):
        def query_day_ahead_prices(self, *a, **k):
            raise RuntimeError("503")
    monkeypatch.setattr(eh.time, "sleep", lambda s: None)
    st = eh.run_items(eh.price_items(["CZ"]), client=Bad())
    assert st["errors"] >= 1
    from core.updaters import UpdateError
    with pytest.raises(UpdateError):
        eh._check(st, "x")


def test_rate_limiter(monkeypatch):
    lim = eh.RateLimiter(3)
    slept = []
    clock = [100.0]
    monkeypatch.setattr(eh.time, "monotonic", lambda: clock[0])

    def fake_sleep(s):
        slept.append(s)
        clock[0] += s
    monkeypatch.setattr(eh.time, "sleep", fake_sleep)
    for _ in range(4):
        lim.wait()
    assert len(slept) == 1 and 59 < slept[0] <= 60.1


def test_normalize_ws_multiindex():
    idx = pd.date_range("2026-01-01", periods=2, freq="h", tz="Europe/Prague")
    df = pd.DataFrame({("Solar", "Actual Aggregated"): [1, 2], ("Wind Onshore", "Actual Aggregated"): [3, 4]},
                      index=idx)
    out = eh.norm_ws(df, "DE_LU")
    assert set(out["psr"]) == {"Solar", "Wind Onshore"} and str(out["time"].dt.tz) == "UTC"


def test_update_all_entsoe_group():
    import scripts.update_all as ua
    assert ua.resolve(["entsoe"], []) == ua.ENTSOE_TASKS
    assert not set(ua.ENTSOE_TASKS) & set(ua.resolve([], []))


def test_market_pages_with_data(dirs, monkeypatch):
    """Stránka Trh EU nad uloženými daty (engine Průzkumníku)."""
    from fastapi.testclient import TestClient
    from core import explore as ex
    from core.cache import clear_all
    c = FakeClient()
    eh.run_items(eh.price_items(["CZ", "DE_LU", "NO_1"]), client=c)
    eh.run_items(eh.load_items(["CZ"]), client=c)
    eh.run_items(eh.ws_items(["CZ"]), client=c)
    eh.run_items(eh.flow_items([("CZ", "DE_LU")]), client=c)
    root = dirs / "history"
    root.mkdir()
    for name, d in (("PRICES_DIR", "entsoe_prices"), ("LOAD_DIR", "entsoe_load"),
                    ("WS_DIR", "entsoe_ws_forecast"), ("FLOWS_DIR", "entsoe_flows")):
        (root / d).symlink_to(dirs / name)
    monkeypatch.setattr(ex, "history_root", lambda: root)
    monkeypatch.setattr(ex, "cache_root", lambda: dirs / "cache")
    monkeypatch.setattr(ex, "_con", None)
    clear_all()
    from web.app import app
    cl = TestClient(app)
    for t in ("ceny", "spotreba", "oze", "toky"):
        r = cl.get(f"/frag/trh-eu/{t}")
        assert r.status_code == 200 and "alert-bad" not in r.text and "zatím nejsou" not in r.text, t
    for cid in ("ceny/line", "ceny/heat", "spotreba/line", "oze/stack", "oze/daily", "toky/net"):
        assert "figure" in cl.get(f"/api/chart/trh-eu/{cid}").json(), cid
    # saldo CZ: import z DE 300 − export 300 = 0 (fake vrací symetrické toky)
    assert "Německo+LU" in cl.get("/frag/trh-eu/toky").text
    clear_all()
