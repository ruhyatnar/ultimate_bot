# 🚀 Ultimate Binance Market-Only Trading Bot — Operations Center

A production-grade **Binance Spot** trading engine with **market-only execution**, one backtest-proven strategy (**`intraday_rsi`**: daily-EMA50 regime gate + RSI dip trigger on a fixed % bracket), **strict risk & drawdown management**, and **Ed25519 asymmetric cryptography** — paired with an interactive **web operations center** for live monitoring, parameter tuning, and 1-click VPS sync.

```
├── ultimate-bot/          # The audited Python trading engine (Debian 13 / PEP 668 / PM2)
│   ├── main.py            # Async engine entrypoint (signal loop, exits, reconciliation)
│   ├── status.py          # CLI dashboard + embedded web monitor / control API
│   ├── config.py          # .env loader, presets, boot-time validation
│   ├── ecosystem.config.cjs# PM2: runs engine AND web monitor
│   ├── .env.example       # Annotated template for every config key
│   └── src/               # exchange / strategy / risk / trade / database / reporting modules
└── src/                   # React + Vite web dashboard (real-engine monitor / ops center)
```

Full documentation and step-by-step Debian 13 VPS instructions live in **[ultimate-bot/README.md](./ultimate-bot/README.md)**.

> **Smoke test**: `npm run smoke` (or `ultimate-bot/venv/bin/python3 ultimate-bot/smoke_test.py`) boots the engine against an isolated temp DB and verifies boot, single-instance locking, second-instance rejection, web-monitor stats consistency, and clean SIGTERM shutdown — all without touching your real `trading.db`.

### 📝 Changelog — 2026-09-22 (live-trade integration audit)

- **Two real integration defects found and fixed on the live order path**:
  - **Live SPOT REST orders could never execute.** `OrderManager.place_market_order` calls `rest.place_order(..., reduce_only=...)` from its REST fallback (whenever the WS API session is down — or always, for an HMAC-only live key where `ws_api` is `None`). The spot `RestClient.place_order` did not accept that keyword, so **every** live spot order raised `TypeError`, was caught by the outer handler, logged as "Order preparation failed", and silently skipped. The spot client now accepts (and ignores) `reduce_only` — a spot SELL always reduces the base holding — matching the two WS API clients and the futures REST client.
  - **The configured futures margin type was never applied.** In `main.py` the per-symbol loop compared `config["FUTURES_MARGIN_TYPE"]` against `effective_margin` *after* publishing `effective_margin` into that same key, so the check was always true and `set_margin_type()` was never called — the account kept its default (CROSSED) while the engine and dashboard reported the requested ISOLATED margin. The loop now always performs the idempotent write and treats Binance `-4046` / `-4059` as "already set"; both codes are also now non-retryable in the futures client so the benign case costs one request instead of three.
  - Removed the dead `user_stream` parameter/attribute from `OrderManager` (nothing constructed it) and seeded `last_order_result` in `__init__`.
- **Regression guard added**: `test_scenarios.py` gains an **S6** block asserting every order-routing client (spot/futures × REST/WS-API) accepts `reduce_only`, plus that the dead `user_stream` param stays gone.
- **New order-builder dry-run probe** (`ultimate-bot/order_dry_run_probe.py`, `npm run probe:orders`): drives the four clients an order can route through — spot/futures × REST/WS-API — for an entry BUY and an exit SELL against LIVE exchangeInfo/prices, sizing via the engine's own `sanitize_order` and building the real signed request, while a stubbed transport captures it. Verifies endpoint, `type=MARKET`, step-quantized quantity, fresh timestamp, and futures `positionSide=BOTH` + exit `reduceOnly=true`. **Never sends an order.** `--self-test` validates the harness offline. Both legs verified green against production with a real Ed25519 key.
- **Cleanliness re-verified**: independent AST scan finds **zero** unreferenced module-level defs/methods across the engine; zero duplicate dict keys, parameters, methods or module-level names; `.env` ≡ `.env.example` with **88 active keys, zero duplicates (active or commented), every key consumed** by a real reader; no unused TS components/exports/types (`tsc --noEmit` + import-graph checks clean); runtime `__pycache__` purged. Full battery green: py_compile all, smoke **13/13**, sync **27/27**, scenarios **ALL_OK**, futures reconcile **ALL_OK**, UI smoke **9/9**.

