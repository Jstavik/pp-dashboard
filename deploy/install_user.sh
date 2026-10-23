#!/usr/bin/env bash
# PP Dashboard — instalace/aktualizace BEZ roota (uživatelské systemd unity).
#
# Idempotentní: lze spouštět opakovaně (i po `git pull` jako "deploy").
#   1. venv (.venv v repu) + závislosti (jen když se změnil requirements-web.txt)
#   2. adresáře dat a konfigurace, ~/.config/ppdash/env ze vzoru (chmod 600)
#   3. počáteční historie z data/history v repu — rsync --ignore-existing
#      (nikdy nepřepíše novější data v PPDASH_DATA_DIR), BEZ mrtvých datasetů
#   4. unity do ~/.config/systemd/user, daemon-reload, enable --now timerů a webu,
#      restart webu (načte nový kód)
#
# Jednou předem (admin s rootem), aby služby běžely i bez přihlášení:
#   sudo loginctl enable-linger claude
#
# Použití: deploy/install_user.sh [--no-seed] [--no-start] [--initial-update]
#   --no-seed         nekopírovat historii z repa
#   --no-start        jen nainstalovat unity, nic nespouštět/nerestartovat
#   --initial-update  po instalaci jednou doběhnout update_all.py (navazuje od konce dat)
#
# Cesty lze přepsat proměnnými: PPDASH_REPO, PPDASH_VENV, PPDASH_DATA_DIR.
set -euo pipefail

REPO="${PPDASH_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
VENV="${PPDASH_VENV:-$REPO/.venv}"
DATA="${PPDASH_DATA_DIR:-$HOME/ppdash-data}"
CONF_DIR="$HOME/.config/ppdash"
UNIT_DIR="$HOME/.config/systemd/user"
UNITS_SRC="$REPO/deploy/systemd-user"
TIMERS=(ppdash-update.timer ppdash-live.timer ppdash-update-cot.timer ppdash-update-entsoe.timer ppdash-update-weather.timer)

SEED=1 START=1 INITIAL=0
for arg in "$@"; do
  case "$arg" in
    --no-seed) SEED=0 ;;
    --no-start) START=0 ;;
    --initial-update) INITIAL=1 ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "neznámý přepínač: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!!\033[0m %s\n' "$*" >&2; }

if [ "$(id -u)" -eq 0 ]; then
  echo "Spouštěj jako běžný uživatel (např. claude), ne jako root." >&2
  exit 1
fi
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

# ── 1. venv ──────────────────────────────────────────────────────────────
PY="${PYTHON:-python3}"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || { echo "Potřeba Python >= 3.10 ($PY)" >&2; exit 1; }
if [ ! -x "$VENV/bin/python" ]; then
  say "vytvářím venv $VENV"
  "$PY" -m venv "$VENV"
fi
REQ_HASH="$(sha256sum "$REPO/requirements-web.txt" | cut -d' ' -f1)"
if [ "$(cat "$VENV/.ppdash-req" 2>/dev/null || true)" != "$REQ_HASH" ]; then
  say "instaluji závislosti (requirements-web.txt)"
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q -r "$REPO/requirements-web.txt"
  echo "$REQ_HASH" > "$VENV/.ppdash-req"
else
  say "závislosti beze změny"
fi
# Chrome pro export reportů do PDF (kaleido) — bez roota do uživatelského
# adresáře; selhání nevadí (zbývá tisk z prohlížeče → PDF)
if [ -x "$VENV/bin/plotly_get_chrome" ] && [ ! -f "$VENV/.ppdash-chrome" ]; then
  say "stahuji Chrome pro export PDF (jednou, ~150 MB)"
  if "$VENV/bin/plotly_get_chrome" -y >/dev/null 2>&1; then touch "$VENV/.ppdash-chrome"
  else say "  Chrome se nepodařilo stáhnout — PDF z webu nepůjde, tisk z prohlížeče ano"; fi
fi

