# REBUILD_NOTES — přechod ze Streamlitu na FastAPI (větev `rebuild/fastapi`)

Stav k 2026-10-23 (kolo 3 a 2 níže). Větev založená z `main` @ 7a36a68. `main` ani
`.github/workflows` se neměnily, `data/history/` v repu zůstává.

**Důležité omezení ověření:** v pracovním prostředí nebyly k dispozici API
tokeny a síť k datovým zdrojům byla zablokovaná (povolené jen PyPI).
**Žádné živé volání žádného API nebylo ověřeno.** Všechno, co sahá na síť,
je otestované jen proti mockovaným odpovědím ve tvaru odvozeném z kódu
(ne ze skutečných odpovědí). Web a výpočty jsou ověřené nad skutečnými
daty z `data/history/` v repu.

## Kolo 3 (2026-10-23): Průzkumník, ENTSO-E naplno, Nordic, počasí

### Průzkumník (`/pruzkumnik`) — à la Power BI
- **Katalog** datasetů je v `core/datasets.py::EXPLORE` (sémantický model:
  časový sloupec, míry s výchozí agregací, dimenze, nativní rozlišení,
  zdroj, výchozí filtry). Záložka **Katalog dat** ukazuje vše uložené
  (rozsah dat, stáří, velikost, frekvence aktualizace, odkaz do Průzkumníku).
- **Engine** `core/explore.py`: DuckDB přímo nad parquet (CSV datasety přes
  parquet zrcadlo v `cache/explore_mirror` — `read_csv(union_by_name)` na
  ~190 souborech GIE trval 8–15 s, zrcadlo 0.1 s; přepočítá se jen změněný
  soubor podle mtime). Čtou se jen soubory v okně (měsíční/denní partitiony
  podle názvu) a jen podadresáře podle filtru partition dimenze (země).
- **Agregace dvoustupňová**: (1) sloučení ostatních dimenzí v časovém bodě
  (`cagg`: součet/průměr/min/max; série s 15min a hodinovým rozlišením se
  nejdřív zarovnají na hodinu), (2) agregace v čase (`agg`: součet, průměr,
  min, max, poslední, **energie** = Σ hodinových průměrů výkonu → MWh/GWh,
  správně i přes změnu času a pro neúplný měsíc).
- **Ovládání**: dataset, míra, agregace, období (předvolby), zrnitost
  (auto/nativní/15 min/hodina/den/týden/měsíc/kvartál/rok), kategorie
  (libovolná dimenze), slicery = multiselect pro každou dimenzi (kaskádové
  — nabídka se omezuje filtry ostatních dimenzí), typ grafu: čára,
  skládané sloupce, skládaná plocha, **porovnání let** (aktuální rok tučně,
  pásmo min–max a medián předchozích let), **heatmapa rok×den** a **den×hodina**.
- **Kontingenční tabulka**: řádky čas/kategorie/rok/měsíc × sloupce
  kategorie/rok/měsíc/—, Σ u součtů; export CSV/XLSX (`/api/explorer/pivot`).
  Grafy mají export PNG/SVG/CSV/XLSX jako jinde.
- **Cross-filtering**: graf „Podle: <kategorie>“ (souhrn za období) — klik na
  sloupec = filtr na tuto kategorii (znovu = zrušit); zoom hlavního
  časového grafu přepočítá souhrn jen pro vybrané okno (`xf_from/xf_to`).
- **Pohledy**: celý stav je v URL (sdílení odkazem, „Kopírovat odkaz“);
  pojmenované pohledy se ukládají na server do
  `<PPDASH_DATA_DIR>/explorer_views.json` (bez autentizace — chrání Access).
- **Limity paměti**: DuckDB `memory_limit` 512 MB (`PPDASH_DUCKDB_MEM`),
  2 vlákna, spill do `cache/duckdb_tmp`; výsledek max 150 000 bodů
  (`PPDASH_EXPLORE_MAX_ROWS`) — zrnitost se podle odhadu (kusy × kategorie)
  zhrubne předem, s varováním; max 30 kategorií (největší). Výsledky
  v TTL cache 5 min. Detaily dotazu (SQL, čas, počet bodů) v rozbalovacím bloku.
- Výkon nad daty z repa: typický dotaz 0.05–0.6 s, výroba DE celá historie
  1.3 s (zhrubnuto na den), heatmapa rok×den celé výroby 0.6 s.

**Perspective vs. vlastní UI — zvoleno vlastní UI + DuckDB.** FINOS
Perspective (WASM, pivot drag&drop) potřebuje data v prohlížeči (nebo
server s perspective-python, který drží tabulky v RAM a posílá přes
websocket) — u našich objemů (výroba 14 M řádků, ENTSOG operational 4.6 M,
ENTSO-E rozšíření řádově víc) by to znamenalo buď posílat do prohlížeče
desítky–stovky MB, nebo držet kopie tabulek v paměti webu na 4 GB LXC.
Zadání chce agregaci na serveru a do prohlížeče jen výsledek; DuckDB to
dělá přímo nad parquet bez kopie dat a s limitem paměti. Vlastní UI navíc
zná doménu (energie z výkonu přes změnu času, zarovnání 15min/hodina,
porovnání let, české názvy a barvy zdrojů), sdílí export a vzhled se zbytkem
webu a nepřidává ~5 MB WASM. Cena: pivot se ovládá výběrem řádků/sloupců,
ne drag&drop. Perspective by šlo později přidat jako „viewer“ jen nad
výsledkem dotazu (malá tabulka), kdyby byl drag&drop potřeba.