- **Fixed: WS-path account equity was inflated ~5× on multi-assets accounts (`$23 → $111.75`).** Observed while watching the first live entry: the engine logged `Live account equity updated: $23.01 (via rest)` and `$111.75 (via ws)` on alternating cycles. Root cause: in Binance **multi-assets collateral mode** the `/fapi/v3/account` payload reports a collateral-equivalent `availableBalance` for *every* asset the wallet could use as margin (U/BFUSD/USDC/RWUSD/USD1/FDUSD/LDUSDT/BNB/BTC/ETH/…) while `walletBalance` stays `0` for the ones it does not actually hold — the same USDT wallet restated in other denominations. `FuturesWSApiClient.seed_balances` kept any row with `availableBalance > 0`, so BNB/BTC/ETH (and USDC, which trades at $1) were cached as real holdings and `_fetch_equity` priced them at the **real** ticker: 0.0274 BNB + 0.000254 BTC + 0.0079 ETH + 23.01 USDC ≈ **$88.7**, giving $111.75 next to the true $23.01. A row is now seeded only when the **wallet** balance is non-zero (legacy `free`/`locked` rows are still accepted), which makes the WS path agree with `totalMarginBalance`. Verified against the live account (before: 11 rows / $111.74; after: 1 row / $23.02 = exchange truth $23.0059) and live after reload (both transports now report $23.0x). This mattered because `total_equity` feeds position sizing, the daily-drawdown breaker and the dashboard's equity. Regression guard added as **S7** in `test_scenarios.py`.
- **Fixed: monitor config echo could never refresh from `.env` (monitor↔engine desync).** Root cause: `capital_roadmap.py` (imported lazily on the first status build) does `import config`, and `config.py` calls `load_dotenv()` — injecting every `.env` key into `os.environ`. `status.py`'s `load_env()` then merged the **live** `os.environ` over the freshly-read file, so after the first payload build it permanently re-applied the **startup snapshot**; `refresh_env_config()` could never observe an out-of-band `.env` edit, and `/api/status`/`/ws` advertised stale values (e.g. showed `FUTURES_MARGIN_TYPE=ISOLATED` while the engine ran `CROSSED`). Fix: `status.py` now snapshots `os.environ` at import (`_PROCESS_ENV`, before any dotenv pollution) and merges overrides from that frozen snapshot, so real process env (PM2/systemd/tests/CLI) still overrides but later dotenv-injected values can't freeze the refresh. Verified live: `SIGNAL_INTERVAL` 10→11→10 in `.env` reflected by the running monitor with **no restart**; smoke **13/13** + sync **27/27** still green.

### 📝 Changelog — 2026-09-21 (hardening, cleanup & monitor refresh)