# ── 2. adresáře + env ────────────────────────────────────────────────────
mkdir -p "$DATA/history" "$DATA/cache" "$CONF_DIR" "$UNIT_DIR"
if [ ! -f "$CONF_DIR/env" ]; then
  say "vytvářím $CONF_DIR/env ze vzoru — doplň ENTSOE_TOKEN a GIE_API_KEY"
  cp "$REPO/deploy/env.example" "$CONF_DIR/env"
fi
chmod 600 "$CONF_DIR/env"
for key in ENTSOE_TOKEN GIE_API_KEY; do
  grep -Eq "^${key}=.+" "$CONF_DIR/env" || warn "$key v $CONF_DIR/env není vyplněný"
done

# ── 3. počáteční historie z repa ─────────────────────────────────────────
if [ "$SEED" -eq 1 ] && [ -d "$REPO/data/history" ]; then
  say "historie z repa → $DATA/history (jen chybějící soubory, bez mrtvých datasetů)"
  EXCLUDES=(--exclude '.gitkeep' --exclude '.update.lock' --exclude '.live.lock')
  while IFS= read -r dead; do
    [ -n "$dead" ] && EXCLUDES+=(--exclude "/$dead")
  done < <(cd "$REPO" && PPDASH_DATA_DIR="$DATA" "$VENV/bin/python" scripts/data_status.py --dead)
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --ignore-existing "${EXCLUDES[@]}" "$REPO/data/history/" "$DATA/history/"
  else
    warn "rsync není nainstalovaný — kopíruji přes cp -an (mrtvé datasety se pak smažou)"
    if cp --help 2>/dev/null | grep -q -- '--update\[=UPDATE\]'; then
      cp -a --update=none "$REPO/data/history/." "$DATA/history/"
    else
      cp -an "$REPO/data/history/." "$DATA/history/"
    fi
    while IFS= read -r dead; do
      [ -n "$dead" ] && rm -rf "${DATA:?}/history/$dead"
    done < <(cd "$REPO" && "$VENV/bin/python" scripts/data_status.py --dead)
  fi
fi

# ── 4. systemd --user ────────────────────────────────────────────────────
say "unity → $UNIT_DIR"
for src in "$UNITS_SRC"/*.service "$UNITS_SRC"/*.timer; do
  dst="$UNIT_DIR/$(basename "$src")"
  tmp="$(mktemp)"
  sed -e "s|@REPO@|$REPO|g" -e "s|@VENV@|$VENV|g" -e "s|@DATA@|$DATA|g" "$src" > "$tmp"
  if ! cmp -s "$tmp" "$dst" 2>/dev/null; then
    mv "$tmp" "$dst"
    echo "   $(basename "$src")"
  else
    rm -f "$tmp"
  fi
done

if ! systemctl --user show-environment >/dev/null 2>&1; then
  warn "systemctl --user nedostupné (chybí uživatelský manager / XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR)."
  warn "Admin: sudo loginctl enable-linger $USER   — pak skript spusť znovu."
  exit 1
fi
if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || echo no)" != "yes" ]; then
  warn "linger není zapnutý — po odhlášení služby skončí. Admin: sudo loginctl enable-linger $USER"
fi

systemctl --user daemon-reload
if [ "$START" -eq 1 ]; then
  systemctl --user enable --now "${TIMERS[@]}" >/dev/null
  systemctl --user enable ppdash-web.service >/dev/null
  say "restart webu"
  systemctl --user restart ppdash-web.service
  for _ in $(seq 1 20); do
    curl -fsS http://127.0.0.1:8501/healthz >/dev/null 2>&1 && break
    sleep 1
  done
  curl -fsS http://127.0.0.1:8501/healthz && echo || warn "web neodpovídá na /healthz (journalctl --user -u ppdash-web -n 50)"
else
  systemctl --user enable "${TIMERS[@]}" ppdash-web.service >/dev/null
fi

if [ "$INITIAL" -eq 1 ]; then
  say "počáteční aktualizace (může trvat desítky minut)"
  systemctl --user start ppdash-update.service || warn "aktualizace skončila chybou (journalctl --user -u ppdash-update -n 200)"
fi

say "hotovo — stav: $REPO/deploy/status.sh"
