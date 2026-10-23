# REBUILD_NOTES — přechod ze Streamlitu na FastAPI (větev `rebuild/fastapi`)

Stav k 2026-10-23. Větev založená z `main` @ 7a36a68. `main` ani
`.github/workflows` se neměnily, `data/history/` v repu zůstává.

**Důležité omezení ověření:** v pracovním prostředí nebyly k dispozici API
tokeny a síť k datovým zdrojům byla zablokovaná (povolené jen PyPI).
**Žádné živé volání žádného API nebylo ověřeno.** Všechno, co sahá na síť,
je otestované jen proti mockovaným odpovědím ve tvaru odvozeném z kódu
(ne ze skutečných odpovědí). Web a výpočty jsou ověřené nad skutečnými
daty z `data/history/` v repu.

## Hotovo

### 1. `core/` — datová vrstva bez Streamlitu
- `core/settings.py` — konfigurace z env + `~/.config/ppdash/env`;
  `PPDASH_DATA_DIR` (default `/var/lib/ppdash`, historie v `.../history`,
  cache v `.../cache`). Vývojový fallback: když proměnná není nastavená
  a `/var/lib/ppdash` neexistuje, použije se `data/history` v repu —
  stará `app.py` i workflow na `main` tak fungují dál.
- `core/cache.py` — `@ttl_cache(ttl, disk=False)`: paměť + volitelně disk
  (výsledky API přežijí restart), single-flight zámek, mělké kopie
  výsledků, výjimky se necachují. TTL převzaté 1:1 z `st.cache_data`.
- `data/*`: žádný import Streamlitu. ČEPS SOAP klient a ENTSO-E klient
  se vytváří líně (dřív při importu — ČEPS stahoval WSDL při startu,
  ENTSO-E bez tokenu shodil import celé aplikace). `config.py` už
  Streamlit neimportuje (jen `data_status_row` pro starou app, líně).
- Tokeny jen z env: `ENTSOE_TOKEN`, `GIE_API_KEY` (dřív natvrdo v
  `data/gie.py`, `data/lng.py`, `scripts/update_gas_history.py` — **klíč
  je v git historii, doporučuji ho na GIE přegenerovat**),
  `DELTAGREEN_API_KEY` (dřív se zadával v sidebaru).
- Stará `app.py` jde spustit (`streamlit run app.py`); ověřeno přes
  Streamlit AppTest: Plyn, Report, CoT bez výjimek. Elektřina a ČEPS
  odstávky bez tokenu spadnou na chybějícím ENTSOE_TOKEN (dřív app bez
  tokenu spadla už při importu) — s tokenem je cesta beze změny.

### 2. `scripts/update_all.py`
- Jeden vstup pro všechny aktualizace: úlohy `entsog, entsog_allocation,
  gie, hydro, entsog_operational, lng, gassco, gassco_umm, dap_europe,
  generation, outages, energy_charts, cot`; skupiny `gas`, `power`, `all`.
  `--only` / `--skip` (čárkou, opakovatelné), `--list`, `-v`, `--log-file`.
- Výchozí běh = vše kromě `cot` (ten má vlastní timer so/út).
- Logování přes `logging` (pod systemd do journald), souhrn na konci.
- Exit kódy: 0 OK (i s varováním — částečná chyba, např. 1 země),
  1 aspoň jedna úloha selhala, 2 špatné argumenty, 3 běží jiná aktualizace
  (zámek `history/.update.lock`).
- Idempotentní: upsert s dedupem / write-once snapshoty (beze změny
  logiky), ověřeno testy (opakovaný zápis nic nezdvojí).
- Logika přesunutá do `core/updaters.py` beze změny chování; úplné
  selhání úlohy teď vyhodí výjimku (dřív se jen vypsalo a skript skončil 0).
- `scripts/update_gas_history.py` a `scripts/update_ice_cot.py` zůstávají
  jako tenké obaly (kvůli workflow na `main`), dál vždy exit 0 a
  `GITHUB_OUTPUT` u COT.

