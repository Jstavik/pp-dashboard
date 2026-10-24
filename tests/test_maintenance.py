"""Údržba historie (kompakce s ověřením, archiv snapshotů, ořez předpovědí,
velikosti), zámky úloh, ruční „Aktualizovat teď“ a frekvence timerů."""
import json
import os
import time

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from data import partitioned_store as ps

NOW = pd.Timestamp("2026-10-24 10:00", tz="UTC")


@pytest.fixture
def hist(tmp_path, monkeypatch):
    import core.jobs as jb
    import core.locks as lk
    import core.maintenance as mt
    import core.runlog as rl
    for mod in (mt, lk, jb, rl):
        monkeypatch.setattr(mod, "history_root", lambda: tmp_path)
    return tmp_path


def _prices(zone_dir, start, end, freq="h"):
    t = pd.date_range(start, end, freq=freq, tz="UTC", inclusive="left")
    df = pd.DataFrame({"time": t, "zone": os.path.basename(zone_dir), "price": range(len(t))})
    ps.upsert_partitioned(df, zone_dir, "time", ["time"], fmt="parquet")
    return df


# ── kompakce ────────────────────────────────────────────────────────────

def test_compaction_year_files_lossless_and_readers(hist):
    from core.maintenance import compact_dataset
    zdir = str(hist / "entsoe_prices" / "CZ")
    full = _prices(zdir, "2024-01-01", "2026-03-01")
    n_before = len(os.listdir(zdir))
    r = compact_dataset("entsoe_prices", "year", NOW)
    names = sorted(os.listdir(zdir))
    assert names == ["2024.parquet", "2025.parquet", "2026-01.parquet", "2026-02.parquet"]
    assert r["groups"] == 2 and r["files_removed"] == n_before - 4 and r["dups"] == 0
    back = ps.read_partitioned(zdir)
    assert ps.content_checksum(back) == ps.content_checksum(full)
    # okno čte jen požadované měsíce i z ročního souboru (stejně jako před kompakcí)
    w = ps.read_partitioned(zdir, date_from="2024-03-10", date_to="2024-04-02")
    t = pd.to_datetime(w["time"], utc=True)
    assert t.min() == pd.Timestamp("2024-03-01", tz="UTC") and t.max() < pd.Timestamp("2024-05-01", tz="UTC")
    w2 = ps.read_partitioned(zdir, date_from="2024-03-10", date_to="2024-04-02", columns=["price"])
    assert list(w2.columns) == ["price"] and len(w2) == len(w)
    assert ps.last_date_partitioned(zdir, "time") == pd.Timestamp("2026-02-28 23:00", tz="UTC")
    # druhý běh nic nedělá
    assert compact_dataset("entsoe_prices", "year", NOW)["groups"] == 0


def test_upsert_revision_goes_into_year_file(hist):
    from core.maintenance import compact_dataset
    zdir = str(hist / "entsoe_prices" / "CZ")
    _prices(zdir, "2025-11-01", "2026-01-10")
    compact_dataset("entsoe_prices", "year", NOW)
    rev = pd.DataFrame({"time": pd.date_range("2025-12-31 20:00", periods=8, freq="h", tz="UTC"),
                        "zone": "CZ", "price": -1})
    ps.upsert_partitioned(rev, zdir, "time", ["time"], fmt="parquet")
    assert sorted(os.listdir(zdir)) == ["2025.parquet", "2026-01.parquet"]
    df = ps.read_partitioned(zdir)
    assert df["time"].duplicated().sum() == 0
    assert (df.set_index("time").loc[rev["time"], "price"] == -1).all()


def test_compaction_detects_mismatch_and_keeps_originals(hist, monkeypatch):
    from core import maintenance as mt
    zdir = str(hist / "entsoe_prices" / "CZ")
    _prices(zdir, "2024-01-01", "2024-04-01")
    real = mt._read
    monkeypatch.setattr(mt, "_read", lambda p, f: real(p, f).iloc[:-1] if ".tmp" in p else real(p, f))
    r = mt.compact_dataset("entsoe_prices", "year", NOW)
    assert r["groups"] == 0 and r["errors"] and "kontrolní součet" in r["errors"][0]
    assert sorted(os.listdir(zdir)) == ["2024-01.parquet", "2024-02.parquet", "2024-03.parquet"]


