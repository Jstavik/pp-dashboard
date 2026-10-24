# Audit dat — co ukládáme, k čemu to je a kolik to zabere

Stav k 2026-10-24. Velikosti „teď“ jsou z kontejneru (LXC, `PPDASH_DATA_DIR/history`,
celkem **603 MB**, backfill ENTSO-E ještě běží). Odhady „po backfillu“ a „růst za rok“
jsou **odhady** z rozsahu dat, rozlišení a počtu řad (ne měření) — skutečný růst
ukáže stránka **/stav** (sloupec „Růst MB/rok“ se počítá z týdenních snímků velikostí,
první číslo po týdnu provozu údržby).

**Zkrácený závěr:** nic z toho disk (20 GB) neohrožuje — po dokončení backfillu
odhadem **~0,9–1 GB**, pak **~150–200 MB/rok**. Opravdu zbytečná data jsou jen 4 mrtvé
datasety v repu (v kontejneru nejsou) a `energy_charts_progress.json`. Hlavní
plýtvání bylo v **počtu souborů** (entsoe_prices 3 956 souborů, 2–5 kB metadat
na soubor) a v **denních snapshotech** (95 % řádků se den ode dne opakuje) — obojí
teď řeší týdenní údržba (`core/maintenance.py`). Druhé plýtvání je **síť**: stránka
Elektřina stahuje část dat z ENTSO-E živě, i když je máme uložená (viz Duplicity).

Legenda doporučení: **ponechat** · **agregovat** (ukládat hruběji) · **retence**
(starší data zhustit/omezit) · **smazat**. Údržba = co už dělá týdenní úloha
`maintenance` (kompakce souborů s ověřením, archiv snapshotů).

## 1. Datasety

