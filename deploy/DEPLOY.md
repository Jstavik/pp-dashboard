# Nasazení PP Dashboard (Debian 13 LXC, 2 jádra, 2 GB RAM)

Web běží jako `uvicorn` na `127.0.0.1:8501`, ven se publikuje přes
**Cloudflare Tunnel** a přístup hlídá **Cloudflare Access**. Historie dat
leží mimo git v `/var/lib/ppdash/history`, aktualizuje ji systemd timer
(`scripts/update_all.py`). Tajné klíče jen v `~/.config/ppdash/env`.

Rozložení:

| Cesta | Obsah |
|---|---|
| `/opt/ppdash/app` | klon repozitáře (větev `rebuild/fastapi`, později `main`) |
| `/opt/ppdash/venv` | Python virtualenv |
| `/var/lib/ppdash/history` | historie (parquet/CSV, stejná struktura jako dřív `data/history/`) |
| `/var/lib/ppdash/cache` | disková TTL cache výsledků API (lze kdykoli smazat) |
| `/home/ppdash/.config/ppdash/env` | tokeny (`chmod 600`), vzor `deploy/env.example` |

## 1. Systém

```bash
apt update && apt install -y python3 python3-venv python3-pip git curl
timedatectl set-timezone Europe/Prague        # časy timerů jsou lokální
adduser --system --group --home /home/ppdash --shell /usr/sbin/nologin ppdash
mkdir -p /opt/ppdash /var/lib/ppdash/history /var/lib/ppdash/cache
chown -R ppdash:ppdash /opt/ppdash /var/lib/ppdash
```

## 2. Aplikace

```bash
sudo -u ppdash git clone -b rebuild/fastapi https://github.com/Jstavik/pp-dashboard.git /opt/ppdash/app
sudo -u ppdash python3 -m venv /opt/ppdash/venv
sudo -u ppdash /opt/ppdash/venv/bin/pip install -r /opt/ppdash/app/requirements-web.txt
```

Debian 13 má Python 3.13 — ověřeno s pandas 2.3, pyarrow 26, plotly 7.1,
fastapi 0.161.

## 3. Tokeny

```bash
sudo -u ppdash mkdir -p /home/ppdash/.config/ppdash
sudo -u ppdash cp /opt/ppdash/app/deploy/env.example /home/ppdash/.config/ppdash/env
sudo -u ppdash chmod 600 /home/ppdash/.config/ppdash/env
sudo -u ppdash nano /home/ppdash/.config/ppdash/env    # ENTSOE_TOKEN, GIE_API_KEY, DELTAGREEN_API_KEY
```

> GIE klíč byl dřív natvrdo v repu (`data/gie.py`, `data/lng.py`,
> `scripts/update_gas_history.py`) a je tedy v git historii — doporučuji
> si na agsi.gie.eu vygenerovat nový.

## 4. Počáteční data

Historie z repa (dokud `data/history/` v repu zůstává):

```bash
sudo -u ppdash rsync -a /opt/ppdash/app/data/history/ /var/lib/ppdash/history/
```

Pak jednou ručně doběhnout aktualizaci (navazuje od konce dat):

```bash
cd /opt/ppdash/app
sudo -u ppdash env PPDASH_DATA_DIR=/var/lib/ppdash \
  bash -c 'set -a; . /home/ppdash/.config/ppdash/env; /opt/ppdash/venv/bin/python scripts/update_all.py'
```

Užitečné přepínače: `--list` (co by se spustilo), `--only gas,cot`,
`--skip outages`, `-v`. Exit kódy: 0 OK (i s varováním), 1 některá úloha
selhala, 2 špatné argumenty, 3 jiná aktualizace běží.

## 5. systemd

```bash
cp /opt/ppdash/app/deploy/systemd/ppdash-*.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now ppdash-web.service
systemctl enable --now ppdash-update.timer ppdash-update-cot.timer
systemctl list-timers 'ppdash*'
curl -s http://127.0.0.1:8501/healthz
```

- `ppdash-web.service` — uvicorn, 1 worker, `MemoryMax=1400M`.
- `ppdash-update.timer` — 06:00 a 17:00 → `update_all.py` (vše kromě COT).
- `ppdash-update-cot.timer` — so + út 08:00 → `update_all.py --only cot`.

Logy: `journalctl -u ppdash-web -f`, `journalctl -u ppdash-update -n 200`.
Ruční spuštění aktualizace: `systemctl start ppdash-update.service`.

## 6. Cloudflare Tunnel + Access

```bash
# repo Cloudflare: https://pkg.cloudflare.com/
apt install -y cloudflared
cloudflared tunnel login
cloudflared tunnel create ppdash
cloudflared tunnel route dns ppdash ppdash.example.com
cp /opt/ppdash/app/deploy/cloudflared-config.example.yml /etc/cloudflared/config.yml   # doplň UUID + hostname
cloudflared service install && systemctl enable --now cloudflared
```

V Zero Trust → Access → Applications přidej *Self-hosted* aplikaci pro
`ppdash.example.com` s politikou (např. e-maily / One-time PIN). Aplikace
sama žádné přihlášení nemá a poslouchá jen na localhostu.

`/healthz` je levný (nečte data) — vhodný pro monitoring; pokud ho chceš
mít dostupný bez přihlášení, přidej v Access bypass politiku jen pro
cestu `/healthz`.

## 7. Aktualizace aplikace

```bash
cd /opt/ppdash/app && sudo -u ppdash git pull
sudo -u ppdash /opt/ppdash/venv/bin/pip install -r requirements-web.txt
systemctl restart ppdash-web
```

## Provozní poznámky

- Paměť: po projití všech záložek drží web ~430–490 MB RSS (cache dat
  ~180 MB). Parquet se čte jen v potřebných sloupcích, textové sloupce
  rovnou jako category; `ARROW_DEFAULT_MEMORY_POOL=system` a
  `MALLOC_ARENA_MAX=2` v unitě vrací uvolněnou paměť OS.
- Cache: v paměti s TTL jako dřív ve Streamlitu (ČEPS 60 s, ENTSO-E
  15–30 min, historie 5 min–6 h), výsledky API i na disku. „Obnovit data“
  v patičce webu cache smaže.
- Stará Streamlit app jde dočasně spustit (`pip install -r requirements.txt`,
  `streamlit run app.py`) — čte stejná data přes `PPDASH_DATA_DIR`.
