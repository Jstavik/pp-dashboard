"""Smoke testy datových zdrojů — síť je mockovaná, ověřuje se parsování
odpovědí a zápis do úložiště (do tmp adresáře), ne živá API."""
import io
import xml.etree.ElementTree as ET

import pandas as pd
import pytest
import requests

from tests.conftest import FakeResponse

TZ = "Europe/Prague"


# ── ČEPS (SOAP) ─────────────────────────────────────────────────────────

def _ceps_xml(series: dict, rows: list):
    ns = "https://www.ceps.cz/CepsData/StructuredData/1.0"
    root = ET.Element(f"{{{ns}}}root")
    ser = ET.SubElement(root, f"{{{ns}}}series")
    for sid, name in series.items():
        ET.SubElement(ser, f"{{{ns}}}serie", id=sid, name=name)
    data = ET.SubElement(root, f"{{{ns}}}data")
    for r in rows:
        ET.SubElement(data, f"{{{ns}}}item", **r)
    return root


class _FakeCepsService:
    def __getattr__(self, method):
        def call(**kw):
            now = pd.Timestamp.now(tz=TZ).floor("min")
            ts = [(now - pd.Timedelta(minutes=i)).isoformat() for i in (2, 1)]
            if method == "OdhadovanaCenaOdchylky":
                return _ceps_xml({}, [{"date": ts[0], "value15": "00:00-00:15", "value2": "2500"}])
            if method == "AktivaceSVRvCR":
                return _ceps_xml({"value1": "aFRR+", "value2": "aFRR-"},
                                 [{"date": t, "value1": "10", "value2": "-5", "value3": "1", "value4": "0",
                                   "value7": "0"} for t in ts])
            if method == "CrossborderPowerFlows":
                return _ceps_xml({"value1": "DE Actual [MW]", "value2": "PL Actual [MW]"},
                                 [{"date": t, "value1": "100", "value2": "-30"} for t in ts])
            if method == "Load":
                return _ceps_xml({"value1": "Load [MW]"}, [{"date": t, "value1": "7000"} for t in ts])
            return _ceps_xml({"value1": method}, [{"date": t, "value1": "12.5"} for t in ts])
        return call


class _FakeCeps:
    service = _FakeCepsService()


def test_ceps_fetch_all(monkeypatch, no_cache):
    from data import ceps
    monkeypatch.setattr(ceps, "_get_ceps_client", lambda: _FakeCeps())
    d = ceps.fetch_ceps_all()
    assert not d["imbal"].empty and list(d["imbal_mw"].columns) == ["odchylka_MW"]
    assert list(d["svr_named"].columns) == list(ceps.SVR_SERIES.values())
    assert d["svr_named"]["aFRR+ [MW]"].iloc[0] == 10
    assert d["cb"]["Net Export (MW)"].iloc[-1] == 70
    assert d["price"]["cena_CZK_MWh"].iloc[0] == 2500
    df, now = ceps.fetch_ceps_imbalance()
    assert df["odchylka_MW"].iloc[-1] == 12.5
    assert not ceps.fetch_ceps_svr().empty and not ceps.fetch_ceps_imbalance_price().empty


def test_ceps_client_failure_returns_empty(monkeypatch, no_cache):
    from data import ceps

    def boom():
        raise requests.ConnectionError("down")
    monkeypatch.setattr(ceps, "_get_ceps_client", boom)
    d = ceps.fetch_ceps_all()
    assert d["imbal"].empty and d["now"] is not None


# ── ENTSO-E ─────────────────────────────────────────────────────────────

