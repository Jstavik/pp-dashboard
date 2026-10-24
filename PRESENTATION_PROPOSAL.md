# Návrh prezentace — jak dashboard lépe číst (analytik trhu s energiemi)

Stav 2026-10-24. Prototyp běží na třech stránkách: **Plyn (zásobníky)**, **Elektřina ČR**
a **Nordic**. Na každé z nich je nahoře odkaz **„✨ Vyzkoušet nový vzhled“**, v novém vzhledu
**„přepnout na starý“**. Volba se pamatuje v prohlížeči (cookie, rok). Starý vzhled
zůstává beze změny a je výchozí, takže jde obojí porovnat. Odkazy na staré záložky
(`?tab=…`) vždy otevřou starý vzhled.

## 1. Co je na současných stránkách špatně

Grafy pocházejí ze Streamlitu a dědí jeho logiku: **jedna záložka = jeden graf + ovládání**.

1. **Moc záložek a málo přehledu.** Plyn má 9 záložek, Elektřina 8. Na odpověď na ranní
   otázku („jak jsme na tom proti normálu a co se změnilo“) se musí proklikat 4–5 záložek
   a v každé nastavit filtry.
2. **Sezónnost se ukazuje jako „špagety“:** každý rok má vlastní barevnou čáru (5–10 čar).
   Oko hledá letošní rok a srovnává ho s ostatními jednotlivě. Analytik ale potřebuje
   jedinou otázku: **je letošek v normálu, nebo mimo něj?**
3. **Hlavní informace chybí jako číslo.** Odchylku od pětiletého průměru, změnu za týden
   a srovnání s loňskem si musí čtenář odhadnout z grafu.
4. **Nesourodé osy a jednotky.** Malé grafy zemí mají každý jiné měřítko, takže vypadají
   stejně „plné“. Někde jsou GWh/d, jinde TWh, někde je na ose „den v roce“ místo měsíců.
5. **Události v grafech nejsou.** Odstávky, UMM a milníky (konec tranzitu přes Ukrajinu,
   15min SDAC) jsou ve feedu Přehledu, ale ne v grafu, kde by vysvětlily skok.
6. **Dvě veličiny v jednom grafu** (ceny + spread) nebo legendy přes titulek.

## 2. Zásady nového vzhledu

| Zásada | Jak se to projevuje |
|---|---|
| **Hierarchie informací: číslo → kontext → detail** | Nahoře řádek KPI (hodnota, změna za týden, **odchylka od průměru 5 let** tučně). Pod ním jeden hlavní graf, pak mřížka malých grafů, nakonec tabulka čísel. Bez filtrů — výchozí pohled odpovídá na ranní otázku. Detaily (výběr let, jiné proměnné) zůstávají ve starém vzhledu nebo v Průzkumníku. |
| **Sezónnost = pásmo, ne špagety** | Letošek silnou modrou čarou, **pásmo min–max posledních 5 let** šedou plochou, **průměr 5 let** šedou čarou, loňský rok tenkou světle šedou. Osa x ukazuje měsíce aktuálního roku. Na konci letošní čáry je popsaná poslední hodnota. Jedna barva má jeden význam na všech stránkách. |
| **Small multiples se stejným měřítkem** | Země (zásobníky), oblasti NO1–NO5, zóny (spready proti DE): mřížka malých grafů se **stejnou osou y**, takže velikost je rovnou srovnatelná. Legenda je jen jednou. Výjimka: zdroje výroby mají každý vlastní měřítko (jádro 3 GW a vítr 0,1 GW by na jedné ose nešly číst) — v titulku je to uvedeno. |
| **Anotace událostí** | Svislá tenká čára s krátkým popiskem: milníky trhu (`core/events.py::MARKET_EVENTS` — invaze, TTF max., Nord Stream, konec tranzitu UA, 15min SDAC) a události z feedu Přehledu (odstávky CZ ≥ 100 MW u cen). Cíle vodorovně (zásobníky 90 %). |
| **Spready a salda jako diverging sloupce** | Kladné hodnoty modře, záporné oranžově, nula zvýrazněná. Vždy ve **vlastním grafu** pod cenami, nikdy na druhé ose y. |
| **Konzistentní jednotky** | €/MWh pro ceny, % a p.b. pro plnění, TWh pro zásoby, TWh/d pro toky v sezónních grafech, GW pro výkon. Jednotka je vždy v názvu osy a v KPI. |
| **Barvy** | Validovaná paleta z `core/palette.py`: země mají pevnou barvu (CZ modrá, DE oranžová…), letošek = slot 1. Šedé tóny pásma a průměru jsou čitelné ve světlém i tmavém režimu (ověřeno screenshotem). |
| **Méně záložek** | Stránka v novém vzhledu je jedna obrazovka. Plyn: 9 záložek → 1 přehled + odkazy na detail. |

