# 🚀 Go-Live Checklist — Paper → Live Trading

Read this top to bottom before flipping `PAPER_TRADE=false`. Every step has a
verification command; do not skip a failed step.

> **Paper first**: run the paper engine for at least 3–5 trading days and
> confirm the monitor's stats (win rate, PF, drawdown) behave like the
> backtest before risking real funds. The engine shares one
> `/tmp/ultimate_bot.live.lock` across all live engines — only one live
> instance can ever run, spot or futures.

---

## 1. Binance account & API key

- [ ] Binance account with Spot **and** USDⓈ-M Futures wallets enabled (futures only if `MARKET=futures`).
- [ ] Create an **API key** with *Enable Spot & Margin Trading* (and *Futures* if needed). **Withdrawals must stay disabled.**
- [ ] Restrict the key to your VPS IP (Binance → API Management → IP whitelist).
- [ ] Two-factor auth enabled on the account.

## 2. Ed25519 signing keys (recommended over HMAC)

```bash
cd ultimate-bot
mkdir -p keys && chmod 700 keys
openssl genpkey -algorithm Ed25519 -out keys/private_key.pem && chmod 600 keys/private_key.pem
openssl pkey -in keys/private_key.pem -pubout -out keys/public_key.pem
cat keys/public_key.pem     # paste this into Binance → API Management → Ed25519 public key
```

- [ ] Public key registered on Binance; private key never left the VPS.
- [ ] `ls -l keys/private_key.pem` shows `-rw-------` (600) and owner-only directory.

*(HMAC fallback: set `BINANCE_API_SECRET` instead — the engine accepts either.)*

## 3. Engine `.env` — flip to live

`ultimate-bot/.env` — change exactly these keys from the paper defaults:

```ini
PAPER_TRADE=false
BINANCE_API_KEY=<your key>
# Either:
BINANCE_PRIVATE_KEY_PATH=./keys/private_key.pem
# ...or (HMAC fallback):
#BINANCE_API_SECRET=<your secret>
```

**Futures only** (skip when `MARKET=spot`):

```ini
MARKET=futures
FUTURES_LEVERAGE=1            # start at 1x — the strategy is not a leverage strategy
FUTURES_MARGIN_TYPE=ISOLATED  # caps the loss at the position's margin
FUNDING_RATE_MAX=0.0005       # skip longs paying >0.05%/8h funding
```

- [ ] `STATIC_SYMBOLS` matches the symbol(s) you validated in the backtest.
- [ ] `MAX_TRADES_PER_DAY`, `RISK_PER_TRADE`, `MAX_DAILY_DRAWDOWN` left at proven values (2/day, 1%, 5%).
- [ ] No inline `# comments` on value lines (this dotenv build passes them into the value).

## 4. Risk sanity (read, don't skim)

- Sizing is **fixed-fractional**: every trade risks `RISK_PER_TRADE` (1%) of
  equity between entry and stop. A $1,000 account risks ~$10 per stop-out.
- `MAX_DAILY_DRAWDOWN=0.05` trips a circuit breaker at −5% on the day; entries
  stay blocked until the UTC reset.
- `LOSS_REENTRY_COOLDOWN=900` blocks re-entry on a symbol for 15 min after any
  stop-out; `MAX_LOSS_STREAK=3` + `COOLDOWN_LOSS=3600` pauses the whole account
  for an hour after 3 consecutive losses.
- **Small accounts**: equity × allocation caps must stay above the $5
  minNotional or every entry is skipped (the engine warns at boot).

## 5. Pre-flight verification (paper engine still running)