class _FakeEntsoe:
    def __init__(self):
        self.idx = pd.date_range(pd.Timestamp.now(tz=TZ).normalize(), periods=96, freq="15min")

    def query_imbalance_volumes(self, *a, **k):
        return pd.Series(range(96), index=self.idx, dtype=float)

    def query_imbalance_prices(self, *a, **k):
        return pd.DataFrame({"Short": 100.0, "Long": 90.0}, index=self.idx)

    def query_generation(self, *a, **k):
        cols = pd.MultiIndex.from_tuples([("Nuclear", "Actual Aggregated"), ("Solar", "Actual Aggregated")])
        return pd.DataFrame([[3000.0, 500.0]] * 96, index=self.idx, columns=cols)

    def query_load(self, *a, **k):
        return pd.DataFrame({"Actual Load": 7000.0}, index=self.idx)

    def query_load_forecast(self, *a, **k):
        return pd.DataFrame({"Forecasted Load": 7100.0}, index=self.idx)

    def query_unavailability_of_production_units(self, *a, **k):
        return pd.DataFrame({
            "start": [self.idx[0]], "end": [self.idx[-1] + pd.Timedelta(days=3)],
            "nominal_power": ["1000"], "avail_qty": ["400"], "businesstype": ["Planned maintenance"],
            "production_resource_name": ["TEMELIN_1"], "production_resource_id": ["X"],
        })

    query_unavailability_of_generation_units = query_unavailability_of_production_units

    def query_day_ahead_prices(self, *a, **k):
        idx = pd.date_range(k["start"], periods=24, freq="1h")
        return pd.Series([50.0 + i for i in range(24)], index=idx)

    def query_installed_generation_capacity(self, *a, **k):
        return pd.DataFrame({"Nuclear": [4000.0], "Solar": [3000.0]}, index=self.idx[:1])

    def query_activated_balancing_energy_prices(self, *a, **k):
        return pd.DataFrame({"ReserveType": ["aFRR", "aFRR"], "Direction": ["Up", "Down"], "Price": [80.0, 20.0]},
                            index=self.idx[:2])

    def query_wind_and_solar_forecast(self, *a, **k):
        return pd.DataFrame({"Solar": 400.0}, index=self.idx)

    def query_contracted_reserve_amount(self, *a, **k):
        return pd.DataFrame({"Up": 100.0, "Down": 90.0}, index=self.idx)

    query_contracted_reserve_prices = query_contracted_reserve_amount


def test_entsoe_fetchers(monkeypatch, no_cache):
    from data import entsoe
    monkeypatch.setattr(entsoe, "_get_client", lambda: _FakeEntsoe())
    imbal, gen, load_actual, load_fc, out, now = entsoe.fetch_entsoe_data()
    assert list(imbal.columns) == ["odchylka_MWh", "price_Short", "price_Long"]
    assert list(gen.columns) == ["Nuclear", "Solar"]
    assert load_fc.name == "forecast_MW" and len(out) == 2 and set(out["unit_level"]) == {"PU", "GU"}
    dap = entsoe.fetch_dap(0)
    assert len(dap) == 96 and dap.name == "dap_EUR_MWh"    # hodinové → 15min
    assert not entsoe.fetch_installed_capacity().empty
    assert not entsoe.fetch_activation_prices().empty
    assert not entsoe.fetch_wind_solar_forecast().empty
    res = entsoe.fetch_reserves()
    assert not res["afrr_d_amt"].empty and "start" in res


def test_entsoe_without_token_raises(no_cache):
    from data import entsoe
    with pytest.raises(entsoe.EntsoeTokenMissing):
        entsoe.fetch_dap(0)


# ── ENTSO-G toky (aggregateddata) ───────────────────────────────────────

def _entsog_rows(day):
    return [{"periodFrom": f"{day}T06:00:00+02:00", "indicator": "Physical Flow", "countryKey": "CZ",
             "countryLabel": "Czechia", "directionKey": d, "adjacentSystemsKey": "TransmissionDE-THE-----",
             "adjacentSystemsLabel": "x", "pointsNames": "VIP Brandov|VIP Waidhaus", "value": "150000000",
             "unit": "kWh/d", "flowStatus": "Confirmed"} for d in ("entry", "exit")]


def test_entsog_update_writes_partitions(monkeypatch, tmp_path):
    from core import updaters
    monkeypatch.setattr(updaters, "ENTSOG_FLOWS_DIR", str(tmp_path))
    monkeypatch.setattr(updaters, "HISTORY_START", pd.Timestamp.now().date() - pd.Timedelta(days=3))
    monkeypatch.setattr(updaters.time, "sleep", lambda s: None)

    def fake_get(url, timeout=None, **kw):
        rows = _entsog_rows(url.split("from=")[1][:10])
        return FakeResponse(json_data={"aggregateddata": rows, "meta": {"total": len(rows)}})
    monkeypatch.setattr(updaters.requests, "get", fake_get)
    res = updaters.update_entsog()
    assert res["rows"] > 0 and res["errors"] == 0
    files = list(tmp_path.glob("*.parquet"))
    assert files
    df = pd.concat(pd.read_parquet(f) for f in files)
    assert set(df["directionKey"]) == {"entry", "exit"} and (df["value_GWh"] == 150).all()