### Energy-Charts (commit 7a36a68)
- V `core`/`update_all.py` jako úloha `energy_charts` (zatím jen `cz`):
  `update_energy_charts()` navazuje **od konce uložených dat** každého
  komba (−2 dny překryv, u forecastu do zítřka), stahuje po chuncích
  (≤ 1 kalendářní rok; frequency 3denní okna) a **každý chunk hned
  zapisuje** (upsert/merge s dedupem). Frequency se ihned převzorkuje na
  **5 min** (`resample_5min`), surová 1s data se nikdy neukládají.
- `_get()` má retry s exponenciálním backoffem pro `ConnectionError`/
  `Timeout` (dřív žádný — jedno zahozené spojení ukončilo celé kombo).
- `load_energy_charts()` umí `date_from/date_to/series_ids` a filtruje
  už při čtení (public_power CZ celé = 2.5M řádků ≈ 1.2 GB jako object).
- Energy-Charts data zatím **nejsou na žádné stránce webu** (dosud je
  nepoužívala ani stará app).

#### `public_power_forecast::cz:wind` — chyba v kódu, nebo v API?
Závěr: **příčina je na straně API**, kód ji ale zhoršoval.
- Stejná kódová cesta (`_fetch_start_end_years` → `_get`) funguje pro
  `solar` a `load` (122k řádků); URL se liší jen hodnotou
  `production_type`. `RemoteDisconnected` = server zavřel spojení bez
  HTTP odpovědi (typicky pád/timeout workeru za proxy), ne chyba v
  parametrech, kterou by API vrátilo jako 4xx.
- Pro CZ vrací `wind_onshore` 200 s **nulou řádků** (viz progress.json)
  a `wind_offshore` taky nic — „wind“ je jen jejich součet, takže server
  pravděpodobně padá při skládání agregátu z prázdných podkladů.
  Je možné i to, že `wind` není platná hodnota `production_type`
  (dokumentace API uvádí solar/wind_onshore/wind_offshore/load) a server
  na ni místo 4xx spadne. **NEOVĚŘENO** — bez přístupu k API.
- Chyba v kódu: `_get` nechytal síťové výjimky ani je neopakoval, takže
  kombo zůstalo trvale `done=false` a při každém běhu backfillu se
  zkoušelo znovu.
- Řešení: `wind` vyřazen z `production_types` (odvoditelný z
  onshore+offshore, pro CZ beztak bez dat), přidán retry. Záznam
  `public_power_forecast::cz:wind` v `energy_charts_progress.json` zůstal
  (soubor jsem neupravoval) — nic ho už nečte.

### 3. `web/` — FastAPI + Jinja2 + Plotly
- Stránky a záložky podle dnešních sekcí:
  - **Elektřina**: Odchylka & Generace, ČEPS, Výroba/Odstávky, DAP ceny,
    Rezervy, Hydro, Delta Green, Data, DAP mapa (+ banner a KPI nahoře)
  - **Plyn**: Mapa, Toky, Sezonnost, Kapacity, Nominace, Zásobníky, LNG,
    GASSCO, Historie
  - **Report**: Toky plynu, GASSCO, Zásobníky, DAP mapa
  - **Odstávky ČEPS** (PU/GU gantt, instalovaná kapacita, tabulky, změny)
  - **CoT**: TTF, EUA, Brent, vlastní výběr
- Záložky se načítají líně (HTML fragment `/frag/<page>/<tab>`), grafy
  jako Plotly JSON (`/api/chart/<page>/<tab>/<id>`) až když doscrollují
  do obrazovky. Ovládací prvky jsou formulář — změna znovu načte
  fragment (kaskádové filtry se řeší na serveru), stav je v URL
  (`?tab=…&…`, jde sdílet/obnovit).
- ČEPS záložka a dashboard: auto-refresh 60 s (přenačtou se jen KPI
  bloky a grafy přes `Plotly.react`, bez probliknutí); hlavička Elektřiny
  taky 60 s. Hlavička bere odchylku a výrobu z ČEPS (živě, ISP průměr ×
  0.25 h → MWh, ať platí původní práh ±20 MWh); ENTSO-E jen jako fallback.
- `/healthz` (bez čtení dat, 503 když chybí adresář historie),
  `/api/cache/clear` (tlačítko „Obnovit data“), `/api/cache/stats`,
  CSV exporty (odchylka, generace, rezervy, odstávky).