| Dataset | Zdroj | Co obsahuje | Rozlišení | Od | Teď | Po backfillu | Růst/rok | Aktualizace | Kde se používá | Doporučení |
|---|---|---|---|---|---|---|---|---|---|---|
| `generation/<země>` | ENTSO-E 16.1.B&C | výroba podle typu zdroje, 16 zemí | 15 min / h | 2019-12 | 111 MB, 878 s. | ~160 MB | ~25 MB | 1× denně 06:00 | Elektřina → Výroba (sezónnost, mix), Průzkumník | **ponechat**; údržba: roční soubory |
| `entsoe_load/<oblast>` | ENTSO-E 6.1.A/B | spotřeba skutečná + prognóza D-1 | 15 min / h | 2019 | 89 MB | ~120 MB | ~18 MB | 1× denně 07:10 | Trh EU → Spotřeba, Průzkumník | **ponechat**; údržba: roční soubory |
| `entsoe_ws_forecast/<oblast>` | ENTSO-E 14.1.D | prognóza slunce/vítr on/offshore D-1 | 15 min / h | 2019 | 88 MB | ~110 MB | ~15 MB | 1× denně 07:10 | Trh EU → Prognóza OZE, Průzkumník | **ponechat**; údržba: roční soubory |
| `entsoe_flows/<od>_<do>` | ENTSO-E 12.1.G/F | fyzické toky + plánované výměny, 45 hranic × 2 směry | 15 min / h | 2021 | 86 MB, 2 138 s. | ~230 MB | ~35 MB | 1× denně 07:10 | Trh EU → Toky, Nordic → Toky, Průzkumník | **ponechat**; největší růst — kdyby vadil, `scheduled` jen pro CZ+sousedy (agregovat); údržba: roční soubory |
| `entsoe_prices/<zóna>` | ENTSO-E 12.1.D | DA ceny 45 bidding zón | h, od 10/2025 15 min | 2019 | 73 MB, **3 956 s.** | ~65 MB po kompakci | ~12 MB | 07:10 + **13:20** (po SDAC) | Trh EU → Ceny, Nordic → Ceny a spready, Přehled (DA CZ/DE), Reporty, Průzkumník | **ponechat** — „pravda“ pro DA ceny; údržba: 3 956 → ~350 souborů |
| `entsog_operational` | ENTSO-G /operationaldata | nominace, renominace, kapacity, kvalita po bodech | den / období | 2007 | 74 MB | 74 MB | ~8 MB | 06:00 + 17:00 | Plyn → Nominace, Kapacity, Průzkumník | **ponechat**; (vlastní měsíční logika zápisu → bez kompakce) |
| `energy_charts/<endpoint>` | Fraunhofer ISE | public_power, prognóza, cbet, cbpf, instalovaný výkon — **jen CZ** | 15 min | 2017-12 | 25 MB | 25 MB | ~4 MB | 06:00 + 17:00 | stránka Energy-Charts, Průzkumník | **ponechat** jako druhý zdroj/kontrola (viz Duplicity); nic dalšího nestahovat |
| `energy_charts.parquet` | Energy-Charts /frequency | frekvence sítě (z 1 s → 5 min) | 5 min | 2014 | 6 MB | 6 MB | ~1 MB | 06:00 + 17:00 | Energy-Charts → Frekvence, Průzkumník | **ponechat** (ČEPS frekvence je jen dnešek a minuty) |
| `gie_all_storage` | GIE AGSI+ | zásobníky plynu všech zemí + EU | den | 2011 | 16 MB (CSV) | 16 MB | ~1,5 MB | 06:00 + 17:00 | Plyn → Zásobníky, Přehled, Reporty, Průzkumník | **ponechat**; údržba: roční soubory; časem CSV → parquet (~5× menší) |
| `entsog_flows` | ENTSO-G /aggregateddata | fyzické toky + alokace po zemích a směrech | den | 2019-12 | 11 MB | 11 MB | ~1 MB | 06:00 + 17:00 | Plyn → Mapa, Toky, Sezonnost, Historie, Report | **ponechat** — „pravda“ pro toky po zemích |
| `outages_snapshots` | ENTSO-E (kopie `outages.parquet`) | denní snímek všech odstávek | snímek/den | 2026-08 | 10 MB (76 dní) | — | ~45 MB bez retence → **~3 MB** s archivem | 1× denně | Elektřina → Výroba/Odstávky „Porovnat s“, Přehled (nové odstávky) | **retence**: 60 dní plně, starší jen změny (údržba, ověřená rekonstrukce) |
| `outages.parquet` | ENTSO-E A77/A80 | aktuální odstávky (rolling) | události | rolling | 0,2 MB | — | 0 | 1× denně 06:00 | Elektřina, Odstávky ČEPS, Přehled | **ponechat** |
| `lng_storage` | GIE ALSI | LNG terminály (zásoba, send-out) | den | 2012 | 7 MB (CSV) | 7 MB | ~0,6 MB | 06:00 + 17:00 | Plyn → LNG, Přehled, Reporty, Průzkumník | **ponechat**; údržba: roční soubory |
| `energinet/<dataset>` | Energi Data Service | DK1/DK2 výroba, spotřeba, výměny, prognózy OZE | h | 2019 | 6 MB (backfill běží) | ~15 MB | ~2 MB | 06:00 + 17:00 (1 dotaz/min!) | Nordic → Finsko a Dánsko, Průzkumník | **ponechat**; údržba: roční soubory |
| `ceps/<řada>` | ČEPS SOAP | odchylka, SVR, zatížení, výroba, OZE, frekvence, toky, cena odchylky | 1 min | provoz | ~20 kB/den | — | **~7 MB** | každou minutu | Elektřina (hlavička, Odchylka & Generace, ČEPS), Přehled (odchylka), Průzkumník | **ponechat v plném rozlišení** (malé); údržba: denní → měsíční soubory |
| `gassco_realtime` | GASSCO realTimeAtom | vnitrodenní nominace po bodech (jen při změně feedu) | publikace | provoz | < 1 MB | — | ~2 MB | každou minutu | Plyn → Mapa, GASSCO (dnešek) | **ponechat**; údržba: denní → měsíční soubory |
| `gassco_nominations` | GASSCO /ch/2Y | denní nominace exit/entry | den | 2020-10 | 1,2 MB (CSV) | 1,2 MB | ~0,2 MB | 06:00 + 17:00 | Plyn → GASSCO, Mapa, Přehled (export NO), Reporty | **ponechat**; údržba: roční soubory |
| `gassco_umm.parquet` + `_snapshots` | GASSCO UMM | odstávky polí/terminálů; snímek/den | publikace | 2026-06 | 1,5 MB | — | ~9 MB → **< 1 MB** s archivem | každou minutu (zápis jen při změně) | Plyn → GASSCO (UMM, změny), Přehled (feed) | **retence** jako u odstávek |
| `hydro_reservoirs` | ENTSO-E 16.1.D | zásoba vody v nádržích po zemích (20 zemí) | týden | 2019-12 | 0,3 MB | — | ~0,05 MB | 1× denně 06:00 | Elektřina → Hydro, Nordic → Hydrobilance (SE, FI), Průzkumník | **ponechat** — „pravda“ pro SE/FI |
| `nve_reservoirs*.parquet` | NVE | plnění nádrží NO + NO1–NO5, statistika min/medián/max | týden | 1995 | < 1 MB | — | ~0,05 MB | 06:00 + 17:00 | Nordic → Hydrobilance, Přehled (hydro NO), Reporty | **ponechat** — „pravda“ pro Norsko |
| `fingrid` | Fingrid (klíč) | FI spotřeba, výroba, vítr, jádro, voda, přenosy | 15 min | 2019 | ≤ 2 MB / bez klíče 0 | ~20 MB | ~3 MB | 06:00 + 17:00 | Nordic → Finsko a Dánsko, Průzkumník | **ponechat** (jen když je klíč) |
| `dap_europe` | ENTSO-E (agregace) | denní base/peak po zemích | den | 2026-06 | 0,1 MB | — | ~0,2 MB | 06:00 + **13:20** | Elektřina → DAP mapa, Report → DAP mapa, Přehled, Průzkumník | **sloučit do odvozeniny** z `entsoe_prices` (viz Duplicity) — zatím ponechat |
| `weather_daily/<oblast>` | Open-Meteo ERA5 | denní teplota/vítr/slunce/srážky (vážené body) | den | 1991 | ≤ 2 MB | ~3 MB | ~0,1 MB | 4× denně | Počasí, Přehled (teplota vs. normál), Reporty, Průzkumník | **ponechat**; údržba: roční soubory (~1 260 → ~110) |
| `weather_fc/<oblast>` | Open-Meteo | hodinová předpověď modelů — pro každou hodinu poslední předpověď | h | provoz | < 1 MB | — | ~3 MB | 4× denně | Počasí (hodinově), Průzkumník | **ponechat** |
| `weather_fc_daily/<oblast>` | Open-Meteo | denní předpověď podle dne vydání (změna předpovědi) | den × vydání | provoz | < 1 MB | — | ~3 MB → ~2 MB | 4× denně | Počasí (změna proti včerejšku), Přehled (7denní výhled), Průzkumník | **retence**: po 90 dnech jen předstih ≤ 10 dní (údržba) |
| `weather_ens` | Open-Meteo ensemble | p10/p50/p90, jen poslední běh | h | — | < 1 MB | — | 0 (přepisuje se) | 4× denně | Počasí (pásmo ENS) | **ponechat** |
| `ice_cot.parquet` (+ status) | ICE MiFID II | Commitments of Traders TTF aj. | týden | 2022-10 | 0,2 MB | — | ~0,1 MB | st 14:00/19:00 | CoT, Přehled (managed money), Reporty, Průzkumník | **ponechat** |
| `energy_charts_progress.json` | — | stav ručního backfillu Energy-Charts | — | — | 4 kB | — | 0 | nikdy | **nepoužito** (jen ruční `run_backfill()`) | **smazat** (až nebude potřeba ruční backfill) |
| `hu_generation`, `nuclear_fr_generation`, `entsog_capacity`, `hydro_reservoirs.csv` | — | staré kopie | — | — | 21 MB **jen v repu** | — | 0 | nikdo | **nepoužito** (mrtvé, do kontejneru se nekopírují) | **smazat** z `main` (rozhodnutí na uživateli — `main` neměním) |

