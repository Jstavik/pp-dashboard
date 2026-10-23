"""Export grafů (CSV/XLSX z Plotly figury), Energy-Charts čtení/agregace
a nasazení (unity, skripty)."""
import io
import os
import subprocess
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import pytest
from fastapi.testclient import TestClient
from plotly.subplots import make_subplots

from web import export
from web.app import app

ROOT = Path(__file__).resolve().parent.parent
client = TestClient(app)


# ── figure → tabulka ────────────────────────────────────────────────────

def test_figure_wide_timeseries():
    idx = pd.date_range("2026-10-23", periods=3, freq="h", tz="Europe/Prague")
    fig = go.Figure([go.Scatter(x=idx, y=[1, 2, 3], name="A"), go.Bar(x=idx, y=[4, 5, 6], name="B")])
    fig.update_layout(xaxis_title="Čas")
    w = export.figure_wide(fig)
    assert list(w.columns) == ["A", "B"] and w.index.name == "Čas" and len(w) == 3
    csv = export.to_csv_bytes(fig).decode("utf-8-sig")
    assert csv.splitlines()[0] == "Čas,A,B"
    assert csv.splitlines()[1].startswith("2026-10-23 00:00:00+02:00")     # místní čas s offsetem


def test_figure_duplicate_names_in_subplots():
    fig = make_subplots(rows=2, cols=1)
    fig.add_trace(go.Scatter(x=[1, 2], y=[1, 2], name="skutečnost"), row=1, col=1)
    fig.add_trace(go.Scatter(x=[1, 2], y=[3, 4], name="skutečnost"), row=2, col=1)
    assert list(export.figure_wide(fig).columns) == ["skutečnost", "skutečnost (y2)"]


def test_figure_long_fallbacks():
    pie = go.Figure(go.Pie(labels=["a", "b"], values=[1, 2]))
    assert export.figure_wide(pie) is None
    long = export.figure_long(pie)
    assert list(long["labels"]) == ["a", "b"] and list(long["values"]) == [1, 2]
    heat = go.Figure(go.Heatmap(z=[[1, 2], [3, 4]], x=["x1", "x2"], y=["y1", "y2"]))
    assert len(export.figure_long(heat)) == 4
    dup = go.Figure(go.Scatter(x=[1, 1, 2], y=[1, 2, 3]))      # x se opakuje → jen long
    assert export.figure_wide(dup) is None and len(export.figure_long(dup)) == 3
    table = go.Figure(go.Table(header=dict(values=["<b>Země</b>", "Hodnota"]), cells=dict(values=[["CZ", "DE"], [1, 2]])))
    t = export.figure_wide(table)
    assert list(t.columns) == ["Země", "Hodnota"] and len(t) == 2


def test_xlsx_sheets():
    fig = go.Figure([go.Scatter(x=pd.date_range("2026-01-01", periods=2, tz="UTC"), y=[1, 2], name="A")],
                    layout=dict(title="Titulek <span>x</span>"))
    sheets = pd.read_excel(io.BytesIO(export.to_xlsx_bytes(fig, {"graf": "a/b/c"})), sheet_name=None)
    assert set(sheets) == {"data", "long", "info"}
    info = dict(zip(sheets["info"]["položka"], sheets["info"]["hodnota"]))
    assert info["graf"] == "a/b/c" and info["titulek"].startswith("Titulek")


def test_chart_data_endpoint():
    r = client.get("/api/chart-data/energy-charts/instalovany/bars?fmt=csv")
    if r.status_code == 404:
        pytest.skip("Energy-Charts data v repu nejsou")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    r = client.get("/api/chart-data/energy-charts/instalovany/bars?fmt=xlsx")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert client.get("/api/chart-data/energy-charts/instalovany/bars?fmt=pdf").status_code == 404
    assert client.get("/api/chart-data/nic/nic/nic?fmt=csv").status_code == 404


def test_export_toolbar_in_js():
    js = (ROOT / "web/static/app.js").read_text()
    assert "/api/chart-data/" in js and "Plotly.toImage" in js and "themed(el._ppFig, true)" in js


# ── Energy-Charts ───────────────────────────────────────────────────────

def test_parse_timestamps_offsets():
    from data.energy_charts import parse_timestamps
    vals = ["2026-09-01T02:00:00+02:00", "2026-01-01T00:00:00+01:00", "2026-09-25T04:45:00+00:00",
            "2026-09-25T04:45:00", "2020-03-01T00:00:00-05:30"]
    got = parse_timestamps(vals)
    exp = pd.to_datetime([vals[0], vals[1], vals[2], "2026-09-25T04:45:00Z", vals[4]], utc=True, format="ISO8601")
    assert list(got) == list(exp)


