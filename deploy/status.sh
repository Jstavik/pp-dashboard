#!/usr/bin/env bash
# PP Dashboard — stav služeb, timerů, posledních běhů a stáří dat per dataset.
# Bez roota (systemctl --user). Exit 0 = vše OK, 1 = něco neběží / data stará.
#   deploy/status.sh            celý přehled
#   deploy/status.sh --logs     navíc posledních 15 řádků logu každé služby
set -uo pipefail

REPO="${PPDASH_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
VENV="${PPDASH_VENV:-$REPO/.venv}"
export PPDASH_DATA_DIR="${PPDASH_DATA_DIR:-$HOME/ppdash-data}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
SERVICES=(ppdash-web ppdash-live ppdash-update ppdash-update-cot ppdash-update-entsoe ppdash-update-weather)
TIMERS=(ppdash-live.timer ppdash-update.timer ppdash-update-cot.timer ppdash-update-entsoe.timer ppdash-update-weather.timer)
LOGS=0
[ "${1:-}" = "--logs" ] && LOGS=1
rc=0

hdr() { printf '\n\033[1m%s\033[0m\n' "$*"; }
prop() { systemctl --user show "$1.service" -p "$2" --value 2>/dev/null; }

hdr "Služby"
for s in "${SERVICES[@]}"; do
  active=$(systemctl --user is-active "$s.service" 2>/dev/null)
  enabled=$(systemctl --user is-enabled "$s.service" 2>/dev/null)
  # poslední běh: začátek, konec, výsledek a exit kód
  start=$(prop "$s" ExecMainStartTimestamp); stop=$(prop "$s" ExecMainExitTimestamp)
  result=$(prop "$s" Result); status=$(prop "$s" ExecMainStatus)
  printf '  %-20s %-10s %-9s posl. start: %-28s konec: %-28s výsledek: %s (exit %s)\n' \
    "$s" "${active:-?}" "${enabled:-?}" "${start:-—}" "${stop:-—}" "${result:-?}" "${status:-?}"
  if [ "$s" = ppdash-web ] && [ "$active" != active ]; then rc=1; fi
  if [ "$s" != ppdash-web ] && [ "${result:-success}" != success ]; then rc=1; fi
done

hdr "Timery"
systemctl --user list-timers --all 'ppdash*' --no-pager 2>/dev/null || { echo "  systemctl --user nedostupné"; rc=1; }
for t in "${TIMERS[@]}"; do
  [ "$(systemctl --user is-active "$t" 2>/dev/null)" = active ] || { echo "  !! $t není aktivní"; rc=1; }
done

hdr "Web"
if out=$(curl -fsS --max-time 5 http://127.0.0.1:8501/healthz 2>&1); then
  echo "  $out"
else
  echo "  !! /healthz neodpovídá: $out"; rc=1
fi

hdr "Úlohy — poslední běhy a chyby (history/.runs.json)"
PY="$VENV/bin/python"; [ -x "$PY" ] || PY=python3
(cd "$REPO" && "$PY" scripts/runs_status.py --errors) | sed 's/^/  /'
[ "${PIPESTATUS[0]}" -eq 0 ] || rc=1
for s in "${SERVICES[@]}"; do
  [ "$s" = ppdash-web ] && continue
  n=$(journalctl --user -u "$s.service" --since "-24h" -p err --no-pager -q 2>/dev/null | wc -l)
  [ "${n:-0}" -gt 0 ] && echo "  $s: $n chybových řádků v journalu za 24 h (deploy/status.sh --logs)"
done

hdr "Živá úloha — posledních 5 běhů"
journalctl --user -u ppdash-live.service -n 5 --no-pager -o short-iso 2>/dev/null | sed 's/^/  /'

hdr "Data ($PPDASH_DATA_DIR/history)"
(cd "$REPO" && "$PY" scripts/data_status.py) | sed 's/^/  /'
[ "${PIPESTATUS[0]}" -eq 0 ] || rc=1

if [ "$LOGS" -eq 1 ]; then
  for s in "${SERVICES[@]}"; do
    hdr "Log $s"
    journalctl --user -u "$s.service" -n 15 --no-pager -o short-iso 2>/dev/null | sed 's/^/  /'
  done
fi

df -h "$PPDASH_DATA_DIR" 2>/dev/null | tail -1 | awk '{print "\nDisk dat: " $3 " použito, " $4 " volno (" $5 ")"}'
exit $rc
