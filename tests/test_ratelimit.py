"""Rate limiting: Retry-After, backoff, Energinet 1 dotaz/min/dataset, kvóta Open-Meteo."""
import pandas as pd
import pytest

from core import http
from data import nordic as nd
from data import weather as wx
from tests.conftest import FakeResponse
from tests.test_nordic import Sess, paths  # noqa: F401
from tests.test_weather import FakeOM, wdirs  # noqa: F401


def test_retry_after_parsing():
    assert http.retry_after_s(FakeResponse(429, headers={"Retry-After": "42"})) == 42
    assert http.retry_after_s(FakeResponse(429, text="Rate limit is exceeded. Try again in 17 seconds.")) == 17
    assert http.retry_after_s(FakeResponse(429)) is None


def test_get_with_backoff_respects_retry_after():
    waits = []
    seq = [FakeResponse(429, headers={"Retry-After": "30"}), FakeResponse(503), FakeResponse(200, json_data={})]
    s = Sess({"x": lambda u, p: seq.pop(0)})
    r = http.get_with_backoff(s, "http://x", base_s=5, sleep=waits.append)
    assert r.status_code == 200 and waits == [30, 10]
    s = Sess({"x": lambda u, p: FakeResponse(429, headers={"Retry-After": "3600"})})
    with pytest.raises(http.RateLimited):
        http.get_with_backoff(s, "http://x", max_wait_s=600, sleep=waits.append)


def test_pacer_interval():
    t = [0.0]
    slept = []

    def sleep(d):
        slept.append(d)
        t[0] += d
    p = http.Pacer(65, sleep=sleep, clock=lambda: t[0])
    p.wait("a"); p.wait("b"); p.wait("a")
    assert slept == [65]


def _recs(p):
    a = pd.Timestamp(p["start"], tz="UTC")
    return [{"HourUTC": (a + pd.Timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%S"), "PriceArea": "DK1",
             "GrossConsumptionMWh": 2000.0} for h in range(0, 48)]


def test_energinet_429_does_not_fail_run(paths, monkeypatch):  # noqa: F811
    monkeypatch.setenv("PPDASH_ENERGINET_WINDOW_DAYS", "10")
    calls = {"n": 0}

    def route(url, p):
        calls["n"] += 1
        if "Forecasts_Hour" in url:  # trvale přetížený dataset
            return FakeResponse(429, text="Rate limit is exceeded. Try again in 3600 seconds.")
        if calls["n"] == 1:
            return FakeResponse(429, headers={"Retry-After": "61"})
        return FakeResponse(json_data={"records": _recs(p)})
    slept = []
    res = nd.update_energinet(session=Sess({"energidataservice": route}), sleep=slept.append)
    assert res["errors"] == 0 and res["pending"] == 1 and res["rows"] > 0
    assert 61 in slept  # Retry-After respektován
    # navázání: další běh začíná u posledního uloženého, ne od začátku
    s2 = Sess({"energidataservice": lambda u, p: FakeResponse(json_data={"records": []})})
    nd.update_energinet(session=s2, sleep=lambda d: None)
    first = [c for c in s2.calls if "ProductionConsumption" in c[0]][0][1]
    assert pd.Timestamp(first["start"]) > pd.Timestamp("2026-10-01")


def test_energinet_all_hard_errors_raise(paths):  # noqa: F811
    from core.updaters import UpdateError
    with pytest.raises(UpdateError):
        nd.update_energinet(session=Sess({"energidataservice": lambda u, p: FakeResponse(500)}),
                            sleep=lambda d: None)


def test_fingrid_429_is_pending(paths, monkeypatch):  # noqa: F811
    monkeypatch.setattr(nd, "fingrid_api_key", lambda: "k")
    monkeypatch.setattr(nd, "FINGRID_DATASETS", {124: "spotřeba"})
    res = nd.update_fingrid(session=Sess({"fingrid": lambda u, p: FakeResponse(429, headers={"Retry-After": "7200"})}))
    assert res["errors"] == 0 and res["pending"] == 1


def test_openmeteo_daily_quota(wdirs, monkeypatch):  # noqa: F811
    monkeypatch.setenv("PPDASH_OPENMETEO_DAILY_LIMIT", "500")
    monkeypatch.setenv("PPDASH_WEATHER_FC_RESERVE", "0")
    om = FakeOM()
    res = wx.update_weather_history(session=om)
    assert res["pending"] > 0 and wx.quota_left() < 500
    n = len(om.calls)
    # vyčerpaná denní kvóta (429 daily) → žádná další volání, bez chyby běhu
    wx._quota_add(0, exhausted=True)
    res2 = wx.update_weather_history(session=om)
    assert len(om.calls) == n and res2["pending"] > 0


def test_openmeteo_429_daily_marks_exhausted(wdirs):  # noqa: F811
    class S:
        def get(self, *a, **k):
            return FakeResponse(429, json_data={"error": True, "reason": "Daily API request limit exceeded."})
    with pytest.raises(wx.QuotaExceeded):
        wx._get(S(), "archive", {"latitude": "1", "longitude": "1", "hourly": "temperature_2m",
                                  "start_date": "2024-01-01", "end_date": "2024-01-10"})
    assert wx.quota_left() == 0
