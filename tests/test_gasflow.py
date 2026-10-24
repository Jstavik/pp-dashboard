"""CEE flowchart plynu a kontrola entry/exit (core/gasflow.py, charts/flowchart.py)."""
import pandas as pd
import pytest

from data import partitioned_store as ps

DAY = pd.Timestamp("2026-10-20")


def _rows(day, rows, indicator="Physical Flow"):
    ts = pd.Timestamp(day).tz_localize("Europe/Prague")
    return pd.DataFrame([{"countryKey": c, "indicator": indicator, "countryLabel": c, "directionKey": d,
                          "adjacentSystemsKey": a, "adjacentSystemsLabel": a, "pointsNames": p, "unit": "kWh/d",
                          "flowStatus": "Provisionnal", "value_GWh": v, "date": ts} for c, d, a, p, v in rows])


BASE = [
    # DE → CZ: obě strany sedí
    ("DE", "exit", "TransmissionCZ---------", "Brandov", 160.0),
    ("CZ", "entry", "TransmissionDE-THE-----", "Brandov", 158.0),
    # CZ → SK: entry SK výrazně jiné než exit CZ
    ("CZ", "exit", "TransmissionSK---------", "Lanžhot", 40.0),
    ("SK", "entry", "TransmissionCZ---------", "Lanžhot", 20.0),
    # UA → SK (vnější bod), NO → DE
    ("SK", "entry", "Transmission", "Uzhhorod (UA) - Velké Kapušany (SK)", 70.0),
    ("DE", "entry", "Transmission", "Dornum / NETRA (OGE)|Emden (EPT1) (OGE)", 1500.0),
    # bilance CZ
    ("CZ", "exit", "Distribution", "Distribution", 100.0),
    ("CZ", "entry", "Storage", "Storage", 5.0),
    ("DE", "entry", "LNG Terminals", "Wilhelmshaven, LNG-Zone", 200.0),
    # CH jako countryKey = TAP → ignorovat
    ("CH", "entry", "Transmission", "Kipoi", 300.0),
]


@pytest.fixture
def flows(tmp_path, monkeypatch, no_cache):
    import data.entsog as en
    import data.entsog_operational as eo
    import core.gasflow as gf
    fdir, odir = tmp_path / "entsog_flows", tmp_path / "entsog_operational"
    fdir.mkdir()
    odir.mkdir()
    monkeypatch.setattr(en, "ENTSOG_FLOWS_DIR", str(fdir))
    monkeypatch.setattr(eo, "OPERATIONAL_DIR", str(odir))
    monkeypatch.setattr(gf, "storage_on", lambda day: {"CZ": {"full": 70.0, "twh": 30.0, "inj": 10.0, "wd": 40.0,
                                                               "net": -30.0, "day": pd.Timestamp(day).date()}})
    monkeypatch.setattr(gf, "lng_on", lambda day: {})
    prev = [(c, d, a, p, v * 0.5 if "Brandov" in p else v) for c, d, a, p, v in BASE]
    df = pd.concat([_rows(DAY, BASE), _rows(DAY - pd.Timedelta(days=1), prev),
                    _rows(DAY, [("NL", "exit", "TransmissionDE-THE-----", "x", 1000.0),
                                ("DE", "entry", "TransmissionNL---------", "x", 1000.0)], "Allocation")])
    df.to_parquet(fdir / "2026-10.parquet", index=False)
    op = pd.DataFrame([
        {"indicator": "Renomination", "operatorKey": ok, "pointKey": "ITP-00051", "pointLabel": "Lanžhot",
         "directionKey": d, "value_GWh": v, "periodFrom_dt": DAY.date(),
         "pointType": "Cross-Border Transmission IP within EU"}
        for ok, d, v in [("CZ-TSO-0001", "exit", 12.0), ("SK-TSO-0001", "entry", 12.0),
                         ("CZ-TSO-0001", "entry", 0.0), ("SK-TSO-0001", "exit", 30.0)]])
    op.to_parquet(odir / "2026-10.parquet", index=False)
    return gf


def test_partner_classification():
    from core.gasflow import partner_of
    assert partner_of("TransmissionDE-THE-----", "") == "DE"
    assert partner_of("TransmissionBE-LUX------", "") == "BE"
    assert partner_of("TransmissionPL-YAMAL---", "") == "PL"
    assert partner_of("Transmission", "Uzhhorod (UA) - Velké Kapušany (SK)|Budince") == "UA"
    assert partner_of("Transmission", "Kiskundorozsma-2 (HU) / Horgos (RS)|VIP Bereg (HU) / VIP Bereg (UA)") == "RS/UA"
    assert partner_of("Transmission", "Dornum / NETRA (GUD)|Emden (EPT1) (GUD)") == "NO"
    assert partner_of("Transmission", "Gela|Mazara del Vallo|Melendugno - IT / TAP") == "SV"
    assert partner_of("LNG Terminals", "") == "LNG"
    assert partner_of("Final Consumers", "") == "CONS"


def test_border_table_and_check(flows):
    gf = flows
    bt = gf.border_table(DAY).set_index(["from", "to"])
    assert bt.loc[("DE", "CZ"), "exit"] == 160 and bt.loc[("DE", "CZ"), "entry"] == 158
    assert bt.loc[("DE", "CZ"), "value"] == 158  # bere se entry přijímající země
    assert bt.loc[("UA", "SK"), "value"] == 70 and pd.isna(bt.loc[("UA", "SK"), "exit"])
    assert ("CH", "CH") not in bt.index and not any("CH" == f for f, _ in bt.index)
    chk = gf.check_borders(DAY, rel_pct=5, abs_gwh=5).set_index(["from", "to"])
    assert not chk.loc[("DE", "CZ"), "flag"]       # 2 GWh/d, 1,25 % → pod prahem
    assert chk.loc[("CZ", "SK"), "flag"]           # 20 GWh/d, 50 %
    assert chk.loc[("CZ", "SK"), "diff"] == -20
    # absolutní práh nad rozdílem → bez odchylky
    assert not gf.check_borders(DAY, rel_pct=5, abs_gwh=25).set_index(["from", "to"]).loc[("CZ", "SK"), "flag"]
    assert ("UA", "SK") not in chk.index           # jen jedna strana → nekontroluje se