- **Fourth full pass — duplicate & cleanliness audit (all clean)**:
  - Duplicate-variable scan across the whole repo: `.env`/`.env.example` (0 duplicate keys, active or commented; the two files are byte-identical), 41 Python files (0 duplicate imports, module defs, class methods, or dict-literal keys — the silent-shadow classes Python doesn't warn about), `package.json`/`tsconfig.json` (0 duplicate JSON keys), PM2 config (only the two expected app blocks), TS/TSX (compile-clean, which proves no duplicate keys/exports).
  - Orphan sweep: only finding was a stray root `__pycache__/` (deleted); every other file re-verified as referenced.
  - Integration re-verified after the audit: py_compile clean, smoke 13/13, sync 27/27, scenarios ALL_OK, futures reconcile ALL_OK, UI smoke 9/9, engine↔monitor payload healthy (clock −48ms synced, streams up, breaker state correctly persisted), zero errors in the engine log.

- **Exchange clock sync, end to end (VPS → engine → dashboard)**:
  - WS API order clients (spot + futures) now match the REST clients' time discipline: midpoint-estimated offset measurement at logon, a 5-minute re-sync loop while connected, auto re-sync when stale, and immediate re-sync on a `-1021` timestamp rejection so the retry succeeds. Signed order timestamps were previously compensated **once** at logon and never re-measured — hours into a session, any local-clock drift leaked into order signatures.
  - The measured offset publishes as `ws_streams.order_api.time_offset_ms` + `clock_synced`, and the dashboard's Engine Health card shows a new **Clock sync (Binance)** row — green when fresh and within ~2.5s of the exchange, amber on stale/drift. The WS API measurement is the preferred source (midpoint estimator); when it has none (paper mode, or live without an Ed25519 key) the payload falls back to the REST client's always-on server-time sync, so the offset is visible in **every** mode.
  - Verified live: VPS drift vs Binance spot/futures measured at +136…+152ms with NTP active (chrony); WS time-sync unit checks pass; REST fallback confirmed publishing in futures-paper mode (`-52ms`, fresh); full battery green after deploy.
  - New `live_signed_probe.py` (+ `npm run probe`): one-command go-live credential check driving the REAL production clients — credential load, offline sign+verify self-test, connectivity + live clock-offset measurement, then the signed account read (no orders, engine-independent, exit-code driven; targeted hints for -2014/-2015/-1021/-1022/-40102/canTrade). Wire into GO_LIVE.md step 5.4, replacing the old inline snippet. Runs pre-key as a network/clock diagnostic.
  - **First full GO_LIVE.md walk-through executed with real keys**: probe ALL GREEN on both legs (futures signed read: wallet 23.02 USDT / available 23.01; spot: 0.00 — signature verified, timestamp accepted, no -1021). Two probe bugs found & fixed during the run (Ed25519 verify must go through `key.public_key()`; live-mode two-pass config so the production client loads its own credentials — doubles as the §5.1 live-config parse). Fixed Ed25519 key file perms 664→600 per checklist §2.
  - **§5.3 backtest gate caught a real strategy killer and the config was fixed**: with keys present, the fresh-data backtest returned −16.5% / PF 0.62 vs the validated +7.11% / PF 2.06. Root cause: `.env` carried `RSI_TIMEFRAME=15m` + explicit `RSI_TIMEFRAME_MS=900000` while the preset pins `RSI_TIMEFRAME=1h` — the 15m bucket re-timed the RSI gate to 4.6× the trade frequency (88 trades, death by fees: 131 USDT fees vs 32 in the validated run). Fixed `.env` to `RSI_TIMEFRAME=1h` with `_MS` derived, commented out the stray explicit `RSI_TIMEFRAME_MS`, restored `MAX_TRADES_PER_DAY` to the preset's 2/day, and added a **boot drift guard** (config.py warns when an explicit `_MS` disagrees with `RSI_TIMEFRAME`). Backtest re-verified: +7.11% / 19 trades / PF 2.06 — exact match with the validated baseline.

- **Live-readiness re-audit (third full pass)**:
  - Orphan sweep re-run: deleted AI-studio scaffold `metadata.json` (referenced by nothing) and the empty `ultimate-bot/__init__.py` (Python cannot import a hyphenated directory as a package, so it was pure clutter); purged all `__pycache__` artifacts.
  - Dead-code scans clean: AST scan confirms **zero unreferenced module-level defs or imports** across the engine `src/` tree; all 16 dashboard components, every `StatusCards` export, and both util modules verified in use.
  - Engine↔monitor field audit: all **374 keys** of the live `/api/status` payload mapped to their typed UI consumers — no missing fields, no dead fields; the engine remains the single source of truth for every KPI (equity, PnL, streaks, breaker, profit factor, entries-today, futures state).
  - Full battery re-verified: `py_compile` across all 27 engine files, smoke 13/13, sync 27/27, scenarios ALL_OK, futures reconcile ALL_OK (incl. multi-fill orphan adoption), UI smoke 9/9, `tsc --noEmit` clean.

- **Repository cleanup — dead code removed**:
  - Deleted the abandoned Reflex app (`ultimate_bot/` folder, `rxconfig.py`, `reflex.lock`, root `requirements.txt` with the reflex pin, empty `apt-packages.txt`, stale `plan.md`) and stray `__init__.py` files inside the React `src/` tree. The repo is now exactly two layers: the React dashboard (root) and the Python engine (`ultimate-bot/`).
  - The web monitor's "Project Code & ZIP" tab now ships `ultimate-bot/.env.example` — a fully annotated template of **every tunable parameter** (also usable as the deployment `.env` template; the live `.env` in the engine folder remains the engine's own source of truth).

