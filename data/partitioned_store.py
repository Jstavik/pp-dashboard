"""Obecná vrstva pro append-only historické zdroje (data se zpětně nemění).

Problém: jeden rostoucí soubor (parquet i CSV) se při každé aktualizaci celý
přepíše a git ho commitne jako novou binární verzi — u velkých/často
aktualizovaných zdrojů roste .git nelineárně (desítky až stovky MB na
zdroj za pár měsíců), bez ohledu na formát; ukázalo se, že CSV soubory
s plným sort_values()+to_csv() na celou historii trpí stejným (u nás i
horším) problémem jako parquet.

Řešení: rozdělit historii na měsíční soubory. Jakmile je měsíc uzavřený
(nový běh do něj už nepíše), jeho soubor se v gitu commitne jednou a zůstane
navždy beze změny — roste jen aktuální (otevřený) měsíc.

Zdroje, které se ZPĚTNĚ MĚNÍ (odstávky a jejich revize), tímhle vzorem
neřešíme — tam je rolling-window přepis nevyhnutelný.
"""
import glob
import os
import pandas as pd


def _is_year_stem(stem: str) -> bool:
    return len(stem) == 4 and stem.isdigit()


def _select_files(base_dir: str, fmt: str, date_from=None, date_to=None) -> list:
    files = sorted(glob.glob(os.path.join(base_dir, f"*.{fmt}")))
    if date_from is not None or date_to is not None:
        lo = pd.Timestamp(date_from).strftime("%Y-%m") if date_from is not None else None
        hi = pd.Timestamp(date_to).strftime("%Y-%m") if date_to is not None else None
        def _in_range(path):
            # měsíční ({YYYY-MM}), denní ({YYYY-MM-DD}) i roční ({YYYY} — po
            # kompakci, core/maintenance.py) partitiony — porovnává se měsíc
            stem = os.path.splitext(os.path.basename(path))[0]
            if _is_year_stem(stem):
                return (lo is None or stem >= lo[:4]) and (hi is None or stem <= hi[:4])
            month = stem[:7]
            if lo is not None and month < lo:
                return False
            if hi is not None and month > hi:
                return False
            return True
        files = [f for f in files if _in_range(f)]
    return files


_TIME_COLS = ("time", "date", "gasDayStart", "updated", "issued", "timestamp", "periodFrom_dt")


def _month_bounds(date_from, date_to):
    lo = pd.Timestamp(pd.Timestamp(date_from).strftime("%Y-%m-01"), tz="UTC") if date_from is not None else None
    hi = (pd.Timestamp(pd.Timestamp(date_to).strftime("%Y-%m-01"), tz="UTC") + pd.offsets.MonthBegin(1)
          if date_to is not None else None)
    return lo, hi


def _year_mask(col, lo, hi):
    """Maska řádků ročního souboru v měsících [lo, hi) — stejný výběr, jako
    kdyby se četly měsíční soubory (read_partitioned filtruje po měsících)."""
    t = pd.to_datetime(pd.Series(col), utc=True, errors="coerce")
    m = pd.Series(True, index=t.index)
    if lo is not None:
        m &= t >= lo
    if hi is not None:
        m &= t < hi
    return m.to_numpy()


def _is_year_file(path: str) -> bool:
    return _is_year_stem(os.path.splitext(os.path.basename(path))[0])


def container_path(base_dir: str, key: str, fmt: str):
    """Soubor, do kterého po kompakci patří partition `key` ({YYYY-MM} nebo
    {YYYY-MM-DD}): roční {YYYY}.fmt nebo měsíční {YYYY-MM}.fmt (denní
    soubory zkompaktované do měsíce). None = žádný kontejner neexistuje."""
    for stem in (key[:4], key[:7] if len(key) > 7 else None):
        if stem:
            p = os.path.join(base_dir, f"{stem}.{fmt}")
            if os.path.exists(p):
                return p
    return None