## 3. Prototyp — co je na kterých stránkách

**Plyn → Zásobníky** (`/plyn` v novém vzhledu)
- KPI: EU, DE, IT, AT, CZ — plnění %, změna za týden, odchylka od průměru 5 let.
- Hlavní graf: EU letos vs. pásmo 2021–2025 + průměr + loni, cíl 90 %, události.
- Mřížka 3×3: DE, IT, FR, NL, AT, CZ, SK, PL, HU — stejné měřítko 0–100 %.
- EU vtláčení/těžba (TWh/d, klouzavý průměr 7 dní) vs. pásmo 5 let.
- Tabulka: plnění, změna 7 dní, vs. průměr 5 let, vs. loni, TWh, čisté vtláčení.

**Elektřina ČR** (`/elektrina` v novém vzhledu; živá hlavička ČEPS zůstává)
- KPI: CZ base (vs. týden, vs. Ø 30 dní), CZ peak (poslední pracovní den), spread CZ−DE,
  podíl jádra, lignitu a slunce za 7 dní.
- Hlavní graf: CZ base (Ø 7 dní) letos vs. pásmo 5 let (z `entsoe_prices` od 2019).
- Base CZ a DE za 60 dní + události (odstávky CZ) a pod tím spread CZ−DE jako sloupce.
- Profil dodávky posledního dne (CZ vs. DE, hodiny / čtvrthodiny).
- Mřížka zdrojů výroby: Ø GW (7 dní) letos vs. pásmo 5 let pro jádro, lignit, slunce,
  vítr, plyn, přečerpávací, průtočné a biomasu.

**Nordic** (`/nordic` v novém vzhledu)
- KPI: hydro Norsko a NO2 (vs. průměr 5 let), ceny NO2, SE3, FI (Ø 7 dní) a jejich spread proti DE.
- Hlavní graf: plnění nádrží Norska (NVE) letos vs. pásmo 5 let. Týdenní data jsou pro pásmo
  interpolovaná na dny.
- Mřížka NO1–NO5 (stejné měřítko).
- Mřížka spreadů zón proti DE (Ø 7 dní, stejné měřítko, nulová čára).
- Tabulky: hydrobilance a ceny zón.

Technicky: `web/tabs/v2.py` (stránky `v2-plyn`, `v2-elektrina`, `v2-nordic`), sdílené grafy
v `charts/v2.py` (`seasonal`, `fig_seasonal`, `fig_small_multiples`, `fig_multiples_ts`,
`fig_bars_signed`, `add_events`), šablony `web/templates/tabs/v2-*/`. Data a výpočty jsou
stejné jako ve starém vzhledu (Průzkumník/DuckDB, `core/overview.py`, `data/*`). Export
PNG/SVG/CSV/XLSX, tmavý režim a česká čísla fungují stejně jako u ostatních grafů.

**Ověřeno** nad daty z repa (zásobníky, výroba, DAP): screenshot ve světlém i tmavém režimu.
Nordic a ceny zón jsou ověřené **jen nad syntetickými daty** (v repu nejsou). S reálnými daty
je to **NEOVĚŘENO**, ověřit v kontejneru.

## 4. Doporučení, jak pokračovat (po porovnání uživatelem)

1. **Když se nový vzhled osvědčí:** přepnout ho jako výchozí a stejné šablony použít pro
   Trh EU (ceny zón: mřížka zón s pásmem 5 let, spready), Počasí (teplota/HDD letos vs.
   normál — tam už pásmo je) a Přehled dne (sparklines s pásmem normálu).
2. **Reporty** (`/reporty`) postavit na stejných grafech. PDF pak bude vypadat jako web
   a ubude duplicitního kódu grafů.
3. **Události**: rozšířit `MARKET_EVENTS` (o vlastní poznámky uživatele, např. editací
   JSON v `history/`) a feed anotovat i do grafů toků (UMM GASSCO na exportu NO).
4. **Forward křivky / TTF**: dashboard nemá ceny TTF ani PXE futures (nejsou volně
   strojově dostupné). Pro reporty o plynu by pomohl ruční vstup, nebo licencovaný zdroj
   denních settlementů — mimo rozsah tohoto kola.
5. **Jedna pravda** podle `DATA_AUDIT.md`: Elektřina v novém vzhledu už čte uložená data
   (ne živé API) — po přepnutí na výchozí lze živé dotazy ENTSO-E omezit jen na dnešek.
