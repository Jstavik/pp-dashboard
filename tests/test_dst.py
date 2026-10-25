"""Změna času (CEST↔CET): okna v pražském čase po kalendářních dnech
(pd.DateOffset), ne po 24 h (pd.Timedelta) — a úplnost dne v DAP mapě
podle délky dne v Praze (23/24/25 h) a rozlišení zóny."""
import json
import logging
import re
from pathlib import Path

import pandas as pd
import pytest

TZ = "Europe/Prague"
ROOT = Path(__file__).resolve().parent.parent


# ── (a) konec okna cen v den změny času ─────────────────────────────────

@pytest.mark.parametrize("now, end", [
    ("2026-10-25 15:00", "2026-10-27 00:00+01:00"),   # podzim: 25.10. má 25 h
    ("2027-03-28 15:00", "2027-03-30 00:00+02:00"),   # jaro: 28.3. má 23 h
])
def test_price_window_end_dst(monkeypatch, now, end):
    from data import entsoe_history as eh
    monkeypatch.setattr(eh, "_now", lambda: pd.Timestamp(now, tz=TZ))
    (item,) = eh.price_items(["CZ"])
    assert item.end == pd.Timestamp(end)
    assert item.end.hour == 0


@pytest.mark.parametrize("now, end", [
    ("2026-10-25 15:00", "2026-10-27 00:00+01:00"),
    ("2027-03-28 15:00", "2027-03-30 00:00+02:00"),
])
def test_dap_europe_window_end_dst(dap, now, end):
    c = FakeDap({"CZ": _series("2026-10-24", 3, 15)})
    dap["run"](c, now)
    assert c.calls[0][2] == pd.Timestamp(end)


# ── (b) update_dap_europe: úplnost dne podle rozlišení ──────────────────

def _series(first_day, days, step_min, drop_last=0):
    a = pd.Timestamp(first_day, tz=TZ)
    idx = pd.date_range(a, a + pd.DateOffset(days=days), freq=f"{step_min}min", inclusive="left")
    s = pd.Series(range(len(idx)), index=idx, dtype=float)
    return s.iloc[:len(s) - drop_last] if drop_last else s


class FakeDap:
    def __init__(self, data):
        self.data, self.calls = data, []

    def query_day_ahead_prices(self, code, start, end):
        self.calls.append((code, start, end))
        return self.data[code].tz_convert("UTC")


@pytest.fixture
def dap(monkeypatch, tmp_path):
    from core import updaters
    from data import dap_europe
    res_file = tmp_path / "res.json"
    monkeypatch.setattr(updaters, "DAP_DIR", str(tmp_path / "dap"))
    monkeypatch.setattr(dap_europe, "RESOLUTION_FILE", str(res_file))

    def run(client, now, countries=None):
        monkeypatch.setattr(dap_europe, "DAP_COUNTRIES", countries or {cc: cc for cc in client.data})
        monkeypatch.setattr(updaters, "_entsoe_client", lambda timeout=False: client)
        return updaters.update_dap_europe(now=pd.Timestamp(now, tz=TZ))

    def rows():
        from data.partitioned_store import read_partitioned
        df = read_partitioned(str(tmp_path / "dap"), fmt="parquet")
        return {(r.cc, str(pd.Timestamp(r.date).date())) for r in df.itertuples()}

    return {"run": run, "rows": rows, "res_file": res_file}


def test_dap_15min_fall_day_complete(dap):
    # 24.10. 96 bodů, 25.10. 100 bodů (25 h), 26.10. 96 bodů
    s = _series("2026-10-24", 3, 15)
    assert s.groupby(s.index.date).size().tolist() == [96, 100, 96]
    dap["run"](FakeDap({"CZ": s}), "2026-10-26 15:00")
    assert dap["rows"]() == {("CZ", "2026-10-25"), ("CZ", "2026-10-26")}
    assert json.loads(dap["res_file"].read_text()) == {"CZ": "15min"}


def test_dap_15min_incomplete_day_skipped(dap, caplog):
    # 25.10. jen 93/100 bodů (konec okna ve 23:00 místo o půlnoci)
    s = _series("2026-10-24", 2, 15, drop_last=7)
    assert s.groupby(s.index.date).size().tolist() == [96, 93]
    t = _series("2026-10-24", 2, 15)
    with caplog.at_level(logging.WARNING, logger="ppdash.update"):
        dap["run"](FakeDap({"CZ": s, "DE": t}), "2026-10-25 15:00")
    assert dap["rows"]() == {("DE", "2026-10-25")}
    assert "neúplné dny" in caplog.text and "CZ 2026-10-25 (93/100)" in caplog.text


def test_dap_hourly_fall_day_complete(dap):
    s = _series("2026-10-24", 3, 60)
    assert s.groupby(s.index.date).size().tolist() == [24, 25, 24]
    dap["run"](FakeDap({"CH": s}), "2026-10-26 15:00")
    assert dap["rows"]() == {("CH", "2026-10-25"), ("CH", "2026-10-26")}


def test_dap_hourly_spring_day_complete(dap):
    s = _series("2027-03-27", 2, 60)
    assert s.groupby(s.index.date).size().tolist() == [24, 23]
    dap["run"](FakeDap({"CH": s}), "2027-03-28 15:00")
    assert dap["rows"]() == {("CH", "2027-03-28")}


def test_dap_resolution_change_warns(dap, caplog):
    dap["res_file"].write_text(json.dumps({"CZ": "60min", "CH": "60min"}))
    c = FakeDap({"CZ": _series("2026-10-24", 3, 15), "CH": _series("2026-10-24", 3, 60)})
    with caplog.at_level(logging.WARNING, logger="ppdash.update"):
        dap["run"](c, "2026-10-26 15:00")
    assert "DAP CZ: ZMĚNA ROZLIŠENÍ 60min → 15min" in caplog.text
    assert "DAP CH" not in caplog.text
    assert json.loads(dap["res_file"].read_text()) == {"CH": "60min", "CZ": "15min"}


def test_dap_retry_then_success(dap, monkeypatch):
    from core import updaters

    class Flaky(FakeDap):
        def query_day_ahead_prices(self, code, start, end):
            if len(self.calls) < 2:
                self.calls.append(None)
                raise ConnectionError("timeout")
            return super().query_day_ahead_prices(code, start, end)

    sleeps = []
    monkeypatch.setattr(updaters.time, "sleep", sleeps.append)
    out = dap["run"](Flaky({"CZ": _series("2026-10-24", 3, 15)}), "2026-10-26 15:00")
    assert out["errors"] == 0 and sleeps == [5, 10]
    assert dap["rows"]() == {("CZ", "2026-10-25"), ("CZ", "2026-10-26")}


# ── (c) žádné normalize() ± pd.Timedelta(days=…) v pražském čase ────────

# Výjimka: půlnoc v UTC (tz="UTC") — UTC nemá změnu času, 24 h = den.
_ALLOWED = re.compile(r'tz="UTC"\)\.normalize\(\)')


def test_no_normalize_plus_timedelta_days():
    pat = re.compile(r"normalize\(\)\s*[-+]\s*pd\.Timedelta\(days=")
    bad = []
    for d in ("core", "data", "web"):
        for f in (ROOT / d).rglob("*.py"):
            for i, line in enumerate(f.read_text().splitlines(), 1):
                if pat.search(line) and not _ALLOWED.search(line):
                    bad.append(f"{f.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not bad, "pd.DateOffset(days=…) místo pd.Timedelta:\n" + "\n".join(bad)
