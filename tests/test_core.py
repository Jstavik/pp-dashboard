"""core/: TTL cache, nastavení, čtení partitioned úložiště."""
import time

import pandas as pd
import pytest

from core.cache import ttl_cache, make_key
from core import settings


def test_cache_ttl_and_copies(monkeypatch):
    monkeypatch.delenv("PPDASH_CACHE_DISABLE", raising=False)
    calls = []

    @ttl_cache(ttl=0.3)
    def load(x):
        calls.append(x)
        return pd.DataFrame({"a": [x]})

    df1 = load(1)
    df1["b"] = 5                      # mutace výsledku nesmí změnit cache
    df2 = load(1)
    assert calls == [1] and "b" not in df2.columns
    load(2)
    assert calls == [1, 2]
    time.sleep(0.35)
    load(1)
    assert calls == [1, 2, 1]


def test_cache_does_not_cache_exceptions(monkeypatch):
    monkeypatch.delenv("PPDASH_CACHE_DISABLE", raising=False)
    n = {"i": 0}

    @ttl_cache(ttl=60)
    def flaky():
        n["i"] += 1
        if n["i"] == 1:
            raise RuntimeError("boom")
        return 42

    with pytest.raises(RuntimeError):
        flaky()
    assert flaky() == 42 and flaky() == 42 and n["i"] == 2


def test_cache_disk_survives_memory_clear(monkeypatch, tmp_path):
    monkeypatch.delenv("PPDASH_CACHE_DISABLE", raising=False)
    monkeypatch.setenv("PPDASH_CACHE_DIR", str(tmp_path))
    settings.reset_caches()
    try:
        calls = []

        @ttl_cache(ttl=60, disk=True, name="test.disk")
        def fetch():
            calls.append(1)
            return {"x": pd.Series([1, 2])}

        fetch()
        fetch._data.clear()            # jako po restartu procesu
        assert fetch()["x"].tolist() == [1, 2]
        assert calls == [1]
        assert list((tmp_path / "test.disk").glob("*.pkl"))
        fetch.clear()
        assert not list((tmp_path / "test.disk").glob("*.pkl"))
    finally:
        monkeypatch.undo()
        settings.reset_caches()


def test_make_key_normalizes_timestamps():
    a = make_key((pd.Timestamp("2026-01-01", tz="UTC"),), {})
    b = make_key((pd.Timestamp("2026-01-01", tz="UTC"),), {})
    c = make_key((pd.Timestamp("2026-01-02", tz="UTC"),), {})
    assert a == b != c


def test_env_file(tmp_path, monkeypatch):
    f = tmp_path / "env"
    f.write_text("# komentář\nENTSOE_TOKEN='abc'\nexport GIE_API_KEY=xyz\n")
    monkeypatch.setenv("PPDASH_ENV_FILE", str(f))
    monkeypatch.delenv("ENTSOE_TOKEN", raising=False)
    settings.reset_caches()
    try:
        assert settings.entsoe_token() == "abc"
        assert settings.gie_api_key() == "xyz"
        monkeypatch.setenv("ENTSOE_TOKEN", "fromenv")   # prostředí má přednost
        assert settings.entsoe_token() == "fromenv"
    finally:
        monkeypatch.undo()
        settings.reset_caches()


def test_read_partitioned_arrow_path(tmp_path):
    from data.partitioned_store import read_partitioned, upsert_partitioned, last_date_partitioned
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-05", "2026-02-03", "2026-02-04"], utc=True),
        "country": ["CZ", "CZ", "DE"], "value": [1.0, 2.0, 3.0],
    })
    touched = upsert_partitioned(df, str(tmp_path), "date", ["date", "country"])
    assert [t[0] for t in touched] == ["2026-01", "2026-02"]
    # idempotence: druhý zápis stejných dat nic nepřepíše
    assert all(not w for _, _, w in upsert_partitioned(df, str(tmp_path), "date", ["date", "country"]))
    out = read_partitioned(str(tmp_path), columns=["date", "country", "value"],
                           filters=[("country", "==", "CZ")], categories=["country"])
    assert len(out) == 2 and isinstance(out["country"].dtype, pd.CategoricalDtype)
    assert str(out["date"].dtype) == "datetime64[ns, UTC]"
    only_feb = read_partitioned(str(tmp_path), date_from="2026-02-01")
    assert len(only_feb) == 2
    assert last_date_partitioned(str(tmp_path), "date") == pd.Timestamp("2026-02-04", tz="UTC")