def test_energy_charts_wide_aggregation(monkeypatch, tmp_path):
    from data import energy_charts as ec
    monkeypatch.setattr(ec, "PARTITIONED_ROOT", str(tmp_path))
    ts = pd.date_range("2026-10-24 22:00", "2026-10-26 02:00", freq="15min", tz="Europe/Prague")  # přes změnu času
    rows = [{"endpoint": "public_power", "geo_key": "cz", "geo_type": "country", "series_id": "solar",
             "series_name": "Solar", "timestamp": t.isoformat(), "value": 4.0, "unit": "MW", "resolution": "PT15M"}
            for t in ts]
    rows += [dict(r, geo_key="de", value=99.0) for r in rows[:4]]
    ec._write_rows("public_power", rows)
    lo, hi = pd.Timestamp("2026-10-25", tz="Europe/Prague"), pd.Timestamp("2026-10-26", tz="Europe/Prague")
    native = ec.load_energy_charts_wide("public_power", "cz", lo, hi)
    assert len(native) == 25 * 4 and (native["solar"] == 4.0).all()     # 25h den (konec letního času)
    hourly = ec.load_energy_charts_wide("public_power", "cz", lo, hi, resolution="1h")
    assert len(hourly) == 25
    daily = ec.load_energy_charts_wide("public_power", "cz", lo, hi, resolution="1D")
    assert list(daily.index) == [lo] and daily["solar"].iloc[0] == 4.0
    assert ec.load_energy_charts_wide("public_power", "pl", lo, hi).empty


def test_crossborder_summary_sign():
    from charts.energy_charts import crossborder_summary
    idx = pd.date_range("2026-10-01", periods=4, freq="15min", tz="Europe/Prague")
    w = pd.DataFrame({"germany": [1.0, 1.0, -1.0, -1.0], "sum": [1.0, 1.0, -1.0, -1.0]}, index=idx)
    s = crossborder_summary(w).set_index("soused")
    assert s.loc["Německo", "import_GWh"] == 0.5 and s.loc["Německo", "export_GWh"] == 0.5
    assert s.index[-1] == "Saldo"


# ── nasazení ────────────────────────────────────────────────────────────

UNITS = ROOT / "deploy" / "systemd-user"


def test_user_units():
    names = sorted(p.name for p in UNITS.iterdir())
    assert names == ["ppdash-live.service", "ppdash-live.timer", "ppdash-update-cot.service",
                     "ppdash-update-cot.timer", "ppdash-update-entsoe.service", "ppdash-update-entsoe.timer",
                     "ppdash-update-weather.service", "ppdash-update-weather.timer",
                     "ppdash-update.service", "ppdash-update.timer",
                     "ppdash-web.service"]
    for p in UNITS.iterdir():
        text = p.read_text()
        assert "User=" not in text and "/var/lib/ppdash" not in text and "/opt/ppdash" not in text, p.name
        if p.suffix == ".service":
            assert "@REPO@" in text and "@DATA@" in text and "%h/.config/ppdash/env" in text
        if p.suffix == ".timer":
            assert "WantedBy=timers.target" in text
    assert "--only live -q" in (UNITS / "ppdash-live.service").read_text()
    assert "OnCalendar=*-*-* *:*:20" in (UNITS / "ppdash-live.timer").read_text()
    assert "Europe/Prague" in (UNITS / "ppdash-update.timer").read_text()
    web = (UNITS / "ppdash-web.service").read_text()
    assert "--host 0.0.0.0 --port 8501" in web and "--forwarded-allow-ips 127.0.0.1,192.168.68.124,192.168.68.103" in web
    cot = (UNITS / "ppdash-update-cot.timer").read_text()
    assert "OnCalendar=Wed *-*-* 14:00:00 Europe/Prague" in cot and "Thu,Sat" in cot
    assert "WantedBy=default.target" in (UNITS / "ppdash-web.service").read_text()


@pytest.mark.parametrize("script", ["install_user.sh", "status.sh"])
def test_deploy_scripts_syntax(script):
    path = ROOT / "deploy" / script
    assert os.access(path, os.X_OK)
    assert subprocess.run(["bash", "-n", str(path)]).returncode == 0


def test_env_example_has_no_deltagreen():
    text = (ROOT / "deploy" / "env.example").read_text()
    assert "DELTAGREEN" not in text and "ENTSOE_TOKEN=" in text and "GIE_API_KEY=" in text


def test_data_status_cli():
    out = subprocess.run([str(ROOT / ".venv/bin/python") if (ROOT / ".venv/bin/python").exists() else "python3",
                          "scripts/data_status.py", "--dead"], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0 and "hu_generation" in out.stdout.split()