### ENTSO-E naplno (`data/entsoe_history.py`, stránka **Trh EU**)
| Dataset (history/) | Úloha | Obsah | Od |
|---|---|---|---|
| `entsoe_prices/<ZONE>/` | `entsoe_prices` | DA ceny 45 bidding zón: CZ, DE_LU, AT, SK, PL, HU, SI, HR, RO, BG, GR, RS, ME, MK, FR, BE, NL, CH, ES, PT, IE_SEM, IT (7 zón), EE, LV, LT, NO1–5, SE1–4, FI, DK1–2 | 2019 |
| `entsoe_load/<AREA>/` | `entsoe_load` | skutečná spotřeba (`kind=actual`) + prognóza D-1 (`forecast`), 25 zemí + Nordic zóny | 2019 |
| `entsoe_ws_forecast/<AREA>/` | `entsoe_ws` | day-ahead prognóza Solar / Wind Onshore / Wind Offshore, 30 oblastí | 2019 |
| `entsoe_flows/<FROM>_<TO>/` | `entsoe_flows` | fyzické toky (`physical`) + plánované výměny (`scheduled`), 45 hranic × 2 směry: CZ + sousedé, DE/AT/PL/SK hranice, celý Nordic | 2021 |
| `generation/<CC>/` (rozšířeno) | `generation` | + NL, BE, IT, ES, CH, DK, NO, SE, FI (`config.GENERATION_COUNTRIES`); odstávky dál jen 7 zemí | 2020 |

- Vlastní timer **`ppdash-update-entsoe`** (07:10, 13:20 po výsledcích SDAC,
  19:10) se zámkem `.entsoe.lock` — běží souběžně s denním během i live,
  `Nice=10`, `CPUWeight=50`, `MemoryMax=800M`. Ve výchozím běhu `update_all.py`
  není (skupina `entsoe`).
- Navazování per řada (u sdílených adresářů actual/forecast a physical/
  scheduled zvlášť podle `kind`), překryv 2–3 dny kvůli revizím, každý chunk
  se hned zapíše; zápis do stejného adresáře z více vláken je serializovaný.
- **Rate limit**: jeden limiter na proces, klouzavé okno 60 s,
  `PPDASH_ENTSOE_RPM=250` (limit ENTSO-E je 400/min, při překročení blokuje
  token na 10 min — rezerva pro denní běh hydro/DAP/výroby/odstávek, který
  jde vlastním klientem sériově), 4 vlákna; 429 → čekání 60 s.
- **Backfill** rozložený do běhů: `PPDASH_ENTSOE_BUDGET_S=2400` s na úlohu
  (pak „pokračuje příští běh“), výroba `PPDASH_GENERATION_BUDGET_S=1800` na zemi
  (nově zapisuje po každém 30denním chunku — dřív až na konci). Odhad prvního
  backfillu: ceny ~350 req, spotřeba ~400, OZE ~240 (roční chunky), toky
  ~180 řad × ~70 měsíců ≈ 12 600 req (entsoe-py `query_crossborder_flows`
  je po měsících) → při 250/min ~1 h čistého času + latence ENTSO-E, tj. několik
  běhů timeru (1–2 dny). Start lze posunout `PPDASH_ENTSOE_START`,
  `PPDASH_ENTSOE_FLOWS_START`.
- Velikost (odhad ze syntetických dat 2024→, reálná budou menší díky
  opakujícím se hodnotám): ceny ~10 MB/rok, toky ~20 MB/rok, spotřeba ~5 MB/rok.
- **Stránka Trh EU** (`/trh-eu`): Ceny zón (čáry vybraných zón, heatmapa
  zóny × dny, tabulka posledního dne base/peak/min/max), Spotřeba a prognóza
  (skutečnost vs. D-1 + odchylka, MAE/bias/MAPE, porovnání let), Prognóza OZE
  (skládaná plocha + denní GWh), Přeshraniční toky (saldo po sousedech,
  fyzické vs. plánované, tabulka GWh). Všechno i v Průzkumníku (4 nové
  datasety v katalogu). Elektřina → Výroba nabízí nové země.
- **NEOVĚŘENO** (bez sítě/tokenu): názvy oblastí v entsoe-py pro load
  `NO`/`SE`/`DK` (country) a `DE_LU` (BZN) — možná bude potřeba `DE`;
  hranice `CZ–DE_LU` pro fyzické toky (jinak `CZ–DE`); tvar odpovědi
  `query_wind_and_solar_forecast` u oblastí s offshore; že IT/ES/DK/NO/SE
  výroba projde `PSR_CODE_BY_SOURCE_TYPE` bez nenamapovaných typů (varuje
  v logu); reálná rychlost backfillu. Testováno s falešným klientem
  (`tests/test_entsoe_history.py`).

### Nordic (`data/nordic.py`, stránka **Nordic**)
- **Co už bylo**: `hydro_reservoirs/` = ENTSO-E 16.1.D (Aggregated Filling
  Rate of Water Reservoirs), **týdně, po celých zemích** (20 zemí vč. NO, SE,
  FI; GWh; od 12/2019). Žádné zóny, žádná min/max statistika.
