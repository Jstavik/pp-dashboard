"""Živé úlohy (timer ppdash-live): ČEPS minutová historie, GASSCO realtime,
UMM bez zbytečných přepisů; registr datasetů; CoT dohánění; update_all live."""
import os

import pandas as pd
import pytest

from tests.conftest import FakeResponse
from tests.test_sources import ATOM, _FakeCeps

TZ = "Europe/Prague"


# ── ČEPS ────────────────────────────────────────────────────────────────

@pytest.fixture
def ceps_store(monkeypatch, tmp_path):
    from data import ceps
    monkeypatch.setattr(ceps, "CEPS_DIR", str(tmp_path / "ceps"))
    monkeypatch.setattr(ceps, "_get_ceps_client", lambda: _FakeCeps())
    return ceps, tmp_path / "ceps"


def _all_rows(base) -> int:
    return sum(len(pd.read_parquet(f)) for f in base.rglob("*.parquet"))


def test_ceps_live_writes_and_is_idempotent(ceps_store, no_cache):
    ceps, base = ceps_store
    res = ceps.update_ceps_live()
    assert res["errors"] == 0 and res["rows"] > 0
    datasets = {p.name for p in base.iterdir()}
    assert datasets == set(ceps.LIVE_DATASETS) | {ceps.PRICE_DATASET}
    # denní partitiony {YYYY-MM-DD}.parquet
    names = [f.name for f in base.rglob("*.parquet")]
    assert all(len(n) == len("2026-10-23.parquet") for n in names)
    n1 = _all_rows(base)
    mtimes = {f: f.stat().st_mtime_ns for f in base.rglob("*.parquet")}
    ceps.update_ceps_live()                               # stejná data znovu
    assert _all_rows(base) == n1                          # žádné duplicity
    assert {f: f.stat().st_mtime_ns for f in base.rglob("*.parquet")} == mtimes  # nic nepřepsáno


def test_ceps_live_continues_from_last_stored(ceps_store, no_cache, monkeypatch):
    ceps, base = ceps_store
    ceps.update_ceps_live()
    seen = []
    orig = ceps.fetch_ceps_range

    def spy(dataset, start, end, client=None):
        seen.append((dataset, start))
        return orig(dataset, start, end, client=client)
    monkeypatch.setattr(ceps, "fetch_ceps_range", spy)
    now = pd.Timestamp.now(tz=TZ)
    ceps.update_ceps_live(now)
    starts = dict(seen)
    last = ceps.last_date_partitioned(str(base / "imbalance"), "time")
    assert starts["imbalance"] == last.tz_convert(TZ) - ceps.LIVE_OVERLAP
    assert starts[ceps.PRICE_DATASET] == starts[ceps.PRICE_DATASET].normalize()   # cena vždy od začátku dne


def test_ceps_store_matches_live_shape(ceps_store, no_cache):
    ceps, _ = ceps_store
    live = ceps.fetch_ceps_live()
    assert ceps.load_ceps_from_store() is None             # prázdné úložiště → None
    ceps.update_ceps_live()
    stored = ceps.load_ceps_from_store()
    assert stored is not None
    for key in ("imbal", "svr", "load", "gen", "res", "freq", "cb", "cena", "svr_named", "imbal_mw", "price"):
        assert list(stored[key].columns) == list(live[key].columns), key
        assert len(stored[key]) == len(live[key]), key
    assert stored["svr_named"]["aFRR+ [MW]"].iloc[0] == 10
    assert stored["svr_named"]["mFRR+ [MW]"].iloc[0] == 1     # nedeklarovaná value3 přežije uložení
    assert stored["cb"]["Net Export (MW)"].iloc[-1] == 70
    assert stored["price"]["cena_CZK_MWh"].iloc[0] == 2500
    assert str(stored["imbal"].index.tz) == TZ
    # fetch_ceps_all bere čerstvé úložiště (žádné SOAP)
    ceps._get_ceps_client = lambda: (_ for _ in ()).throw(AssertionError("SOAP se nemá volat"))
    assert not ceps.fetch_ceps_all()["imbal"].empty


def test_ceps_store_stale_falls_back_to_live(ceps_store, no_cache):
    ceps, _ = ceps_store
    ceps.update_ceps_live()
    later = pd.Timestamp.now(tz=TZ) + ceps.CEPS_STORE_MAX_AGE + pd.Timedelta(minutes=5)
    assert ceps.load_ceps_from_store(later) is None


def test_ceps_live_all_fail_raises(monkeypatch, tmp_path):
    from data import ceps
    from core.updaters import UpdateError

    class Boom:
        class service:
            def __getattr__(self, m):
                raise RuntimeError("down")
        service = service()
    monkeypatch.setattr(ceps, "CEPS_DIR", str(tmp_path))
    monkeypatch.setattr(ceps, "_get_ceps_client", lambda: Boom())
    with pytest.raises(UpdateError):
        ceps.update_ceps_live()