- **Engine fixes found in the audit**:
  - `config.py`: the preset's `TRAILING_ATR_MULTIPLIER: 2.0` was ignored at load time (hard-defaulted to `0.0`), which forced %-callback validation on preset values (`TRAILING_STOP_CALLBACK` == `TRAILING_STOP_ACTIVATE` = 1%) and **crashed every boot** with `TRAILING_STOP_CALLBACK must be less than TRAILING_STOP_ACTIVATE`. The loader now reads the value from the preset like every other preset parameter.
  - `trade_logic.py`: removed a duplicated RSI-bucket computation in `process_symbol` (the latch now reads and sets the bucket in one place).
  - Boot, single-instance locking, graceful SIGTERM shutdown, spot + futures reconciliation, orphan adoption, PnL verification, and the entry/exit/scale-out/breakeven/trailing paths all re-verified by the four test batteries.

- **Dashboard ↔ engine config drift eliminated (integration safety)**:
  - The monitor's tuner defaults previously disagreed with the deployed `.env` (e.g. `COOLDOWN_LOSS` 86400 vs 3600, `MAX_DAILY_DRAWDOWN` 3% vs 5%, `SIGNAL_INTERVAL` 6s vs 10s, empty symbol list vs `NEARUSDT`). Pushing the tuner to the engine would have silently rewritten proven risk parameters. Defaults now mirror the engine's `.env` exactly, and the first `/api/status` snapshot overwrites them with the engine's live values anyway.

- **Web monitor UX/UI rebuild (professional ops console)**:
  - New design-token layer (`src/index.css`): shared card primitive, tabular-numeral typography for all prices, thin scrollbars, consistent focus rings, subtle brand gradients, and `prefers-reduced-motion` support.
  - Rebuilt header: connection light (Synced/Offline), unmissable live-vs-paper mode chip (LIVE always renders in the danger palette), futures leverage/margin chip, KPI strip (equity / floating / positions), and a pause/resume control that is always visible on small screens.
  - Status cards (StreamLights, FuturesPanel, FuturesSoakCard, RoadmapCard) extracted to `StatusCards.tsx` for maintainability.
  - Trading tables: human-readable hold times with %-of-time-stop progress, dated exit timestamps with entry-hold context, honest empty states explaining exactly what the engine is waiting for, and engine-sourced trailing mode (ATR× vs %) instead of a hardcoded assumption.

- **Verified live state**: PM2 runs the engine (spot paper, NEARUSDT, WS streams up, $1,000 simulated equity) and the web monitor (port 3000) against the fresh build; `/api/status` reports `spot-paper / RUNNING`; test batteries: smoke 13/13, sync 27/27, scenarios S1–S5 ALL_OK, futures reconcile ALL_OK.

---

### 📝 Recent Changelog & Live Trading Hardening

- **Full Spot & Futures Dual-Engine Live Readiness**:
  - **Ed25519 Cryptographic Execution**: Full support for Ed25519 asymmetric key signing across both Binance Spot (`/api/v3`) and USDⓈ-M Futures (`/fapi/v1`, `/fapi/v3`) REST and WebSocket APIs.
  - **Futures User-Data Stream**: Dedicated user stream socket (`wss://fstream.binance.com/ws/<listenKey>`) with automated 30-minute keep-alives, auto-rotation upon expiration, and immediate REST balance seeding on connection.
  - **Leverage-Aware Margin Pre-Validation**: Order preparation calculates required margin as `(quantity * price) / leverage` against available margin, eliminating false rejections that treated leveraged orders as 100% spot notional.
  - **One-Way Position Protection**: Futures SELL exits automatically inject `reduceOnly=true`, guaranteeing an exit can never inadvertently flip the account into a short position.
  - **Multi-Assets Margin Mode Auto-Detection**: Boot-time detection overrides conflicting margin configuration to prevent Binance `-4168` errors.
  - **8-Decimal Order Quantization**: Standardized string formatting (`{qty:.8f}` without trailing zeros) across REST and WebSocket clients, preventing truncation on sub-penny tokens or high-precision pairs.