- **Doplněno** (úlohy ve výchozím běhu 2× denně, skupina `nordic`):
  | Dataset | Úloha | Zdroj | Obsah |
  |---|---|---|---|
  | `nve_reservoirs.parquet` (+ `_stats`) | `nve` | NVE Magasinstatistikk, bez klíče | plnění % a TWh, celé NO + NO1–NO5, týdně (historie celá v 1 requestu), statistika min/medián/max po týdnech |
  | `fingrid/` | `fingrid` | Fingrid open data, **`FINGRID_API_KEY`** (zdarma), bez klíče se přeskočí | spotřeba, výroba, vítr, jádro, voda, prognózy, přenosy FI–SE1/SE3/EE/NO (15 min) |
  | `energinet/<dataset>/` | `energinet` | Energi Data Service, bez klíče | `ProductionConsumptionSettlement` (výroba/spotřeba/výměny DK1/DK2 hodinově), `Forecasts_Hour` (prognózy OZE) — sloupce převedené obecně na série |
- **Švédsko**: volně strojově čitelná data o nádržích nejsou (Energiföretagen
  „Kraftläget“ jen PDF/Excel, Nord Pool hydro placený) → SE z ENTSO-E.
- **Stránka Nordic** (`/nordic`): Ceny a spready (ceny NO1–5, SE1–4, FI,
  DK1–2, DE_LU; spread vůči DE nebo „systémové ceně“ — Nord Pool systémová
  cena není volně, použit **proxy = prostý průměr nordických zón**, tabulka
  průměrů a spreadů), Hydrobilance (NVE plnění aktuální rok vs. min–max a
  medián, KPI vs. medián a vs. loni, tabulka NO1–5; ENTSO-E NO/SE/FI vs.
  medián/pásmo předchozích let), Toky (saldo zóny po sousedech, Nordic
  NO+SE+FI ↔ okolí DK/DE/NL/PL/LT/EE), Finsko a Dánsko (Fingrid, Energinet).
  Vše i v Průzkumníku (datasety NVE, Fingrid, Energinet, hydro).
- **NEOVĚŘENO**: tvar JSON NVE (`dato_Id`, `omrType` NO/EL, `omrnr`,
  `iso_aar`, `iso_uke`, `fyllingsgrad` 0–1, `fylling_TWh`, `kapasitet_TWh`;
  `HentOffentligDataMinMaxMedian`: `minFyllingsgrad`, `medianFyllingsgrad`…) —
  parsování je case-insensitive a toleruje chybějící pole; **ID datasetů
  Fingrid** (124 spotřeba, 74 výroba, 75 vítr, 188 jádro, 191 voda, 165/245/248
  prognózy, 87/89/180/187 přenosy) a tvar `/api/datasets/{id}/data`
  (`data[].startTime/value`, `pagination.lastPage`), limit ~10 req/min;
  Energinet parametry `start/end/timezone=utc/limit=0` a názvy datasetů
  (Energinet je občas přejmenovává — chyba jednoho = WARN).

