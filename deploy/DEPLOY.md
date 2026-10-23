# Nasazení PP Dashboard bez roota (uživatelské systemd unity)

Kontejner (Debian 13 LXC, 2 jádra, 4 GB RAM), běžný uživatel **`claude`
bez sudo**. Všechno běží jako `systemctl --user`. Web poslouchá na
`0.0.0.0:8501` (LAN), ven ho publikuje **Cloudflare Tunnel** z jiného
stroje v LAN a přístup hlídá **Cloudflare Access** (mimo tento návod).
Hlavičkám `X-Forwarded-*` se věří jen od `127.0.0.1`, `192.168.68.124`
a `192.168.68.103` (`--forwarded-allow-ips` v `ppdash-web.service` —
při změně stroje s tunelem upravit tam).

| Cesta | Obsah |
|---|---|
| `/home/claude/pp-dashboard` | klon repozitáře (větev `rebuild/fastapi`) |
| `/home/claude/pp-dashboard/.venv` | Python virtualenv |
| `/home/claude/ppdash-data/history` | vlastní „databáze“ — parquet/CSV historie (`PPDASH_DATA_DIR`) |
| `/home/claude/ppdash-data/cache` | disková TTL cache API (lze kdykoli smazat) |
| `~/.config/ppdash/env` | `ENTSOE_TOKEN`, `GIE_API_KEY` (`chmod 600`), vzor `deploy/env.example` |
| `~/.config/systemd/user/ppdash-*` | unity (generuje `install_user.sh` z `deploy/systemd-user/`) |

## 0. Jednou admin (root)

```bash
loginctl enable-linger claude      # uživatelské služby běží i bez přihlášení a po rebootu
apt install -y python3-venv rsync curl   # pokud v obrazu chybí
```

Časové pásmo kontejneru měnit není potřeba — timery mají `Europe/Prague`
přímo v `OnCalendar`.

## 1. Instalace (uživatel claude)

```bash
git clone -b rebuild/fastapi https://github.com/Jstavik/pp-dashboard.git ~/pp-dashboard
~/pp-dashboard/deploy/install_user.sh            # venv, adresáře, historie z repa, unity, start
nano ~/.config/ppdash/env                        # ENTSOE_TOKEN=…, GIE_API_KEY=…
~/pp-dashboard/deploy/install_user.sh --initial-update   # restart webu + jednou doběhnout aktualizaci
```

`install_user.sh` je idempotentní (přepínače `--no-seed`, `--no-start`,
`--initial-update`):

1. `.venv` + `pip install -r requirements-web.txt` (jen při změně souboru),
2. `~/ppdash-data/{history,cache}`, `~/.config/ppdash/env` ze vzoru,
3. historie z `data/history` v repu přes `rsync -a --ignore-existing` —
   doplní jen chybějící soubory, novější data v `~/ppdash-data` nikdy
   nepřepíše; **mrtvé datasety** (`scripts/data_status.py --dead`:
   `hu_generation`, `nuclear_fr_generation`, `entsog_capacity`,
   `hydro_reservoirs.csv`) vynechá,
4. unity do `~/.config/systemd/user`, `daemon-reload`, `enable --now`
   timerů, restart webu a kontrola `/healthz`.

> GIE klíč byl dřív natvrdo v repu a je v git historii — doporučuji ho
> na agsi.gie.eu přegenerovat.

## 2. Služby a timery

| Unita | Kdy | Co |
|---|---|---|
| `ppdash-web.service` | trvale | uvicorn, 1 worker, `MemoryHigh=1300M`, `MemoryMax=1600M` |
| `ppdash-live.timer` | každou minutu (:20 s) | `update_all.py --only live -q`: ČEPS minutová data (odchylka, SVR, zatížení, výroba, OZE, frekvence, toky, cena odchylky), GASSCO realtime nominace, GASSCO UMM |
| `ppdash-update.timer` | 06:00 a 17:00 Praha | `update_all.py`: ENTSO-G toky/allocation/operational, GIE, LNG, GASSCO nominace, hydro, DAP, výroba, odstávky, Energy-Charts |
| `ppdash-update-entsoe.timer` | 07:10, 13:20, 19:10 Praha | `update_all.py --only entsoe`: ceny všech zón, spotřeba + prognóza, prognóza OZE, toky (vlastní zámek, backfill po částech s rozpočtem) |
| `ppdash-update-cot.timer` | st 14:00 a 19:00, čt + so 08:00 Praha | `update_all.py --only cot` (ICE vydává report za pátek ve středu; dohání i chybějící týdny) |

Všechny aktualizace jsou inkrementální: zjistí konec uložených dat, stáhnou
jen novější (s malým překryvem kvůli revizím) a zapíšou upsertem s dedupem —
opakovaný běh nic nezdvojí, nezměněný soubor se nepřepisuje. Live běh má
vlastní zámek (`history/.live.lock`), takže ho dlouhý denní běh neblokuje.

```bash
deploy/status.sh                 # služby, timery, poslední běhy, /healthz, stáří dat per dataset
deploy/status.sh --logs          # + posledních 15 řádků logu každé služby
journalctl --user -u ppdash-web -f
journalctl --user -u ppdash-update -n 200
systemctl --user start ppdash-update.service     # ruční aktualizace
systemctl --user list-timers 'ppdash*'
```

Ruční spuštění z shellu (načte `~/.config/ppdash/env`, data bere z
`~/ppdash-data`, pokud existuje):

```bash
cd ~/pp-dashboard && .venv/bin/python scripts/update_all.py --list
.venv/bin/python scripts/update_all.py --only energy_charts -v
.venv/bin/python scripts/data_status.py
```

Exit kódy `update_all.py`: 0 OK (i s varováním), 1 některá úloha selhala,
2 špatné argumenty, 3 jiná aktualizace běží.

## 3. Aktualizace aplikace

```bash
cd ~/pp-dashboard && git pull && deploy/install_user.sh --no-seed
```

## 4. Cloudflare Tunnel + Access

Mimo rozsah tohoto skriptu; vzor konfigurace `cloudflared-config.example.yml`
(služba `http://<IP kontejneru>:8501`, z kontejneru samotného `http://127.0.0.1:8501`). `/healthz` je levný (nečte data) — vhodný
pro monitoring (případně Access bypass jen pro tuto cestu).

## Provozní poznámky

- Paměť: web ~430–490 MB RSS po projití všech záložek; aktualizace má
  `MemoryMax=1000M`, live běh 400M. Limity platí, jen když má uživatelský
  manager delegovaný memory controller (Debian 13 ano).
- Minutový timer spouští krátký Python proces (~1–2 s CPU); WSDL ČEPS je
  cachovaný na disku (`cache/zeep-wsdl.db`), takže se nestahuje každou minutu.
- Objem live dat: ČEPS ~10–15 MB/měsíc, GASSCO realtime < 1 MB/měsíc
  (zapisuje se jen při nové publikaci feedu).
- Stará Streamlit app jde dočasně spustit (`pip install -r requirements.txt`,
  `streamlit run app.py`) — čte stejná data přes `PPDASH_DATA_DIR`.