- Vlastní vzhled: CSS proměnné, světlý/tmavý režim (podle systému +
  přepínač, uloženo v localStorage), grafy se přebarvují na klientu
  (průhledné pozadí, mřížka, popisky, podklad mapy). Mobil: jednosloupcové
  rozložení, posuvná navigace a záložky.
- plotly.js se servíruje z nainstalovaného balíku `plotly` (žádné CDN).
- Obsah grafů zachován — web volá stejné funkce z `charts/`. Výjimky:
  KPI hlavička z ČEPS (viz výš), CoT historie net pozice je Plotly graf
  místo `st.line_chart`, Delta Green klíč z env místo sidebaru,
  nastavení baterie je ve formuláři záložky DAP místo sidebaru,
  přepínače „načíst celou historii“ zmizely (celá historie je vždy).

### 4. Výkon pro 2 GB RAM
- Parquet se čte přes pyarrow jen v potřebných sloupcích, s filtry při
  čtení a textové sloupce rovnou jako dictionary → pandas category:
  - ENTSO-G toky celá historie: ~1 GB (object) → 27 MB, čtení 0.3 s
    (dřív 4–8 s pro celou historii, proto ji stará app oknovala).
  - ENTSOG operational (Nominace): místo okna 12 měsíců celé Evropy
    (~180 MB, celá historie ~4.8 GB) se čte jen vybraný bod (~0.5 MB,
    0.14 s) + malý index zemí/bodů. Kapacity/interrupce s filtrem při
    čtení (22 MB).
  - Výroba (sezonnost): jen vybraný zdroj.
- `ARROW_DEFAULT_MEMORY_POOL=system` (+ nastaveno i v kódu),
  `MALLOC_ARENA_MAX=2`, `malloc_trim` po pomalých načteních.
- Naměřeno (lokálně, data z repa): start 125 MB RSS, po projití všech
  záložek a grafů ~430–490 MB RSS, z toho ~180 MB data v cache.
- Max 8 souběžně počítaných požadavků (`PPDASH_THREADS`), 1 uvicorn worker.
- Při startu se nic nenačítá (žádná síť, žádná data).

#### OPTIMIZATION_REPORT.md — stav
| Nález | Stav |
|---|---|
| `load_entsog_history()` 3× za sebou | už opraveno dřív (app.py načítá jednou); web cache |
| live `fetch_entsog_flows` pro `pivot_gas` | už opraveno dřív (pivot z historie) |
| `load_entsog_history()` bez cache | opraveno (cache), navíc category čtení |
| odchylka/generace z ENTSO-E místo ČEPS | **web: hlavička z ČEPS**; ENTSO-E zůstává pro generaci podle PSR, odstávky, prognózu, ceny |
| duplicitní SOAP volání ČEPS | **opraveno** — vše z jednoho `fetch_ceps_all()` |
| `update_gie()` duplicitní CZ soubor | už opraveno dřív |
| `update_entsog()` čte celý parquet kvůli last_date | **opraveno** (`last_date_partitioned` čte jen sloupec data) |
| `YEAR_COLORS` dead code | už opraveno dřív |
| `expand_capacity()` O(n×m) | modul už neexistuje; obdobná smyčka v `active_capacity_dedup` zůstala (počítá se jen pro 1 bod, rychlé) |
| hydro pevný 30denní překryv | **opraveno** (`max(30, mezera + 7)`) |
| GIE 14denní cutoff | už parametrizováno (`GIE_ALSI_REVISION_WINDOW_DAYS`) |
| `range(-104, 52*3)` | modul už neexistuje |
| `fetch_reserves()` pevných 10 dní | neopraveno (beze změny chování) |
| `load_gie_all()` celé CSV | částečně — čtou se jen používané sloupce |
| `sys.path.insert` absolutní cesta | už opraveno dřív |

### 5. `deploy/`
- `systemd/ppdash-web.service` (uvicorn `127.0.0.1:8501`, MemoryMax 1400M,
  hardening), `ppdash-update.service` + `.timer` (06:00, 17:00),
  `ppdash-update-cot.service` + `.timer` (so + út 08:00).
- `env.example` (bez hodnot), `cloudflared-config.example.yml`,
  `DEPLOY.md` (LXC, uživatel, venv, migrace dat, timery, Tunnel + Access).
