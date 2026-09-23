# 🚀 Ultimate Binance Market-Only Trading Bot — Operations Center

A production **Binance trading engine** paired with a real-engine **web operations center**.
One backtest-proven strategy (`intraday_rsi`), market-only execution, Ed25519-signed order
routing across **Spot** (`/api/v3`) and **USDⓈ-M Futures** (`/fapi/v1`, `/fapi/v3`), a
fixed-fractional risk model with a daily-drawdown circuit breaker, and a React dashboard that
reads every number straight from the running engine — nothing is simulated in the browser.

| | |
|---|---|
| **Repository** | `github.com/ruhyatnar/ultimate_bot` |
| **Runtime** | Python 3.13 (Debian 13, PEP 668 / venv) + Node 20 (dashboard build only) |
| **Supervision** | PM2 — `ultimate-bot` (engine) + `bot-web-monitor` (dashboard/API on `:3000`) |
| **Trading mode** | `PAPER_TRADE=true` (default) → `false` = live money. See [GO_LIVE.md](./GO_LIVE.md) |
| **Strategy** | `intraday_rsi` — only one exists; `STRATEGY_MODE=rsi_dip` is validated at boot |
| **Size** | 81 tracked files · 42 Python (33 non-empty, ~13.5k LOC) · 25 TS/TSX (15 components, ~6.7k LOC) |
| **Deep engine docs** | **[ultimate-bot/README.md](./ultimate-bot/README.md)** — indicator math, full `.env` guide, backtest methodology |

> ⚠️ **This is live-trading software.** Every default in the repo is the *safe* one
> (`PAPER_TRADE=true`, `USE_TESTNET=false`, `AUTO_LIQUIDATE_ORPHANS=false`). Read
> [GO_LIVE.md](./GO_LIVE.md) top to bottom before flipping `PAPER_TRADE`.

---

## Contents

