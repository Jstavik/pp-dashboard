"""Jednotný vzhled grafů: barvy entit, pražský čas, prázdné stavy, český formát."""
import pandas as pd
import plotly.graph_objects as go

from core.palette import entity_color
from web.consistency import apply_entity_colors, is_empty, localize_times
from web.util import fmt_value


def test_entity_color_aliases():
    assert entity_color("CZ") == entity_color("Czechia") == entity_color("Česko")
    assert entity_color("DE_LU") == entity_color("Germany") == entity_color("DE")
    assert entity_color("2026") is None


def test_colors_follow_entity_not_order():
    f1 = go.Figure([go.Scatter(x=[1, 2], y=[1, 2], name="CZ", line=dict(color="#000000")),
                    go.Scatter(x=[1, 2], y=[1, 2], name="AT")])
    f2 = go.Figure([go.Scatter(x=[1, 2], y=[1, 2], name="AT")])
    apply_entity_colors(f1), apply_entity_colors(f2)
    assert f1.data[0].line.color == entity_color("CZ")
    assert f1.data[1].line.color == f2.data[0].line.color == entity_color("AT")


def test_localize_times_to_prague():
    idx = pd.date_range("2026-10-22 22:00", periods=3, freq="h", tz="UTC")
    f = go.Figure(go.Scatter(x=list(idx), y=[1, 2, 3]))
    localize_times(f)
    assert str(pd.Timestamp(f.data[0].x[0])) == "2026-10-23 00:00:00"


def test_empty_detection():
    assert is_empty(go.Figure())
    assert is_empty(go.Figure(go.Scatter(x=[], y=[])))
    assert not is_empty(go.Figure(go.Scatter(x=[1], y=[1])))


def test_czech_number_format():
    assert fmt_value(1234.5, ",.1f") == "1 234,5"
    assert fmt_value(-3.25, "+.2f") == "−3,25"


def test_navigation_groups():
    from fastapi.testclient import TestClient
    from web.app import app
    r = TestClient(app).get("/plyn")
    for label in ("Elektřina", "Plyn", "Trh EU", "Nordic", "Počasí", "Průzkumník", "Reporty"):
        assert label in r.text
    assert "/reporty?tab=tydenni" in r.text and 'href="/stav"' in r.text