def test_compaction_recovers_exact_duplicates(hist):
    from core.maintenance import compact_dataset
    zdir = str(hist / "entsoe_prices" / "CZ")
    _prices(zdir, "2024-01-01", "2024-03-01")
    # přerušená kompakce: roční soubor vznikl, měsíce zůstaly
    pd.read_parquet(os.path.join(zdir, "2024-01.parquet")).to_parquet(os.path.join(zdir, "2024.parquet"), index=False)
    r = compact_dataset("entsoe_prices", "year", NOW)
    assert r["dups"] == 744 and os.listdir(zdir) == ["2024.parquet"]
    assert ps.read_partitioned(zdir)["time"].is_unique


def test_compaction_days_to_month_and_csv(hist):
    from core.maintenance import compact_dataset
    cdir = str(hist / "ceps" / "load")
    t = pd.date_range("2026-08-30", "2026-10-24", freq="30min", tz="UTC", inclusive="left")
    df = pd.DataFrame({"time": t, "series_id": "x", "series": "x", "value": 1.5})
    ps.upsert_partitioned(df, cdir, "time", ["time", "series_id"], period="day")
    compact_dataset("ceps", "month", NOW)
    names = sorted(os.listdir(cdir))
    assert names[:2] == ["2026-08.parquet", "2026-09.parquet"] and names[2].startswith("2026-10-")
    assert len(ps.read_partitioned(cdir)) == len(df)
    # dohánění výpadku do už zkompaktovaného měsíce
    late = df[df["time"] < "2026-09-02"].assign(value=2.0)
    ps.upsert_partitioned(late, cdir, "time", ["time", "series_id"], period="day")
    assert sorted(os.listdir(cdir))[:2] == ["2026-08.parquet", "2026-09.parquet"]
    back = ps.read_partitioned(cdir, date_from="2026-09-01", date_to="2026-09-01")
    assert len(back) == 30 * 48 and len(ps.read_partitioned(cdir)) == len(df)
    # CSV (GIE)
    gdir = hist / "gie_all_storage"
    g = pd.DataFrame({"gasDayStart": pd.date_range("2024-01-01", "2024-03-31", tz="UTC"), "country_code": "CZ",
                      "full": 50.5, "name": "Czech"})
    ps.upsert_partitioned(g, str(gdir), "gasDayStart", ["gasDayStart", "country_code"], fmt="csv")
    r = compact_dataset("gie_all_storage", "year", NOW)
    assert r["groups"] == 1 and os.listdir(gdir) == ["2024.csv"]
    assert len(ps.read_partitioned(str(gdir), fmt="csv", date_from="2024-02-05", date_to="2024-02-06")) == 29


def test_explore_files_for_year_files(hist, monkeypatch):
    import core.explore as ex
    from core.datasets import EXPLORE_BY_KEY
    monkeypatch.setattr(ex, "history_root", lambda: hist)
    for z in ("CZ", "AT"):
        d = hist / "entsoe_prices" / z
        d.mkdir(parents=True)
        for stem in ("2024", "2025", "2026-01", "2026-02"):
            (d / f"{stem}.parquet").write_bytes(b"")
    spec = EXPLORE_BY_KEY["entsoe_prices"]
    from datetime import date
    got = {os.path.basename(f) for f in ex.files_for(spec, {"zone": ["CZ"]}, date(2025, 3, 1), date(2025, 4, 1))}
    assert got == {"2025.parquet"}
    got = {os.path.basename(f) for f in ex.files_for(spec, {"zone": ["CZ"]}, date(2026, 1, 1), date(2026, 1, 31))}
    assert got == {"2025.parquet", "2026-01.parquet", "2026-02.parquet"}  # +prosinec (UTC vs Praha)


# ── snapshoty ───────────────────────────────────────────────────────────

def _snap(base, day, rows):
    df = pd.DataFrame(rows)
    ps.write_daily_snapshot(df, str(base), pd.Timestamp(day, tz="UTC"))
    return df