def _normalize_arrow_table(table):
    """Sjednotí typy, které se mezi měsíčními soubory historicky liší
    (string vs large_string, timestamp[us/ns] v UTC vs Europe/Prague) —
    jinak by concat_tables selhal. Časové sloupce s tz se převedou na
    timestamp[ns, UTC] (totéž, co dřív dělal pd.to_datetime(..., utc=True)
    ve volajících), dictionary sloupce na dictionary<int32, string>;
    int64/double a null sloupce sjednotí až
    concat_tables(promote_options="permissive")."""
    import pyarrow as pa
    cols, names = [], []
    for i, field in enumerate(table.schema):
        col = table.column(i)
        t = field.type
        if pa.types.is_dictionary(t):
            if t.value_type != pa.string() or t.index_type != pa.int32():
                col = col.cast(pa.dictionary(pa.int32(), pa.string()))
        elif pa.types.is_large_string(t):
            col = col.cast(pa.string())
        elif pa.types.is_timestamp(t) and t.tz is not None:
            col = col.cast(pa.timestamp("ns", tz="UTC"))
        cols.append(col)
        names.append(field.name)
    return pa.table(cols, names=names)


def _read_parquet_arrow(files: list, columns=None, filters=None, categories=None, trim=None) -> pd.DataFrame:
    """Sloupce z `categories` se čtou rovnou jako parquet dictionary
    (read_dictionary) — nikdy se nematerializují jako plné stringy, což
    drží špičku paměti nízko (entsog_flows celá historie: +40 MB RSS
    místo ~+500 MB při čtení stringů a následné kategorizaci)."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    cats = set(categories or [])
    tables = []
    for f in files:
        present = pq.read_schema(f).names
        cols = [c for c in columns if c in present] if columns is not None else None
        dict_cols = [c for c in present if c in cats and (cols is None or c in cols)]
        tc = (next((c for c in _TIME_COLS if c in present), None)
              if trim is not None and _is_year_file(f) else None)
        extra = cols is not None and tc is not None and tc not in cols
        t = pq.read_table(f, columns=cols + [tc] if extra else cols, filters=filters,
                          read_dictionary=dict_cols or None)
        if tc is not None and t.num_rows:
            t = t.filter(pa.array(_year_mask(t.column(tc).to_pandas(), *trim)))
            if extra:
                t = t.drop_columns([tc])
        if t.num_rows:
            tables.append(_normalize_arrow_table(t))
    if not tables:
        return pd.DataFrame(columns=list(columns) if columns else None)
    table = pa.concat_tables(tables, promote_options="permissive")
    del tables
    df = table.to_pandas(date_as_object=True, split_blocks=True, self_destruct=True)
    del table
    pa.default_memory_pool().release_unused()
    return df


def read_partitioned(base_dir: str, fmt: str = "parquet", date_from=None, date_to=None, row_filter=None,
                     columns=None, filters=None, categories=None) -> pd.DataFrame:
    """Načte a sloučí měsíční soubory v base_dir (data/history/<zdroj>/*.fmt).

    date_from/date_to (volitelné) omezí čtení jen na soubory {YYYY-MM}.fmt
    ležící (byť částečně) v zadaném rozsahu — filtruje se podle NÁZVU
    souboru, ne podle obsahu, takže se soubory mimo okno vůbec neotvírají.
    Bez date_from/date_to (default) čte úplně všechno, beze změny chování
    pro stávající volající.

    row_filter (volitelné, DataFrame -> DataFrame) se aplikuje PER SOUBOR
    hned po přečtení, PŘED concat — pro zdroje, kde je potřeba projít
    celou historii (date-okno nejde použít, viz
    entsog_operational.py::load_eu_operational_open_ended), ale výsledek
    je řádkově malý (jen pár sloupcových hodnot z každého souboru). Drží
    špičkovou paměť na velikosti jednoho souboru + filtrovaného výsledku,
    ne na velikosti celého datasetu.

    columns / filters / categories (jen parquet, volitelné) — paměťově
    úsporné čtení přes pyarrow kvůli provozu na 2 GB RAM:
    - columns: načíst jen tyto sloupce (chybějící v souboru se ignorují),
    - filters: pyarrow filtr (DNF list, např. [("pointLabel", "==", "X")])
      aplikovaný při čtení každého souboru (predicate pushdown),
    - categories: textové sloupce, které se rovnou vytvoří jako pandas
      category (entsog_flows celá historie: ~1 GB jako object → ~35 MB).
    Pokud je zadaný kterýkoli z nich, čte se cestou pyarrow (typy
    sjednocené, viz _normalize_arrow_table); bez nich přesně jako dřív.
    row_filter se v pyarrow cestě aplikuje až na výsledný DataFrame."""
    files = _select_files(base_dir, fmt, date_from, date_to)
    if not files:
        return pd.DataFrame()
    trim = _month_bounds(date_from, date_to) if (date_from is not None or date_to is not None) else None
    if fmt == "parquet" and (columns is not None or filters is not None or categories is not None):
        df = _read_parquet_arrow(files, columns=columns, filters=filters, categories=categories, trim=trim)
        return row_filter(df) if row_filter is not None and not df.empty else df
    if fmt == "parquet":
        reader = pd.read_parquet
    elif columns is not None:
        wanted = set(columns)
        reader = lambda f: pd.read_csv(f, usecols=lambda c: c in wanted)  # noqa: E731
    else:
        reader = pd.read_csv
    frames = (reader(f) for f in files)
    if trim is not None and any(_is_year_file(f) for f in files):
        base_reader = reader

        def reader(f):  # noqa: F811 — roční soubor ořezat na měsíce okna
            df = base_reader(f)
            tc = next((c for c in _TIME_COLS if c in df.columns), None) if _is_year_file(f) else None
            return df[_year_mask(df[tc], *trim)] if tc is not None and not df.empty else df
        frames = (reader(f) for f in files)
    if row_filter is not None:
        frames = (row_filter(df) for df in frames)
    return pd.concat(frames, ignore_index=True)


def last_date_partitioned(base_dir: str, date_col: str, fmt: str = "parquet"):
    """Vrátí nejnovější datum v base_dir bez čtení celé historie — stačí
    přečíst poslední (lexikograficky největší, tedy nejnovější) měsíční
    soubor podle názvu {YYYY-MM}.<fmt>."""
    files = sorted(glob.glob(os.path.join(base_dir, f"*.{fmt}")))
    if not files:
        return None
    # jen sloupec s datem — ne celý soubor (OPTIMIZATION_REPORT.md, update_entsog)
    if fmt == "parquet":
        df = pd.read_parquet(files[-1], columns=[date_col])
    else:
        df = pd.read_csv(files[-1], usecols=[date_col])
    if df.empty:
        return None
    return pd.to_datetime(df[date_col], utc=True).max()


def write_daily_snapshot(df: pd.DataFrame, base_dir: str, day: pd.Timestamp = None, fmt: str = "parquet") -> bool:
    """Zapíše celý df jako denní snapshot base_dir/{YYYY-MM-DD}.<fmt> —
    write-once, na rozdíl od upsert_partitioned se jednou zapsaný den UŽ
    NIKDY nepřepisuje (žádný merge, žádná "obsah beze změny" kontrola).
    Pokud dnešní soubor už existuje, no-op (vrátí False) — bezpečné volat
    při každém scheduled běhu, zapíše se jen jednou za den.

    Používá se pro rolling zdroje (odstávky), kde potřebujeme "jak to
    vypadalo před N dny" bez závislosti na git historii (shallow clone na
    produkci by to nemusel mít, navíc koliduje s budoucím úklidem historie).
    """
    day = (day or pd.Timestamp.now(tz="UTC")).normalize()
    os.makedirs(base_dir, exist_ok=True)
    path = os.path.join(base_dir, f"{day.strftime('%Y-%m-%d')}.{fmt}")
    if os.path.exists(path):
        return False
    if fmt == "parquet":
        df.to_parquet(path, index=False)
    else:
        df.to_csv(path, index=False)
    return True


def read_snapshot(base_dir: str, day: pd.Timestamp, fmt: str = "parquet") -> pd.DataFrame:
    """Načte konkrétní denní snapshot base_dir/{YYYY-MM-DD}.<fmt>.
    Prázdný DataFrame, pokud pro daný den snapshot neexistuje."""
    day = pd.Timestamp(day).normalize()
    path = os.path.join(base_dir, f"{day.strftime('%Y-%m-%d')}.{fmt}")
    if not os.path.exists(path):
        # starší dny po údržbě leží v archivu změn (core/maintenance.py)
        if fmt == "parquet" and os.path.exists(os.path.join(base_dir, ARCHIVE_META)):
            return read_archived_snapshot(base_dir, day.strftime("%Y-%m-%d"))
        return pd.DataFrame()
    return pd.read_parquet(path) if fmt == "parquet" else pd.read_csv(path)


def upsert_partitioned(
    new_data: pd.DataFrame,
    base_dir: str,
    date_col: str,
    dedup_subset: list,
    fmt: str = "parquet",
    period: str = "month",
) -> list:
    """Zmerguje new_data do měsíčně partitionovaných souborů v base_dir.

    period="day" → denní soubory {YYYY-MM-DD}.fmt (živé minutové zdroje —
    ČEPS, GASSCO realtime — zapisují každou minutu, takže přepis celého
    měsíce by byl zbytečně drahý). Čtení (read_partitioned) i
    last_date_partitioned fungují pro oba tvary názvů.

    Přepíše JEN měsíce, které se v new_data vyskytují — starší uzavřené
    měsíce zůstanou nedotčené (a tedy negenerují novou binární verzi v gitu).
    Pokud se výsledný obsah měsíce po mergi nezmění, soubor se nepřepisuje
    vůbec (žádný git diff) — důležité pro zdroje, co při každém běhu
    přeposílají širší okno, než je skutečně nové.

    Vrací [(měsíc, počet řádků po merge, zapsáno: bool), ...] pro logging.
    """
    if new_data.empty:
        return []
    os.makedirs(base_dir, exist_ok=True)

    new_data = new_data.copy()
    # Měsíční klíč pro groupby se počítá do samostatné (nepersistované)
    # Series — new_data[date_col] se NIKDY nepřepisuje. Zápis musí zachovat
    # PŮVODNÍ dtype volajícího (parquet round-trip je beze ztráty; některé
    # zdroje mají date_col jako obyčejné date objekty, ne Timestamp — např.
    # entsog_capacity periodFrom_dt, dap_europe "date" — a downstream kód
    # je tak i porovnává. Přepsání na tz-aware Timestamp by je tiše rozbilo).
    month_key = pd.to_datetime(new_data[date_col], utc=True)
    months = month_key.dt.strftime("%Y-%m-%d" if period == "day" else "%Y-%m")

    reader = pd.read_parquet if fmt == "parquet" else pd.read_csv
    touched = []

    for month, grp_new in new_data.groupby(months):
        # dedup uvnitř samotného new_data batche — bez tohohle by se
        # duplicity vyskytující se přímo v jednom fetchi (ne jen mezi
        # existing/latest) zapsaly beze změny do NOVÉHO měsíčního
        # souboru (větev "existing is None" níž new_data jinak nijak
        # nefiltruje). Ověřeno na entsog_capacity — API vrací pravé
        # duplicitní řádky v rámci jednoho stažení.
        grp_new = grp_new.drop_duplicates(subset=dedup_subset, keep="last")
        # po kompakci (core/maintenance.py) leží uzavřený rok/měsíc v jednom
        # souboru {YYYY}/{YYYY-MM} — revize (překryv 2–3 dny) se mergují do něj
        path = container_path(base_dir, month, fmt) or os.path.join(base_dir, f"{month}.{fmt}")

        if os.path.exists(path):
            existing = reader(path)
            if fmt == "csv":
                # CSV round-trip ztrácí dtype (date_col se vrátí jako
                # string) — bez sjednocení na Timestamp by
                # drop_duplicates/equals tiše selhávaly na mixed-dtype
                # sloupci a duplicity by se hromadily při každém běhu.
                # Parquet dtype zachovává beze ztráty, takže tohle
                # potřebuje jen CSV — jinak by se přepsal i zdrojům, kde
                # na přesném dtype date_col záleží downstream.
                existing[date_col] = pd.to_datetime(existing[date_col], utc=True)
                grp_new = grp_new.copy()
                grp_new[date_col] = pd.to_datetime(grp_new[date_col], utc=True)
            combined = pd.concat([existing, grp_new], ignore_index=True)
            combined = combined.drop_duplicates(subset=dedup_subset, keep="last")
        else:
            existing = None
            combined = grp_new

        combined = combined.sort_values(dedup_subset).reset_index(drop=True)

        if existing is not None:
            existing_sorted = existing.sort_values(dedup_subset).reset_index(drop=True)
            if existing_sorted.equals(combined):
                touched.append((month, len(combined), False))
                continue

        if fmt == "parquet":
            combined.to_parquet(path, index=False)
        else:
            combined.to_csv(path, index=False)
        touched.append((month, len(combined), True))

    return touched


# ── Archiv denních snapshotů („jen změny“) ──────────────────────────────
#
# Starší denní snapshoty (odstávky, GASSCO UMM) jsou z ~95 % stejné jako
# předchozí den. core/maintenance.py je po N dnech přesune do jednoho
# souboru base_dir/_archive.parquet: každý řádek snapshotu je tam jen
# jednou s intervalem dnů (_from, _to), kdy v snapshotech nepřetržitě byl.
# Snapshot dne D = řádky s _from ≤ D ≤ _to → rekonstrukce je přesná
# (ověřuje se před smazáním denního souboru). _archive.json drží seznam
# archivovaných dnů a sloupce/typy každého dne.

ARCHIVE_FILE = "_archive.parquet"
ARCHIVE_META = "_archive.json"


def _canon_obj(x) -> str:
    import datetime as dt
    if x is None or x is pd.NaT or (isinstance(x, float) and x != x):
        return "∅"
    if isinstance(x, dt.datetime):  # vč. pd.Timestamp; aware i naive → ns UTC
        return str(pd.Timestamp(x).value)
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return repr(float(x))
    return str(x)


def _col_hash(s: pd.Series):
    """uint64 hash hodnot sloupce, nezávislý na typu (int 5 = float 5.0,
    čas = ns UTC, chybějící hodnota = jedna hodnota)."""
    import numpy as np
    hp = pd.util.hash_pandas_object
    if isinstance(s.dtype, pd.DatetimeTZDtype):
        v = s.dt.tz_convert("UTC").dt.as_unit("ns").astype("int64").astype("float64")
        return hp(v.where(s.notna(), np.nan), index=False).to_numpy()
    if pd.api.types.is_datetime64_any_dtype(s):
        v = s.dt.as_unit("ns").astype("int64").astype("float64")
        return hp(v.where(s.notna(), np.nan), index=False).to_numpy()
    if pd.api.types.is_bool_dtype(s):
        return hp(s.astype(str), index=False).to_numpy()
    if pd.api.types.is_numeric_dtype(s):
        return hp(s.astype("float64"), index=False).to_numpy()
    if isinstance(s.dtype, pd.CategoricalDtype):
        s = s.astype(object)
    kind = pd.api.types.infer_dtype(s, skipna=True)
    if kind in ("string", "empty"):
        return hp(s.where(s.notna(), None).astype(object), index=False).to_numpy()
    return hp(s.map(_canon_obj), index=False).to_numpy()


def canonical_rows(df: pd.DataFrame) -> pd.Series:
    """Řádek → uint64 hash nezávislý na typech a pořadí sloupců. Pro
    porovnání obsahu (kompakce, archiv snapshotů)."""
    import numpy as np
    if df.empty:
        return pd.Series([], dtype="uint64")
    h = np.zeros(len(df), dtype="uint64")
    with np.errstate(over="ignore"):
        for i, c in enumerate(sorted(df.columns)):
            # jméno sloupce + pozice → pořadí sloupců nehraje roli, hodnoty v různých sloupcích ano
            salt = np.uint64(int(pd.util.hash_array(np.array([str(c)], dtype=object))[0]) | 1)
            h = h * np.uint64(1099511628211) + _col_hash(df[c]) * salt
    return pd.Series(h, index=df.index)


def content_checksum(df: pd.DataFrame) -> tuple:
    """(počet řádků, součet hashů řádků mod 2^64) — na pořadí nezávislé."""
    h = canonical_rows(df)
    return len(h), int(h.sum()) if len(h) else 0


def _archive_meta(base_dir: str) -> dict:
    import json
    p = os.path.join(base_dir, ARCHIVE_META)
    if not os.path.exists(p):
        return {"days": [], "schema": {}}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _row_keys(df: pd.DataFrame) -> pd.DataFrame:
    h = canonical_rows(df).reset_index(drop=True)
    return pd.DataFrame({"_kh": h, "_ko": h.groupby(h).cumcount().astype("int64")})


def archive_snapshot_days(base_dir: str, days: list) -> dict:
    """Přidá denní snapshoty `days` (["YYYY-MM-DD"], novější než poslední
    archivovaný den) do archivu. Denní soubory NEMAŽE — to dělá volající
    až po ověření (read_snapshot proti originálu). Vrací {"added", "rows"}."""
    import json
    meta = _archive_meta(base_dir)
    apath = os.path.join(base_dir, ARCHIVE_FILE)
    arch = pd.read_parquet(apath) if os.path.exists(apath) else pd.DataFrame()
    added = 0
    for day in sorted(days):
        if meta["days"] and day <= meta["days"][-1]:
            continue
        snap = pd.read_parquet(os.path.join(base_dir, f"{day}.parquet")).reset_index(drop=True)
        keys = _row_keys(snap)
        prev = meta["days"][-1] if meta["days"] else None
        if not arch.empty and prev is not None:
            open_rows = arch.index[arch["_to"] == prev]
            open_keys = pd.MultiIndex.from_frame(arch.loc[open_rows, ["_kh", "_ko"]])
            cur_keys = pd.MultiIndex.from_frame(keys)
            cont = open_keys.isin(cur_keys)
            arch.loc[open_rows[cont], "_to"] = day
            new_mask = ~cur_keys.isin(open_keys)
        else:
            new_mask = pd.Series(True, index=keys.index).to_numpy()
        new = pd.concat([snap[new_mask].reset_index(drop=True), keys[new_mask].reset_index(drop=True)], axis=1)
        new["_from"] = day
        new["_to"] = day
        arch = new if arch.empty else pd.concat([arch, new], ignore_index=True)
        meta["days"].append(day)
        meta["schema"][day] = {c: str(t) for c, t in snap.dtypes.items()}
        added += 1
    if added:
        tmp = apath + ".tmp"
        arch.to_parquet(tmp, index=False)
        os.replace(tmp, apath)
        with open(os.path.join(base_dir, ARCHIVE_META) + ".tmp", "w", encoding="utf-8") as f:
            json.dump(meta, f)
        os.replace(os.path.join(base_dir, ARCHIVE_META) + ".tmp", os.path.join(base_dir, ARCHIVE_META))
        _ARCHIVE_CACHE.pop(base_dir, None)
    return {"added": added, "rows": len(arch)}


_ARCHIVE_CACHE = {}


def read_archived_snapshot(base_dir: str, day: str) -> pd.DataFrame:
    meta = _archive_meta(base_dir)
    if day not in meta.get("schema", {}):
        return pd.DataFrame()
    apath = os.path.join(base_dir, ARCHIVE_FILE)
    mtime = os.path.getmtime(apath)
    cached = _ARCHIVE_CACHE.get(base_dir)
    if cached is None or cached[0] != mtime:
        cached = (mtime, pd.read_parquet(apath))
        _ARCHIVE_CACHE[base_dir] = cached
    arch = cached[1]
    schema = meta["schema"][day]
    out = arch[(arch["_from"] <= day) & (arch["_to"] >= day)][list(schema)].reset_index(drop=True)
    for c, t in schema.items():
        if str(out[c].dtype) != t:
            try:
                out[c] = out[c].astype(t)
            except (TypeError, ValueError):
                pass
    return out


def snapshot_days(base_dir: str, fmt: str = "parquet") -> list:
    """Dny ("YYYY-MM-DD"), pro které je snapshot — denní soubory + archiv."""
    days = set(_archive_meta(base_dir).get("days", [])) if os.path.isdir(base_dir) else set()
    for f in glob.glob(os.path.join(base_dir, f"*.{fmt}")):
        stem = os.path.basename(f).removesuffix(f".{fmt}")
        if len(stem) == 10 and stem[4] == "-" and stem[7] == "-":
            days.add(stem)
    return sorted(days)