def test_ceps_frequency_resampled_to_minute():
    from data import ceps
    from tests.test_sources import _ceps_xml
    t0 = pd.Timestamp("2026-10-23T10:00:00", tz=TZ)
    rows = [{"date": (t0 + pd.Timedelta(seconds=s)).isoformat(), "value1": str(50 + s / 1000)} for s in range(0, 120, 10)]
    df = ceps._parse_long(_ceps_xml({"value1": "Frekvence"}, rows), "frequency")
    assert len(df) == 2 and df["time"].dt.second.eq(0).all()


# ── GASSCO realtime + UMM ───────────────────────────────────────────────

def test_gassco_realtime_store(monkeypatch, tmp_path, no_cache):
    from data import gassco
    monkeypatch.setattr(gassco, "GASSCO_REALTIME_DIR", str(tmp_path / "rt"))
    monkeypatch.setattr(gassco.requests, "get", lambda *a, **k: FakeResponse(text=ATOM))
    gassco.update_gassco_realtime()
    f = next((tmp_path / "rt").glob("*.parquet"))
    assert f.name == "2026-10-23.parquet"
    mtime = f.stat().st_mtime_ns
    gassco.update_gassco_realtime()                       # stejná publikace → beze změny
    assert f.stat().st_mtime_ns == mtime and len(pd.read_parquet(f)) == 2
    newer = ATOM.replace("2026-10-23T10:00:00Z", "2026-10-23T11:00:00Z").replace(">40<", ">41<")
    monkeypatch.setattr(gassco.requests, "get", lambda *a, **k: FakeResponse(text=newer))
    gassco.update_gassco_realtime()
    df = gassco.load_gassco_realtime()
    assert len(df) == 4 and df["updated"].nunique() == 2


def test_gassco_realtime_prefers_fresh_store(monkeypatch, tmp_path, no_cache):
    from data import gassco
    monkeypatch.setattr(gassco, "GASSCO_REALTIME_DIR", str(tmp_path / "rt"))
    now = pd.Timestamp.now(tz="UTC").floor("s").strftime("%Y-%m-%dT%H:%M:%SZ")
    atom = ATOM.replace("2026-10-23T10:00:00Z", now)
    monkeypatch.setattr(gassco.requests, "get", lambda *a, **k: FakeResponse(text=atom))
    gassco.update_gassco_realtime()
    monkeypatch.setattr(gassco.requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("live GET")))
    df = gassco.fetch_realtime_nominations()
    assert set(df["point"]) == {"Emden", "Dornum"} and df["realtime"].all()
    assert "updated" not in df.columns


def test_gassco_umm_not_rewritten_when_unchanged(monkeypatch, tmp_path):
    from data import gassco
    monkeypatch.setattr(gassco, "GASSCO_UMM_PATH", str(tmp_path / "umm.parquet"))
    monkeypatch.setattr(gassco, "GASSCO_UMM_SNAPSHOTS_DIR", str(tmp_path / "snap"))
    df = pd.DataFrame({"base_id": ["a", "b"], "revision": [1, 2], "x": [1.0, 2.0]})
    monkeypatch.setattr(gassco, "fetch_gassco_umm", lambda: df.copy())
    assert gassco.update_gassco_umm()["changed"]
    mtime = os.stat(tmp_path / "umm.parquet").st_mtime_ns
    assert not gassco.update_gassco_umm()["changed"]
    assert os.stat(tmp_path / "umm.parquet").st_mtime_ns == mtime
    assert len(list((tmp_path / "snap").glob("*.parquet"))) == 1


# ── úložiště: denní partitiony ──────────────────────────────────────────

def test_upsert_partitioned_daily(tmp_path):
    from data.partitioned_store import upsert_partitioned, read_partitioned, last_date_partitioned
    df = pd.DataFrame({"time": pd.date_range("2026-09-30 23:58", periods=4, freq="min", tz="UTC"),
                       "v": [1.0, 2.0, 3.0, 4.0]})
    touched = upsert_partitioned(df, str(tmp_path), "time", ["time"], period="day")
    assert sorted(m for m, _, _ in touched) == ["2026-09-30", "2026-10-01"]
    assert len(read_partitioned(str(tmp_path), date_from="2026-10-01", date_to="2026-10-01")) == 2
    assert len(read_partitioned(str(tmp_path), date_to="2026-09-30")) == 2
    assert last_date_partitioned(str(tmp_path), "time") == df["time"].max()


# ── update_all: skupina live ────────────────────────────────────────────

def test_update_all_live_group(monkeypatch, tmp_path):
    from scripts import update_all
    default = update_all.resolve([], [])
    assert not set(update_all.LIVE_TASKS) & set(default) and "cot" not in default
    assert update_all.resolve(["live"], []) == ["ceps", "gassco_realtime", "gassco_umm"]
    assert "gassco_umm" not in update_all.GROUPS["gas"]

    from core import settings
    monkeypatch.setattr(settings, "history_root", lambda: tmp_path)
    monkeypatch.setattr(update_all, "_tasks", lambda: {n: ("x", lambda: {"errors": 0}) for n in update_all.TASK_ORDER})
    # denní běh drží .update.lock — live běh má vlastní zámek a projde
    lock = update_all._Lock(tmp_path / ".update.lock")
    assert lock.acquire()
    try:
        assert update_all.main(["--only", "live", "-q"]) == update_all.EXIT_OK
        assert update_all.main(["--only", "gie"]) == update_all.EXIT_LOCKED
    finally:
        lock.release()
    live = update_all._Lock(tmp_path / ".live.lock")
    assert live.acquire()
    try:
        assert update_all.main(["--only", "ceps"]) == update_all.EXIT_LOCKED
    finally:
        live.release()