def test_snapshot_archive_exact_reconstruction(hist):
    from core.maintenance import archive_snapshots
    base = hist / "outages_snapshots"
    st = pd.Timestamp("2026-07-01", tz="UTC")
    a = {"country": "CZ", "plant_type": "Nuclear", "start": st, "unavail_mw": 1000.0, "revision": 1}
    b = {"country": "DE", "plant_type": "Lignite", "start": st, "unavail_mw": 300.0, "revision": 2}
    c = {"country": "CZ", "plant_type": "Gas", "start": st, "unavail_mw": None, "revision": 1}
    days = {"2026-08-01": [a, b], "2026-08-02": [a, b, b], "2026-08-03": [a, c],
            "2026-08-05": [a, b], "2026-10-20": [a]}
    orig = {d: _snap(base, d, r) for d, r in days.items()}
    r = archive_snapshots("outages_snapshots", 60, NOW)
    assert r == {"archived": 4, "removed": 4, "errors": []}
    assert sorted(os.listdir(base)) == ["2026-10-20.parquet", "_archive.json", "_archive.parquet"]
    arch = pd.read_parquet(base / "_archive.parquet")
    assert len(arch) == 5  # a (1 interval), b (2 intervaly) + duplicitní b, c
    for d, df in orig.items():
        rec = ps.read_snapshot(str(base), pd.Timestamp(d))
        schema = json.loads((base / "_archive.json").read_text())["schema"].get(d)
        assert list(rec.columns) == list(df.columns)
        if schema:  # archivované dny: typy jako v originálním souboru
            assert {c: str(t) for c, t in rec.dtypes.items()} == schema
        assert sorted(ps.canonical_rows(rec)) == sorted(ps.canonical_rows(df)), d
    assert ps.read_snapshot(str(base), pd.Timestamp("2026-08-04")).empty
    assert ps.snapshot_days(str(base)) == sorted(days)
    # další běh: nic nového
    assert archive_snapshots("outages_snapshots", 60, NOW)["archived"] == 0


def test_outages_snapshot_dates_include_archive(hist, monkeypatch, no_cache):
    from core.maintenance import archive_snapshots
    import data.outages as out
    base = hist / "outages_snapshots"
    monkeypatch.setattr(out, "OUTAGES_SNAPSHOTS_DIR", str(base))
    row = {"country": "CZ", "start": pd.Timestamp("2026-07-01", tz="UTC"), "unavail_mw": 1.0}
    for d in ("2026-08-01", "2026-08-02", "2026-10-22"):
        _snap(base, d, [row])
    _snap(base, "2026-08-03", [{**row, "country": "DE"}])
    archive_snapshots("outages_snapshots", 60, NOW)
    got = [d.strftime("%Y-%m-%d") for d in out.list_available_snapshot_dates("CZ")]
    assert got == ["2026-08-01", "2026-08-02", "2026-10-22"]


def test_snapshot_mismatch_keeps_file(hist, monkeypatch):
    from core import maintenance as mt
    base = hist / "gassco_umm_snapshots"
    _snap(base, "2026-08-01", [{"messageId": "x", "v": 1.0}])
    monkeypatch.setattr(ps, "read_archived_snapshot", lambda b, d: pd.DataFrame({"messageId": ["y"], "v": [1.0]}))
    r = mt.archive_snapshots("gassco_umm_snapshots", 60, NOW)
    assert r["removed"] == 0 and r["errors"] and (base / "2026-08-01.parquet").exists()


# ── předpovědi ──────────────────────────────────────────────────────────

def test_trim_forecasts(hist):
    from core.maintenance import trim_forecasts
    d = hist / "weather_fc_daily" / "CZ"
    rows = []
    for issued in pd.date_range("2026-06-01", "2026-10-20", freq="7D", tz="UTC"):
        for lead in range(16):
            rows.append({"issued": issued, "date": issued + pd.Timedelta(days=lead), "var": "temp",
                         "model": "m", "value": float(lead)})
    df = pd.DataFrame(rows)
    ps.upsert_partitioned(df, str(d), "issued", ["issued", "date", "var", "model"])
    r = trim_forecasts("weather_fc_daily", 90, 10, NOW)
    back = ps.read_partitioned(str(d))
    lead = (pd.to_datetime(back["date"], utc=True) - pd.to_datetime(back["issued"], utc=True)).dt.days
    old = pd.to_datetime(back["issued"], utc=True) < NOW.normalize() - pd.Timedelta(days=90)
    assert lead[old].max() == 10 and lead[~old].max() == 15
    assert r["rows_removed"] == len(df) - len(back) > 0


# ── velikosti, celý běh, /stav ──────────────────────────────────────────

