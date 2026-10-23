"""Přehled dne (core/overview.py + web/overview.py) a záznam běhů."""
import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from core import overview as ov
from tests.conftest import HAS_REPO_DATA


@pytest.fixture
def hist(tmp_path, monkeypatch):
    import core.settings as cs
    monkeypatch.setattr(ov, "history_root", lambda: tmp_path)
    import core.runlog as rl
    monkeypatch.setattr(rl, "history_root", lambda: tmp_path)
    return tmp_path


def test_merge_events_keeps_first_seen():
    now = pd.Timestamp("2026-10-23 12:00", tz="UTC")
    old = [{"id": "a", "t": "2026-10-20T10:00:00+00:00"}, {"id": "x", "t": "2026-09-01T00:00:00+00:00"}]
    new = [{"id": "a", "t": "2026-10-23T11:00:00+00:00"}, {"id": "b", "t": "2026-10-23T11:30:00+00:00"}]
    ev = ov.merge_events(old, new, now)
    assert [e["id"] for e in ev] == ["b", "a"] and ev[1]["t"].startswith("2026-10-20")


def test_build_overview_and_live_merge(hist, monkeypatch):
    data = ov.build_overview()
    keys = {t["key"] for t in data["tiles"]}
    assert {"da_CZ_base", "stor_EU", "ceps_imb", "cot_ttf", "hydro_no", "temp_CZ"} <= keys
    assert (hist / "overview.json").exists() and (hist / "overview_events.json").exists()
    if HAS_REPO_DATA:
        t = next(t for t in data["tiles"] if t["key"] == "stor_EU")
        assert t["value"] is not None and len(t["spark"]) > 5 and t["href"].startswith("/plyn")
        assert any(e["cat"] == "GASSCO UMM" for e in data["events"])
    # live běh přepíše jen živé dlaždice, ostatní zůstanou
    monkeypatch.setattr(ov, "_ceps_tile", lambda: [ov.tile("ceps_imb", "power", "ČEPS odchylka", 42, "MW")])
    ov._BUILDERS[:] = [(ov._ceps_tile if fn.__name__ == "_ceps_tile" else fn, live) for fn, live in ov._BUILDERS]
    d2 = ov.build_overview(live_only=True)
    assert {t["key"] for t in d2["tiles"]} == keys
    assert next(t for t in d2["tiles"] if t["key"] == "ceps_imb")["value"] == 42


def test_builder_error_isolated(hist, monkeypatch):
    def boom():
        raise RuntimeError("x")
    monkeypatch.setattr(ov, "_BUILDERS", [(boom, False)] + list(ov._BUILDERS))
    d = ov.build_overview()
    assert d["errors"] and d["tiles"]


def test_homepage_renders(hist):
    from web.app import app
    cl = TestClient(app)
    r = cl.get("/")
    assert r.status_code == 200 and "Přehled dne" in r.text and "Zprávy a události" in r.text
    assert 'href="/plyn?tab=zasobniky"' in r.text
    f = cl.get("/frag-overview")
    assert f.status_code == 200 and "tile" in f.text


def test_runlog(hist):
    from core import runlog
    runlog.record([("energinet", "FAIL", 3.2, "UpdateError: 429"), ("cot", "OK", 1.0, "")])
    runlog.record([("energinet", "WARN", 2.0, "1 pokračuje příští běh")])
    s = runlog.summary()
    assert s[0]["task"] == "energinet" and s[0]["status"] == "WARN" and s[0]["fails"] == 1
    assert json.loads((hist / ".runs.json").read_text())["cot"][0]["status"] == "OK"


def test_status_page(hist):
    from core import runlog
    from web.app import app
    runlog.record([("energinet", "FAIL", 3.2, "UpdateError: Energinet: všechny datasety selhaly")])
    cl = TestClient(app)
    r = cl.get("/stav")
    assert r.status_code == 200 and "Energinet" in r.text and "všechny datasety selhaly" in r.text
    assert "Datasety" in r.text


def test_runs_status_script(hist, capsys):
    from core import runlog
    from scripts import runs_status
    runlog.record([("cot", "OK", 1.0, ""), ("weather", "FAIL", 2.0, "QuotaExceeded")])
    assert runs_status.main(["--errors"]) == 1
    out = capsys.readouterr().out
    assert "weather" in out and "QuotaExceeded" in out
