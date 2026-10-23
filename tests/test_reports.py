"""Reporty (core/report.py, /reporty, /api/report/...)."""
import io

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from core import report
from tests.conftest import HAS_REPO_DATA
from web.app import app

client = TestClient(app)
DATE = "2026-10-22"


@pytest.mark.parametrize("kind", list(report.KINDS))
def test_report_page_and_charts(kind):
    r = client.get(f"/frag/reporty/{kind}?date={DATE}")
    assert r.status_code == 200 and "report-doc" in r.text and "Tisk" in r.text
    for sid, _t, _fn in report.SECTIONS[kind]:
        j = client.get(f"/api/chart/reporty/{kind}/{sid}?date={DATE}").json()
        assert "error" not in j, (sid, j)
    if HAS_REPO_DATA:
        j = client.get(f"/api/chart/reporty/{kind}/klicova?date={DATE}").json()
        assert j["figure"]["data"][0]["type"] == "table"


def test_report_xlsx_has_numbers():
    r = client.get(f"/api/report/denni.xlsx?date={DATE}")
    assert r.status_code == 200
    xl = pd.read_excel(io.BytesIO(r.content), sheet_name=None)
    assert "klíčová čísla" in xl and "info" in xl
    if HAS_REPO_DATA:
        kn = xl["klíčová čísla"]
        assert kn.set_index("ukazatel").loc["Zásobníky EU", "hodnota"] > 0
        assert any(k.startswith("Day-ahead") for k in xl)


def test_report_pdf_or_clear_message(monkeypatch):
    monkeypatch.setattr(report, "_render_pngs", lambda figs, width=1150: (_ for _ in ()).throw(
        report.PdfUnavailable("Chybí Chrome")))
    r = client.get(f"/api/report/tydenni.pdf?date={DATE}")
    assert r.status_code == 503 and "Chybí Chrome" in r.text


def test_report_pdf_assembly(monkeypatch):
    from PIL import Image

    def fake(figs, width=1150):
        out = []
        for _ in figs:
            b = io.BytesIO()
            Image.new("RGB", (1150, 500), "white").save(b, format="PNG")
            out.append(b.getvalue())
        return out
    monkeypatch.setattr(report, "_render_pngs", fake)
    r = client.get(f"/api/report/denni.pdf?date={DATE}")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"


def test_report_with_saved_view(tmp_path, monkeypatch):
    from web.tabs import explorer
    monkeypatch.setattr(explorer, "_views_path", lambda: tmp_path / "v.json")
    explorer.save_view("Ceny test", "ds=dap_europe")
    r = client.get(f"/frag/reporty/denni?date={DATE}&views=Ceny+test")
    assert "Pohled: Ceny test" in r.text and 'data-page="pruzkumnik"' in r.text
    x = client.get(f"/api/report/denni.xlsx?date={DATE}&views=Ceny+test")
    assert x.status_code == 200


def test_parse_date_future_clamped():
    assert report.parse_date("2999-01-01") <= pd.Timestamp.now().normalize()
    assert report.parse_date("nesmysl") == pd.Timestamp.now(tz="Europe/Prague").tz_localize(None).normalize()