def test_run_maintenance_records_sizes_and_growth(hist):
    from core import maintenance as mt
    _prices(str(hist / "entsoe_prices" / "CZ"), "2024-01-01", "2024-03-01")
    old = {"t": (NOW - pd.Timedelta(days=30)).isoformat(), "ds": {"entsoe_prices": [0, 0]}}
    (hist / ".sizes.json").write_text(json.dumps([old]))
    res = mt.run_maintenance(NOW)
    assert res == {"errors": 0, "pending": 0}
    m = mt.last_maintenance()
    assert m["files_before"] == 2 and m["files_after"] == 1 and m["actions"][0]["dataset"] == "entsoe_prices"
    g = mt.growth(now=NOW)
    assert g["entsoe_prices"]["files"] == 1 and g["entsoe_prices"]["per_year"] > 0
    assert len(mt.load_sizes_history()) == 2


def test_maintenance_skips_busy_dataset(hist, monkeypatch):
    from core import maintenance as mt
    from core.locks import task_lock
    monkeypatch.setitem(mt.POLICY, "lock_wait_s", 0)
    _prices(str(hist / "entsoe_prices" / "CZ"), "2024-01-01", "2024-03-01")
    with task_lock("entsoe_prices"):
        res = mt.run_maintenance(NOW)
    assert res["pending"] >= 1
    assert len(os.listdir(hist / "entsoe_prices" / "CZ")) == 2


def test_status_page_shows_storage(hist, monkeypatch):
    import web.status as ws
    from core import maintenance as mt
    from web.app import app
    monkeypatch.setattr(ws, "_sizes", {"t": 0.0, "g": None})
    _prices(str(hist / "entsoe_prices" / "CZ"), "2024-01-01", "2024-03-01")
    mt.run_maintenance(NOW)
    r = TestClient(app).get("/stav")
    assert r.status_code == 200 and "Úložiště a údržba" in r.text and "Poslední údržba" in r.text
    assert 'data-refresh-target="entsoe_prices"' in r.text and 'data-refresh-target="ceps"' not in r.text


# ── zámky úloh, update_all ──────────────────────────────────────────────

def test_task_lock_busy_and_wait(hist):
    from core.locks import Busy, is_locked, task_lock
    with task_lock("gie"):
        assert is_locked("gie")
        with pytest.raises(Busy):
            with task_lock("gie", wait_s=0.1):
                pass
    assert not is_locked("gie")


def test_run_tasks_skip_when_busy_and_after_wait(hist, monkeypatch):
    from core import runlog
    from core.locks import task_lock
    from scripts import update_all as ua
    calls = []
    monkeypatch.setattr(ua, "_tasks", lambda: {n: ("x", lambda n=n: calls.append(n)) for n in ua.TASK_ORDER})
    with task_lock("gie"):
        res = ua.run_tasks(["gie", "lng"], wait_s=0)
    assert [r[1] for r in res] == ["SKIP", "OK"] and calls == ["lng"]
    # čekal, a mezitím úlohu úspěšně dokončil jiný proces → znovu se nespouští
    monkeypatch.setattr(ua, "_finished_ok_since", lambda name, since: True)
    import threading
    lk = task_lock("gie")
    lk.__enter__()
    threading.Timer(0.3, lambda: lk.__exit__(None, None, None)).start()
    res = ua.run_tasks(["gie"], wait_s=5)
    assert res[0][1] == "SKIP" and "mezitím" in res[0][3]
    runlog.record([r for r in res if r[1] != "SKIP"])
    assert "gie" not in runlog.load()


def test_update_all_manual_ignores_group_lock_and_writes_job(hist, monkeypatch):
    from core.jobs import JobFile
    from scripts import update_all as ua
    monkeypatch.setattr(ua, "_tasks", lambda: {n: ("x", lambda: {"errors": 0}) for n in ua.TASK_ORDER})
    monkeypatch.setattr(ua, "_refresh_overview", lambda names: None)
    import core.settings as cs
    monkeypatch.setattr(cs, "history_root", lambda: hist)
    lock = ua._Lock(hist / ".update.lock")
    assert lock.acquire()
    try:
        assert ua.main(["--only", "gie"]) == ua.EXIT_LOCKED
        assert ua.main(["--only", "gas", "--manual", "--job", "gas"]) == ua.EXIT_OK
    finally:
        lock.release()
    j = JobFile("gas").read()
    assert j["state"] == "done" and j["ok"] is True and len(j["results"]) == len(ua.GROUPS["gas"])