def test_entsog_update_all_failed_raises(monkeypatch, tmp_path):
    from core import updaters
    monkeypatch.setattr(updaters, "ENTSOG_FLOWS_DIR", str(tmp_path))
    monkeypatch.setattr(updaters, "HISTORY_START", pd.Timestamp.now().date())
    monkeypatch.setattr(updaters.time, "sleep", lambda s: None)
    monkeypatch.setattr(updaters.requests, "get", lambda *a, **k: FakeResponse(status=500, text="err"))
    with pytest.raises(updaters.UpdateError):
        updaters.update_entsog()


def test_entsog_live_fallback(monkeypatch, no_cache):
    from data import entsog
    monkeypatch.setattr(entsog.requests, "get",
                        lambda *a, **k: FakeResponse(json_data={"aggregateddata": _entsog_rows("2026-10-20")}))
    pivot = entsog.fetch_entsog_flows(days=5)
    assert "Brandov/Waidhaus (DE)" in pivot.columns


# ── ENTSOG operational ──────────────────────────────────────────────────

def test_entsog_operational_pagination_and_process(monkeypatch):
    from data import entsog_operational as eo
    pages = {0: [{"id": f"i{i}", "indicator": "Nomination", "periodFrom": "2026-10-01T06:00:00+02:00",
                  "periodTo": "2026-10-02T06:00:00+02:00", "operatorKey": "CZ-TSO-0001", "pointLabel": "VIP Brandov",
                  "directionKey": "entry", "unit": "kWh/d", "value": 1e8, "isNA": 0,
                  "lastUpdateDateTime": "2026-10-01T10:00:00+02:00"} for i in range(2000)],
             2000: [{"id": "last", "indicator": "Nomination", "periodFrom": "2026-10-02T06:00:00+02:00",
                     "periodTo": "2026-10-03T06:00:00+02:00", "operatorKey": "CZ-TSO-0001",
                     "pointLabel": "VIP Brandov", "directionKey": "entry", "unit": "kWh/d", "value": 2e8,
                     "isNA": 1, "lastUpdateDateTime": "x"}]}
    monkeypatch.setattr(eo.time, "sleep", lambda s: None)

    def fake_get(url, timeout=None):
        off = int(url.split("offset=")[1].split("&")[0])
        return FakeResponse(json_data={"operationaldata": pages[off]})
    monkeypatch.setattr(eo.requests, "get", fake_get)
    rows = eo.fetch_operational_month(pd.Timestamp("2026-10-01").date(), pd.Timestamp("2026-10-31").date(),
                                      "Nomination")
    assert len(rows) == 2001
    df = eo._process(rows)
    assert len(df) == 2000 and df["value_GWh"].iloc[0] == 100          # isNA==1 odfiltrováno
    s = eo.active_capacity_dedup(df.assign(lastUpdateDateTime="2026-10-01T10:00:00+02:00"),
                                 [pd.Timestamp("2026-10-01")])
    assert s.iloc[0] == 100


# ── GIE AGSI+ / ALSI ────────────────────────────────────────────────────

def test_gie_update(monkeypatch, tmp_path):
    from core import updaters
    monkeypatch.setenv("GIE_API_KEY", "test")
    monkeypatch.setattr(updaters, "GIE_ALL_DIR", str(tmp_path))
    monkeypatch.setattr(updaters.time, "sleep", lambda s: None)

    def fake_get(url, headers=None, timeout=None):
        assert headers == {"x-key": "test"}
        return FakeResponse(json_data={"last_page": 1, "data": [
            {"gasDayStart": "2026-10-20", "full": "55,5", "gasInStorage": "10.1", "injection": 1,
             "withdrawal": 0, "info": []}]})
    monkeypatch.setattr(updaters.requests, "get", fake_get)
    res = updaters.update_gie_all()
    assert res["rows"] > 0
    df = pd.read_csv(next(tmp_path.glob("*.csv")))
    assert df["full"].iloc[0] == 55.5 and "info" not in df.columns


def test_gie_without_key_fails():
    from core import updaters
    with pytest.raises(updaters.UpdateError):
        updaters.fetch_gie_all_countries()