- **Failure Scenario Handling & Engine Resilience**:
  - **HTTP 429 & 418 Rate-Limit Protection**: Both Spot and Futures REST clients now inspect HTTP 429 response headers (`Retry-After`) with automated sleep backoffs, preventing transient rate spikes from escalating into HTTP 418 IP bans.
  - **Orphan Position Adoption with Real Cost Basis**: Disconnects or out-of-band fills are detected during startup/runtime position risk polling, adopted with exact volume-weighted cost basis (VWAP of recorded fills), and immediately armed with SL/TP brackets.
  - **Pre-Entry Wick Exclusion**: Dip-buying trigger candle lows are strictly excluded from stop-loss trailing evaluation; only candles opened after order execution can trigger stop checks, eliminating instant stop-outs.
  - **Loss Re-Entry Gate (`LOSS_REENTRY_COOLDOWN`)**: Configurable symbol cooldown (default 900s) prevents immediate churn and revenge trading on high-volatility pairs after a stop-out.
  - **Live PnL Auto-Verification**: Closed futures positions cross-check recorded PnL against Binance `/fapi/v1/userTrades` (`Σ realizedPnl − Σ commission`), self-healing any drift beyond $0.02.
  - **Non-Blocking Graceful Shutdown**: Signal handlers set termination flags rather than cancelling active loops in-flight; tasks wind down cleanly with bounded timeouts, guaranteeing release of `/tmp/ultimate_bot.*.lock`.

- **Web Monitor UX/UI Rebuild & Engine Synchronization**:
  - **Dynamic Market & Mode Badging**: Header displays real-time status (`LIVE FUTURES`, `LIVE SPOT`, `FUTURES PAPER`, `SPOT PAPER`), current leverage, and sub-second engine synchronization latency.
  - **Futures Risk & Position Telemetry**: Integrated liquidation price gauges, margin type display (`ISOLATED` / `CROSSED`), and live unrealized PnL monitoring.
  - **Zero-Simulation Telemetry**: All metrics (equity, open positions, closed orders, win rate, profit factor, streaks) sync directly from the engine's SQLite database (`trading.db`) over WebSocket and REST.
  - **Remote Operations Suite**: Full browser-based command channel for pausing entries, resuming trading, triggering emergency liquidations, or executing per-symbol market exits.

---

## ⚡ Quick Start on Debian 13 VPS

```bash
# 1. System packages
sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip git curl openssl
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs && sudo npm install -g pm2

# 2. Virtual environment setup (PEP 668 compliant)
cd ultimate-bot
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

# 3. Generate Ed25519 Keys for Binance API
mkdir -p keys && chmod 700 keys
openssl genpkey -algorithm Ed25519 -out keys/private_key.pem && chmod 600 keys/private_key.pem
openssl pkey -in keys/private_key.pem -pubout -out keys/public_key.pem
cat keys/public_key.pem

# 4. Configure .env (starts in safe PAPER_TRADE=true mode)
cp .env.example .env
nano .env

# 5. Build the React dashboard (run from the REPO ROOT, not ultimate-bot/)
cd ..
npm install
npm run build        # outputs to ./dist at the repo root

# 6. Launch engine + web monitor under PM2 supervision (run from ultimate-bot/)
cd ultimate-bot
pm2 start ecosystem.config.cjs
pm2 save

# 7. Monitor in real time
./venv/bin/python3 status.py --watch        # terminal dashboard
./venv/bin/python3 status.py --web 3000     # web monitor / API (http://YOUR_VPS_IP:3000)
```

> **Which folder?** The repo has two layers: the **repo root** is the React dashboard project (npm/vite) — `npm run build`/`npm run dev` run there and emit `dist/`. The **`ultimate-bot/` folder** is the Python trading engine — venv, `.env`, `main.py`, `status.py` and PM2 all live and run there. `status.py --web` automatically picks up the compiled UI from `../dist`, so you never need `npx serve` or a second server.

---