def test_maintenance_not_in_default_run():
    from scripts import update_all as ua
    assert "maintenance" not in ua.resolve([], []) and ua.resolve(["maintenance"], []) == ["maintenance"]


# ── ruční „Aktualizovat teď“ ────────────────────────────────────────────

def test_jobs_start_cooldown_and_spam(hist, monkeypatch):
    from core import jobs
    started = []
    monkeypatch.setenv("PPDASH_REFRESH_MODE", "subprocess")
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda args, **kw: started.append(args))
    r = jobs.start("gas")
    assert r["ok"] and "--manual" in started[0] and started[0][-1] == "gas"
    assert not jobs.start("gas")["ok"]  # už běží (queued)
    jobs.JobFile("gas").update(state="done", ok=True, results=[])
    r = jobs.start("gas")
    assert not r["ok"] and "znovu za" in r["message"]
    assert jobs.target_info("ceps") is None and jobs.target_info("nesmysl") is None
    assert jobs.target_info("generation")[1] == "generation"
    # max paralelních
    jobs.JobFile("weather").update(state="running", pid=os.getpid())
    jobs.JobFile("cot").update(state="waiting", pid=os.getpid())
    r = jobs.start("nordic")
    assert r["ok"] is False and "běží už 2" in r["message"]
    # mrtvý proces → stale (nezablokuje tlačítko navždy)
    jobs.JobFile("weather").update(state="running", pid=999999)
    assert jobs.status("weather")["state"] == "stale"


def test_jobs_systemd_mode(hist, monkeypatch):
    from core import jobs
    calls = []

    class R:
        returncode = 0
        stderr = ""
    monkeypatch.setenv("PPDASH_REFRESH_MODE", "systemd")
    monkeypatch.setattr(jobs.subprocess, "run", lambda args, **kw: calls.append(args) or R())
    assert jobs.start("entsoe_prices")["ok"]
    assert calls[0][:4] == ["systemctl", "--user", "start", "--no-block"]
    assert calls[0][4] == "ppdash-run@entsoe_prices.service"


def test_refresh_api_and_page_button(hist, monkeypatch):
    from core import jobs
    from web.app import app
    monkeypatch.setenv("PPDASH_REFRESH_MODE", "subprocess")
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda args, **kw: None)
    cl = TestClient(app)
    assert cl.get("/api/refresh/gas").json()["state"] == "idle"
    r = cl.post("/api/refresh/gas")
    assert r.status_code == 202 and r.json()["started"]
    assert cl.post("/api/refresh/gas").status_code == 409
    jobs.JobFile("gas").update(state="done", ok=True, tasks=["gie"], results=[["gie", "OK", 1.2, ""]])
    j = cl.get("/api/refresh/gas").json()
    assert j["state"] == "done" and j["results"][0]["task"] == "GIE zásobníky" and j["cooldown_left"] > 0
    assert cl.post("/api/refresh/ceps").status_code == 404
    assert 'data-refresh-target="gas"' in cl.get("/plyn").text


# ── frekvence timerů ────────────────────────────────────────────────────

def test_timer_frequencies():
    from pathlib import Path
    u = Path(__file__).resolve().parent.parent / "deploy" / "systemd-user"
    entsoe = (u / "ppdash-update-entsoe.timer").read_text()
    assert entsoe.count("OnCalendar=") == 1 and "07:10" in entsoe
    prices = (u / "ppdash-update-entsoe-prices.service").read_text()
    assert "--only entsoe_prices,dap_europe" in prices and "--manual" in prices
    assert "13:20" in (u / "ppdash-update-entsoe-prices.timer").read_text()
    assert (u / "ppdash-update.timer").read_text().count("OnCalendar=") == 1
    assert "--skip power" in (u / "ppdash-update-evening.service").read_text()
    assert "Sun" in (u / "ppdash-maintenance.timer").read_text()
    run = (u / "ppdash-run@.service").read_text()
    assert "--manual --only %i --job %i" in run
    install = (u.parent / "install_user.sh").read_text()
    for t in ("ppdash-maintenance.timer", "ppdash-update-entsoe-prices.timer", "ppdash-update-evening.timer"):
        assert t in install
