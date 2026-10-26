"""Analýza toků plynu (/plyn/analyza): core/gas_analysis.py, web/tabs/gas_analysis.py,
backfill kapacit/přerušení (data/entsog_operational.py) — syntetická data."""
from datetime import date

import pandas as pd
import pytest

D0 = date(2026, 8, 1)
DAYS = pd.date_range("2026-08-01", "2026-09-30", freq="D")


def _flows():
    rows = []
    for d in DAYS:
        ts = pd.Timestamp(d).tz_localize("Europe/Prague")
        for dir_, key, lbl, pts, v in [
            ("entry", "TransmissionDE-THE-----", "DE THE BZ", "VIP Brandov|VIP Waidhaus", 100.0),
            ("entry", "LNG Terminals", "LNG", "Some LNG", 20.0),
            ("exit", "TransmissionSK---------", "Slovakia", "Lanžhot", 30.0),
            ("exit", "Distribution", "Distribution", "Aggregated Distribution", 50.0 if d.month == 8 else 70.0),
        ]:
            rows.append({"indicator": "Physical Flow", "countryKey": "CZ", "countryLabel": "Czechia",
                         "directionKey": dir_, "adjacentSystemsKey": key, "adjacentSystemsLabel": lbl,
                         "pointsNames": pts, "unit": "kWh/d", "flowStatus": "Confirmed", "value_GWh": v, "date": ts})
    return pd.DataFrame(rows)


def _op(ind, frm, to, v, upd="2026-01-01T10:00:00+01:00", op="CZ-TSO-0001", point="VIP Brandov", d="entry"):
    return {"id": f"{ind}{frm}{to}{op}{upd}{d}", "indicator": ind, "periodType": "day",
            "periodFrom": f"{frm}T06:00:00+02:00", "periodTo": f"{to}T06:00:00+02:00", "operatorKey": op,
            "operatorLabel": op[:2] + " TSO", "pointKey": "ITP-1", "pointLabel": point, "directionKey": d,
            "unit": "kWh/d", "value": v * 1e6, "value_GWh": v, "lastUpdateDateTime": upd,
            "periodFrom_dt": pd.Timestamp(frm).date(), "periodTo_dt": pd.Timestamp(to).date(), "pointType": "x"}