# ── CoT dohánění ────────────────────────────────────────────────────────

def test_cot_pending_fridays():
    from datetime import date
    from core.updaters import _cot_pending_fridays, COT_MAX_CATCHUP_WEEKS
    fri = date(2026, 10, 23)
    assert _cot_pending_fridays(fri, None) == [fri]
    assert _cot_pending_fridays(fri, fri) == [fri]
    assert _cot_pending_fridays(fri, date(2026, 9, 18)) == [date(2026, 9, 25), date(2026, 10, 2),
                                                            date(2026, 10, 9), date(2026, 10, 16), fri]
    assert len(_cot_pending_fridays(fri, date(2025, 1, 3))) == COT_MAX_CATCHUP_WEEKS


def test_update_cot_catches_up(monkeypatch):
    from core import updaters
    import data.ice_cot as ic
    fetched = []
    monkeypatch.setattr(ic, "probe_rate_limit", lambda venue="IFEU": {"rate_limited": False})
    monkeypatch.setattr(ic, "load_ice_cot", lambda: pd.DataFrame({"report_date": ["2026-10-09"]}))
    monkeypatch.setattr(ic, "fetch_ice_cot", lambda d, venues=ic.VENUES: fetched.append(str(d)) or
                        {v: {"success": True, "rows_fetched": 1, "rows_in_store": 1} for v in ic.VENUES})
    res = updaters.update_cot(pd.Timestamp("2026-10-24", tz="UTC"))
    assert fetched == ["2026-10-16"] and res["errors"] == 0   # 10-23 vyjde až ve středu 10-28
    fetched.clear()
    updaters.update_cot(pd.Timestamp("2026-10-28 13:00", tz="UTC"))
    assert fetched == ["2026-10-16", "2026-10-23"]


def test_last_published_friday():
    from datetime import date
    from core.updaters import last_published_friday
    assert last_published_friday(pd.Timestamp("2026-10-20")) == date(2026, 10, 9)   # út
    assert last_published_friday(pd.Timestamp("2026-10-21")) == date(2026, 10, 16)  # st = publikace
    assert last_published_friday(pd.Timestamp("2026-10-24")) == date(2026, 10, 16)  # so


def test_update_cot_not_yet_published_is_warn(monkeypatch):
    from core import updaters
    import data.ice_cot as ic
    monkeypatch.setattr(ic, "probe_rate_limit", lambda venue="IFEU": {"rate_limited": False})
    monkeypatch.setattr(ic, "load_ice_cot", lambda: pd.DataFrame({"report_date": ["2026-10-09"]}))
    monkeypatch.setattr(ic, "fetch_ice_cot", lambda d, venues=ic.VENUES:
                        {v: {"success": False, "http_status": 404} for v in ic.VENUES})
    res = updaters.update_cot(pd.Timestamp("2026-10-21 12:00", tz="UTC"))
    assert res["errors"] == 2
    with pytest.raises(updaters.UpdateError):
        updaters.update_cot(pd.Timestamp("2026-10-23 12:00", tz="UTC"))


# ── registr datasetů ────────────────────────────────────────────────────

def test_dataset_status(monkeypatch, tmp_path):
    from core import datasets
    monkeypatch.setattr(datasets, "history_root", lambda: tmp_path)
    (tmp_path / "hu_generation").mkdir()
    gen = tmp_path / "generation" / "CZ"
    gen.mkdir(parents=True)
    now = pd.Timestamp.now(tz="UTC").floor("min")
    pd.DataFrame({"date": [now - pd.Timedelta(hours=2)], "mw": [1.0]}).to_parquet(gen / f"{now:%Y-%m}.parquet")
    rows = {r["name"]: r for r in datasets.dataset_status(now)}
    assert rows["generation"]["ok"] and rows["generation"]["age_h"] == 2.0
    assert not rows["ceps"]["ok"] and rows["ceps"]["files"] == 0
    assert datasets.dead_present() == ["hu_generation"]
    names = {d.name for d in datasets.DATASETS}
    assert not names & set(datasets.DEAD)


def test_dead_datasets_have_no_readers():
    """Mrtvé datasety nesmí nikdo číst ani zapisovat (kromě registru)."""
    import subprocess
    from core.datasets import DEAD
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in DEAD:
        out = subprocess.run(["grep", "-rlF", "--include=*.py", f'"{name}"', "core", "data", "web", "charts", "scripts"],
                             cwd=root, capture_output=True, text=True).stdout.split()
        assert [f for f in out if f != "core/datasets.py"] == [], (name, out)