def test_point_check_and_mismatches(flows):
    gf = flows
    pt = gf.check_points(DAY, rel_pct=5, abs_gwh=5)
    row = pt[(pt["from"] == "SK") & (pt["to"] == "CZ")].iloc[0]
    assert row["flag"] and row["exit"] == 30 and row["entry"] == 0 and row["point"] == "Lanžhot"
    assert not pt[(pt["from"] == "CZ") & (pt["to"] == "SK")]["flag"].any()
    mm = gf.mismatches(DAY, 5, 5)
    assert set(zip(mm["level"], mm["from"], mm["to"])) == {("hranice", "CZ", "SK"), ("bod", "SK", "CZ")}
    assert mm["flag"].all()


def test_flowchart_and_svg(flows):
    from charts.flowchart import render_svg
    gf = flows
    fc = gf.flowchart(DAY, "d", "gwh", 5, 5)
    edges = {(e["from"], e["to"]): e for e in fc["edges"]}
    assert edges[("DE", "CZ")]["value"] == 158 and edges[("DE", "CZ")]["prev"] == 79
    assert edges[("DE", "CZ")]["change"] == 79
    assert edges[("CZ", "SK")]["checks"] and edges[("CZ", "SK")]["value"] == 20
    assert ("NO", "DE") in edges and ("UA", "SK") in edges
    assert fc["lng"]["DE"]["value"] == 200
    b = fc["nodes"]["CZ"]["balance"]
    # vstupy 158 − výstupy 40 + zásobníky (GIE těžba 30) = 148; hlášená spotřeba 100
    assert b["in"] == 158 and b["out"] == 40 and b["stor_wd"] == 30 and b["implied"] == 148 and b["reported"] == 100
    svg = render_svg(fc, "gwh")
    assert svg.startswith("<svg") and svg.endswith("</svg>") and "Toky plynu CEE — 20.10.2026" in svg
    assert "gf-warn" in svg and "exit CZ 40 vs. entry SK 20" in svg
    import xml.dom.minidom
    xml.dom.minidom.parseString(svg)  # validní XML (export SVG/PNG v prohlížeči)
    mcm = render_svg(gf.flowchart(DAY, "w", "mcm"), "mcm")
    assert "mcm/d" in mcm and "15,0" in mcm  # 158 GWh/d ≈ 15,0 mcm/d
    t = gf.flowchart_table(fc)
    assert {"tok", "LNG", "země"} <= set(t["typ"])


def test_thresholds_env(monkeypatch):
    from core.gasflow import thresholds
    monkeypatch.setenv("PPDASH_EE_REL_PCT", "10")
    monkeypatch.setenv("PPDASH_EE_ABS_GWH", "2.5")
    assert thresholds() == (10.0, 2.5)
    monkeypatch.setenv("PPDASH_EE_REL_PCT", "x")
    assert thresholds()[0] == 5.0


def test_events_and_report(flows, monkeypatch):
    from core import overview as ov
    from core import report as rp
    monkeypatch.setattr(flows, "available_days", lambda indicator="Physical Flow": [DAY.date()])
    evs = ov._gas_entry_exit_events()
    assert [e["id"] for e in evs] == ["ee:2026-10-20:CZ-SK"]
    assert "exit ≠ entry o −20 GWh/d" in evs[0]["title"] and evs[0]["href"].endswith("to=2026-10-20")
    fig = rp.fig_gas_mismatch(pd.Timestamp("2026-10-24"))
    assert fig is not None and "Lanžhot" in str(fig.data[0].cells.values)
    assert rp.fig_gas_mismatch(pd.Timestamp("2026-01-01")) is None


def test_web_flowchart(flows, monkeypatch):
    from fastapi.testclient import TestClient
    from web.app import app
    monkeypatch.setattr(flows, "available_days", lambda indicator="Physical Flow": [(DAY - pd.Timedelta(days=1)).date(),
                                                                                    DAY.date()])
    c = TestClient(app)
    r = c.get("/frag/plyn/mapa")
    assert r.status_code == 200 and '<svg xmlns="http://www.w3.org/2000/svg" class="gf"' in r.text
    assert "data-days" in r.text and "Nesrovnalosti entry/exit" in r.text and "Lanžhot" in r.text
    r = c.get("/frag/plyn/mapa?date=2026-10-19&cmp=w&unit=mcm")
    assert r.status_code == 200 and "19.10.2026" in r.text and "mcm/d" in r.text
    assert c.get("/frag/plyn/mapa?view=mapa").text.count('data-chart="map"') == 1
    r = c.get("/frag/plyn/entry-exit?from=2026-10-19&to=2026-10-20&rel=5&abs=5")
    assert r.status_code == 200 and "CZ → SK" in r.text and "Nesrovnalosti entry/exit (3)" in r.text  # 19. i 20. hranice + bod
    r = c.get("/api/csv/flowchart?date=2026-10-20")
    assert r.status_code == 200 and "čistý tok GWh/d" in r.text
    r = c.get("/api/csv/entry-exit?to=2026-10-20&fmt=xlsx")
    assert r.status_code == 200 and r.content[:2] == b"PK"
