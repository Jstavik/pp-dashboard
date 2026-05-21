# PP Dashboard — Optimization Audit Report
_Vygenerováno: 2026-05-21_

## Shrnutí

| Priorita | Počet nálezů |
|---|---|
| 🔴 1 — kritické (výkon / chybná data) | 4 |
| 🟡 2 — důležité (redundance / dead code) | 5 |
| 🟢 3 — nízká priorita (hardcoded hodnoty / micro-opt) | 6 |

---

## Detailní tabulka

| Soubor | Funkce | Kategorie | Problém | Navrhované řešení | Priorita |
|---|---|---|---|---|---|
| `app.py` | gas sekce | DUPLICITNÍ NAČÍTÁNÍ | `load_entsog_history()` voláno 3× za sebou (`tab_map`, `tab_bar`, `tab_season`) — každé volání čte 7.3 MB parquet | Načíst jednou před `st.tabs()`, předat hotový `df_hist` do všech záložek | 🔴 1 |
| `app.py` | gas sekce | DUPLICITNÍ API | `fetch_entsog_flows(days=90)` volá live ENTSO-G API pro `pivot_gas`, přestože `load_entsog_history()` obsahuje ta samá (a více) data | Derivovat `pivot_gas` z `load_entsog_history()` pomocí `_short_name` pivot — eliminuje HTTP call | 🔴 1 |
| `data/entsog.py` | `load_entsog_history()` | CACHE | Žádný `@st.cache_data` — při 3 voláních z `app.py` čte 7.3 MB parquet 3× per refresh | Přidat `@st.cache_data(ttl=3600)` (lazy pattern jako ostatní `load_*` funkce) | 🔴 1 |
| `data/entsoe.py` | `fetch_entsoe_data()` | ENTSO-E vs ČEPS | Stahuje `imbalance_volumes` a `generation` z ENTSO-E (TTL 30 min, zpoždění dat ~15 min) — ČEPS vrací stejná data každou minutu s menším zpožděním (~1–5 min) | Odchylka → `fetch_ceps_imbalance()`, generace → `fetch_ceps_all()["gen"]`; ENTSO-E ponechat jen pro odstávky, DAP a rezervy | 🔴 1 |
| `data/ceps.py` | `fetch_ceps_imbalance()` + `fetch_ceps_all()` | DUPLICITNÍ API | Oba volají `AktualniSystemovaOdchylkaCR` — dva SOAP requesty na stejná data (v `app.py` jsou voláni oba) | V `app.py` použít pouze `fetch_ceps_all()`, `fetch_ceps_imbalance()` zrušit nebo přesměrovat na `fetch_ceps_all()["imbal"]` | 🟡 2 |
| `scripts/update_gas_history.py` | `update_gie()` | DUPLICITNÍ SOUBOR | Stahuje CZ data do `gie_cz_storage.csv`, přestože `gie_all_storage.csv` (generovaný `update_gie_all()`) obsahuje identická CZ data ve sloupci `country_code == "CZ"` | Odstranit `update_gie()` a `fetch_gie_month()`; `load_gie_history()` přesměrovat na `gie_all_storage.csv` filtrovaný na CZ | 🟡 2 |
| `scripts/update_gas_history.py` | `update_entsog()` | NAČÍTÁNÍ DAT | `pd.read_parquet(PARQUET_PATH)` načte celý 7.3 MB soubor jen pro zjištění `last_date` | `pd.read_parquet(PARQUET_PATH, columns=["date"])` — načte jen datový sloupec (~20× méně dat) | 🟡 2 |
| `data/gie.py` | `YEAR_COLORS` | DEAD CODE | Dict s hardcoded roky 2018–2026 — `charts/storage.py` ho již neimportuje (používá vlastní `year_color()`); v roce 2027 bude neaktuální | Odstranit `YEAR_COLORS` z `data/gie.py` — je mrtvý kód | 🟡 2 |
| `data/entsog_capacity.py` | `expand_capacity()` | NAČÍTÁNÍ DAT | O(n×m) iterace: pro každé datum v `target_dates` (5 let týdně ≈ 260 iterací) prochází celý DataFrame — pomalé při velkých datech | Vectorized přístup: `pd.merge_asof` nebo interval join pomocí `pd.IntervalIndex` | 🟡 2 |
| `scripts/update_gas_history.py` | `update_hydro()` | PEVNÁ DATA | `timedelta(days=30)` hardcoded overlap — pokud Actions neběží déle než 30 dní (dovolená, výpadek), data budou mít mezeru | `overlap = max(30, (date.today() - last_date.date()).days + 7)` | 🟢 3 |
| `scripts/update_gas_history.py` | `update_gie_all()` | PEVNÁ DATA | `pd.Timedelta(days=14)` hardcoded cutoff pro overlap nových dat | Konzistentně použít `timedelta(days=7)` jako ostatní updaters, nebo parametrizovat | 🟢 3 |
| `data/entsog_capacity.py` | `expand_capacity()` | PEVNÁ DATA | `range(-104, 52*3)` = 2 roky zpět + 3 roky dopředu hardcoded | Odvodit z `df_raw["periodFrom_dt"].min()` a `df_raw["periodTo_dt"].max()` | 🟢 3 |
| `data/entsoe.py` | `fetch_reserves()` | PEVNÁ DATA | `pd.Timedelta(days=10)` hardcoded předhled rezerv | Parametrizovat `days_ahead: int = 10` | 🟢 3 |
| `data/gie.py` | `load_gie_all()` | NAČÍTÁNÍ DAT | Načítá celý 5.7 MB CSV při každém refreshi — bez filtrace na relevant roky | Přidat `nrows` nebo filtraci po načtení na `gasDayStart >= pd.Timestamp.now() - pd.DateOffset(years=8)` | 🟢 3 |
| `scripts/update_gas_history.py` | `update_hydro()` | PEVNÁ DATA | `sys.path.insert(0, "/workspaces/pp-dashboard")` hardcoded absolutní cesta | Použít `os.path.dirname(os.path.dirname(os.path.abspath(__file__)))` nebo spouštět script vždy z kořene repozitáře | 🟢 3 |

---

## Nejvyšší dopad (co opravit nejdřív)

### 1. Cache pro `load_entsog_history()` + jedno volání v app.py

```python
# data/entsog.py — přidat cache
def load_entsog_history() -> pd.DataFrame:
    try:
        import streamlit as st
        @st.cache_data(ttl=3600, show_spinner=False)
        def _load():
            ...
        return _load()
    except ImportError:
        ...

# app.py — načíst jednou
df_hist = load_entsog_history()   # jeden read, cached
with tab_map:    fig_gas_map(df_hist)
with tab_bar:    ... df_hist ...
with tab_season: ... df_hist ...
```

### 2. Eliminovat live API call pro `pivot_gas`

```python
# Místo: pivot_gas = fetch_entsog_flows(days=90)
# Použít:
def _make_pivot(df_hist: pd.DataFrame) -> pd.DataFrame:
    df = df_hist.copy()
    df["point"] = df["pointsNames"].apply(_short_name)
    entry = df[df["directionKey"]=="entry"].groupby(["date","point"])["value_GWh"].sum()
    exit_ = df[df["directionKey"]=="exit" ].groupby(["date","point"])["value_GWh"].sum()
    ...
```

### 3. Vectorized `expand_capacity()`

```python
# Místo O(n*m) smyčky:
df_raw["period"] = pd.IntervalIndex.from_arrays(
    df_raw["periodFrom_dt"], df_raw["periodTo_dt"], closed="both"
)
# pak pd.merge nebo boolean masking přes vektorizovaný interval lookup
```