def test_lng_fetch(monkeypatch):
    from data import lng
    monkeypatch.setenv("GIE_API_KEY", "test")
    monkeypatch.setattr(lng.time, "sleep", lambda s: None)
    monkeypatch.setattr(lng.requests, "get", lambda *a, **k: FakeResponse(json_data={"last_page": 1, "data": [
        {"gasDayStart": "2026-10-20", "name": "X", "inventory": {"gwh": "50"}, "dtmi": {"gwh": "100"},
         "sendOut": "12,5", "status": "C"}]}))
    df = lng.fetch_lng_all()
    assert len(df) == len(lng.COUNTRIES_ALSI) + 1
    assert df["full_pct"].iloc[0] == 50.0 and df["sendOut"].iloc[0] == 12.5


# ── GASSCO ──────────────────────────────────────────────────────────────

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><updated>2026-10-23T10:00:00Z</updated>
<entry><title>Exit Nomination Emden (MSm3)</title><content>40</content></entry>
<entry><title>Exit Nomination Dornum (MSm3)</title><content>30.5</content></entry></feed>"""


def test_gassco_realtime(monkeypatch, no_cache):
    from data import gassco
    monkeypatch.setattr(gassco.requests, "get", lambda *a, **k: FakeResponse(text=ATOM))
    df = gassco.fetch_realtime_nominations()
    assert set(df["point"]) == {"Emden", "Dornum"}
    assert df.loc[df["point"] == "Emden", "value_GWh"].iloc[0] == pytest.approx(40 * 10.55)


class _FakeSession:
    headers = {}

    def __init__(self, responses):
        self.responses = responses

    def get(self, url, **kw):
        for key, resp in self.responses.items():
            if key in url:
                return resp
        return FakeResponse(text="")


def test_gassco_nominations(monkeypatch):
    from data import gassco
    sess = _FakeSession({
        "/ch/points": FakeResponse(json_data=[{"id": 1, "name": "Emden"}]),
        "/ch/2Y/1": FakeResponse(json_data={"data": [{"x": 1760000000000, "y": 40.0}]}, text='{"data": []}'),
    })
    monkeypatch.setattr(gassco, "_get_session", lambda: sess)
    df = gassco.fetch_gassco_nominations()
    assert df["value_GWh"].iloc[0] == pytest.approx(422.0)


def test_gassco_umm_xlexport(monkeypatch):
    from openpyxl import Workbook
    from data import gassco
    wb = Workbook()
    ws = wb.active
    ws.append(["UMM export"])
    ws.append(["Message ID", "Affected Asset or Unit", "Event Status", "Type of unavailability", "Type of event",
               "Publication date/time", "Event", "", "Unit of Meassurement", "Capacity", "", ""])
    ws.append(["", "", "", "", "", "", "Start", "Stop", "", "Technical", "Available", "Unavailable"])
    ws.append(["ABC_2", "Troll", "Active", "Planned", "Maintenance", "2026-10-01 10:00", "2026-10-25 06:00",
               "2026-10-27 06:00", "mcm/d", 100, 60, 40])
    buf = io.BytesIO()
    wb.save(buf)
    monkeypatch.setattr(gassco, "_get_session",
                        lambda: _FakeSession({"/xlexport": FakeResponse(content=buf.getvalue(), text="xlsx")}))
    df = gassco.fetch_gassco_umm()
    assert df["base_id"].iloc[0] == "ABC" and df["revision"].iloc[0] == 2
    assert df["unavailableCapacity_GWh"].iloc[0] == pytest.approx(40 * 10.55)
    delta = gassco.compute_umm_delta(df, df.iloc[0:0])
    assert len(delta["new"]) == 1


# ── ICE CoT ─────────────────────────────────────────────────────────────

def _cot_csv():
    cats = ["Investment_Firms_or_Credit_Institutions", "Investment_Funds", "Other_Financial_Institutions",
            "Commercial_Undertakings", "Operators_Directive_2003_87_EC"]
    base = ["Trading_Venue_Identifier", "Venue_Product_Code", "Name_of_Commodity_Contract_or_Emission_Allowance",
            "Date_of_which_the_weekly_report_refers", "Report_Status", "NotationofthePositionQuantity",
            "Date_and_time_of_the_publication"]
    nops = [f"NOPS_{c}_{s}_{sc}" for c in cats for s in ("Long", "Short") for sc in ("Risk_Reducing", "Other", "Total")]
    row = ["NDEX", "TFM", "Dutch TTF", "2026-10-16", "NEWT", "MWHO", "2026-10-16T18:30:00"] + ["10"] * len(nops)
    return ",".join(base + nops) + "\n" + ",".join(row) + "\n" + ",".join([""] * (len(base) + len(nops))) + "\n"


def test_ice_cot_fetch(monkeypatch, tmp_path):
    from data import ice_cot
    monkeypatch.setattr(ice_cot, "COT_PATH", str(tmp_path / "cot.parquet"))
    monkeypatch.setattr(ice_cot, "STATUS_PATH", str(tmp_path / "status.json"))
    monkeypatch.setattr(ice_cot.SESSION, "get", lambda url, timeout=None: FakeResponse(text=_cot_csv()))
    res = ice_cot.fetch_ice_cot("2026-10-16", venues=["NDEX"])
    assert res["NDEX"]["success"] and res["NDEX"]["rows_fetched"] == 5 * 2 * 3
    # idempotence: druhé stažení stejného týdne nezdvojí řádky
    ice_cot.fetch_ice_cot("2026-10-16", venues=["NDEX"])
    assert len(pd.read_parquet(tmp_path / "cot.parquet")) == 30
    from charts.cot import compute_financial_net_position
    kpi = compute_financial_net_position(pd.read_parquet(tmp_path / "cot.parquet"), "NDEX", "TFM")
    assert kpi["current_value"] == 0 and kpi["n_weeks"] == 1


def test_update_cot_rate_limited(monkeypatch):
    from core import updaters
    import data.ice_cot as ic
    monkeypatch.setattr(ic, "probe_rate_limit", lambda venue="IFEU": {"rate_limited": True, "retry_after_s": "60"})
    res = updaters.update_cot(pd.Timestamp("2026-10-24", tz="UTC"))
    assert res["skipped"] and str(res["report_date"]) == "2026-10-23"


# ── Energy-Charts ───────────────────────────────────────────────────────

def test_energy_charts_incremental(monkeypatch, tmp_path):
    from data import energy_charts as ec
    monkeypatch.setattr(ec, "DATA_PATH", str(tmp_path / "flat.parquet"))
    monkeypatch.setattr(ec, "PARTITIONED_ROOT", str(tmp_path / "ec"))
    calls = []

    def fake_get(path, params, pace=0):
        calls.append((path, dict(params)))
        start = pd.Timestamp(params.get("start", "2026-10-20"))
        if path.endswith("frequency"):
            ts = pd.date_range(start, periods=600, freq="1s", tz="UTC")
            return 200, {"unit": "Hz", "data": [{"timestamp": t.isoformat(), "values": {"freq": 50.0}} for t in ts]}
        ts = pd.date_range(pd.Timestamp(params.get("end", start)), periods=4, freq="15min", tz="UTC")
        series = params.get("production_type", "load")
        return 200, {"unit": "MW", "resolution": "PT15M", "series": [{"id": series, "name": series}],
                     "data": [{"timestamp": t.isoformat(), "values": {series: 1.0}} for t in ts]}
    monkeypatch.setattr(ec, "_get", fake_get)
    today = pd.Timestamp("2026-10-23").date()
    res = ec.update_energy_charts(countries=["cz"], today=today, log=lambda m: None)
    assert not res["failed"], res["failed"]
    combos = {c for c, _ in res["done"]}
    assert "public_power_forecast::cz:wind" not in combos           # vyřazeno
    assert "frequency::DE-Freiburg" in combos
    freq = pd.read_parquet(tmp_path / "flat.parquet")
    f = freq[freq["endpoint"] == "frequency"]
    assert set(f["resolution"]) == {"PT5M"} and len(f) == 2 * 3      # 600 s → 2 buckets × 3 chunky
    # druhý běh navazuje od konce dat (overlap), ne od začátku
    calls.clear()
    ec.update_energy_charts(countries=["cz"], endpoints=["public_power"], today=today, log=lambda m: None)
    assert calls[0][1]["start"] == "2026-10-21"       # konec dat 23.10. − 2 dny překryvu


def test_energy_charts_get_retries(monkeypatch):
    from data import energy_charts as ec
    monkeypatch.setattr(ec.time, "sleep", lambda s: None)
    n = {"i": 0}

    def flaky(url, params=None, timeout=None):
        n["i"] += 1
        if n["i"] < 3:
            raise requests.ConnectionError("RemoteDisconnected")
        return FakeResponse(json_data={"data": []}, text="{}")
    monkeypatch.setattr(ec.SESSION, "get", flaky)
    status, body = ec._get("/v2/public_power", {"country": "cz"}, pace=0)
    assert status == 200 and n["i"] == 3
    n["i"] = -10
    with pytest.raises(requests.ConnectionError):
        ec._get("/v2/public_power", {"country": "cz"}, pace=0)


# ── ENTSO-E updaters (hydro, DAP Europe, výroba, odstávky) ─────────────

class _FakeEntsoeHist(_FakeEntsoe):
    def query_aggregate_water_reservoirs_and_hydro_storage(self, country_code, start, end):
        return pd.Series([1000.0, 1100.0], index=pd.date_range("2026-10-05", periods=2, freq="7D", tz="UTC"))

    def query_day_ahead_prices(self, code, start, end):
        idx = pd.date_range(start, end, freq="1h", inclusive="left")
        return pd.Series(50.0, index=idx)

    def query_unavailability_of_generation_units(self, country, start, end, docstatus=None):
        return pd.DataFrame({"production_resource_name": ["A"], "start": [start], "end": [end],
                             "revision": [1], "docstatus": ["Active"], "nominal_power": ["100"],
                             "avail_qty": ["0"], "plant_type": ["Nuclear"], "businesstype": ["Planned"]})


def test_entsoe_updaters(monkeypatch, tmp_path):
    from core import updaters
    monkeypatch.setattr(updaters, "_entsoe_client", lambda timeout=False: _FakeEntsoeHist())
    monkeypatch.setattr(updaters, "HYDRO_DIR", str(tmp_path / "hydro"))
    monkeypatch.setattr(updaters, "DAP_DIR", str(tmp_path / "dap"))
    monkeypatch.setattr(updaters, "OUTAGES_PATH", str(tmp_path / "outages.parquet"))
    monkeypatch.setattr(updaters, "OUTAGES_SNAPSHOTS_DIR", str(tmp_path / "snap"))
    monkeypatch.setattr(updaters, "history_path", lambda *p: str(tmp_path.joinpath(*p)))
    monkeypatch.setattr(updaters, "HISTORY_START", pd.Timestamp.now().date() - pd.Timedelta(days=2))
    monkeypatch.setattr(updaters.time, "sleep", lambda s: None)
    assert updaters.update_hydro()["rows"] > 0
    assert updaters.update_dap_europe()["rows"] > 0
    assert updaters.update_generation("CZ")["rows"] > 0
    assert updaters.update_outages("CZ")["rows"] == 1
    updaters.update_outages("CZ")                                   # idempotentní
    assert len(pd.read_parquet(tmp_path / "outages.parquet")) == 1
    assert len(list((tmp_path / "snap").glob("*.parquet"))) == 1


# ── update_all.py ───────────────────────────────────────────────────────

def test_update_all_resolve_and_exit_codes(monkeypatch, tmp_path):
    from scripts import update_all
    assert "cot" not in update_all.resolve([], [])
    assert update_all.resolve(["cot"], []) == ["cot"]
    gas = update_all.resolve(["gas"], ["lng"])
    assert "lng" not in gas and "entsog" in gas and "hydro" not in gas
    with pytest.raises(ValueError):
        update_all.resolve(["nope"], [])
    assert update_all.main(["--only", "nope"]) == update_all.EXIT_USAGE

    from core import settings
    monkeypatch.setattr(settings, "history_root", lambda: tmp_path)
    monkeypatch.setattr(update_all, "_tasks", lambda: {
        "gie": ("ok", lambda: {"errors": 0}),
        "hydro": ("warn", lambda: {"errors": 2}),
        "cot": ("fail", lambda: (_ for _ in ()).throw(RuntimeError("x"))),
    })
    assert update_all.main(["--only", "gie,hydro"]) == update_all.EXIT_OK
    assert update_all.main(["--only", "gie,cot"]) == update_all.EXIT_FAILED
    lock = update_all._Lock(tmp_path / ".update.lock")
    assert lock.acquire()
    try:
        assert update_all.main(["--only", "gie"]) == update_all.EXIT_LOCKED
    finally:
        lock.release()