## 🌐 Interactive Web Operations Center

Run the React dashboard (`npm run dev`, or serve the compiled `dist/` next to `status.py`), then:

- **Live VPS Bot Sync**: point the dashboard at your VPS (`http://IP:3000`) to stream the real engine state from SQLite — active positions, orders, equity, daily PnL, win/loss streaks, scanned candidates, and the engine's own log file. No fake local balances are shown for the live server.
- **Engine-Exact Performance Stats**: in VPS mode the Performance card uses the engine's own SQLite aggregates — win rate, wins/losses/breakevens, profit factor, average win/loss, total realized PnL and streaks — instead of recomputing from a small window of recent orders. Only SELL exit orders with realized PnL are ever counted as closed trades, so the win/loss numbers always match the engine's win rate.
- **Comprehensive `status.py` Fallback Dashboard**: when no compiled `dist/` is present, `status.py --web` serves an upgraded standalone dashboard with a full performance section (win rate with W/L/B breakdown, profit factor, total realized PnL, average win/loss, and streak monitor) in addition to balance, scanner, positions and orders.
- **Full `npx serve -s dist` Parity in One Python File**: with a compiled `dist/` present, `status.py --web` serves the React dashboard with SPA fallback, clean-URL redirects, ETag/304 revalidation, immutable caching for hashed assets, gzip compression, HTTP Range support and HTTP/1.1 keep-alive on a multi-threaded server — no Node.js or reverse proxy needed on the VPS.
- **Tune Strategy Parameters**: live sliders for the RSI dip trigger (period/oversold), the fixed % SL/TP bracket, capital allocation, scan interval, max positions, and max entries per UTC day.
- **Engine Signal State (not synthetic)**: the Signal tab renders the engine's real per-symbol decision — daily regime, sampled RSI, trigger flag and the engine's own reason string — streamed over `/api/status` + `/ws`.
- **Dynamic Momentum Screener**: ranked candidates via the 4-factor Z-score (Volume, 24h Change, Volatility, ADX) with a correlation penalty, exactly as the engine scanned them.
- **Symbol Deep-Dive**: inspect the engine signal gate breakdown for any tracked pair, plus a bracket/sizing breakdown with `MIN_NOTIONAL` validation.
- **Remote Engine Control**: **Pause/Resume** new entries (open positions stay managed), **Emergency Close All**, and per-symbol **Market Close** straight from the browser via the engine's control-file channel.
- **1-Click VPS .env Sync**: push your tuned configuration to the VPS `.env` (whitelisted keys only — never credentials) and `pm2 reload ultimate-bot` with zero downtime.
- **Project Code & ZIP**: browse every audited engine file or download a byte-identical `ultimate-bot.zip` for deployment.

The dashboard is a **real-engine monitor**: every number it shows (equity, positions, orders, stats, signal state, screener pool) is read from the running engine's SQLite/status API. Nothing is simulated in the browser, so the monitor and the engine can never disagree.

---

## 🔑 Key Features of the Engine

- 100% market orders with slippage guards, filter-exact `LOT_SIZE`/`MIN_NOTIONAL` quantization, and orphan-order cleanup on exit.
- Ed25519-signed REST **and** WebSocket API order routing (HMAC secret supported as a fallback).
- Public WebSocket price stream with automatic REST fallback and gap-breach stop protection on bar lows.
- Net PnL always deducts 0.1% taker fees per leg; fee-aware breakeven lock at entry +0.25%.
- **Professional risk model**: fixed-fractional 1% risk-per-trade sizing (entry→stop distance), regime-alignment gate (never buys a downtrend), minimum 1.5 R:R entry filter, CVD noise floor, and +1R partial scale-out with a breakeven-locked runner.
- Daily drawdown circuit breaker, per-symbol win/loss streak cooldowns, exchange position reconciliation, and single-instance locking.
- WAL-mode SQLite with a batched async write queue and read-only monitor access.
- PM2 supervision of both the engine and the web monitor (`ecosystem.config.cjs`).

For technical indicator definitions, full `.env` reference, presets, and the live-readiness checklist, see **[ultimate-bot/README.md](./ultimate-bot/README.md)**.