```bash
cd ultimate-bot

# 5.1 Config validates in live mode (dry parse — does not trade)
./venv/bin/python3 -c "
import sys; sys.path.insert(0, '.')
from config import load_config
c = load_config()
assert c['PAPER_TRADE'] is False, 'PAPER_TRADE still true'
assert c['API_KEY'], 'BINANCE_API_KEY missing'
print('live config OK:', c['MARKET'], c['PRESET'])"

# 5.2 Full test battery against the NEW config
./venv/bin/python3 smoke_test.py           # 13/13
./venv/bin/python3 sync_test.py            # 27/27

# 5.3 Backtest re-validation on the exact symbol (fresh data)
./venv/bin/python3 backtest.py --symbol NEARUSDT --pages 30 --quiet

# 5.4 Keys + signature + clock + signed account read — one command, no orders
./venv/bin/python3 live_signed_probe.py                  # uses MARKET from .env
./venv/bin/python3 live_signed_probe.py --market futures # or probe the other leg
```

The probe drives the engine's REAL production clients end to end: credential load
(Ed25519 parse / HMAC), an offline sign+verify self-test, connectivity with a
live clock-offset measurement (the -1021 guard), then the signed account read —
with targeted hints for every failure mode (-2014/-2015/-1021/-1022/-40102,
canTrade=false). It never places an order and runs outside the engine (no lock,
no DB). Exit 0 = safe to continue.

```bash
# 5.5 Order-builder dry run — REAL builders, REAL filters/prices, NO order sent
./venv/bin/python3 order_dry_run_probe.py                 # both legs
./venv/bin/python3 order_dry_run_probe.py --market futures # or one leg
```

The dry run drives the four clients the engine can route an order through —
spot/futures × REST/WS-API — for an entry BUY and an exit SELL. It sizes a
quantity through the engine's own `sanitize_order` against LIVE exchangeInfo
(LOT_SIZE / minNotional), builds the real request (timestamp + signature), then
verifies the result while a stubbed transport captures it instead of sending.
Checks: correct path, MARKET type, step-quantized quantity, fresh timestamp
(recvWindow), and `positionSide=BOTH` / exit `reduceOnly=true` on futures. It
never transmits an order. `--self-test` validates the harness offline.

- [ ] Dry run exit code is 0 (all four builders green). A ✗ names the exact
  invalid field — fix before flipping `PAPER_TRADE`.
- [ ] Probe exit code is 0. A failing signed read means keys/IP-whitelist/permissions — fix before continuing.

## 6. Go live

```bash
pm2 stop ultimate-bot                 # stop the paper instance (releases the lock)
pm2 delete ultimate-bot               # clear the old definition
pm2 start ecosystem.config.cjs        # boots main.py with PAPER_TRADE=false
pm2 save                              # persist the new process list
pm2 logs ultimate-bot                 # watch the first boot
```

First-boot checklist (all in `logs/trading.log`):

- [ ] `Starting MARKET-ONLY BOT [LIVE EXECUTION]` — **not** paper.
- [ ] `Live account equity updated: $… USDT` — matches your real wallet.
- [ ] No `CONFIG WARNING` about minNotional feasibility.
- [ ] WS streams subscribed; monitor at `http://VPS:3000` shows **LIVE** chip and real equity.

## 7. First hour in live

- [ ] Watch the first entry end-to-end: `Entry market order placed` → fill → SL/TP tracked in the Positions table.
- [ ] Trigger one manual **Market Close** from the monitor on a tiny position to prove the exit path.
- [ ] Confirm `PnL verified vs exchange` lines appear on the first close (futures).
- [ ] `pm2 ls` clean, no restart loops (`↺` counter stable).

## 8. Rollback plan

```bash
# Pause new entries instantly (positions stay managed) — from the web monitor
# or via the control file:
echo '{"paused": true}' > ultimate-bot/data/engine_control.json

# Full stop (positions close via market orders ONLY if you ask the engine to;
# pm2 stop just halts the loop — open positions remain on the exchange and are
# re-adopted with SL/TP on the next boot):
pm2 stop ultimate-bot
```

- Emergency liquidation of everything: web monitor → **Close All** (or set
  `close_all` in `engine_control.json`).

## 9. Ongoing

- `DISCORD_WEBHOOK_URL` set in `.env` so fills/exits/breaker trips reach you.
- Check `logs/trading.log` daily the first week; keep `LOG_LEVEL=INFO`.
- Re-run the backtest after any `.env` strategy change — the backtest and the
  engine share the exact same decision code by design.