### Počasí — rešerše (co je zdarma a legálně použitelné)
| Zdroj | Co | Licence / podmínky | Použití tady |
|---|---|---|---|
| **Open-Meteo** API | forecast (ECMWF IFS 0.25°, AIFS, GFS, ICON, …), ensemble (ECMWF ENS 51 čl., GEFS, ICON-EPS), archive (ERA5/ERA5-Land od 1940) | data **CC BY 4.0** (uvést „Weather data by Open-Meteo.com“ + odkaz); **free API jen nekomerčně**, < 10 000 volání/den, 5 000/h, 600/min ([terms](https://open-meteo.com/en/terms)). Komerčně: placený klíč (`customer-*` hosty) nebo vlastní instance (open-source, AGPL) | **implementováno** — `OPENMETEO_API_KEY` přepne na komerční hosty, `OPENMETEO_BASE_URL` na vlastní instanci |
| **ECMWF open data** (přímo GRIB2) | IFS + AIFS 0.25°, ENS, posledních ~12 běhů | **CC BY 4.0 + ECMWF Terms of Use** — i komerčně s atribucí ([open data](https://www.ecmwf.int/en/forecasts/datasets/open-data)) | neimplementováno (GRIB → potřeba eccodes/cfgrib, ~stovky MB/běh); legální alternativa k Open-Meteo pro komerční použití |
| **ECMWF charts** (charts.ecmwf.int) | mapy, meteogramy | podmínky pro grafiku nejsou jasně CC BY (open data ano) | **jen odkaz** |
| **Tropical Tidbits** | obrázky map modelů | žádné zveřejněné podmínky pro vkládání/stahování; obrázky autora → bez svolení **jen odkaz**, nestahovat, neembedovat | odkaz |
| **DWD Open Data** | ICON/ICON-EU GRIB, pozorování stanic, klima | zdarma i komerčně, atribuce („Quelle: Deutscher Wetterdienst“, GeoNutzV / CC BY 4.0) | odkaz; kandidát na DE stanice |
| **ČHMÚ open data** (opendata.chmi.cz) | stanice, klimatologie | otevřená data (CC BY 4.0) | odkaz; kandidát na CZ stanice/normály |
| **Windy** | interaktivní mapa | oficiální embed widget je k vkládání určený | embed v záložce Mapy |
| Copernicus CDS (ERA5) | reanalýza | zdarma i komerčně (Copernicus licence, atribuce) | přes Open-Meteo archive |

**Pozor:** PP Dashboard je interní nástroj firmy → Open-Meteo **free API
pravděpodobně nesplňuje „nekomerční použití“**. Doporučení: koupit Open-Meteo
API předplatné (nejjednodušší, kód připravený — jen `OPENMETEO_API_KEY`) nebo
provozovat vlastní Open-Meteo instanci (Docker, stahuje ECMWF/DWD open data,
které komerční použití dovolují) a nastavit `OPENMETEO_BASE_URL`.

### Počasí — implementace (`data/weather.py`, stránka **Počasí**)
- Oblasti CZ, DE, NORDIC; pro každou veličinu vlastní vážené body (teplota
  ~ obyvatelstvo, vítr 100 m ~ větrné parky, slunce ~ FVE, srážky jen
  Nordic = hydro povodí NO). Váhy jsou hrubý odhad v `REGIONS`.
- Datasety: `weather_daily/<R>/` (ERA5 denně od 1991 kvůli normálu
  1991–2020 + posledních ~6 dní z forecast API, ERA5 je pak přepíše),
  `weather_fc/<R>/` (hodinová předpověď modelů, poslední běh),
  `weather_fc_daily/<R>/` (denní snapshot každého dne vydání → změna
  předpovědi proti včerejšku), `weather_ens/<R>.parquet` (p10/p50/p90
  ensemblu, poslední běh).
- Timer **`ppdash-update-weather`** 05:50/11:50/17:50/23:50 (po bězích
  00/06/12/18 UTC), zámek `.weather.lock`; ~30 volání na běh + backfill
  ERA5 s rozpočtem `PPDASH_WEATHER_BUDGET_CALLS=2500` „volání“ na běh
  (Open-Meteo váží volání délkou období a počtem míst; celý backfill od 1991
  ≈ 2–3 dny běhů, pod denním limitem).
- Modely `PPDASH_WEATHER_MODELS` (default `ecmwf_ifs025,ecmwf_aifs025_single,
  gfs_seamless,icon_seamless`), ensemble `PPDASH_WEATHER_ENS_MODELS`
  (`ecmwf_ifs025,gfs025`); neznámý model (HTTP 400) → zkusí se modely
  jednotlivě a vadný se přeskočí.
- Stránka `/pocasi`: Teplota a HDD, Vítr 100 m, Slunce, Srážky (Nordic) —
  denní výhled (skutečnost + modely + ENS p10–p90 + normál ±1σ), tabulka
  7/14 dní po modelech vs. normál (u teploty HDD vs. normál), kumulované HDD
  v topné sezóně (letos + ECMWF předpověď vs. loni vs. normál), hodinová
  předpověď s ensemble pásmem, změna předpovědi proti předchozímu dni.
  Záložka Mapy: odkazy (ECMWF, TT, DWD, ČHMÚ, yr.no) + Windy embed.
- HDD/CDD podle Eurostatu (HDD = 18 − Tm pro Tm ≤ 15 °C; CDD = Tm − 21 pro
  Tm ≥ 24 °C). V Průzkumníku datasety Počasí (denní vč. HDD/CDD měr,
  hodinová předpověď, denní předpovědi podle dne vydání).
- **NEOVĚŘENO**: názvy modelů v Open-Meteo (`ecmwf_aifs025_single`, `gfs025`
  pro ensemble), tvar klíčů ensemble (`<var>_memberNN_<model>`), že
  `past_days` + `models=best_match` vrací poslední dny, reálná „cena“ backfillu
  v kvótě. Testováno proti falešným odpovědím ve tvaru Open-Meteo.

### Testy (kolo 3)
- 0 testů (zelené i s `PPDASH_TEST_EMPTY=1`): nové `tests/test_explore.py`
  (engine: energie přes změnu času, dvoustupňová agregace, prořezávání
  partition, zhrubnutí, CSV zrcadlo, sezónnost, pivot/pohledy/cross-filter
  endpointy, limity DuckDB), `tests/test_entsoe_history.py` (navazování,
  rozpočet, rate limiter, souběžný zápis, stránka Trh EU nad daty),
  `tests/test_nordic.py` (NVE, Fingrid stránkování/klíč, Energinet melt,
  stránka Nordic), `tests/test_weather.py` (modely + fallback, ensemble
  kvantily, ERA5 backfill s rozpočtem, normál, HDD/CDD, komerční URL,
  stránka Počasí). Nové stránky jsou i v parametrizovaných smoke testech.

### Paměť a souběh úloh
- Měřeno: web nad historií z repa + syntetická ENTSO-E data (2024→, 45 zón,
  toky) + počasí; proklikány **všechny** záložky a grafy všech stránek a
  Průzkumník přes všechny datasety (celá historie, heatmapy, sezónnost,
  nativní zrnitost, pivot, i bez filtrů): **max RSS 853 MB**, ustáleně
  ~550 MB (cíl < 1,2 GB). Největší špička je stávající graf Energy-Charts
  „Výroba“ za celou historii (+135 MB), Průzkumník bez filtrů na výrobě
  (14 M řádků) +100 MB.
- DuckDB `memory_limit` sníženo na **256 MB** (při 512 MB dělal dotaz bez
  filtrů +330 MB RSS; 256 MB je stejně rychlé — 4.7 s — a spilluje na disk),
  2 vlákna, sdílený limit pro všechny souběžné dotazy (jedna instance).
- Web: `MemoryHigh=1300M`, `MemoryMax=1600M` (4 GB LXC).
- Minutový `ppdash-live` nic neblokuje: vlastní zámky `.live.lock`,
  `.entsoe.lock`, `.weather.lock`, `.update.lock` (každá skupina zapisuje do
  jiných datasetů); live má `CPUWeight/IOWeight=200`, dlouhé aktualizace
  `Nice=10`, `CPUWeight/IOWeight=50`. Součet `MemoryMax` všech služeb
  (web 1.6 G + update 1 G + entsoe 0.8 G + weather 0.5 G + live 0.4 G) je
  nad 4 GB jen teoreticky — typicky běží současně nejvýš 2 aktualizace.
- **NEOVĚŘENO**: že uživatelský systemd má delegované `cpu`/`io`
  controllery (bez nich se `CPUWeight/IOWeight` ignorují — neškodí);
  skutečná paměť s plnou historií ENTSO-E od 2019 (data ~5× větší než
  syntetická; dotazy jsou ale omezené DuckDB limitem a stropem výsledku).

### Katalog datasetů (stav kola 3)
| Dataset (history/) | Úloha | Timer / frekvence | Rozlišení | Od |
|---|---|---|---|---|
| `ceps/<ds>/` | ceps | live, 1 min | 1 min | provoz |
| `gassco_realtime/`, `gassco_umm*` | gassco_realtime, gassco_umm | live, 1 min | publikace | provoz |
| `entsog_flows/`, `entsog_operational/`, `gie_all_storage/`, `lng_storage/`, `gassco_nominations/` | entsog*, gie, lng, gassco | update 06:00+17:00 | den | 2011–2021 |
| `hydro_reservoirs/` | hydro | update 2× denně | týden, země | 2019-12 |
| `dap_europe/` | dap_europe | update 2× denně | den (base/peak) | 2026-06 |
| `generation/<CC>/` (16 zemí) | generation | update 2× denně | 15 min/h | 2020 |
| `outages*` | outages | update 2× denně | události | rolling |
| `energy_charts/…` | energy_charts | update 2× denně | 15 min / 5 min | 2018 |
| `nve_reservoirs*.parquet` | nve | update 2× denně (NVE st) | týden | 1995 |
| `fingrid/` | fingrid (klíč) | update 2× denně | 15 min | 2019 |
| `energinet/<ds>/` | energinet | update 2× denně | hodina | 2019 |
| `entsoe_prices/`, `entsoe_load/`, `entsoe_ws_forecast/` | entsoe_* | entsoe 07:10/13:20/19:10 | 15 min/h | 2019 |
| `entsoe_flows/` | entsoe_flows | entsoe 3× denně | 15 min/h | 2021 |
| `weather_daily/`, `weather_fc*/`, `weather_ens/` | weather | weather 4× denně | den / hodina | 1991 (ERA5) |
| `ice_cot.parquet` | cot | st 14:00+19:00, čt+so 08:00 | týden | 2022 |

Sémantický katalog pro Průzkumník = `core/datasets.py::EXPLORE` (26 datasetů),
záložka Průzkumník → Katalog dat ukazuje rozsah a stáří živě.

### Co má ověřit sezení v kontejneru (kolo 3)
1. `git pull && deploy/install_user.sh --no-seed` (doinstaluje `duckdb`,
   nové unity `ppdash-update-entsoe`, `ppdash-update-weather`), `deploy/status.sh`.
2. Web: `/pruzkumnik`, `/trh-eu`, `/nordic`, `/pocasi` vrací stránky;
   `/healthz` `rss_mb` po proklikání < 1200.
3. ENTSO-E: `update_all.py --only entsoe_prices -v` (rychlé) → zkontrolovat
   zóny bez dat / chyby v logu (IE_SEM, IT_CALA, ME, MK), pak `--only entsoe`
   na pozadí přes timer; ověřit `CZ–DE_LU` toky (případně změnit na `DE`),
   load `NO/SE/DK`, že limiter drží < 400 req/min (journal: `requests`).
   Výroba nových zemí: `--only generation` (rozpočet 30 min/zemi).
4. Nordic: `--only nve -v` (tvar JSON NVE!), `--only energinet -v`,
   Fingrid s `FINGRID_API_KEY` (ID datasetů v `data/nordic.py`).
5. Počasí: `--only weather -v` — názvy modelů (`ecmwf_aifs025_single`,
   `gfs025`), že ensemble vrací členy, kvóta. **Rozhodnout licenci**
   Open-Meteo (komerční klíč / vlastní instance), viz rešerše výš.
6. ICE CoT ve středu 14:00/19:00 — `journalctl --user -u ppdash-update-cot`.

## Kolo 2 (2026-10-23): vlastní databáze, živá data, Energy-Charts, export, nasazení bez roota

### Audit historie (`data/history` → `PPDASH_DATA_DIR/history`)

Registr všech datasetů je v kódu: `core/datasets.py` (zapisující úloha,
max. stáří, mrtvá data) — používá ho `scripts/data_status.py`,
`deploy/status.sh` i `deploy/install_user.sh` (vynechání mrtvých dat).

| Dataset | Zapisuje (úloha `update_all.py`) | Čte | Zobrazení |
|---|---|---|---|
| `ceps/<ds>/<YYYY-MM-DD>.parquet` **nové** | `ceps` (live, 1 min) | `data/ceps.load_ceps_from_store` ← `fetch_ceps_all` | Elektřina: hlavička KPI, Odchylka & Generace, ČEPS |
| `gassco_realtime/<YYYY-MM-DD>.parquet` **nové** | `gassco_realtime` (live, 1 min) | `data/gassco.fetch_realtime_nominations` ← `load_gassco`, `charts/gas.fig_gas_map` | Plyn: Mapa, GASSCO (dnešek); Report: Toky, GASSCO |
| `gassco_umm.parquet` | `gassco_umm` (live, 1 min; dřív 2× denně) | `load_gassco_umm` | Plyn → GASSCO (UMM tabulky, výhled) |
| `gassco_umm_snapshots/` | `gassco_umm` (1 snapshot/den) | `load_gassco_umm_snapshot`, `list_available_umm_snapshot_dates` | Plyn → GASSCO (změny UMM) |
| `gassco_nominations/` | `gassco` (2× denně) | `load_gassco_history`/`load_gassco` | Plyn → GASSCO, Mapa; Report → GASSCO |
| `entsog_flows/` | `entsog`, `entsog_allocation` | `data/entsog.load_entsog_history` | Plyn: Mapa, Toky, Sezonnost, Historie; Report → Toky |
| `entsog_operational/` | `entsog_operational` | `load_operational_point`, `operational_index`, `load_eu_operational_open_ended` | Plyn: Nominace, Kapacity |
| `gie_all_storage/` | `gie` | `data/gie.load_gie_all` | Plyn → Zásobníky; Report → Zásobníky |
| `lng_storage/` | `lng` | `data/lng.load_lng` | Plyn → LNG |
| `hydro_reservoirs/` | `hydro` | `data/hydro.load_hydro` | Elektřina → Hydro |
| `dap_europe/` | `dap_europe` | `load_dap_europe(_all)` | Elektřina → DAP mapa; Report → DAP |
| `generation/<CC>/` | `generation` | `data/generation.load_generation` | Elektřina → Výroba |
| `outages.parquet`, `outages_snapshots/` | `outages` | `data/outages.load_outages(_snapshot)` | Elektřina → Výroba/Odstávky |
| `energy_charts/<ep>/`, `energy_charts.parquet` | `energy_charts` | `load_energy_charts_wide`, `load_energy_charts` | **Energy-Charts (nová stránka)** |
| `energy_charts_progress.json` | jen ruční `run_backfill()` | `run_backfill()` | — |
| `ice_cot.parquet`, `ice_cot_status.json` | `cot` (so/út) | `load_ice_cot(_status)` | CoT |
| `hu_generation/` | **nikdo** | **nikdo** | — mrtvé |
| `nuclear_fr_generation/` | **nikdo** | **nikdo** | — mrtvé |
| `entsog_capacity/` | **nikdo** | **nikdo** | — mrtvé |
| `hydro_reservoirs.csv` | **nikdo** | **nikdo** | — mrtvé |

Ověřená podezření:
- `hu_generation` a `nuclear_fr_generation` jsou **zastaralé kopie** —
  jediný odkaz byl smazaný migrační skript; data končí 2026-08-29 resp.
  2026-08-18, aktuální řady jsou v `generation/HU` a `generation/FR`
  (do 2026-10-23). Stejně `entsog_capacity/` (kapacity teď v
  `entsog_operational`, žádný čtenář ani zapisovatel) a
  `hydro_reservoirs.csv` (nahrazeno `hydro_reservoirs/`). Ve větvi ani na
  `main` se z gitu nemažou; `install_user.sh` je do `PPDASH_DATA_DIR`
  nenahrává (`scripts/data_status.py --dead`), test hlídá, že je nikdo nečte.
- `gassco_nominations`, `gassco_umm_snapshots`, `lng_storage`,
  `ice_cot_status.json` **updater mají** (`gassco`, `gassco_umm`, `lng`,
  `cot` — v `data/gassco.py`, `data/lng.py`, `data/ice_cot.py`, jen
  registrované v `update_all.py`); v repu jsou aktuální (23.10.).
  `entsog_capacity` updater nemá a nikdo ho nečte → mrtvé (viz výš).
- **ICE CoT** (opraveno v kole 3): URL `…/IFEU_FUT_YYYYMMDD.csv` i
  `NDEX_FUT_…` **fungují** (ověřeno v kontejneru: 200 až do 20261016).
  Dřívější 404 v `ice_cot_status.json` měly dvě příčiny: blokovaná síť
  v cloudovém prostředí a hlavně to, že ICE vydává report za pátek až
  **následující středu** — sobotní/úterní běh chtěl pátek, který ještě
  nevyšel. Úloha `cot` teď bere nejnovější pátek + 5 dní ≤ dnes
  (`last_published_friday`), timer běží st 14:00 a 19:00 + záloha čt/so
  08:00; 404 v den publikace je jen WARN. Chybějící pátky dohání (max 8).
- **Energy-Charts v repu končí 2026-09-25** — workflow na `main` je
  nespouští (`update_gas_history.py` je vynechává), aktualizuje je až
  timer v kontejneru (navazuje od konce dat).
- Mrtvý kód smazán: `scripts/migrate_partitioned_step0.py`,
  `migrate_step1_generation_outages.py`, `migrate_step2_gassco.py`
  (četly soubory, které už neexistují).

### Frekvence aktualizací (vše inkrementálně, upsert bez duplicit)

| Timer | Kdy | Úlohy |
|---|---|---|
| `ppdash-live.timer` | každou minutu (:20 s) | `update_all.py --only live -q` = `ceps`, `gassco_realtime`, `gassco_umm` |
| `ppdash-update.timer` | 06:00 a 17:00 Europe/Prague | výchozí běh = vše kromě `live` a `cot` |
| `ppdash-update-cot.timer` | st 14:00 + 19:00, čt + so 08:00 Europe/Prague | `cot` |

- **ČEPS**: v kódu se všechny minutové řady volají s `agregation="MI"`
  (výroba a cena odchylky po čtvrthodinách) a web je obnovuje po 60 s
  (`refresh=60`, `ttl_cache(ttl=60)`) — 5s polling v kódu nebyl a nemá
  smysl. Úloha `ceps` navazuje od posledního uloženého času − 30 min
  (ČEPS dopočítává poslední minuty), cena odchylky vždy od začátku dne,
  první běh od půlnoci včerejška, výpadek timeru se dohání max 7 dní
  (po dnech). Ukládá long formát `time, series_id, series, value` do
  denních souborů (`upsert_partitioned(period="day")`), frekvenci jako
  minutový průměr. Web čte dnešek z úložiště, když je čerstvé (≤ 10 min),
  jinak živě přes SOAP jako dřív. WSDL se cachuje na disk (1 den).
- **GASSCO**: realtime nominace (`realTimeAtom.xml`, 1 request) se ukládají
  po publikacích (dedup na `updated, point` — beze změny feedu se nic
  nezapíše); UMM (`xlexport`) se přepisuje jen při změně obsahu, snapshot
  1×/den. Denní nominace (`/ch/2Y`, ~30 requestů) zůstávají 2× denně.
- Live běh má vlastní zámek `history/.live.lock` (denní běh trvá i hodiny
  a zapisuje do jiných datasetů), `-q` = jen varování + 1 řádek souhrnu.
- `scripts/update_gas_history.py` (workflow na `main`) dál spouští plyn +
  elektřinu + GASSCO UMM jako dřív, bez live a Energy-Charts.

### Energy-Charts stránka (`/energy-charts`)
- Záložky (líně jako ostatní): **Frekvence sítě** (5min, KPI průměr/min/max/
  směr. odchylka/% mimo ±50 mHz, histogram odchylky; delší rozsahy průměr +
  pásmo min–max), **Přeshraniční toky** (cbet i cbpf po sousedech + saldo,
  tabulka energie import/export/saldo), **Výroba a prognóza** (public_power
  stack podle zdroje se zatížením/reziduálním zatížením/obchodem, day-ahead
  prognóza vs. skutečnost + MAE/bias/MAPE), **Instalovaný výkon** (roční).
- Výběr země z uložených dat (teď jen `cz`), rozsah s předvolbami, rozlišení
  auto/15 min/hodina/den. Paleta a české názvy sdílené s ENTSO-E grafy.
- Znaménko přeshraničních řad **ověřeno na datech**: výroba + obchod ≈
  zatížení → kladné = import, záporné = export.
- Výkon: `load_energy_charts_wide()` čte a agreguje v pyarrow; ISO časy
  s offsetem se parsují v pyarrow (pandas `to_datetime` na 2.5M řádků
  ~12 s → <1 s). Celá historie CZ výroby: ~4 s (denní průměry), 7 dní 0.4 s.

### Delta Green
- Z nového webu odstraněno úplně: záložka, šablona, `deltagreen_api_key()`,
  `DELTAGREEN_API_KEY` v `env.example`, test. Stará Streamlit app ji má dál
  (`views/delta_green.py` + `data/deltagreen.py`, klíč ze sidebaru).

### Export pro reporty
- U každého grafu (i GASSCO — vizuálně beze změny, lišta se ukáže až při
  najetí myší) tlačítka **PNG / SVG / CSV / XLSX**.
- PNG/SVG renderuje klient z poslední figury, **vždy ve světlém režimu
  s bílým pozadím**, šířka ≥ 1600 px, PNG 3× (např. 4800×1380).
- CSV/XLSX: `/api/chart-data/<page>/<tab>/<id>?fmt=csv|xlsx&<parametry>` —
  data přímo z Plotly figury (= co je v grafu): „wide“ (osa x × série),
  když to jde, jinak „long“; tabulky (go.Table) jako tabulka. XLSX má listy
  `data`, `long`, `info` (titulek, parametry, čas). Časy v Europe/Prague
  (v XLSX bez tz), CSV s BOM kvůli Excelu.
- Vedlejší oprava tmavého režimu: osy bez explicitního nastavení měly bílou
  mřížku ze šablony `plotly_white`; téměř černé čáry se zesvětlí.

### Nasazení bez roota (`deploy/`)
- `deploy/systemd-user/*` — uživatelské unity (`systemctl --user`) se
  zástupnými `@REPO@/@VENV@/@DATA@`; `deploy/systemd/` (systémové unity
  pro uživatele `ppdash`) smazáno.
- `deploy/install_user.sh` — idempotentní: venv (pip jen při změně
  requirements), adresáře, `~/.config/ppdash/env` (600), historie z repa
  `rsync -a --ignore-existing` bez mrtvých datasetů, unity, `enable --now`
  timerů, restart webu, kontrola `/healthz`. `--no-seed`, `--no-start`,
  `--initial-update`.
- `deploy/status.sh` — stav služeb, poslední běhy (start/konec/výsledek/
  exit), timery, `/healthz`, posledních 5 live běhů, stáří dat per dataset,
  disk; `--logs`. Exit 1, když něco neběží nebo jsou data stará.
- `core/settings.py`: bez `PPDASH_DATA_DIR` se bere `~/ppdash-data`, pokud
  existuje (ruční spuštění skriptů z shellu tak nepíše do repa).
- Ověřeno v tomto prostředí: `systemd-analyze --user verify` unit bez chyb,
  `systemd-analyze calendar` časů timerů, `install_user.sh --no-start`
  jako neprivilegovaný uživatel (venv, env 600, kopie historie bez mrtvých
  dat, nepřepisuje existující soubory, unity s doplněnými cestami; bez
  uživatelského systemd skončí jasnou hláškou o `enable-linger`),
  `status.sh` bez systemd. Větev s `rsync` neověřena (rsync tu není,
  ověřena záložní větev `cp`).

### Testy
- 171 testů (+38): `tests/test_live.py` (ČEPS live zápis/idempotence/
  navazování/tvar dat z úložiště = živý tvar/fallback, frekvence po
  minutách, GASSCO realtime + preferování čerstvého úložiště, UMM bez
  přepisu, denní partitiony, live skupina a zámky, CoT dohánění, registr
  datasetů, mrtvá data nikdo nečte), `tests/test_export.py` (figura →
  wide/long/tabulka, XLSX listy, endpoint, parsování časů, agregace
  Energy-Charts přes změnu času, znaménko toků, unity, skripty, env).
  Nová stránka a její grafy jsou v parametrizovaných smoke testech webu.
  Zelené i s `PPDASH_TEST_EMPTY=1`.

### Co má udělat sezení v cílovém kontejneru
1. Admin jednou: `loginctl enable-linger claude` (+ `apt install rsync
   python3-venv`, pokud chybí).
2. `git clone -b rebuild/fastapi … ~/pp-dashboard && ~/pp-dashboard/deploy/install_user.sh`,
   doplnit `~/.config/ppdash/env`, pak `install_user.sh --initial-update`.
3. `deploy/status.sh` — po 2–3 minutách musí mít `ceps` a `gassco_realtime`
   stáří v minutách; `journalctl --user -u ppdash-live -n 20` bez chyb.
4. Ověřit živá API (níže NEOVĚŘENO), hlavně ČEPS SOAP odpovědi (názvy
   sérií, `Frekvence` bez parametrů, rozsah víc dní) a že web na
   Elektřině po spuštění timeru čte z úložiště (stejná čísla jako dřív).
5. ~~Zjistit příčinu 404 u ICE CoT~~ — vyřešeno (publikace ve středu, viz výš).
6. Prohlédnout export PNG/SVG u mapy (MapLibre) — v headless Chromiu
   ověřen jen sloupcový graf.

## Kolo 1 — hotovo

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
  je v git historii, doporučuji ho na GIE přegenerovat**).
  (Delta Green z nového webu v kole 2 odstraněn.)
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
    Rezervy, Hydro, Data, DAP mapa (+ banner a KPI nahoře)
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
  místo `st.line_chart`, nastavení baterie je ve formuláři záložky DAP místo sidebaru,
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
- (kolo 1: systémové unity pro uživatele `ppdash` — v kole 2 nahrazeny
  uživatelskými unitami `deploy/systemd-user/`, viz výš)
- `env.example` (bez hodnot), `cloudflared-config.example.yml`,
  `DEPLOY.md` (LXC, uživatel, venv, migrace dat, timery, Tunnel + Access).
- `requirements-web.txt` (bez Streamlitu), `requirements-dev.txt`.

### 6. Testy (`pytest`, 133 testů v kole 1, ~12 s)
- `tests/test_core.py` — cache (TTL, kopie, výjimky, disk), env soubor,
  partitioned čtení/zápis (idempotence).
- `tests/test_sources.py` — každý zdroj s mockovanou sítí: ČEPS, ENTSO-E
  (fetchery i updatery hydro/DAP Europe/výroba/odstávky), ENTSO-G toky,
  ENTSOG operational, GIE, ALSI/LNG, GASSCO (nominace, realtime, UMM
  xlexport), ICE CoT, Energy-Charts (inkrementální update, frequency
  5 min, retry), `update_all.py` (výběr úloh, exit kódy, zámek).
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
- `update_energy_charts` běží jen pro `cz` (stránka Energy-Charts umí
  víc zemí, jakmile budou data — přidat do `update_all.py`, `countries=`).
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
  realTimeAtom), ICE CoT, Energy-Charts. Testy používají
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
- Uživatelské systemd unity nebyly spuštěné (v prostředí není systemd ani
  linger) — syntaxe ověřena `systemd-analyze --user verify`; běh timerů,
  `MemoryMax` v uživatelském manageru a Cloudflare Tunnel NEOVĚŘENO.
- ČEPS live: že `Frekvence` bez parametrů vrací rozumná data, že dotaz na
  víc dní (dohánění výpadku) projde, a že čísla z úložiště = živá čísla.
- GASSCO realtime: že `atom:updated` se mění jen při nové publikaci (jinak
  by se zapisovalo každou minutu — pořád bez duplicit, jen víc řádků).
- Zátěž minutového timeru na 2 jádrech (start Pythonu + pandas ~1–2 s CPU
  za minutu) — změřit `systemd-cgtop`/`journalctl`.
- Objem live dat (odhad ČEPS ~10–15 MB/měsíc) — změřit `status.sh`.
- Výkon/paměť naměřené na vývojovém stroji s daty z repa; na LXC změřit
  `journalctl`/`/healthz` (`rss_mb`) po pár dnech provozu.
- Vzhled ověřen screenshoty v headless Chromiu (desktop světlý/tmavý,
  mobil 390 px) — ne na skutečných zařízeních.