## 2. Duplicity — stejná veličina z více zdrojů a jedna „pravda“

| Veličina | Zdroje | Pravda | Proč / co s druhým zdrojem |
|---|---|---|---|
| **DA ceny elektřiny** | `entsoe_prices` (hodinové/15min, 45 zón), `dap_europe` (denní base/peak), živé `fetch_dap` na Elektřině (API při každém otevření, 1 h cache) | **`entsoe_prices`** | base/peak je jen agregace hodin (base = průměr, peak = 8–20 h po–pá). Doporučení: `dap_europe` počítat z `entsoe_prices` (úlohu `dap_europe` pak zrušit) a Elektřina → DAP ceny číst z úložiště místo živého API. Přehled už bere entsoe_prices a dap_europe jen doplňuje. |
| **Výroba podle zdroje CZ/DE** | `generation` (ENTSO-E), `energy_charts/public_power` (Fraunhofer, jen CZ), živé `fetch_entsoe_data` na Elektřině | **`generation`** | ENTSO-E je primární zdroj pro všechny země, stejné kategorie napříč zeměmi; Energy-Charts pro CZ přebírá ENTSO-E/ČEPS data s jinými kategoriemi. Energy-Charts ponechat jako **kontrolu a doplněk** (zatížení, reziduální zatížení, obchod v jednom grafu), ne jako druhou pravdu. Živé stahování výroby CZ na Elektřině nahradit čtením `generation/CZ` (+ živě jen poslední hodiny). |
| **Spotřeba CZ** | `entsoe_load` (skutečnost + D-1), živé `fetch_entsoe_data` (Elektřina), `ceps/load` (minutově, dnešek), Energy-Charts load | **`entsoe_load`** (historie), **`ceps`** (dnešek po minutách) | ČEPS je zdroj, ENTSO-E data ČEPS přebírá s hodinovým zpožděním; pro „teď“ ČEPS, pro historii a porovnání zemí ENTSO-E. |
| **Prognóza OZE** | `entsoe_ws_forecast`, živé `fetch_wind_solar_forecast` (Elektřina), `energy_charts/public_power_forecast` | **`entsoe_ws_forecast`** | Stejný důvod jako u výroby; Elektřina by měla číst z úložiště. |
| **Přeshraniční toky elektřiny** | `entsoe_flows` (physical + scheduled), `energy_charts/cbpf` + `cbet` (CZ), `ceps` toky (minutově) | **`entsoe_flows`** | Pokrývá všechny hranice; Energy-Charts cbpf/cbet pro CZ jsou tatáž čísla (ověřeno znaménko). ČEPS pro dnešek. Energy-Charts toky lze časem přestat stahovat (ušetří ~10 MB a 2 endpointy). |
| **Frekvence sítě** | `energy_charts.parquet` (5 min, od 2014), `ceps/frekvence` (minutově, provoz) | Energy-Charts pro historii, ČEPS pro dnešek | Nepřekrývají se v účelu. |
| **Hydro Norsko** | `nve_reservoirs` (NO + NO1–5, %, TWh, od 1995, statistika), `hydro_reservoirs` NO (ENTSO-E, GWh, od 2019) | **NVE** | Delší historie, zóny, oficiální min/medián/max. ENTSO-E použít jen pro **SE a FI** (jiný volný zdroj není). |
| **Toky plynu** | `entsog_flows` (agregace po zemích a směrech), `entsog_operational` (po bodech, nominace/kapacity) | **`entsog_flows`** pro toky zemí, **`entsog_operational`** pro body, nominace, kapacity | Nejsou to duplikáty v pravém smyslu — jiná granularita. Součty z operational nesedí přesně na aggregated (jiné body/revize), proto nesčítat body pro zemi. |
| **Export plynu z Norska** | `gassco_nominations` (denní), `gassco_realtime` (vnitrodenní), `entsog_flows` (vstupy do EU) | **GASSCO** (denní) | realtime jen pro dnešek; ENTSO-G jako kontrola na straně EU. |
| **Zásobníky EU** | `gie_all_storage` řádek EU vs. součet zemí | **řádek EU od GIE** | Součet zemí se liší (chybějící/opožděné reporty). |
| **Odstávky CZ** | `outages` (ENTSO-E, ČEPS publikuje přes ENTSO-E), živé `fetch_entsoe_data` (Elektřina) | **`outages`** | Elektřina by mohla brát z úložiště (aktualizace 1× denně stačí jen pokud nevadí zpoždění — odstávky přicházejí průběžně; proto živý dotaz zatím ponechán). |