@pytest.fixture
def ga(tmp_path, monkeypatch, no_cache):
    from core import gas_analysis as mod
    (tmp_path / "entsog_flows").mkdir()
    (tmp_path / "entsog_operational").mkdir()
    f = _flows()
    for m, g in f.groupby(f["date"].dt.strftime("%Y-%m")):
        g.to_parquet(tmp_path / "entsog_flows" / f"{m}.parquet", index=False)
    ops = []
    for d in DAYS:
        n = d.strftime("%Y-%m-%d")
        n1 = (d + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        ops.append(_op("Nomination", n, n1, 90.0))
        ops.append(_op("Physical Flow", n, n1, 80.0))
        ops.append(_op("Physical Flow", n, n1, 5.0, op="CZ-TSO-0002"))   # druhý operátor → součet
        ops.append(_op("Physical Flow", n, n1, 999.0, op="DE-TSO-0001"))  # jiná země → mimo
        ops.append(_op("GCV", n, n1, 0.0) | {"value": 11.0})
    # kapacita: dlouhé období (starší revize) + kratší novější revize; periodTo exkluzivní
    ops.append(_op("Firm Technical", "2025-01-01", "2027-01-01", 200.0, upd="2025-01-01T00:00:00+01:00"))
    ops.append(_op("Firm Technical", "2026-09-10", "2026-09-20", 150.0, upd="2026-05-01T00:00:00+02:00"))
    ops.append(_op("Firm Technical", "2025-01-01", "2027-01-01", 40.0, op="CZ-TSO-0002"))
    ops.append(_op("Planned interruption of firm capacity", "2026-09-05", "2026-09-08", 30.0))
    ops.append(_op("Planned interruption of firm capacity", "2026-09-05", "2026-09-08", 35.0,
                   upd="2026-06-01T00:00:00+02:00"))
    op = pd.DataFrame(ops)
    for m, g in op.groupby(op["periodFrom_dt"].map(lambda x: x.strftime("%Y-%m"))):
        g.to_parquet(tmp_path / "entsog_operational" / f"{m}.parquet", index=False)
    monkeypatch.setattr(mod, "history_root", lambda: tmp_path)
    return mod


def test_country_daily_signs_and_categories(ga):
    w = ga.country_daily("CZ", "both", "partner", D0, date(2026, 9, 30))
    assert set(w.columns) == {"Německo · vstup", "LNG terminály · vstup", "Slovensko · výstup",
                              "Spotřeba / distribuce · výstup"}
    assert w["Slovensko · výstup"].iloc[0] == -30 and w["Německo · vstup"].iloc[0] == 100
    assert w.sum(axis=1).iloc[0] == pytest.approx(40)            # bilance 120 − 80
    e = ga.country_daily("CZ", "exit", "adjacent", D0, date(2026, 9, 30))
    assert (e >= 0).all().all() and "Slovakia" in e.columns     # jeden směr = kladně
    only = ga.country_daily("CZ", "entry", "partner", D0, date(2026, 9, 30), ("LNG terminály",))
    assert list(only.columns) == ["LNG terminály"]


def test_aggregate_units_pivot_and_compare(ga):
    w = ga.country_daily("CZ", "exit", "partner", D0, date(2026, 9, 30))
    avg = ga.aggregate(w, "month", "gwhd")
    assert avg["Spotřeba / distribuce"].tolist() == [50, 70]
    tot = ga.aggregate(w, "month", "gwh")
    assert tot["Spotřeba / distribuce"].tolist() == [50 * 31, 70 * 30]
    mcm = ga.aggregate(w, "month", "mcmd", gcv=10.0)
    assert mcm["Spotřeba / distribuce"].iloc[0] == 5
    pv = ga.pivot_table(tot, "month", "gwh")
    assert list(pv.index) == ["2026-08", "2026-09", "Součet", "Průměr", "Min", "Max"]
    assert pv.loc["Součet", "Celkem"] == pytest.approx(80 * 31 + 100 * 30)
    cmp = ga.compare(ga.country_daily("CZ", "exit", "partner", date(2026, 9, 1), date(2026, 9, 30)),
                     ga.country_daily("CZ", "exit", "partner", D0, date(2026, 8, 31)), "gwhd")
    r = cmp.loc["Spotřeba / distribuce"]
    assert r["A"] == 70 and r["B"] == 50 and r["rozdíl"] == 20 and r["rozdíl %"] == pytest.approx(40)
    assert cmp.index[-1] == "Celkem"
    assert ga.period_label(pd.Timestamp("2026-09-07"), "week") == "2026-T37"


def test_seasonal(ga):
    tot = ga.country_total("CZ", "exit", D0, date(2026, 9, 30))
    m = ga.seasonal(tot, "month", "gwhd", 2026)
    assert m.set_index("x")["v"].to_dict() == {8: 80, 9: 100}
    d = ga.seasonal(tot, "day", "gwhd", 2026)
    assert d["x"].dt.year.unique().tolist() == [2000] and d["v"].iloc[-1] == 100


def test_point_flows_capacity_and_interruptions(ga):
    w = ga.point_daily("CZ", "entry", "VIP Brandov", D0, date(2026, 9, 30))
    assert w["Physical Flow"].iloc[0] == 85                     # 80 + 5, DE operátor mimo
    assert ga.best_flow(w) == "Physical Flow"
    ft = w["Firm Technical"]
    assert ft[pd.Timestamp("2026-09-09")] == 240                 # 200 + 40
    assert ft[pd.Timestamp("2026-09-10")] == 190                 # novější revize 150 + 40
    assert ft[pd.Timestamp("2026-09-19")] == 190
    assert ft[pd.Timestamp("2026-09-20")] == 240                 # periodTo exkluzivní
    ints = ga.point_periods("CZ", "entry", "VIP Brandov", ga.INT_IND, D0, date(2026, 9, 30))
    col = ints["Planned interruption of firm capacity"]
    assert col[pd.Timestamp("2026-09-05")] == 35 and pd.Timestamp("2026-09-08") not in col.index
    lst = ga.point_interruptions("CZ", "entry", "VIP Brandov", D0, date(2026, 12, 31))
    assert len(lst) == 1 and lst["v"].iloc[0] == 35 and pd.Timestamp(lst["to"].iloc[0]) == pd.Timestamp("2026-09-07")
    assert ga.point_gcv("CZ", "entry", "VIP Brandov", D0, date(2026, 9, 30)) == 11
    idx = ga.point_index()
    row = idx[(idx["country"] == "CZ") & (idx["point"] == "VIP Brandov")].iloc[0]
    assert row["adj"] == "Německo"


def test_page_and_charts(ga):
    from fastapi.testclient import TestClient
    from web.app import app
    c = TestClient(app)
    r = c.get("/plyn/analyza")
    assert r.status_code == 200 and "plyn-analyza" in r.text
    for q in ["mode=zeme&from=2026-08-01&to=2026-09-30", "mode=zeme&dir=entry&by=point&g=week&u=gwh",
              "mode=bod&cc=CZ&dir=entry&pt=VIP%20Brandov&from=2026-08-01&to=2026-09-30&u=mcmd"]:
        fr = c.get(f"/frag/plyn-analyza/analyza?{q}")
        assert fr.status_code == 200 and "alert-bad" not in fr.text, fr.text[:500]
        for ch in ("main", "season", "compare"):
            j = c.get(f"/api/chart/plyn-analyza/analyza/{ch}?{q}").json()
            assert "figure" in j, (ch, j)
        assert c.get(f"/api/csv/plyn_pivot?fmt=xlsx&{q}").status_code == 200
        assert c.get(f"/api/csv/plyn_srovnani?fmt=csv&{q}").status_code == 200
    j = c.get("/api/chart/plyn-analyza/analyza/int?mode=bod&cc=CZ&dir=entry&pt=VIP%20Brandov"
              "&from=2026-08-01&to=2026-09-30").json()
    assert "figure" in j


def test_backfill_periods_resumes(tmp_path, monkeypatch):
    from data import entsog_operational as eo
    monkeypatch.setattr(eo, "OPERATIONAL_DIR", str(tmp_path))
    calls = []

    def fake(frm, to, ind):
        calls.append((frm.strftime("%Y-%m"), ind))
        if frm.month == 2 and ind == "Firm Booked" and len(calls) < 10:
            raise RuntimeError("HTTP 503")
        return [{"id": f"{ind}{frm}", "indicator": ind, "periodType": "day", "periodFrom": f"{frm}T06:00:00+01:00",
                 "periodTo": f"{to}T06:00:00+01:00", "operatorKey": "CZ-TSO-0001", "pointLabel": "X",
                 "directionKey": "entry", "unit": "kWh/d", "value": 1e6, "isNA": 0,
                 "lastUpdateDateTime": "2026-01-01T00:00:00+01:00"}]
    monkeypatch.setattr(eo, "fetch_operational_month", fake)
    inds = ["Firm Technical", "Firm Booked"]
    eo.backfill_periods(inds, start=date(2026, 1, 1), end=date(2026, 3, 15))
    assert len(calls) == 6
    calls.clear()
    eo.backfill_periods(inds, start=date(2026, 1, 1), end=date(2026, 3, 15))
    # hotové měsíce se přeskočí; dotáhne se selhaný (02, Firm Booked) a aktuální měsíc vždy
    assert sorted(calls) == [("2026-02", "Firm Booked"), ("2026-03", "Firm Booked"), ("2026-03", "Firm Technical")]
    assert len(pd.read_parquet(tmp_path / "2026-01.parquet")) == 2