- [1. What this is](#1-what-this-is)
- [2. Repository layout](#2-repository-layout)
- [3. Quick start (Debian 13 VPS)](#3-quick-start-debian-13-vps)
- [4. The strategy: `intraday_rsi`](#4-the-strategy-intraday_rsi)
- [5. Risk & safety model](#5-risk--safety-model)
- [6. Engine reference (module by module)](#6-engine-reference-module-by-module)
- [7. Web operations center (React)](#7-web-operations-center-react)
- [8. Configuration reference (`.env`)](#8-configuration-reference-env)
- [9. HTTP API, WebSocket & control channel](#9-http-api-websocket--control-channel)
- [10. Operations (PM2, logs, soak)](#10-operations-pm2-logs-soak)
- [11. Verification & test matrix](#11-verification--test-matrix)
- [12. Security & secrets](#12-security--secrets)
- [13. Go-live checklist (summary)](#13-go-live-checklist-summary)
- [14. Troubleshooting](#14-troubleshooting)
- [15. Repository audit — current findings](#15-repository-audit--current-findings)
- [16. Changelog](#16-changelog)

---

## 1. What this is

Two layers, one source of truth.

```
┌──────────────────────────────── REPO ROOT (npm / vite) ──────────────────────────────┐
│  React 19 + Vite 6 + Tailwind 4 dashboard                                            │
│  src/App.tsx ──► 5 tabs ──► 15 components ──► reads ONLY the engine's own state      │
│  npm run build ──► dist/  (served by status.py --web on :3000)                       │
└──────────────────────────────────────────────────────────────────────────────────────┘
                                        ▲  /api/status  +  /ws (WebSocket push)
                                        │  /api/soak (POST)   control file (pause/close)
┌───────────────────────────────────────┴──────────────────────────────────────────────┐
│  ultimate-bot/  —  Python 3.13 async engine                                          │
│                                                                                      │
│  main.py ─► TradeLogic.run() ─► SignalGenerator.decide()  (regime + RSI dip)         │
│                   │                  │                                               │
│                   │                  └─► trade_policy.py   (bracket, exits, trailing)│
│                   ├─► RiskManager       (sizing, breaker, streaks, cooldowns)        │
│                   ├─► OrderManager      (quantize → market order → fill)             │
│                   └─► DatabaseManager   (WAL SQLite + batched async writer)          │
│                                                                                      │
│  exchange/ ── spot REST+WS-API   futures REST+WS-API   public market streams         │
│  status.py ── CLI dashboard, standalone fallback HTML, HTTP/WS monitor, tuning API   │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

**Engine → dashboard data flow.** The engine publishes its state into SQLite
(`risk_state`, `active_trades`, `orders`) and `status.py` composes `/api/status` from that
DB plus the `.env` and the engine's log file. The dashboard consumes it over a WebSocket
with an HTTP polling fallback. There is **no second copy of the truth** — if the engine did
not write it, the UI cannot show it.

**Two serving modes.** With a compiled `dist/` present, `status.py --web` serves the React
app with SPA fallback, gzip, ETag/304, Range and HTTP/1.1 keep-alive. Without one, it serves
a built-in standalone HTML dashboard (balance, scanner, positions, orders, performance
stats) so the monitor always works on a bare VPS.

---

## 2. Repository layout

```
ultimate_bot/
├── README.md                     # this file — ops-center overview, module reference, changelog
├── GO_LIVE.md                    # paper → live checklist, 9 gated steps with verification commands
├── index.html                    # Vite entry (dark theme, meta/OG tags)
├── package.json                  # npm scripts (dev/build/lint/smoke/test:ui/probe/…)
├── package-lock.json
├── tsconfig.json                 # ES2022, bundler resolution, "@/*" path alias, noEmit
├── vite.config.ts                # react + tailwind plugins, vendor chunking, dev proxy → :5001
├── .gitignore                    # secrets, runtime state and build artifacts
│
├── scripts/
│   ├── ui_smoke.mjs              # bundles + runs the dashboard smoke tests (no new deps)
│   └── install-hooks.sh          # links .githooks/* into .git/hooks (idempotent)
│
├── .githooks/
│   └── pre-commit                # blocks a commit when .env and .env.example drift
│
├── src/                          # ── React dashboard ───────────────────────────────
│   ├── main.tsx                  # React 19 createRoot + StrictMode
│   ├── App.tsx        (1174)     # state hub: WS + polling, config sanitizer, tab routing
│   ├── types.ts        (416)     # the engine↔UI contract: 22 exported types/interfaces
│   ├── index.css       (101)     # design tokens, tabular numerals, card primitive, a11y
│   ├── vite-env.d.ts
│   ├── components/               # 15 components, one responsibility each
│   │   ├── Header.tsx            # brand, sync light, mode chip, KPI strip, 5-tab nav
│   │   ├── VpsConnectionBar.tsx  # endpoint input, transport state, pause/refresh, clock
│   │   ├── SyncClock.tsx         # engine ↔ browser clock + payload age
│   │   ├── LiveDashboard.tsx     # Trading Desk: 6 sections (the main surface)
│   │   ├── EngineHealthCard.tsx  # transport/loop/clock health panel
│   │   ├── StatusCards.tsx       # StreamLights, FuturesPanel, RoadmapCard, FuturesSoakCard
│   │   ├── DynamicScreener.tsx   # ranked screener candidates with factor breakdown
│   │   ├── TuningControlBar.tsx  # the live tuner (sliders/inputs + preset + push)
│   │   ├── SymbolDetailModal.tsx # per-symbol gate + bracket/sizing breakdown
│   │   ├── SignalInspector.tsx   # Signal State tab: the engine's real decisions
│   │   ├── DebugConsole.tsx      # Engine Log tab: filterable, copyable log stream
│   │   ├── ConfigTab.tsx         # Strategy & .env tab: every tunable + generated .env
│   │   ├── DeployGuide.tsx       # VPS Guide tab: deployment runbook
│   │   ├── VpsSyncModal.tsx      # 1-click .env push confirmation + diff
│   │   └── Section.tsx           # Section shell + Chip (shared layout primitives)
│   └── utils/
│       ├── envGenerator.ts (172) # generateEnvString() + effectiveAllocation() + NOTIONAL floor
│       └── vpsSocket.ts    (178) # WebSocket client: backoff, keepalive, polling fallback
│
├── tests/smoke/
│   └── dashboard.test.tsx (438)  # 15 SSR tests: desk sections, tabs, edge states, loop/pause
│
└── ultimate-bot/                 # ── Python engine ─────────────────────────────────
    ├── main.py            (274)  # async entrypoint: boot, wiring, loops, graceful shutdown
    ├── config.py          (398)  # preset + .env loader + ~60 boot-time validations
    ├── status.py         (3629)  # CLI dashboard, HTML fallback, HTTP + WS monitor, tuning API
    ├── backtest.py        (634)  # replay the strategy on real klines (fee/minNotional honest)
    ├── capital_roadmap.py (298)  # growth stages vs live futures floors (read-only, engine's own pairs)
    ├── ecosystem.config.cjs       # PM2: ultimate-bot + bot-web-monitor (with crash-loop guard)
    ├── futures_soak.sh     (68)  # opt-in 24h futures paper soak launcher (PM2)
    ├── soak_watchdog.py   (322)  # supervises a soak: deadline summary, death/stall alerts
    ├── requirements.txt           # aiohttp, websockets, aiosqlite, cryptography, pandas, …
    ├── .env / .env.example        # live config / 90-key template that mirrors it
    ├── logs/ data/ keys/          # runtime + secrets (all gitignored, .gitkeep only)
    │
    ├── probes & tests
    │   ├── check_env_drift.py   (302)  # fails when .env and .env.example diverge (--update re-mirrors)
    │   ├── smoke_test.py        (412)  # 13 checks: boot, lock, API, SIGTERM cleanup
    │   ├── sync_test.py         (799)  # 46 checks: engine ↔ monitor end-to-end sync (position mark, heartbeat, roadmap pairs)
    │   ├── test_scenarios.py    (315)  # offline failure battery S1–S9
    │   ├── test_futures_reconcile.py (120)  # futures reconcile reads positionAmt, not wallet
    │   ├── live_signed_probe.py (242)  # go-live credential/signature/clock check (no orders)
    │   ├── order_dry_run_probe.py (537)# builds real orders through all 4 clients, sends none
    │   ├── watch_live_position.py (138)# watchdog for an on-exchange position
    │   └── soak_report.py       (210)  # summarise a paper soak run
    │
    └── src/
        ├── core/
        │   ├── backoff.py        (34)  # async_retry + NonRetryableError
        │   ├── error_handler.py  (16)  # ErrorHandler: log + Discord-report exceptions
        │   └── health_check.py   (75)  # HealthCheck: periodic component probes + reconnect
        ├── exchange/
        │   ├── rest_client.py          (270)  # Spot REST: signing, retry, filters, 429/418
        │   ├── ws_api_client.py        (460)  # Spot WS-API order routing + user stream + cache
        │   ├── ws_stream_client.py     (295)  # Spot public streams (!miniTicker@arr, klines)
        │   ├── balance_cache.py         (45)  # WS-first balances, REST fallback by max-age
        │   ├── futures_rest_client.py  (449)  # USDⓈ-M REST: account/positions/orders/margin
        │   ├── futures_ws_api_client.py(393)  # USDⓈ-M WS-API orders + fstream user data
        │   └── futures_ws_stream_client.py (405) # USDⓈ-M public streams + REST ticker fallback
        ├── strategies/
        │   ├── signal_generator.py (271)  # regime gate + RSI dip → the decision
        │   ├── trade_policy.py     (217)  # bracket/exit policy SHARED with the backtest
        │   └── trend_detector.py   (241)  # 4-factor Z-score screener + correlation penalty
        ├── risk/
        │   └── risk_manager.py     (555)  # equity, sizing, breaker, streaks, cooldowns, publish
        ├── trade/
        │   ├── order_manager.py    (265)  # sanitize → place → confirm fills
        │   └── trade_logic.py     (1749)  # the runtime: entries, exits, reconciliation, reports
        ├── database/
        │   └── db_manager.py       (192)  # WAL SQLite, async write queue, read helpers
        ├── reporting/
        │   └── discord_webhook.py   (86)  # batched, rate-limited Discord alerts
        └── utils/
            └── helpers.py           (20)  # rotating log handler setup
```

Directories marked with a line count are byte-verified against `git ls-files`; the 9 empty
`__init__.py` package markers are omitted from the tree above for readability.

---

## 3. Quick start (Debian 13 VPS)

```bash
# 1. System packages
sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip git curl openssl
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs && sudo npm install -g pm2

# 2. Python environment (PEP 668 compliant — never pip install outside the venv)
cd ultimate-bot
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

# 3. Ed25519 signing keys (preferred over HMAC)
mkdir -p keys && chmod 700 keys
openssl genpkey -algorithm Ed25519 -out keys/private_key.pem && chmod 600 keys/private_key.pem
openssl pkey -in keys/private_key.pem -pubout -out keys/public_key.pem
cat keys/public_key.pem          # register this on Binance → API Management

# 4. Configure (starts in safe PAPER_TRADE=true mode)
cp .env.example .env
nano .env

# 5. Build the dashboard — from the REPO ROOT, not ultimate-bot/
cd .. && npm install && npm run build      # emits ./dist

# 6. Launch engine + monitor under PM2 — from ultimate-bot/
cd ultimate-bot
pm2 start ecosystem.config.cjs
pm2 save

# 7. Observe
./venv/bin/python3 status.py --watch        # terminal dashboard
./venv/bin/python3 status.py --web 3000     # web monitor + API → http://VPS_IP:3000
```

> **Which folder?** The repo root is the npm/Vite project (`npm run build` → `dist/`).
> `ultimate-bot/` is the Python engine — the venv, `.env`, `main.py`, `status.py` and PM2
> all live there. `status.py --web` picks up `../dist` automatically; you never need a
> second web server.

**Port collision to know about:** `npm run dev` and `status.py --web` both default to
`:3000`. Run one at a time, or use `status.py --web 3001`. In dev mode Vite proxies `/api`
and `/ws` to `127.0.0.1:5001`, so to drive the dev server against the live monitor use
`status.py --web 5001`.

---

## 4. The strategy: `intraday_rsi`

One preset exists. `config.py` refuses to boot with any other `STRATEGY_MODE`:

```python
if config["STRATEGY_MODE"] != "rsi_dip":
    raise ValueError("STRATEGY_MODE must be 'rsi_dip' (the only supported strategy).")
```

### 4.1 Regime gate (daily, completed candles only)

Bullish regime requires the daily close **above** a rising EMA:

- `REGIME_EMA=50` — close > EMA50 *and* the EMA slope over `REGIME_SLOPE_DAYS=3` is positive.
- Completed candles only, so the gate cannot repaint intraday.

Never buys a downtrend: a flat or falling regime blocks entries entirely.

### 4.2 Entry trigger (RSI dip)

- RSI sampled on `RSI_TIMEFRAME=1h` buckets (Wilder smoothing, `RSI_PERIOD=7`).
- Trigger requires **oversold and turning up**: `RSI < RSI_OVERSOLD (40)` *and* RSI rising
  versus the previous read. `ENTRY_RSI_MIN=35` sets the floor below which the dip is
  considered a falling knife rather than a dip.
- Optional gates exist and are **off** in the proven preset (`ENTRY_MAX_EXT_ATR`,
  `ENTRY_VOL_MULT`, `ENTRY_REQUIRE_RSI_RISE2`).

### 4.3 Exits

| Mechanism | Setting | Proven value |
|---|---|---|
| Fixed % bracket | `SL_PERCENT` / `TP_PERCENT` | −1.2% / +3.0% (both MARKET orders) |
| Minimum reward | `MIN_RISK_REWARD` / `MIN_TP_PERCENT` | 1.5 R / 3% floor |
| Time stop | `MAX_HOLD_TIME` | 84 600 s (~1 day) |
| Trailing | `TRAILING_ATR_MULTIPLIER` | 2.0 × ATR (ATR-trail mode, activate at +1%) |
| Breakeven lock | `BREAKEVEN_ENABLED` | **off** — the edge was proven without it |
| Scale-out | `SCALE_OUT_ENABLED` | **off** — disabled by the preset for this strategy |
| Day-end flat | `CLOSE_AT_UTC_DAY_END` | on in the preset (backtest parity) |

Exit policy lives in **`src/strategies/trade_policy.py`** and is imported by *both* the live
path (`trade_logic.py`) and the backtest — so a backtest cannot disagree with live behaviour.

### 4.4 Sizing & frequency

- **Fixed-fractional:** risk `RISK_PER_TRADE` (1%) of equity between entry and stop, so the
  *stop distance* sets the position size — not the balance.
- `BALANCE_USAGE_PERCENT` / `MAX_SYMBOL_ALLOCATION_PERCENT` are secondary ceilings and must
  stay at `1.0` on small accounts.
- Proven frequency: **2 entries per UTC day** (`MAX_TRADES_PER_DAY=2` in the preset;
  `0` = unlimited).

### 4.5 Preset provenance

```
intraday_rsi — 2026-09-14 battery, NEARUSDT, 30 pages ≈ 104 days, $22 equity,
honest taker fees + exchange minNotional, same-UTC-day close:
  +10.77%  ·  42 trades  ·  WR 42.9%  ·  PF 1.43  ·  max DD 6.8%
Robustness: stable across a 3×3 SL/TP neighbourhood and 3 time sub-windows.
Rejected: 15m RSI buckets and 3+ entries/day (failed the same battery).
```

The backtest and the engine share the same decision code, so re-run it after any strategy
change (see [§11](#11-verification--test-matrix)). Since 2026-09-22 it also resolves configuration the
way the engine does (`.env` overrides the preset), so a bare run measures the config that is actually
trading rather than the preset.

> ⚠️ **The deployed `.env` overrides 8 of these preset keys** — `RSI_TIMEFRAME`, `RSI_PERIOD`,
> `REGIME_EMA`, `REGIME_SLOPE_DAYS`, `MTF_TIMEFRAME`, `MAX_TRADES_PER_DAY`,
> `BREAKEVEN_ENABLED` and `CLOSE_AT_UTC_DAY_END` — so the *running* strategy is not
> byte-for-byte the validated preset. Now that the backtest resolves config the same way, the two can
> be compared on one window: the preset returns **+0.41% / PF 1.02**, the deployed config
> **−7.80% / PF 0.73**. See [§15](#15-repository-audit--current-findings) item 1.

---

## 5. Risk & safety model

| Mechanism | Where | Behaviour |
|---|---|---|
| Fixed-fractional sizing | `risk_manager.calculate_position_size` | 1% equity risk per trade, entry→stop distance |
| Daily drawdown breaker | `risk_manager.check_risk` | Trips at `MAX_DAILY_DRAWDOWN` (5%), blocks entries until the UTC reset |
| Loss-streak cooldown | `risk_manager.update_trade_result` | `MAX_LOSS_STREAK=3` → whole-account pause `COOLDOWN_LOSS=3600` s |
| Win-streak cooldown | same | `MAX_WIN_STREAK=5` → `COOLDOWN_WIN=1800` s |
| Loss re-entry brake | `trade_logic` | `LOSS_REENTRY_COOLDOWN=900` s per symbol after **any** stop-out |
| Single-instance locks | `main.py` | `/tmp/ultimate_bot.*.lock` — one live engine per mode, paper may coexist |
| Restart-proof daily cap | `trade_logic._save/_load_entry_cap` | Entries-today survives a restart (persisted in `risk_state`) |
| Orphan adoption | `trade_logic._adopt_orphan_position` | Out-of-band fills re-adopted at VWAP cost basis and armed with SL/TP |
| Naked-position healing | `trade_logic.reconcile_positions` | Prevents unmanaged exposure after a disconnect |
| Exchange PnL verification | `trade_logic._exchange_pnl_for_exit` | Live futures exits cross-checked against `userTrades`; drift > 5% or $0.02 self-heals |
| Slippage guard | `order_manager.place_market_order` | Rejects a fill beyond `MAX_SLIPPAGE_PERCENT` (0.5%) |
| Rate-limit protection | both REST clients | HTTP 429 `Retry-After` backoff; 418 triggers a protective pause |
| Notional feasibility | `risk_manager._check_notional_feasibility` | Boot-time warning when equity × caps cannot clear minNotional |
| Funding gate | `trade_logic` (futures) | Skips longs paying more than `FUNDING_RATE_MAX` per interval |
| Crash-loop breaker | `ecosystem.config.cjs` | `max_restarts: 5` / `min_uptime: 30s`, so a bad `.env` shows as `errored` |
| Graceful shutdown | `main.py` | Signal handlers set flags; tasks wind down; lock always released |

### 5.1 Spot vs. futures

The strategy is market-agnostic: `SignalGenerator.decide()`, the `trade_policy` helpers
(`effective_bracket`, `effective_stop`, `ratchet_stops`, `scale_out_plan`) plus the shared exit decision
`evaluate_exit`, and `trend_detector` contain **no** `MARKET` branch, and there is one `.env` for both
venues. Only three shared files branch on it —
`trade_logic` (11 sites), `order_manager` (8) and `risk_manager` (1 — its equity source).

| | Spot | Futures |
|---|---|---|
| Entry margin check | free quote ≥ notional | free quote ≥ notional / leverage |
| Extra entry gate | — | funding rate (`FUNDING_RATE_MAX`) |
| Exit quantity source | free base-asset balance | `positionAmt` |
| Exit flag | `reduce_only` accepted and ignored | `reduceOnly=true` on every SELL |
| Equity source | per-asset wallet sums | `totalMarginBalance` (includes unrealised PnL) |
| Fee model | 0.1% per leg | 0.05% per leg |
| Exit PnL check | — | cross-checked against `userTrades`, self-healing |
| Reconcile source | wallet balance | `positionRisk` |
| Orphan cleanup | `/api/v3/openOrders` | `/fapi/v1/allOpenOrders` |
| Market data | `stream.binance.com` | `fstream` + REST ticker fallback |

Same signal, same bracket, same exit policy on both venues — futures simply takes **fewer** entries,
because it applies two gates spot does not have (funding, and margin rather than full notional). One gap
remains in [ultimate-bot/README.md](./ultimate-bot/README.md#-spot-vs-futures-shared-logic--divergences):
futures paper mode returns the simulated fill before `is_futures` is computed, so it never exercises the
futures order path. The funding gate, by contrast, is now implemented in `backtest.py` as well and is
reachable from the CLI via `--market futures`.

---

## 6. Engine reference (module by module)

### 6.1 Entry points & operator tools

**`main.py`** — the async entrypoint. `_warm_up`, client construction (spot or futures based
on `MARKET`), lock acquisition, then the concurrent loops: signal, health-check, symbol
refresh, daily report, and `TradeLogic.run()`. `request_shutdown(sig)` only *flags* the
shutdown event so in-flight orders are never cancelled mid-request.

**`config.py`** — `load_config()` is the single config authority: reads the `PRESET`, merges
`.env` overrides (via `python-dotenv`, with a stdlib fallback parser), then applies ~60
validations (`_env_int`/`_env_float` warn-and-default rather than raise, so one bad value
can't start a PM2 crash loop). Includes a **drift guard** warning when an explicit
`RSI_TIMEFRAME_MS` disagrees with `RSI_TIMEFRAME`. Only `intraday_rsi` is accepted.

**`status.py`** *(3394 lines — the largest file)* — everything the operator sees:
- `load_env` / `refresh_env_config` — re-reads `.env` on mtime change so a tuner push
  appears without a restart. It merges overrides from a **frozen import-time env snapshot**,
  so dotenv-injected keys cannot freeze the refresh.
- `build_status_payload` — composes the `/api/status` JSON from the DB + `.env` + log.
- `_valid_tuning_value` / `apply_env_updates` — whitelisted, type-checked writes to `.env`.
- WebSocket server (`_ws_*` helpers) — RFC 6455 handshake, frame codec, 1 s coalesced
  status push plus log tails.
- `start_web_server` — the HTTP handler: SPA fallback, gzip, ETag/304, Range, keep-alive.
- `render_dashboard` + `get_standalone_html` — the no-`dist/` fallback UI.
- `capital_roadmap.compute_roadmap` is imported **lazily** inside the payload build, and is
  handed the engine's watched pairs (`risk_state.monitored_symbols`) — the same value served as
  `monitored_symbols`, read once per payload, so the two cannot describe different universes.

**`backtest.py`** (634 lines) — replays the real strategy on real klines (`--pages` × 1000 bars) with
honest taker fees and the exchange's real *per-symbol* minNotional. `--market futures` (or
`BACKTEST_MARKET=futures`) switches to fapi klines, 0.05%/leg, real historical funding charged at each
8 h settlement, and the same `FUNDING_RATE_MAX` entry gate the engine applies — blocked entries are
reported as `funding_gate_skips`. Configuration resolves exactly like the engine (env-first), so a bare
run measures the deployed `.env`. Imports the shared `SignalGenerator.decide()` and `trade_policy`
helpers.

**`capital_roadmap.py`** — read-only: fetches live futures floors, prices and funding, then
computes growth stages and per-pair viability. Serves both the CLI (`--equity`/`--pairs`) and the
dashboard's Roadmap panel, and **both analyse the engine's live watched pairs** —
`risk_state.monitored_symbols`, written by `trade_logic.update_symbols` (screener picks +
`STATIC_SYMBOLS` + any symbol holding an open trade) — rather than a list committed to the module.
`DEFAULT_PAIRS` is only the fallback for when the engine has published none, `pairs_source` records
which was used, and the stage thresholds therefore track the watched set: the binding constraint is
the highest floor *among the pairs actually being traded*.

**`soak_watchdog.py` / `futures_soak.sh` / `soak_report.py`** — opt-in 24 h futures paper
soak: isolated DB and control file, its own PM2 app, a watchdog that posts a deadline summary
to Discord and stops the soak, plus death/stall alerts (`SOAK_STALL_S`) written to a JSONL
event feed the dashboard renders.

**Probes** — `live_signed_probe.py` (credentials, sign+verify, connectivity, clock offset,
signed account read — **no orders**) and `order_dry_run_probe.py` (drives all four order
clients with a stubbed transport, verifying path, MARKET type, step-quantized quantity,
fresh timestamp, `positionSide=BOTH`, exit `reduceOnly=true` — **nothing is transmitted**).

### 6.2 `exchange/` — transports

| Module | Role |
|---|---|
| `rest_client.py` | Spot REST. Ed25519 + HMAC signing, `async_retry`, weight limiting, 429/418 handling, `exchangeInfo` filters, bulk tickers, orders. |
| `ws_api_client.py` | Spot WS-API **order routing** on an authenticated session, user-data events, the balance cache (`seed_balances`, `cached_account`, `balance_age_s`), clock sync + re-sync loop, `wait_for_fill_event`. |
| `ws_stream_client.py` | Spot public market streams; a dedicated `!miniTicker@arr` socket feeds the all-market ticker cache used for live pricing. |
| `balance_cache.py` | `get_account()` — WS cache first, REST snapshot when the cache is older than `WS_BALANCE_MAX_AGE`. One balance path for sizing, pre-trade checks and the UI. |
| `futures_rest_client.py` | USDⓈ-M REST: `get_account` normalised to the spot `balances` shape, `positionRisk`, `set_leverage`, `set_margin_type`, `set_position_mode`, listenKey lifecycle, `get_user_trades`. |
| `futures_ws_api_client.py` | Futures WS-API orders, a **separate** `fstream` user-data socket via listenKey with keepalive/rotation, and the multi-assets-safe balance seed (only non-zero **wallet** rows are cached). |
| `futures_ws_stream_client.py` | Futures public streams + a REST bulk-ticker refresher that feeds the same cache when the fstream frames stall, with honest `transport` provenance (`ws`/`rest`). |

### 6.3 `strategies/`

| Module | Role |
|---|---|
| `signal_generator.py` | `generate_signal(symbol)` fetches daily + execution-TF candles and calls the pure `decide()` core: regime gate, RSI dip, optional quality gates. `_record()` stores each decision for the UI. |
| `trade_policy.py` | `effective_bracket`, `effective_stop`, `scale_out_plan/price`, `ratchet_stops`, and `evaluate_exit` — the **ONE exit decision** both the engine and the backtest call, plus the shared level maths. |
| `trend_detector.py` | The dynamic screener: 4-factor Z-scores (volume / 24 h change / volatility / ADX) with a correlation penalty, plus in-place price refresh between rescans. |

### 6.4 `risk/` and `trade/`

**`risk_manager.py`** — `load_state`/`save_state` (breaker + streaks persisted in SQLite),
`_fetch_equity` (via the shared balance cache), `check_risk`, `update_trade_result`,
`calculate_position_size`, and `_publish_risk_snapshot` which writes the `engine_risk` and
`ws_streams` keys the dashboard reads.

**`order_manager.py`** — `sanitize_order` (LOT_SIZE step + minNotional against live filters),
`place_market_order` (routes through WS-API when available, REST fallback otherwise),
`wait_for_fill`.

**`trade_logic.py`** *(1666 lines)* — the runtime: `run()` loop, `process_symbol`,
`enter_trade`, `manage_trade`, `_scale_out`, `close_trade`, `reconcile_positions`,
`sync_positions_from_exchange`, `_adopt_orphan_position`, `_exchange_pnl_for_exit`,
`calculate_unrealized_pnl`, the control-file reader (`_process_control_commands`), the WS
health/balance snapshots and the daily report loop.

### 6.5 `database/`, `reporting/`, `core/`, `utils/`

- **`db_manager.py`** — WAL SQLite with a batched async write queue; orders, active trades,
  risk state; read helpers for the monitor.
- **`discord_webhook.py`** — batched, chunk-aware, cooldown-limited alerts (also used for
  breaker trips and daily reports).
- **`core/backoff.py`** — `async_retry` + `NonRetryableError` (permanent failures are not
  retried — used for Binance codes like `-4046`/`-4059`).
- **`core/health_check.py`** — periodic probes with automatic reconnect per component.
- **`core/error_handler.py`** — log + optionally report unhandled exceptions.
- **`utils/helpers.py`** — `setup_logging` (bounded `RotatingFileHandler`, 10 MB × 5).

---

## 7. Web operations center (React)

### 7.1 Tabs

| Tab | Component | What it shows |
|---|---|---|
| **Trading Desk** | `LiveDashboard` | 6 sections: tuner, KPI row, engine health, monitored symbols, active positions (bracket ladder), recent completed trades |
| **Signal State** | `SignalInspector` | The engine's real per-symbol decision: regime, sampled RSI, trigger flag, reason string |
| **Engine Log** | `DebugConsole` | Filterable log stream (skipped/orders/risk/debug), copy + clear |
| **Strategy & .env** | `ConfigTab` | Every strategy/risk tunable, the generated `.env`, and a push-to-engine action |
| **VPS Guide** | `DeployGuide` | The deployment runbook, in-app |

### 7.2 Component reference

| Component | Props (essential) | Responsibility |
|---|---|---|
| `App` | — | **State hub.** `processStatus(...)` is the single status handler used by *both* the WebSocket push and the HTTP poll, so the two paths cannot drift. Owns config, trades, symbols, risk, futures, roadmap, soak, control state; sanitizes every tuner update (`sanitizeConfigUpdate`, exported for tests). |
| `Header` | `isRunning`, `onToggleRunning`, `activeTab`, `setActiveTab`, `config`, `activeTradesCount`, `unrealizedPnl`, `totalEquity`, `vpsConnected`, `isLossCooldown` | Brand, engine-sync light, mode chip (`Spot/Futures × LIVE/Paper`), futures leverage/margin chip, KPI strip, pause/resume, and the 5-tab nav. |
| `VpsConnectionBar` | `vpsStatus`, `vpsEndpoint`, `onUpdateVpsEndpoint`, `onRefreshVps`, `isPolling`, `controlPaused`, `onToggleVpsPause`, `wsTransport` | Endpoint control, transport indicator, manual refresh, pause/resume, hosts `SyncClock`. |
| `SyncClock` | clock props | Live engine↔browser clock and payload age — proves the link is real, not cached. |
| `LiveDashboard` | 40 props (equity, PnL, trades, symbols, candidates, config, callbacks, risk, futures, roadmap, soak) | The desk. Contains `BracketLadder`, `Kpi`, `ExitedCell` and the formatters (`formatPrice`, `formatHoldTime`, `holdCell`). |
| `EngineHealthCard` | engine/transport state | "Is everything actually working?" — transport lights, the engine's **loop heartbeat** (not the signal-snapshot age, which freezes legitimately while paused or fully invested), the applied-vs-requested pause state, and exchange clock sync. |
| `StatusCards` | 4 exports | `StreamLights`, `FuturesPanel`, `RoadmapCard`, `FuturesSoakCard` — realtime transports, futures account, capital roadmap, soak health. |
| `DynamicScreener` | `candidates`, `config`, `onInspectSymbol` | Ranked candidates with the factor breakdown that produced the ranking. |
| `TuningControlBar` | `config`, `onUpdateConfig`, `onApplyPreset`, `onCloseAllTrades`, `onOpenVpsSync`, `activeTradesCount`, `vpsConnected`, `controlPaused`, `onToggleVpsPause` | The live tuner + emergency controls. |
| `SymbolDetailModal` | `symbolData`, `config`, `equity`, `onClose` | Gate-by-gate explanation and a bracket/sizing breakdown with minNotional validation. |
| `SignalInspector` | `symbolsData`, `config` | Engine signal state (never recomputed in the browser). |
| `DebugConsole` | `logs`, `onClearLogs`, `signalInterval` | Log console with category filters and auto-scroll. |
| `ConfigTab` | `config`, `onUpdateConfig`, `onApplyPreset`, `vpsConnected`, `onPushToVps` | Every tunable + the generated `.env` + push. |
| `DeployGuide` | — | The VPS runbook. |
| `VpsSyncModal` | `config`, `onClose`, `vpsConnected`, `onApplyToVps`, `lastPushResult` | Confirm-and-push the tuned config (whitelisted keys only — never credentials). |
| `Section` / `Chip` | `Section`: title/icon/actions; `Chip`: label/value/tone | Shared layout primitives so every block looks identical. |

### 7.3 Utils & the type contract

- **`envGenerator.ts`** — `generateEnvString(config, apiKey)` is the single source of truth
  for the generated `.env` (the template and the UI cannot drift);
  `effectiveAllocation(config, equity)` mirrors the engine's notional maths;
  `MIN_NOTIONAL_USDT = 5`.
- **`vpsSocket.ts`** — `VpsSocket` owns one WebSocket with exponential backoff (capped at
  30 s), a 5 s keepalive ping, and automatic downgrade to HTTP polling. `WsTransport` is
  `'websocket' | 'polling' | 'connecting'`, surfaced in the UI.
- **`types.ts`** — 21 exported types mirroring the engine payload: `BotConfig` (56 keys),
  `SignalState`, `ActiveTrade`, `ClosedTrade`, `EngineRiskState`, `MarketSymbolData`,
  `CandidateSymbol`, `VpsBotStatus`, `VpsBalanceData`, `WsStreams`, `FuturesState`,
  `Roadmap`, `FuturesSoak`, `WatchdogEvent`, `LogMessage`, `PushResult`, …

### 7.4 Build & serving

`vite.config.ts` splits vendor code into `vendor-react` / `vendor-icons` chunks (so an
app-only change doesn't invalidate the React chunk cache), aliases `@` to the root, and
proxies `/api` + `/ws` to `127.0.0.1:5001` in dev. `npm run build` emits `dist/`, which
`status.py --web` serves directly.

---

## 8. Configuration reference (`.env`)

Loaded by `config.py`. `ultimate-bot/.env.example` is the annotated template; the live
`.env` in the engine folder is the source of truth. After editing: `pm2 reload ultimate-bot`.

| Group | Keys |
|---|---|
| **Boot** | `PRESET`, `PAPER_TRADE`, `MARKET`, `USE_TESTNET`, `STRATEGY_MODE` |
| **Credentials** | `BINANCE_API_KEY`, `BINANCE_API_SECRET` (alt), `BINANCE_PRIVATE_KEY_PATH` |
| **Storage** | `DB_PATH`, `CONTROL_FILE`, `LOG_FILE` |
| **Symbols & screener** | `STATIC_SYMBOLS`, `DYNAMIC_SYMBOLS`, `MAX_SYMBOLS`, `TOP_CANDIDATES`, `MIN_VOLUME_USDT`, `MIN_PRICE_CHANGE_PERCENT`, `MIN_VOLATILITY_PERCENT`, `EXCLUDE_SYMBOLS`, `SYMBOL_REFRESH_INTERVAL`, `PRICE_REFRESH_INTERVAL`, `Z_SCORE_WEIGHT_*`, `CORRELATION_THRESHOLD`, `CORRELATION_PENALTY`, `ADX_THRESHOLD`, `ADX_PERIOD`, `TREND_LOOKBACK`, `QUOTE_ASSET` |
| **Strategy** | `RSI_TIMEFRAME`, `RSI_TIMEFRAME_MS`, `RSI_PERIOD`, `RSI_OVERSOLD`, `ENTRY_RSI_MIN`, `ENTRY_REQUIRE_RSI_RISE2`, `ENTRY_MAX_EXT_ATR`, `ENTRY_EXT_EMA`, `ENTRY_VOL_MULT`, `ENTRY_VOL_LOOKBACK`, `REGIME_EMA`, `REGIME_SLOPE_DAYS`, `TIMEFRAME`, `MTF_TIMEFRAME`, `ATR_PERIOD` |
| **Exits** | `SL_PERCENT`, `TP_PERCENT`, `SL_ATR_MULTIPLIER`, `SL_ATR_MAX_PERCENT`, `TRAILING_ATR_MULTIPLIER`, `TRAILING_STOP_ACTIVATE`, `TRAILING_STOP_CALLBACK`, `BREAKEVEN_ENABLED`, `BREAKEVEN_TRIGGER`, `BREAKEVEN_OFFSET`, `SCALE_OUT_ENABLED`, `SCALE_OUT_R_MULTIPLE`, `SCALE_OUT_FRACTION`, `CLOSE_AT_UTC_DAY_END`, `MAX_HOLD_TIME`, `MIN_TP_PERCENT` |
| **Risk** | `RISK_PER_TRADE`, `MIN_RISK_REWARD`, `BALANCE_USAGE_PERCENT`, `MAX_SYMBOL_ALLOCATION_PERCENT`, `MAX_DAILY_DRAWDOWN`, `MAX_LOSS_STREAK`, `MAX_WIN_STREAK`, `COOLDOWN_LOSS`, `COOLDOWN_WIN`, `LOSS_REENTRY_COOLDOWN`, `MAX_TRADES_PER_DAY`, `MAX_SLIPPAGE_PERCENT`, `AUTO_LIQUIDATE_ORPHANS`, `ORPHAN_ADOPT_WINDOW_HOURS` |
| **Futures** | `FUTURES_ONE_WAY_MODE`, `FUTURES_MARGIN_TYPE`, `FUTURES_LEVERAGE`, `FUNDING_RATE_MAX`, `FUTURES_REST_WEIGHT_LIMIT` |
| **Cadence/infra** | `SIGNAL_INTERVAL`, `ENTRY_TIMEOUT`, `HEALTH_CHECK_INTERVAL`, `REST_WEIGHT_LIMIT`, `WS_BALANCE_MAX_AGE`, `TICKERS_REST_FALLBACK_S`, `SOAK_STALL_S` |
| **Notifications** | `DISCORD_WEBHOOK_URL`, `DISCORD_COOLDOWN` |
| **Logging** | `LOG_LEVEL` (keep `INFO`) |

**Boot-time validation examples** — the engine refuses to start on: a missing live API key,
an unknown `STRATEGY_MODE`, `SL_PERCENT >= TP_PERCENT`, a `TRAILING_STOP_CALLBACK` that is
not tighter than its activation (callback mode), `RISK_PER_TRADE > 0.1`, allocations outside
`(0, 1]`, or a `MARKET` other than `spot`/`futures`.

**No inline comments on value lines.** This dotenv build passes trailing `# …` straight into
the value; comment on the line above.

**`.env.example` mirrors the live deployment, not a safe sandbox** — reconciled 2026-09-22 so
the template and `.env` share all **90 keys with zero drift** (only the three credential keys
keep placeholders). This matters beyond documentation: `status.py` falls back to the template
as its config source when `.env` is missing, and seeds a new `.env` from it. The template
therefore opens in the production posture (`PAPER_TRADE=false`, `FUTURES_MARGIN_TYPE=CROSSED`,
`MAX_TRADES_PER_DAY=0`), and its header block lists exactly which keys to change back for a
fresh deployment.

**Keep the two files in sync — automatically.** `npm run check:env` fails when they diverge,
so a tuner push (which writes `.env` only) can never leave the template quietly lying about
what the bot runs. After an intentional config change, re-mirror it with
`npm run check:env -- --update`: that rewrites changed values and appends missing keys, but
**never** copies a credential — the exempt keys keep their placeholders, and any new key whose
name looks sensitive is skipped rather than written into a tracked file.

**A pre-commit hook enforces it on every commit.** Git only executes `.git/hooks/`, which is
never committed, so the hook itself is versioned at `.githooks/pre-commit` and enabled once per
clone:

```bash
npm run hooks:install      # symlinks .githooks/pre-commit into .git/hooks/
```

After that a drifted commit is refused with the exact fix printed, and
`git commit --no-verify` bypasses it deliberately. The installer is idempotent and moves any
pre-existing hook aside instead of clobbering it. Teams can skip the installer and point git at
the directory instead with `git config core.hooksPath .githooks`.

---

## 9. HTTP API, WebSocket & control channel

| Endpoint | Method | Purpose |
|---|---|---|
| `/` and any unknown path | GET | Dashboard SPA (or the standalone fallback), with SPA fallback, gzip, ETag/304, Range, keep-alive |
| `/assets/*` | GET | Hashed bundles — `immutable, max-age=31536000` |
| `/api/status` (and `/api`) | GET | The full engine payload (see below). `Cache-Control: max-age=0, must-revalidate` |
| `/api/logs` | GET | Tail of `trading.log` for the Engine Log tab |
| `/api/health` | GET | Process/transport health summary |
| `/api/config` | GET | The engine's current effective config, as the UI and the generated `.env` see it |
| `/api/control` | POST | `{action: "pause"｜"resume"｜"close_all"｜"close_symbol", …}` — writes the control file. Idempotent: a repeated `action` + `command_id` is recognised, not double-executed |
| `/api/soak` | POST | `{action: "start"｜"stop"}` — arms/stops the futures paper soak. Guards: duplicate start → `409`, stop when idle → `409`, unknown action → `400` |
| `/api/config` | POST | Whitelisted, type-validated `.env` writes (the tuner push) |
| `/ws` | WS | RFC 6455. Sends a `hello` snapshot, then a ~1 s coalesced status push plus log tails |

**`/api/status` payload** — 87 config keys and 29 top-level sections (`account`, `balance`,
`candidates`, `config`, `control`, `data`, `futures`, `loop_state`, `market`, `mode`,
`monitored_symbols`, `open_positions`, `paper_trade`, `positions_age_s`, `positions_live`,
`positions_pnl`, `positions_tracked`, `positions_untracked`, `process`, `roadmap`,
`scanned_pairs`, `server_epoch_ms`, `server_time`, `server_time_utc`, `signal_state`,
`soak`, `timestamp`, `untracked_pnl`, `ws_streams`). The nested `data` object carries
`stats`, `risk`, `trades`, `orders`, `signal_state`, `ws_streams`, `loop_state`,
`monitored_symbols`, `scanned_pairs`, `balance`, `futures`, `untracked_pnl`, `positions_pnl`,
`mode`, `soak`.

**Active-position sync** — every row in `data.trades` is the engine's persisted management
state (stops, targets, trail, locks, size, scale-out) stamped with the engine's own live
position numbers: `current_price`, `unrealized_pnl`, `live_qty`, `position_source`
(`engine-futures` / `engine-scan` / `engine` / `db`), `position_age_s`, `position_stale`. The mark comes
from `risk_state.futures_state` or, failing that, the screener's prices — never from the
browser and never from a monitor-side exchange call. So "Entry → Now", the change %, the
Unrealized column and the bracket-ladder marker always describe the position the engine is
managing. `open_positions` is `tracked ∪ live`, `untracked_pnl` covers only exchange positions with no
tracking row, and `positions_pnl` is the fresh total floating PnL across the engine's live
positions. Full contract in the engine README's *Active-position sync*.

**Loop liveness & the control channel** — the engine writes a `loop_state` heartbeat once per
iteration on **every** path (trading / paused / health-pause), served with an `age_s`. The
dashboard's *Decision loop* light reads that, not the age of the newest per-symbol signal
snapshot — that snapshot only advances when a symbol gets past every gate, so pausing or holding
a full book used to freeze it while the engine was healthy. The heartbeat also carries
`paused_requested` / `paused_applied`, so the UI shows *pause requested…* until the engine has
actually applied the pause instead of echoing its own request back as fact.

**Control channel** — two interchangeable paths into the same state: `POST /api/control` (what
the dashboard buttons use) and the control file `data/engine_control.json` (or `CONTROL_FILE`),
which the engine polls with a cheap mtime check:

```json
{ "paused": true, "close_all": false, "close_symbol": "NEARUSDT" }
```

- `paused` — blocks **new entries** only; open positions stay managed.
- `close_all` / `close_symbol` — one-shot market exits; the engine clears the command after executing.
- `soak` controls are exposed as buttons in the UI and backed by `/api/soak`.

---

## 10. Operations (PM2, logs, soak)

```bash
pm2 start ecosystem.config.cjs     # engine + web monitor
pm2 save                           # persist the process list
pm2 reload ultimate-bot            # zero-downtime restart AFTER an .env/code change
pm2 logs ultimate-bot
```

| PM2 app | Runs | Logs |
|---|---|---|
| `ultimate-bot` | `main.py` (venv interpreter), `max_memory_restart: 2G`, crash-loop breaker | `logs/pm2-out.log`, `logs/pm2-error.log` |
| `bot-web-monitor` | `status.py --web 3000` | `logs/web-out.log`, `logs/web-error.log` |
| `futures-soak` *(opt-in)* | `futures_soak.sh start` — isolated DB/control/log | `logs/futures_soak.log` |

- **Application log:** `logs/trading.log`, bounded at 10 MB × 5 by `RotatingFileHandler`.
- **PM2 does not rotate** app logs; install `pm2-logrotate` if stdout files must be capped.
  At `LOG_LEVEL=DEBUG` stdout grew 355 MB in ~6 hours — keep `INFO`.
- **Runtime state lives in `data/`** (`trading.db` + WAL, `engine_control.json`) and is
  created at boot (`os.makedirs(..., exist_ok=True)`), so both directories are safe to ignore
  in git.
- **`npm run clean`** removes `dist/`.

---

## 11. Verification & test matrix

All commands run from the repo root unless noted. `npm run smoke` and friends handle the
`cd ultimate-bot` and venv path for you.

| Suite | Command | Covers | Status |
|---|---|---|---|
| **Config drift** | `npm run check:env` | `.env` vs `.env.example`: keys present in only one file, duplicate keys, inline `#` comments on value lines, value drift, and that no real credential has reached the template | **IN SYNC** |
| Engine smoke | `npm run smoke` | Boot, single-instance lock, second-instance rejection, web-monitor stats consistency, clean SIGTERM shutdown and lock release (isolated temp DB) | **13/13** |
| Sync integration | `npm run test:sync` | Engine ↔ monitor end-to-end: real engine boot, `/api/status` + `/ws` payload, streaks, control channel, DB↔UI agreement, **active-position sync** (E2: the served row carries the engine's mark/uPnL/size, per-position leverage/margin normalised, untracked positions counted not double-counted), **loop heartbeat + pause acknowledgement** (B1), **roadmap watchlist** (E3: `roadmap.pairs` == the served `monitored_symbols`, ok/blocked partition the watched set, a rotation invalidates the cache) | **46/46** |
| Failure scenarios | `npm run test:scenarios` | Offline battery S1–S9 (incl. S6 order-routing `reduce_only` contract, S7 multi-assets balance seed, **S8 the shared exit decision** — ordering, reason labels, gap-aware fills, **S9 active-trade persistence** — flush on change, heartbeat, DB-failure tolerance) | **ALL OK** (40 checks, S8: 13, S9: 8) |
| Futures reconcile | `./venv/bin/python3 ultimate-bot/test_futures_reconcile.py` | Reconcile reads `positionAmt`, not wallet balance | **ALL OK** |
| Dashboard UI | `npm run test:ui` | 16 SSR tests: 6 desk sections, bracket ladder, breaker, cooldown, flat state, engine stats, screener empty state, untracked PnL, futures chip, **stale-position flag**, **loop-heartbeat source**, **pause acknowledgement**, **roadmap universe label**, **app shell (5 tabs)**, tab bodies | **16/16** |
| Typecheck | `npm run lint` | `tsc --noEmit` across all TS/TSX | **clean** |
| Credentials | `npm run probe` | Sign+verify self-test, connectivity, clock offset, signed account read (no orders) | exit 0 = go |
| Order builders | `npm run probe:orders` | All 4 clients build a valid order (stubbed transport, nothing sent) | exit 0 = go |
| Backtest | `./venv/bin/python3 ultimate-bot/backtest.py --symbol NEARUSDT --pages 30 --quiet` | Strategy edge on fresh data, resolved against the **deployed `.env`** | **−7.80% / PF 0.73** (preset values, same window: +0.41% / PF 1.02) |

The smoke/sync suites use isolated temp DBs and never touch the real `trading.db`.
`npm run verify` chains the whole battery (`lint` → `check:env` → `smoke` → `test:ui` →
`test:sync` → `test:scenarios`) in one command, and the versioned pre-commit hook
(`npm run hooks:install`) runs the drift check on every commit.

---

## 12. Security & secrets

- **`.gitignore` blocks, by construction:** `node_modules/`, `dist/`, `__pycache__/`,
  `venv/`, `*.log`, `*.db`(+`-wal/-shm/-journal`), the whole contents of `ultimate-bot/data/`
  and `ultimate-bot/logs/` and `ultimate-bot/keys/` (each with a `.gitkeep` exception),
  `*.pem`, `*.key`, and `.env*` with `!.env.example`.
- **Only `.env.example` is tracked**, and only with placeholders. `BINANCE_PRIVATE_KEY_PATH`
  is configurable, so the `*.pem`/`*.key` globals cover keys stored outside `keys/`.
- **The monitor does not leak credentials.** Verified: `/api/status` publishes 87 config
  keys and excludes exactly the three sensitive ones (`BINANCE_API_KEY`,
  `BINANCE_PRIVATE_KEY_PATH`, `DISCORD_WEBHOOK_URL`). Never expose `:3000` to the open
  internet without a reverse proxy + auth — it can pause the engine and close positions.
- **`status.py` tuning writes are whitelisted** (`TUNING_KEYS` + typed validation) — the
  dashboard can never push credentials or arbitrary keys into `.env`.
- **Operational hygiene:** keep withdrawals disabled on the API key, restrict it by IP,
  register the Ed25519 key instead of using an HMAC secret, `chmod 600` the private key, and
  rotate the API key and Discord webhook periodically. If a credential ever reaches git
  history, rotate it — a history rewrite does not un-expose it.
- **No secrets in logs:** the engine logs equity and order results, never key material.

---

## 13. Go-live checklist (summary)

Full version with verification commands: **[GO_LIVE.md](./GO_LIVE.md)**.

1. Binance key created with trading enabled, **withdrawals disabled**, IP-whitelisted, 2FA on.
2. Ed25519 keypair generated; public key registered; `private_key.pem` is `600`.
3. `.env` flipped: `PAPER_TRADE=false`, key + key path set (`MARKET=futures` only if intended).
4. Risk sanity: `RISK_PER_TRADE=1%`, `MAX_DAILY_DRAWDOWN=5%`, proven symbols/frequency.
5. Pre-flight: `smoke` → `sync` → **fresh-data backtest** → `probe` → `probe:orders`, all green.
6. `pm2 stop/delete/start ecosystem.config.cjs`, then `pm2 save`; watch the first boot.
7. First hour: watch one entry end-to-end, fire one manual Market Close, confirm PnL
   verification lines, check `pm2 ls` for a stable restart counter.
8. Rollback: `{"paused": true}` in the control file, `pm2 stop ultimate-bot`, or **Close All**.
9. Ongoing: `DISCORD_WEBHOOK_URL` set, `LOG_LEVEL=INFO`, daily log review for the first week.

---

## 14. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Another instance is already running` | The `/tmp/ultimate_bot.*.lock` file is held. Only one *live* engine may run (spot or futures); a paper engine may coexist. |
| `-1021` timestamp errors | Clock drift. The engine re-syncs automatically; check `chrony`/NTP, then the Engine Health card's clock row. |
| Binance `-1022` signature errors | Signature ordering / key mismatch — re-run `npm run probe`. |
| Monitor shows stale or zero data | Wrong endpoint/port, or the monitor started before an `.env` edit (it re-reads on mtime change). Check `pm2 logs bot-web-monitor`. |
| Monitor shows **Offline** light | No `/api/status` snapshot arriving — the engine is down or on another port. |
| Entries silently skipped | Equity × allocation caps below the $5 minNotional (boot warns), or a symbol is in `LOSS_REENTRY_COOLDOWN`. |
| Discord alerts not arriving | Empty/invalid `DISCORD_WEBHOOK_URL`, or the `DISCORD_COOLDOWN` window is still open. |
| Dashboard API doesn't work in `npm run dev` | Vite proxies to `:5001` — run `status.py --web 5001` (dev) and keep `:3000` for production. |
| `pm2 ls` shows `errored` | The crash-loop breaker tripped (5 fast failures) — read `logs/pm2-error.log`; usually a bad `.env` value. |
| Engine exited, positions still open | Expected on `pm2 stop`: open positions remain on the exchange and are re-adopted with SL/TP on the next boot. |

---

## 15. Repository audit — current findings

Re-audited on 2026-09-22 (every tracked file, every function, both languages).

**Clean**

- **No dead modules:** every one of the 19 engine modules (excluding `__init__.py`) has an importer
  (`capital_roadmap` via a lazy import inside `status.py`; the other 14 top-level scripts are
  genuine entry points).
- **No dead code in the Python tree:** an AST scan reports zero unreferenced module-level
  defs, classes or methods, and zero unused imports after this pass.
- **No orphan front-end files:** all 15 components are imported; the only files without an
  importer are the genuine entry points (`main.tsx`, `vite.config.ts`, `ui_smoke.mjs`,
  `vite-env.d.ts`).
- **No unused dependencies:** every `package.json` dependency and every `requirements.txt`
  package is used.
- **No temporary files:** zero `__pycache__`, `*.pyc`, `.tmp/.bak/.orig/.rej/*~`,
  `.DS_Store`, `.tsbuildinfo` or `.swp` artifacts.
- **No unreachable code:** an AST scan of every function body in the Python tree finds no statement
  following a `return`/`continue`/`raise` (a 7-line duplicated block in `backtest.py` was found this
  way in the 2026-09-22 audit and removed).
- **No credential exposure in the monitor** (see [§12](#12-security--secrets)).
- **No stale position state in the monitor:** every served row of `data.trades` is joined to the
  engine's own published position snapshot (mark/uPnL/exchange size) and comes with an age and a
  staleness flag, and the engine now flushes a managed trade to SQLite on every real change rather
  than inside a one-second window per 30 s. Guarded by `sync_test.py` **E2** and
  `test_scenarios.py` **S9**.
- **Liveness is reported by the engine, not inferred:** an engine→monitor audit of every published
  field found the *Decision loop* light measuring the age of the newest per-symbol signal snapshot —
  which only advances when a symbol gets past every gate, so **pausing the bot from the dashboard,
  or holding a full book, made a healthy engine report a wedged loop**. The engine now writes a
  `loop_state` heartbeat once per iteration on every path, the pause is *acknowledged* rather than
  echoed, and the fields that are change-driven (and therefore must never be read as heartbeats)
  are documented in the engine README. Guarded by `sync_test.py` **B1** and two new UI tests.
- **No config drift:** `.env` and `.env.example` share all **90 keys with zero drift** (the
  only differences are the three credential keys, which keep placeholders). Nine keys
disagreed before the 2026-09-22 reconciliation recorded in the changelog.
- **Drift is now guarded, not just documented:** `ultimate-bot/check_env_drift.py`
  (`npm run check:env`) fails on lost/added keys, duplicate keys, value drift, inline comments
  and any real credential reaching the template. It was verified against ten scenarios,
  including a simulated secret leak and a fresh clone with no `.env` (which skips cleanly
  rather than failing).

**Watch these**

1. **The deployed config overrides the `intraday_rsi` preset on 8 strategy keys — and it is now
   measurable.** The preset is what §4 documents and what the provenance battery validated, but the
   live `.env` wins wherever it sets a key — and it sets these eight:

   | Key | Preset (validated) | Live `.env` |
   |---|---|---|
   | `RSI_TIMEFRAME` | `1h` | `30m` |
   | `RSI_PERIOD` | `7` | `14` |
   | `REGIME_EMA` | `50` | `21` |
   | `REGIME_SLOPE_DAYS` | `3` | `2` |
   | `MTF_TIMEFRAME` | `1d` | `4h` |
   | `MAX_TRADES_PER_DAY` | `2` | `0` (unlimited) |
   | `BREAKEVEN_ENABLED` | `false` | `true` |
   | `CLOSE_AT_UTC_DAY_END` | `true` | `false` |

   These may be deliberate operator choices — the engine is the one trading — but they mean the
   running strategy is **not** the configuration behind +10.77% / PF 1.43. Since the audit fixed the
   backtest's config resolution, the two can be compared on an **identical window** (NEARUSDT, 30 pages
   ≈ 104 days, $22 equity, run 2026-09-22):

   | Config | Return | Trades | WR | PF | Expectancy | Fees | Max DD |
   |---|---|---|---|---|---|---|---|
   | Preset (validated values) | **+0.41%** | 56 | 52% | **1.02** | +0.07 | 92.6 | 7.6% |
   | Deployed `.env` | **−7.80%** | 58 | 55% | **0.73** | −1.34 | 90.7 | 10.3% |

   Same window, same fee model, same signal code: the deployed overrides are worth roughly **8 points
   of return and 0.29 of profit factor**. Neither figure is the historic +10.77% — the preset itself has
   degraded on recent data — but the deployed config is the weaker of the two, and it is the one trading.
   Re-tune or revert only against a fresh backtest (GO_LIVE.md step 5.3): a 15m `RSI_TIMEFRAME` was
   already measured turning the validated +7.11% into −16.5% through fee drag.
2. **Backtest↔live parity: the exit decision is now literally shared.** Fixed 2026-09-22/23 — the
   backtest resolves config **env-first** exactly like `config.load_config()` (verified at zero
   differences across every non-credential key), it applies the `FUNDING_RATE_MAX` **entry gate** so a
   futures backtest can no longer take an entry the engine refuses, the futures mode is wired to
   `--market {spot,futures}`, and since 2026-09-23 `trade_policy.evaluate_exit` is the **single** exit
   decision — the same call `manage_trade` makes every poll — with one ordering (stop → take-profit →
   time stop → day-end → +R scale-out), comparable reason labels and gap-aware fills on both sides. The
   old `bar_exit`, called by nothing but the backtest, is gone, and the trailing stop is fed the same
   favourable extreme by both. Still different, and documented rather than hidden: **sampling
   granularity** (a 10 s tick plus the last two klines vs one completed bar, and no wall clock in a
   replay), the exchange's `stepSize`/`minQty` quantisation and free-quote haircut in sizing, and
   backtest fills that are spread-, slippage- and latency-free. Full table in
   [ultimate-bot/README.md § Backtest vs. Live](./ultimate-bot/README.md#-backtest-vs-live-verified-parity-divergences).
3. **Port `:3000` is claimed by two things** — `npm run dev` and `status.py --web`. Only one
   can run at a time.
4. **`data/` and `logs/` were deliberately retained** — they hold the running engine's live
   trade database and logs and are gitignored, so they must never be committed.

---

## 16. Changelog

Newest first. Entries marked **⚙️ engine** carry deeper detail in
[ultimate-bot/README.md](./ultimate-bot/README.md).

### 2026-09-23 (4) — The Capital Roadmap analyses the pairs the engine actually trades
- **The card described a different universe from the one being traded.** `capital_roadmap.DEFAULT_PAIRS` was a six-symbol literal (`NEAR/LINK/DOT/ARB/OP/LSK`) compiled into the module, while the engine runs `DYNAMIC_SYMBOLS=true` and the screener rotates `MAX_SYMBOLS` picks every cycle. The panel therefore reported floors for pairs the bot was not watching and omitted the ones it was: the live book's binding constraint — **BCHUSDT's $20 floor** against a proven $19.69 notional — was invisible, because BCHUSDT is not a default pair. Both surfaces now take the **engine's live watched set** (`risk_state.monitored_symbols`, written by `trade_logic.update_symbols`: screener picks + `STATIC_SYMBOLS` + any symbol holding an open trade): `status.py` reads it once per payload (`_engine_watched_symbols`) and passes it to `compute_roadmap(pairs=…)`, and the CLI defaults `--pairs` to the same DB row. `DEFAULT_PAIRS` survives only as the fallback when the engine has published nothing, and the new **`pairs_source`** field says which was used — the card never presents a fallback list as the live one.
- **A rotated watchlist invalidates the 10-minute cache.** The cached exchange data (floors, prices, funding) belongs to *specific* symbols, so the cache is now keyed on the watched set and recomputed the moment it changes, instead of reporting dropped pairs — and missing newly-picked ones — for up to ten more minutes. The card header states the universe and its size: `5 engine-watched pairs · live exchange floors · computed 42s ago`.
- **Stage thresholds follow the watched set**, which is the honest reading of the question: the binding constraint is the highest floor among the pairs being traded, not among an arbitrary list. Verified against the live engine's own selection — 4 of 5 watched pairs clear, BCHUSDT does not.
- **CLI output corrected while touching it:** the stage label hardcoded `proven 1% risk` (now prints the configured `RISK_PER_TRADE`), the header prints the pair source, and `equity_from_db` / the new `watched_symbols_from_db` share one read-only `risk_state` reader.
- **Verification:** new **E3** in `sync_test.py` (**46/46**) — the served `roadmap.pairs` equals the served `monitored_symbols`, ok/blocked partition the watched set against the served proven notional, and a rotation invalidates the cache; stubbed exchange data against a private DB copy, so it needs no network and cannot disturb the serving monitor. One new UI test pinning the universe label (**16/16**); scenarios **40/40**, smoke **13/13**, `tsc --noEmit` clean, `check:env` IN SYNC.

### 2026-09-23 (3) — Engine→monitor field audit: loop liveness, the pause, and the futures fields
- **A paused engine reported a wedged loop.** The Engine Health panel's *Decision loop* row measured
  the age of the newest per-symbol signal snapshot, and its tooltip promised "a frozen age means the
  strategy loop is wedged". That timestamp only advances when a symbol gets **past every gate**
  (`process_symbol` returns before `generate_signal` for active trades, cooling-down symbols, a full
  slot list and a tripped breaker, and the pause branch never evaluates symbols at all) — so pausing,
  or simply holding a full slot list, froze it within one cycle while the engine was healthy. The
  engine now publishes a `loop_state` heartbeat once per iteration on **every** path (trading /
  paused / health-pause); the panel reads that, the snapshot age is only a fallback for older
  engines, and the tooltip changes with the source.
- **The pause is acknowledged, not echoed.** `control.paused` records what the monitor *asked* for,
  and the UI displayed that request back as fact. The heartbeat carries `paused_requested` /
  `paused_applied`, so the chip reads *entries paused* only once the engine has applied it, and
  *pause requested…* until then.
- **Futures positions are normalised.** On the common WS-cache path every position arrived with
  `leverage: null` / `margin_type: null` (the real values are on the snapshot's top level) and no
  `markPrice` (only the positionRisk fallback has one), so the Futures card showed `1× CROSSED` in
  its header directly above `—×` for the same position. Both are now filled, and the mark is derived
  from the published uPnL.
- **A fresh total floating PnL (`positions_pnl`)**, and `roadmap.age_s` so the 10-minute-cached
  roadmap states its own age instead of showing an "Equity" cell next to the live one unlabelled.
- **Documented, deliberately unchanged:** `risk.unrealized_pnl` (engine-persisted, read by nothing in
  the dashboard), `engine_risk.updated_at` and `signal_state` (change-driven, so **not** heartbeats),
  `account_balances` (~60 s, standalone page only), and the monitor-computed PM2/soak state.
- **Verification:** `sync_test.py` **41/41** (new B1 heartbeat + pause acknowledgement, E2
  normalisation), UI **15/15** (2 new), scenarios **40/40**, smoke **13/13**, `tsc --noEmit` clean,
  `check:env` IN SYNC.

### 2026-09-23 (2) — The monitor's active position is the engine's active position
- **The dashboard showed the entry price as the current price.** `active_trades` has no price
  column, so the served row carried no `current_price` and the UI's `current_price || entryPrice`
  fallback rendered the entry as "now": **0.00%** change, **$0.00** unrealized, and the
  bracket-ladder marker parked on the entry — while the engine knew the real mark. `status.py`
  now joins the engine's **own published state** onto every served row (`risk_state.futures_state`
  plus the screener's per-symbol prices), adding `current_price`, `unrealized_pnl`, `live_qty`,
  `position_source`, `position_age_s` and `position_stale`. When the engine publishes a
  `markPrice` it is used verbatim; otherwise the mark is derived exactly from the published uPnL
  (`mark = entry + uPnL / amount`, valid for a long or a short). No browser math, no monitor-side
  exchange call.
- **The served position state no longer lags the engine.** `manage_trade` flushed a managed trade
  to SQLite only on `int(time.time()) % 30 == 0` — a one-second window every 30 s that the 10 s
  decision cadence routinely missed, so a ratcheted trailing stop or a breakeven lock could sit
  unpublished for minutes while exits were decided from memory. `_persist_active_trade_if_changed()`
  now writes once per real change, with a 60 s heartbeat, signature cleared on close, and a DB
  failure downgraded to a warning that retries next cycle.
- **Counts are explicit:** `positions_tracked` / `positions_untracked` / `positions_live`, with
  `open_positions = tracked ∪ live`; `untracked_pnl` sums only untracked positions, so a tracked
  row is never double-counted.
- **The standalone fallback dashboard** gained **Now** and **Unrealized** columns driven by the
  same joined mark.
- **Verification:** new `E2` in `sync_test.py` (8 checks — injects a futures snapshot and asserts
  the served row carries the engine's mark/PnL/size/provenance, 35/35 total) and new `S9` in
  `test_scenarios.py` (8 checks — flush on change, no per-cycle churn, heartbeat, DB-failure
  tolerance, 40/40 total). Plus `tsc --noEmit` clean, smoke 13/13, UI 13/13, `check:env` IN SYNC,
  and the live futures payload re-read against the real DB. The join's own first draft counted a
  `NaN` amount as a live position; `_safe_float` now rejects non-finite sizes and derived marks.

### 2026-09-23 — Exit logic unified: one decision function for live and backtest

- **`trade_policy.bar_exit` is gone; `trade_policy.evaluate_exit` replaces it, and the live engine now
  calls it too.** Trade management was previously shared only at the level-setting helpers: the backtest
  replayed `bar_exit` per bar while `trade_logic.manage_trade` ran its own three-batch poll loop that
  ordered the +1R scale-out *before* the stop check and filled a wick stop at `min(stop, low)`. Both now
  make one call with normalised evidence (live: the wick range of the post-entry klines folded together
  with the live tick; backtest: the bar's low/high) under one ordering — **stop → take-profit → time
  stop → day-end flatten → +R scale-out** — so the priority, the labels and the fills cannot drift again.
- **Parity proof, not a claim:** on one identical NEARUSDT window (30 pages ≈ 104 days) all 59 trades
  reproduce to the last decimal — same entry, exit price and PnL — the only change being a
  window-relative index. The refactor moved no fill.
- **22 of 55 stop-outs were misfiled as `STOP_LOSS` and are now `TRAILING_STOP`.** Exit-reason
  histograms from the two systems were previously not comparable, because the backtest never emitted
  `TRAILING_STOP` or `EOD_CLOSE` and recorded its day-end close as `TIME_STOP`. They are now — and the
  split shows the ATR trail, not the hard stop, closing over a third of all exits.
- **The trail follows the observed extreme on both sides.** The backtest ratchets at 2×ATR below the bar
  high; live ratcheted below the bare tick, so its trail lagged the model's. Live now feeds the same
  wick high. This is the one change that moves live stop levels, and it only ever tightens: the raised
  trail is always ≥ the old one, so it can exit at the same price or earlier, never looser. It is also
  the one piece the backtest cannot validate, because it moves live only.
- **Fills are gap-aware on both sides.** A level already traded through fills at the decision-moment
  price the caller supplies (the live tick; the bar's open in a replay) instead of at a level the market
  has left behind. Live's old `min(stop, low)` booked the bottom of the wick — pessimistic past realism.
- **A failed kline fetch no longer skips the stop check.** Live used to fall through to the time stop
  and day-end only; it now degrades to tick evidence and still evaluates the stop.
- **New `S8` contract battery** in `test_scenarios.py` (13 checks: ordering, reason labels, gap-aware
  fills, armed vs unarmed trail, quiet and absent-evidence windows) — and the battery is finally wired
  into the standard flow as `npm run test:scenarios` and into `npm run verify`. It existed but nothing
  ran it.
- **Verified:** backtest trade list diffed trade-by-trade against the pre-change run · spot and futures
  paths · forced `SCALE_OUT_ENABLED=true` / `CLOSE_AT_UTC_DAY_END=true` runs (11 partials, 1 `EOD_CLOSE`)
  · `tsc --noEmit` clean · smoke **13/13** · UI **12/12** · sync **27/27** · scenarios **ALL_OK** ·
  `check:env` **IN SYNC**. No live parameter was touched, and the running engine keeps executing the
  previous code until it is reloaded.

### 2026-09-22 (5) — Directory-wide audit: six defects found and fixed

- **Unreachable dead code in `backtest.py`** — a 7-line duplicated exit block sat *after* a `continue`
  in `run_backtest`, so it could never run. Found with an AST scan for statements following a
  `return`/`continue`/`raise`, and removed; no other unreachable code exists in the Python tree.
- **The futures backtest was unreachable from the CLI** — `run_backtest(..., market=...)` existed and
  `fetch_klines`, the fee model, funding and minNotional all branched on it, but `main()` never passed it
  and no flag set it, so every documented futures assumption was dead as far as a user was concerned.
  Added `--market {spot,futures}` with a `BACKTEST_MARKET` env default.
- **The funding-rate entry gate is now implemented in `backtest.py`** (parity with
  `trade_logic.enter_trade`): a long whose last settled rate exceeds `FUNDING_RATE_MAX` is skipped, and
  the run reports `funding_gate_skips`. Verified by forcing the cap to ~0 — 2 entries blocked, 0 trades,
  `funding_gate_skips=2` — while the same cap under `--market spot` changes nothing, as it should.
- **Config precedence fixed (the important one)** — `backtest.run_backtest` applied the preset *on top
  of* `load_config()`, so a bare run validated the preset while the engine ran `.env`; on this repo that
  was 8 strategy keys apart. It now selects the preset through `PRESET` and loads normally, which is
  env-first, exactly like the engine. This is what makes the comparison in
  [§15](#15-repository-audit--current-findings) item 1 possible.
- **`backtest.py` docstring corrected** — it claimed the futures `NOTIONAL.minNotional` floor "defaults
  to 100 USDT (futures reality)"; the code reads the real per-symbol fapi value (5 USDT on most USDT
  perps, which the module docstring already said).
- **`npm run clean` no longer references the deleted `server.js`** — it was `rm -rf dist server.js`, and
  that file has not existed since the original scaffold was removed.
- **Swept clean, no action needed:** `compileall` across the whole engine · `tsc --noEmit` · `node --check`
  on `ecosystem.config.cjs` and `ui_smoke.mjs` · `bash -n` on both shell scripts · zero
  `TODO`/`FIXME`/`HACK` · zero `eval`/`exec`/`os.system`/`shell=True`/`pickle.load` · zero mutable default
  arguments · all SQL parameterised (the interpolated `fields` in `update_order_status` are literal
  column names, never user input) · all 9 `requirements.txt` pins map 1:1 to real imports · no dead
  exports or orphan components across the 21 TS/TSX files · nothing tracked that should not be
  (`dist/`, `node_modules/`, `venv/`, `logs/`, `data/`, `.env`, `*.pem`, `*.db` all ignored and untracked).

### 2026-09-22 (4) — Spot/futures and backtest↔live parity documented; 13 divergences audited

- **New [§5.1](#51-spot-vs-futures)** documents the two-venue split: the signal layer, the `trade_policy`
  level helpers and the screener contain **zero** `MARKET` branches, and only `trade_logic` (11 sites),
  `order_manager` (8) and `risk_manager` (1) branch on it at all. Same signal and same bracket on both
  venues; futures takes fewer entries because it adds a funding gate and a margin-based (not notional)
  entry check.
- **§15 gained a watch item for backtest↔live parity** — a full audit of both paths found 13 places they
  can disagree. The largest: `config.load_config()` is env-first, but `backtest.run_backtest` applies the
  preset on top of it, so a bare `backtest.py` reproduces the **preset**, not the deployed `.env`
  (8 strategy keys differ). Also verified: `trade_policy.bar_exit` is called by nothing but the backtest —
  live's `manage_trade` runs its own loop and orders the +1R scale-out ahead of the stop/TP check; the
  backtest never applies the funding gate; sizing skips the exchange's `stepSize`/`minQty` quantisation
  and the free-quote haircut; the daily breaker ignores unrealised PnL; and every backtest fill is
  slippage- and spread-free.
- **Deep dive** in [ultimate-bot/README.md](./ultimate-bot/README.md) — two new sections
  (*Spot vs. Futures: Shared Logic & Divergences*, *Backtest vs. Live: Verified Parity Divergences*) with
  the full 13-row table, a runnable config-precedence reproduction, and corrections to the two claims
  this audit disproved (that trade management is "identical", and that a bare backtest reproduces the
  deployed `.env`). No engine or backtest code was changed.

### 2026-09-22 (3) — Automated config-drift guard

- **New `ultimate-bot/check_env_drift.py`** (`npm run check:env`, plus a new `npm run verify`
  that chains lint → check:env → smoke → test:ui → test:sync). It parses `.env` and
  `.env.example` strictly and exits non-zero on: keys present in only one file, duplicate
  keys, inline `#` comments on value lines, any non-exempt value difference, and a real
  credential reaching the template — either a non-placeholder value for a known credential key
  or a new secret-shaped key mirrored verbatim. Output redacts anything whose name looks
  sensitive and strips ANSI colour when piped, and it exits 0 with a `SKIP` line when no
  `.env` exists (fresh clone / CI), so it is safe to gate a commit or a deploy on.
- **`--update` re-mirrors the template** from `.env` after an intentional config change: it
  rewrites changed values, appends keys the live config has and the template lacks, comments
  out keys the live config no longer sets, and **refuses to copy anything credential-shaped**
  into a tracked file.
- **Pre-commit hook** — `.githooks/pre-commit`, enabled with `npm run hooks:install` (or
  `bash scripts/install-hooks.sh`): every commit runs the check and is refused on drift, with
  the fix command printed. The hook is versioned because git only executes `.git/hooks/`, which
  is never committed; the installer symlinks it into place, is idempotent, and moves any
  pre-existing hook aside rather than clobbering it. If no Python interpreter exists it warns
  and lets the commit through instead of blocking all work.
- **Verified against ten scenarios:** in-sync, value drift, key only in `.env`, key only in
  the template, credential leak, personal absolute key path, duplicate keys, inline comment,
  missing `.env`, and the `--update` round-trip (which leaves the credential placeholders
  intact). One false expectation was corrected during testing: an inline comment fails only
  because the comment text genuinely becomes part of the value, which is the documented
  dotenv behaviour.

### 2026-09-22 (2) — Repository hygiene, dashboard simplification, documentation

- **Removed the "Project Code & ZIP" tab and its entire feature**: deleted
  `src/components/CodeExplorer.tsx` and `src/data/botFiles.ts`, dropped the tab from
  `Header.tsx` and the `'code'` member from both tab unions and the `App.tsx` render branch,
  removed the `jszip` dependency (13 packages pruned from the lockfile) and the
  `vendor-jszip` / `fs.allow` config that existed only to raw-import the engine tree. The
  bundle went from **1,009.89 kB → 174.71 kB** (gzip 285.79 → 43.46 kB). The in-app guide and
  meta descriptions no longer mention unzipping.
- **Dead-code purge across both layers** (6 files, 13 deletions): an unused `MAGENTA`
  constant and two unused `cfg`-derived variables, two swallowed `ok` values, an unused loop
  variable, an unused import, a duplicated banner comment, and an unreferenced `total_pnl_state`.
  Ten `__pycache__` directories were also purged.
- **New dashboard regression tests** — `tests/smoke/dashboard.test.tsx` grew from 9 to **12
  tests**, adding app-shell coverage: exactly the five surviving tabs (in order, no dead
  `code` entry), the app opening on the Trading Desk, and every remaining tab body rendering.
- **`.gitignore` hardened** after an empirical `git add -A` harness run: the old rules only
  covered `*.db` and `engine_control.json`, so the soak watchdog's `state.json`,
  `events.jsonl` and `start_ms` marker would have been committed, as would any non-`.pem`
  secret in `keys/`. Now the whole contents of `data/`, `logs/` and `keys/` are ignored
  (with `.gitkeep` exceptions) alongside global `*.pem`/`*.key`/`*.db*` and the cache dirs.
- **Credential sanitization** — a snapshot copy of `.env.example` had been polluted with a
  real API key, a live Discord webhook and a personal absolute path; all three were restored
  to template values before the repository's first commit, so nothing sensitive was ever
  published from this directory.
- **Repository initialised and published** as `github.com/ruhyatnar/ultimate_bot` (single
  root commit, 81 files, zero secrets in the tree or history).
- **`dist/` rebuilt** from the cleaned sources and verified against the live monitor: served
  `index.html` matches disk byte-for-byte, all three JS + one CSS asset return 200, gzip
  compresses to 43 kB, ETag/`If-None-Match` revalidates to 304, and the SPA fallback serves
  deep links.
- **`.env.example` reconciled with the live `.env`.** The template had drifted from the engine
  on 9 keys: `PAPER_TRADE`, `FUTURES_MARGIN_TYPE`, `FUTURES_ONE_WAY_MODE`,
  `REST_WEIGHT_LIMIT`, `RSI_TIMEFRAME`, `STATIC_SYMBOLS` and `TOP_CANDIDATES` held different
  values, and `MAX_TRADES_PER_DAY` / `RSI_TIMEFRAME_MS` existed only in `.env`. The template
  now mirrors the deployment exactly — same 90 keys, zero drift, only the three credential
  keys keeping placeholders — and carries a new header block warning that it is **not** a safe
  sandbox, listing the five keys to change back on a fresh machine. **`.env` itself was not
  modified** (verified by hash before and after).
- **Documentation rewritten** — this file was restructured into a full operations-center
  reference (architecture, module-by-module engine reference, component reference, config
  reference, API surface, test matrix, security, audit findings and a complete changelog).

> These changes are **uncommitted** at the time of writing.

### 2026-09-22 — First live entry watch; multi-assets phantom-balance equity fix ⚙️

- **Fixed (WS-path equity inflated ~5×, `$23.01 → $111.75`)**: in Binance multi-assets
  collateral mode the account payload reports a collateral-equivalent `availableBalance` for
  *every* asset the wallet could use as margin while `walletBalance` stays `0` for the ones
  it does not hold — the same USDT restated in other denominations. `seed_balances` kept any
  row with `availableBalance > 0`, so BNB/BTC/ETH/USDC were cached as real holdings and priced
  at the real ticker (≈ $88.7), inflating `total_equity` — which feeds position sizing, the
  drawdown breaker, the allocation caps and the dashboard. A row is now seeded only when its
  **wallet** balance is non-zero. Verified live: 11 rows / $111.74 → 1 row / $23.02, matching
  `totalMarginBalance` $23.0059. Regression guard **S7**.
- **Confirmed live entry-gate behaviour**: the gate evaluates once per hourly `RSI_TIMEFRAME`
  bucket close; at the 01:00 UTC roll all five screened symbols refreshed with live RSI, and
  `AVAXUSDT` printed RSI 31.66 (< oversold 40) but stayed `trigger=False` because RSI was
  still falling — oversold **and** turning up is required.
- **Boot-critical fix (`config.py`)**: the preset's `TRAILING_ATR_MULTIPLIER: 2.0` was
  hard-defaulted to `0.0`, which forced %-callback validation onto equal activate/callback
  values and **crashed every boot** with `TRAILING_STOP_CALLBACK must be less than
  TRAILING_STOP_ACTIVATE`. The loader now reads it from the preset like every other parameter.
- **Live SPOT REST orders could never execute**: `OrderManager.place_market_order` passes
  `reduce_only=` to whichever client is available, but the spot `RestClient.place_order` did
  not accept the keyword — so every live spot order raised `TypeError`, logged "Order
  preparation failed" and was silently skipped. The spot client now accepts (and ignores) it,
  matching the two WS-API clients and the futures REST client.
- **The configured futures margin type was never applied**: `main.py` compared
  `FUTURES_MARGIN_TYPE` against `effective_margin` *after* publishing into that same key, so
  the check was always true and `set_margin_type()` was never called — the account kept
  CROSSED while the engine and dashboard reported ISOLATED. The loop now always performs the
  idempotent write and treats `-4046`/`-4059` as "already set" (and both are now
  non-retryable, so the benign case costs one request instead of three).
- Removed the dead `user_stream` parameter from `OrderManager` and seeded
  `last_order_result` in `__init__`. Regression guard **S6**.
- **New order-builder dry-run probe** (`order_dry_run_probe.py`, `npm run probe:orders`) —
  see [§6.1](#61-entry-points--operator-tools).

### 2026-09-21 — Live-readiness audit, boot-critical config fix, dead-code purge, monitor rebuild ⚙️

- **Fixed: monitor config echo could never refresh from `.env`** (monitor↔engine desync).
  `capital_roadmap.py` imports `config`, and `config.py` calls `load_dotenv()`, injecting
  every `.env` key into `os.environ`; `status.py`'s `load_env()` then merged the live
  `os.environ` over the freshly-read file, permanently re-applying the startup snapshot. It
  now merges overrides from a **frozen import-time snapshot**, so real process env still
  overrides but later dotenv values cannot freeze the refresh. Verified live:
  `SIGNAL_INTERVAL` 10→11→10 reflected with **no restart**.
- **Exchange clock sync end to end**: WS-API order clients now match the REST clients'
  discipline — midpoint offset measurement at logon, a 5-minute re-sync loop, auto re-sync
  when stale, and immediate re-sync on a `-1021` rejection. The offset publishes as
  `ws_streams.order_api.time_offset_ms` and the Engine Health card shows a **Clock sync
  (Binance)** row, with the REST measurement as fallback in every mode.
- **New `live_signed_probe.py`** (+ `npm run probe`) and GO_LIVE step 5.4; first full
  GO_LIVE walk-through executed with real keys (probe all green on both legs, two probe bugs
  found and fixed during the run, key file perms corrected 664→600).
- **The §5.3 backtest gate caught a real strategy killer**: fresh-data backtest returned
  −16.5% / PF 0.62 vs the validated +7.11% / PF 2.06, because `.env` carried
  `RSI_TIMEFRAME=15m` + an explicit `RSI_TIMEFRAME_MS=900000` while the preset pins `1h` —
  re-timing the gate to 4.6× the trade frequency (88 trades, 131 USDT of fees vs 32). Fixed
  the config, restored the preset frequency and added a **boot drift guard**; re-verified
  +7.11% / 19 trades / PF 2.06, an exact match with the baseline.
- **Fourth duplicate/cleanliness pass** (0 duplicate keys, imports, module defs, class
  methods or dict keys anywhere), orphan sweep, and the web-monitor UX/UI rebuild
  (design tokens, header rebuild, `StatusCards.tsx` extraction, honest empty states).

### 2026-09-20 06:36 UTC — Rate-limiting resilience, 8-decimal WS quantization ⚙️

- HTTP **429 and 418** handling in both REST clients: parse `Retry-After` and sleep;
  418 triggers a 120 s protective pause — a transient spike can no longer escalate to an IP ban.
- WS order clients switched to full 8-decimal quantization with trailing-zero stripping,
  matching REST placement exactly (previously 6 decimals truncated sub-penny / 8-step pairs).

### 2026-09-20 06:11 UTC — Live exit verification, funding gate, PM2 recovery ⚙️

- All four completed futures exits reconciled exactly against the exchange's own
  `userTrades` (`realizedPnl − commission`). PM2 had lost its process list after a session
  drop — restored with `pm2 resurrect`. RR sweep verdict recorded; funding gate live.

### 2026-09-20 03:14 UTC — PnL auto-verification, dashboard trade fix, breaker retune ⚙️

- Every live futures exit now cross-checks recorded PnL against exchange fills, self-healing
  drift beyond 5% / $0.02 (the B2USDT +0.90 class of error can no longer silently corrupt
  breakers, streaks or the dashboard).
- Dashboard completed trades fixed: SELL rows store `price=0`, so the UI showed
  "$0.00 → …"; `status.py` now attaches the matched BUY leg's fill price/timestamp and the
  exit reason is derived honestly (stopped / target / partial).
- Drawdown breaker retuned 0.01 → 0.03; ATR-stop backtest verdict recorded.

### 2026-09-20 02:34 UTC — Adopted-position PnL to exchange truth ⚙️

Adopted positions took cost basis from our own record instead of the exchange's; corrected,
and the dashboard streak display fixed.

### 2026-09-20 01:54 UTC — Full cross-module audit ⚙️

Systematic audit of every function across engine and monitor: 0 unresolved internal imports,
dead code purged, scenario battery green, sync verified.

### 2026-09-20 01:12 UTC — Instant-stop-out root cause ⚙️

The dip trigger candle's low was being counted by the trailing/stop evaluation, so orders
were stopped out within seconds of entry. Only candles that **open** at or after execution are
now considered, and `LOSS_REENTRY_COOLDOWN` was added as a per-symbol revenge-trade brake.

### 2026-09-20 00:19 UTC — Futures/spot separation, naked-position healing ⚙️

Five defects fixed around futures/spot separation that had caused the engine to orphan its
own futures fills (unmanaged, naked exposure). Now healed end-to-end at reconciliation.

### 2026-09-19 15:45 UTC — Futures market-data silent-frame workaround ⚙️

This box's WARP path completed the `fstream` handshake and SUBSCRIBE ACK but never delivered
market-data frames, leaving the ARR listener blocked in `recv()` forever. Added a REST bulk
ticker refresher feeding the same cache (`TICKERS_REST_FALLBACK_S`, provenance `ws`/`rest`),
enabled automatically when no frames land within a 20 s grace window.

### 2026-09-19 13:35 UTC — Live futures go-live ⚙️

`get_account()` normalised to the spot `balances` shape (so one balance path serves sizing,
pre-trade checks and the UI), listenKey wiring, a mode pre-check, and sizing/dashboard fixes.

### 2026-09-19 11:56 UTC — Soaks cleared, go-live posture confirmed ⚙️

Soak PM2 apps and runtime artifacts removed; the Futures Soak card correctly disappears when
no soak exists.

### 2026-09-19 11:36 UTC — Watchdog event feed, end-to-end WS proof ⚙️

`soak_watchdog.py` appends supervision events to a JSONL feed served as
`soak.watchdog_events` and rendered newest-first; a real RFC 6455 handshake against `/ws` was
executed headlessly to prove the exact browser path.

### 2026-09-19 10:05 UTC — Interactive soak control ⚙️

Start/stop soak from the dashboard via `POST /api/soak` with server-side guards;
`SOAK_STALL_S` became a tunable everywhere (whitelist, template, Config UI, env generator).

### 2026-09-19 09:40 / 07:41 UTC — Watchdog on the dashboard; futures NameError fixed ⚙️

Supervision health, alert flags and the exact 24 h deadline surfaced in the soak card.
Fixed a live `NameError`: `get_filters` referenced an undefined
`FUTURES_DEFAULT_MIN_NOTIONAL`, crashing the signal cycle for any symbol whose exchangeInfo
lacks the MIN_NOTIONAL filter (e.g. `GUSDT`).

### 2026-09-19 06:55 / 06:22 / 06:09 UTC — Futures module, roadmap, soak ⚙️

USDⓈ-M transported layer added behind one `.env` key; the futures edge proven with real
fapi klines, 0.05%/leg fees and real historical funding; the live futures floor corrected
from folklore (~100 USDT) to the real 5 USDT on 719 of 725 perps; the Capital Roadmap and
Futures Soak panels shipped; 24 h soak launched on an isolated DB.

### 2026-09-19 01:22 UTC — Unlimited frequency, all gates exposed ⚙️

`MAX_TRADES_PER_DAY=0` (unlimited) restored, with every optional entry gate exposed as a
tunable in the web monitor.

### 2026-09-18 23:51 UTC — Full engine↔monitor integration audit ⚙️

Sync gaps closed, dead code cleared, failure scenarios fixed — every finding re-verified
against the running stack.

### 2026-09-18 22:10 UTC — Terminology: bullish/bearish ⚙️

Spot-only vocabulary cleanup across comments, docstrings, logs and docs (no behaviour change).

### 2026-09-18 01:16 UTC — Champion strategy (A3) deployed end-to-end ⚙️

The engine now trades exactly what the research proved: RSI floor 35 plus a 2× ATR trail
activating at +1%.

### 2026-09-17 14:45 / 14:02 UTC — Dashboard stream lights; everything on WebSockets ⚙️

Every realtime process moved to a WebSocket with REST as fallback only, and the dashboard
began showing each transport plus the engine's own WS-cached balances.

### 2026-09-17 13:23 UTC — Live price sync fix ⚙️

Dashboard prices drifted up to ~0.9% from Binance between hourly rescans; the screener now
refreshes prices in place every `PRICE_REFRESH_INTERVAL` (10 s).

### 2026-09-17 06:00 / 05:45 UTC — Entry-time matching fix; exit timestamps ⚙️

Per-symbol BUY lookup kept only the latest fill, so older exits could pair with the wrong
entry; fixed with timestamp matching. Added an **Exited** column with date, time and hold
duration.

### 2026-09-16 15:10 UTC — Full audit: dead code, failure scenarios, corruption drills ⚙️

Repo-wide AST plus cross-language reference index: no dead code, and all flags were framework
callbacks.

### 2026-09-16 (2) UTC — "Trade more = earn more?" proven NO ⚙️

A frequency sweep showed every looser configuration looked better only in-sample and failed
robustness. Added an allocation floor guard and hardened the sync clock.

### 2026-09-16 UTC — Account-level streaks ⚙️

Win/loss streaks were per symbol, so with dynamic symbols consecutive account losses landed
on different pairs and no cooldown ever armed. Streaks are now account-wide.

### 2026-09-15 UTC — Sync clock; breaker & cooldowns visible ⚙️

New `SyncClock` widget (`server_epoch_ms`, `server_time_utc`), and a real fix: streak
cooldowns were gated on a condition that could never be true, so they never blocked entries.

### 2026-09-14 (2) / 2026-09-14 UTC — Unlimited mode, crash-proof config, proven frequency ⚙️

`MAX_TRADES_PER_DAY=0` documented as infinite with `∞` rendering; crash-proof config parsing
and push validation after a `MAX_TRADES_PER_DAY=NaN` incident; frequency raised 1 → 2
entries/day **only after** surviving the proof battery (1/day +6.8%, 2/day +10.8% with
higher PF and lower DD).

### 2026-09-13 (2) / 2026-09-13 UTC — Single-strategy consolidation ⚙️

Removed the 5-factor confluence engine and the `scalping`/`day`/`swing`/`swing_rsi` presets;
the monitor now renders the engine's real signal state. Fixed a critical live-only defect
where a ~$22 account could never place a single trade (allocation caps below minNotional).

### 2026-09-12 (6) / (5) / (4) / (2) / 2026-09-12 UTC — Paper/live audit rounds ⚙️

minNotional parity between backtest and live; restart-proof daily entry cap; the backtest was
under-charging fees (a maker rate auto-applied to TP exits), so the edge was overstated —
corrected; `BACKTEST_*` env wiring documented; a 710 MB log cleanup; retry-safe signing (a
retried request reused a mutated params dict); and the live-critical **Ed25519 signature
ordering** fix (`urlencode(sorted(params))` vs aiohttp's insertion order → `-1022`).

### 2026-09-11 (3) / (2) / 2026-09-11 UTC — `intraday_rsi` proven; sync test; full audit ⚙️

The daytrading preset was proved and deployed; the permanent engine↔monitor sync test was
added (`npm run test:sync`); and a full audit covered all 153 functions across 17 Python
modules plus every React component.

---

*Changelog entries before 2026-09-11 are not preserved in this file; the engine README carries
the earliest records. When you change behaviour, add a dated entry here — the engine README
keeps the deep technical detail.*