## 3. Co zatím neměnit, ale stojí za zvážení

1. **Elektřina → čtení z úložiště** místo živého ENTSO-E (výroba CZ, spotřeba, prognóza OZE,
   DA ceny) — méně dotazů na ENTSO-E (rate limit), rychlejší stránka, stejná čísla jako
   v Průzkumníku a Trhu EU. Živě jen poslední hodiny/dnešek. Zásah do nejstarší stránky →
   samostatný krok.
2. **`dap_europe` z `entsoe_prices`** — jedna pravda, o jednu úlohu méně (DAP mapa i Přehled
   by četly odvozeninu).
3. **CSV → parquet** u GIE/LNG/GASSCO (~5× menší, rychlejší Průzkumník bez zrcadla).
4. **Energy-Charts toky** (cbet/cbpf) přestat stahovat, pokud uživatel nepotřebuje
   jejich graf (duplicita s `entsoe_flows`).
5. Mrtvé datasety smazat z `main` (21 MB v gitu).

## 4. Údržba — co se teď děje automaticky

Týdenní úloha `maintenance` (ne 03:30, ručně z /stav „Spustit údržbu teď“), pravidla
v jednom místě `core/maintenance.py::POLICY`:

- **Kompakce**: uzavřené roky → `{YYYY}.parquet|csv` (ENTSO-E ceny/spotřeba/OZE/toky,
  výroba, Fingrid, Energinet, ERA5, GIE, LNG, GASSCO nominace, hydro), uzavřené dny
  → měsíc (ČEPS, GASSCO realtime, 8 dní po konci měsíce). Před smazáním originálů se
  porovná počet řádků a kontrolní součet obsahu (hash každého řádku, nezávislý na
  typu sloupce) zapsaného a znovu přečteného souboru. Neshoda = originály zůstanou,
  chyba v /stav. Na datech z repa: **1 873 → 1 008 souborů**, −11 MB, 0 chyb, 60 s.
- **Snapshoty** (odstávky, GASSCO UMM): 60 dní plné (`PPDASH_SNAPSHOT_KEEP_DAYS`), starší
  v `_archive.parquet` jako intervaly výskytu řádků; každý den se zrekonstruuje
  a porovná s originálem, teprve pak se denní soubor smaže. „Porovnat s“ na webu
  funguje dál pro všechny dny.
- **Předpovědi podle dne vydání**: po 90 dnech jen předstih ≤ 10 dní
  (`PPDASH_FC_KEEP_DAYS`, `PPDASH_FC_MAX_LEAD_DAYS`).
- **Velikosti**: snímek do `history/.sizes.json` (růst na /stav), výsledek do
  `history/.maintenance.json`.
- Úprava datasetu probíhá pod zámkem úlohy, která ho zapisuje — když právě běží,
  dataset se přeskočí a dožene příští týden.