- `requirements-web.txt` (bez Streamlitu), `requirements-dev.txt`.

### 6. Testy (`pytest`, 133 testů, ~12 s)
- `tests/test_core.py` — cache (TTL, kopie, výjimky, disk), env soubor,
  partitioned čtení/zápis (idempotence).
- `tests/test_sources.py` — každý zdroj s mockovanou sítí: ČEPS, ENTSO-E
  (fetchery i updatery hydro/DAP Europe/výroba/odstávky), ENTSO-G toky,
  ENTSOG operational, GIE, ALSI/LNG, GASSCO (nominace, realtime, UMM
  xlexport), ICE CoT, Energy-Charts (inkrementální update, frequency
  5 min, retry), Delta Green, `update_all.py` (výběr úloh, exit kódy, zámek).
- `tests/test_web.py` — každá stránka, každý fragment a každý graf vrací
  200 (bez tokenů a se zakázanou sítí: grafy živých API vrací JSON
  s chybou/prázdnem, ne 500), CSV exporty, `/healthz`.
- Síť je v testech globálně zakázaná. `PPDASH_TEST_EMPTY=1` spustí testy
  nad prázdným datovým adresářem (také prochází).

### Opravené chyby po cestě
- `data/lng.py::fetch_lng_all` — `break` po poslední stránce/chybě
  ukončil jen smyčku pokusů, stránkování běželo dál (až 29 requestů na
  zemi, při nečekané odpovědi duplicity).
- `charts/gas.py::fig_gas_map` — živý GET na GASSCO realTimeAtom při
  každém vykreslení mapy; teď přes cachovanou `fetch_realtime_nominations`.

## Nehotovo / další kroky
- Energy-Charts data nejsou ve webu (žádná stránka je zatím nepoužívá);
  `update_energy_charts` běží jen pro `cz`.
- `data/history/` v repu se nemaže (dle zadání) a workflow na `main` dál
  commitují data — po přechodu na LXC workflow vypnout a historii z repa
  odstranit (případně i z git historie, má stovky MB).
- Streamlit (`app.py`, `views/`, `.streamlit/`, `requirements.txt`) zatím
  zůstává kvůli dočasnému souběhu; po přechodu smazat.
- Detekce „nových odstávek“ se pamatuje jen v rámci procesu (po restartu
  web první porovnání nemá) — stejné jako dřív session_state.
- `fetch_reserves()` 10 dní natvrdo, `load_gie_all()` čte všechny roky.
- Přihlášení řeší výhradně Cloudflare Access — aplikace sama žádnou
  autentizaci nemá (`/api/cache/clear` je POST bez ochrany).

## NEOVĚŘENO (nutno zkontrolovat po nasazení s tokeny)
- **Všechna živá API**: ENTSO-E, ČEPS SOAP (včetně zeep `Transport`
  timeoutů), ENTSO-G, GIE AGSI+/ALSI, GASSCO (UMM xlexport, nominace,
  realTimeAtom), ICE CoT, Energy-Charts, Delta Green. Testy používají
  odpovědi odvozené z parsovacího kódu, ne skutečné vzorky.
- Že ČEPS hlavička (odchylka jako ISP MWh z minutových MW, výroba jako
  součet sloupců `Generation`) dává smysluplná čísla — sloupce ČEPS
  odpovědi jsem neviděl.
- `fetch_ceps_svr()` teď parsuje stejnou SOAP odpověď jako
  `fetch_ceps_all()` (mapování value1..value7 beze změny) — ověřit, že
  graf SVR je stejný jako dřív.
- Chování Energy-Charts pro `production_type=wind` (viz analýza výš) a
  že inkrementální update správně navazuje na skutečná data (testováno
  jen s mockem).
- systemd unity a Cloudflare Tunnel nebyly spuštěné (v prostředí není
  systemd) — syntaxi ověřit `systemd-analyze verify`.
- Výkon/paměť naměřené na vývojovém stroji s daty z repa; na LXC změřit
  `journalctl`/`/healthz` (`rss_mb`) po pár dnech provozu.
- Vzhled ověřen screenshoty v headless Chromiu (desktop světlý/tmavý,
  mobil 390 px) — ne na skutečných zařízeních.
