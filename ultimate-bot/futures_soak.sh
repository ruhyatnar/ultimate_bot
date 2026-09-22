#!/usr/bin/env bash
# 24h futures paper soak launcher (idempotent).
#
# Boots the engine with MARKET=futures PAPER_TRADE=true on an ISOLATED DB, under
# PM2 (so it survives SSH disconnects and restarts on crash), and reports status.
# The live spot engine is untouched: mode-scoped locks (main.py) let a paper
# engine run alongside it.
#
#   ./futures_soak.sh start    # (re)start the soak, fresh DB unless SOAK_KEEP_DB=1
#   ./futures_soak.sh status   # one-line health summary
#   ./futures_soak.sh stop     # stop the soak (DB is kept for analysis)
set -euo pipefail
cd "$(dirname "$0")"

SOAK_DB="${SOAK_DB:-./data/futures_soak.db}"
SOAK_LOG="${SOAK_LOG:-./logs/futures_soak.log}"
SOAK_CONTROL="${SOAK_CONTROL:-./data/futures_soak_control.json}"

case "${1:-status}" in
  start)
    if [ "${SOAK_KEEP_DB:-0}" != "1" ]; then
      rm -f "$SOAK_DB"
      echo "soak: fresh DB at $SOAK_DB (SOAK_KEEP_DB=1 to preserve)"
    fi
    mkdir -p ./data ./logs
    # Seed the paper balance to the REAL account equity so the soak mirrors
    # the operator's actual sizing math (default 22, override SOAK_EQUITY).
    SOAK_EQUITY="${SOAK_EQUITY:-22}"
    pm2 delete futures-soak >/dev/null 2>&1 || true
    date +%s%3N > ./data/futures_soak_start_ms
    rm -f ./data/soak_watchdog_state.json
    MARKET=futures \
    PAPER_TRADE=true \
    USE_TESTNET=false \
    DB_PATH="$SOAK_DB" \
    CONTROL_FILE="$SOAK_CONTROL" \
    LOG_FILE="$SOAK_LOG" \
    MAX_SYMBOLS="${SOAK_MAX_SYMBOLS:-3}" \
    PM2_PROCESS_NAME=futures-soak \
    pm2 start ./venv/bin/python3 --name futures-soak -- main.py
    pm2 save
    echo "soak: started (PM2: futures-soak, DB: $SOAK_DB, log: $SOAK_LOG)"
    ;;
  stop)
    pm2 delete futures-soak >/dev/null 2>&1 || true
    pm2 save
    echo "soak: stopped (DB kept at $SOAK_DB)"
    ;;
  status|*)
    if pm2 describe futures-soak >/dev/null 2>&1; then
      status=$(pm2 jlist | python3 -c "import json,sys; d=json.load(sys.stdin); print(next((p['pm2_env']['status'] for p in d if p['name']=='futures-soak'), 'unknown'))")
      start_ms=$(cat ./data/futures_soak_start_ms 2>/dev/null || true)
      if [ -z "$start_ms" ]; then
        # No marker (soak started before markers existed): fall back to PM2's
        # process start time so the 24h clock stays anchored to the real start.
        start_ms=$(pm2 jlist 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(next((p['pm2_env'].get('pm_uptime','') for p in d if p['name']=='futures-soak'), ''))" 2>/dev/null || true)
      fi
      if [ -n "$start_ms" ]; then
        hours=$(python3 -c "print(f'{(($(date +%s%3N))-$start_ms)/3600000:.1f}')")
      else
        hours="?"
      fi
      echo "soak: $status | ${hours}h / 24h | DB: $SOAK_DB | log: $SOAK_LOG"
    else
      echo "soak: not running"
    fi
    ;;
esac
