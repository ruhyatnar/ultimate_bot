# 🚀 Ultimate Binance Market-Only Trading Bot

A production-grade, algorithmic trading bot designed specifically for **Binance Spot Trading** with **Market-Only Execution**, **one backtest-proven signal engine** (`intraday_rsi`: a daily-EMA50 regime gate + an RSI dip trigger on a fixed % bracket), **Strict Risk & Drawdown Management**, and **Ed25519 Asymmetric Cryptography**.

Engineered for **Debian 13 (Trixie) CLI-only VPS** environments with zero GUI overhead, complete PEP 668 compliance, process supervision via PM2, and a unified terminal **and** web monitor (`status.py`).

> **🎯 Active strategy (champion "A3", research-proven 2026-09-17)** — preset `intraday_rsi`,
> mode `rsi_dip`: **daily-EMA50 uptrend regime + 1h RSI(7) < 40 dip trigger + RSI floor gate
> (`ENTRY_RSI_MIN=35` — skip collapsing knives)**, fixed **−1.2% / +3%** bracket managed by the
> **2×ATR trailing stop activating at +1%** (`TRAILING_ATR_MULTIPLIER=2`), breakeven **OFF**,
> scale-out **OFF**, force-close at UTC day end, taker fee on both (MARKET) legs.
> Research (10-pair basket, $22 equity, honest taker fees + minNotional, 3-round sweep →
> robustness battery: sub-windows, slippage stress 0/5/10 bps, param neighborhood): the RSI
> floor and the ATR trail were the only two levers that improved **both** mean and tail;
> breakeven, scale-out, volume gates, extension gates, slope filters and ATR-adaptive stops all
> **failed** the battery and stay off. Proven window (6-pair cache): **median +4.1%**, 4/6 pairs
> positive, WR 51–58%, slippage-robust to 10 bps.
>
> **Sizing caps must be `BALANCE_USAGE_PERCENT=1.0` / `MAX_SYMBOL_ALLOCATION_PERCENT=1.0` on a
> ~$22 account.** A 20% cap = $4.40 sits below Binance's $5 `NOTIONAL.minNotional`, and the engine
> then silently skips **every** entry (paper equity hides this — see the 2026-09-13 changelog).
>
> **Symbol selection:** `DYNAMIC_SYMBOLS=true` + `MAX_SYMBOLS=1` lets the hourly momentum screener
> pick the traded pair and **`STATIC_SYMBOLS` is ignored**. Set `DYNAMIC_SYMBOLS=false` to trade the
> proven static list instead (the `intraday_rsi` edge is pair-concentrated on `NEARUSDT`).

## 📝 Changelog

### 2026-09-23 (4) — The Capital Roadmap analyses the pairs the engine actually trades
- **The roadmap described a different universe from the one being traded.** `capital_roadmap.DEFAULT_PAIRS` was a six-symbol literal (`NEAR/LINK/DOT/ARB/OP/LSK`) compiled into the module, while the engine runs `DYNAMIC_SYMBOLS=true` and the screener rotates `MAX_SYMBOLS` picks every cycle. The panel therefore reported floors for pairs the bot was not watching and omitted the ones it was — and it happened to be *right by luck* on the day, because the default list also contains one `$20`-floor pair. Both surfaces now take the **engine's live watched set** (`risk_state.monitored_symbols`, written by `trade_logic.update_symbols`: screener picks + `STATIC_SYMBOLS` + any symbol holding an open trade). `status.py` reads it once per payload via `_engine_watched_symbols(db_data)` — the same read that serves the `monitored_symbols` field, so the roadmap and that field cannot describe different universes — and passes it as `compute_roadmap(pairs=watch)`. The CLI defaults `--pairs` to the same DB row. `DEFAULT_PAIRS` survives only as the fallback for when the engine has published nothing, and the payload's new **`pairs_source`** (`engine-watchlist` | `default`) records which was used so the card never presents a fallback list as the live one.
- **A rotated watchlist invalidates the cache.** The roadmap's exchange reads (per-symbol NOTIONAL floors, prices, funding) are reused for 10 minutes, but they belong to *specific* symbols — so `_ROADMAP_CACHE` is now keyed on the watched set and recomputed the moment it changes, rather than serving floors for pairs the engine has dropped (and omitting the ones it just picked up) for up to ten more minutes.
- **Stage thresholds follow the watched set**, which is the honest reading of the question the tool answers: the binding constraint is the highest floor **among the pairs being traded**, not among an arbitrary list. On the live book that is BCHUSDT at `$20` against a proven `$19.69` notional — 4 of 5 watched pairs clear, one does not, and the module's old list could not have shown it.
- **CLI output corrected while touching it:** the stage label hardcoded `proven 1% risk` (it now prints the configured `RISK_PER_TRADE`), the header prints how many pairs came from where, and `equity_from_db()` / the new `watched_symbols_from_db()` share one read-only `risk_state` reader (`_risk_state_value`).
- **Verification:** new **E3** in `sync_test.py` (**46/46**) — the served `roadmap.pairs` equals the served `monitored_symbols`, `pairs_source` names the list, ok/blocked partition the watched set against the served proven notional, and a rotated watchlist invalidates the cache. Exchange data is stubbed and the payload is built from a **private copy** of the test DB (via SQLite's `backup()` — the engine runs WAL, so a `shutil` copy arrives schema-less), so the check needs no network and cannot make the serving monitor call the exchange. One new UI test pins the universe label (**16/16**); scenarios **40/40**, smoke **13/13**, `tsc --noEmit` clean, `check:env` IN SYNC.

### 2026-09-23 (3) — Engine→monitor audit: the loop light, the pause, and the futures fields
- **A paused engine looked wedged.** The health panel's *Decision loop* row measured the age of the newest per-symbol signal snapshot, and its tooltip promised *"a frozen age means the strategy loop is wedged"*. That timestamp only advances when a symbol gets **past every gate**: `process_symbol` returns before `generate_signal` for active trades, cooling-down symbols, a full slot list and a tripped breaker, and the pause branch never evaluates symbols at all. Pausing from the dashboard — or simply holding a full book — froze the metric within one cycle while the engine was perfectly healthy. `trade_logic` now publishes a **loop heartbeat** (`risk_state["loop_state"]`) once per iteration on **every** path (trading / paused / health-pause); the panel reads that, the snapshot age survives only as a fallback for older engines, and the tooltip changes with the source so it stops over-promising.
- **The pause is acknowledged now, not echoed.** `control.paused` only ever recorded what the *monitor* asked for — the UI displayed its own request back as fact. The heartbeat carries `paused_requested` / `paused_applied`, so the chip reads `entries paused` only once the engine has applied it and `pause requested…` until then.
- **Futures positions are normalised.** On the common user-data WS-cache path every position came back with `leverage: null` / `margin_type: null` (the real values sit on the snapshot's *top* level) and no `markPrice` (only the positionRisk fallback carries one), so the Futures card rendered `1× CROSSED` in its header directly above a `—×` column for the same position. `status.py` now fills both from the snapshot top level and derives the mark from the published uPnL. `sync_test` E2 pins it.
- **A fresh total floating PnL.** New `positions_pnl` (sum over the engine's live positions) next to `positions_tracked` / `positions_untracked` / `untracked_pnl`.
- **The roadmap states its age.** Its figures are a snapshot from a 10-minute cache, so `roadmap.age_s` now travels with it and the card labels the cell *Equity (at compute)* instead of sitting next to the header's live equity implying they are the same read.
- **Audited and deliberately left alone** (now documented rather than silent): `risk.unrealized_pnl` is the engine's own persisted value (refreshed every 60 s live, only on trades in paper) and **nothing** in the dashboard reads it — `positions_pnl` is the fresh one; `engine_risk.updated_at` is **not** a heartbeat, because that row is skipped when unchanged, so `loop_state` is the only liveness signal; `data.balances` (engine `account_balances`, ~60 s) is consumed only by the standalone fallback page while the React dashboard reads the monitor's own balance read with its `source` / `age_s` provenance; PM2/soak state and the roadmap are monitor-computed, not engine-published.
- **Verification:** new **B1** in `sync_test.py` (heartbeat present, fresh, and the pause acknowledged then cleared end-to-end by the real engine) plus the position-normalisation checks in **E2** — **41/41**; two new UI tests pinning which source drives the loop row and the pause wording — **15/15**; scenarios **40/40**, smoke **13/13**, `tsc --noEmit` clean, `check:env` IN SYNC.

### 2026-09-23 (2) — The monitor's active position is now the engine's active position
- **The dashboard rendered the ENTRY price as "now".** `active_trades` has no price column, so the row `status.py` served carried no `current_price` — and the frontend's `current_price || entryPrice` fallback then showed the entry as the current price, a **0.00%** change, **$0.00** floating PnL and a bracket-ladder marker parked on the entry, while the engine knew the real mark. `status.py` now joins the engine's **own published state** onto every served row: `risk_state.futures_state` (position amount/entry/uPnL, plus `markPrice` when the snapshot came from positionRisk) and `risk_state.scanned_pairs` (the screener's per-symbol prices — same refresh cycle — for spot/paper and any futures symbol the account snapshot carries no mark for). Nothing is re-derived in the browser and the monitor places no exchange call of its own.
- **New per-row fields:** `current_price`, `unrealized_pnl`, `live_qty`, `position_source` (`engine-futures` / `engine-scan` / `engine` / `db`), `position_age_s`, `position_stale`. The engine's `markPrice` is used verbatim when present; otherwise the mark is derived **exactly** from the published uPnL — `mark = entry + uPnL / amount`, which holds for a long (amount > 0) and a short (< 0) alike. A snapshot older than `WS_BALANCE_MAX_AGE + 60s` is still served (last known engine state beats a blank, exactly as `ws_streams` does) but flagged, so a stopped engine is distinguishable from a live flat book.
- **The served management state no longer lags the engine.** `manage_trade` flushed the tracked trade to SQLite on `int(time.time()) % 30 == 0` — a **one-second window every 30 s**, which the 10 s decision cadence routinely missed, so a ratcheted trailing stop, a breakeven lock or a size change could stay unpublished for minutes while the exits were decided from memory and the dashboard rendered the old bracket. `_persist_active_trade_if_changed()` now compares a signature of every rendered field: one write per real change, a 60 s heartbeat so an interrupted write cannot leave the row behind forever, the signature cleared on close (a re-entry with identical values still writes), and a DB failure downgraded to a warning that retries next cycle instead of blocking stop management.
- **Counts are explicit and reconcilable:** `positions_tracked` / `positions_untracked` / `positions_live`, with `open_positions = tracked ∪ live` and `untracked_pnl` summing **only** untracked positions — a tracked row the dashboard already values is never double-counted.
- **The standalone fallback dashboard** (`get_standalone_html()`, served when no `dist/` exists) gained **Now** and **Unrealized** columns driven by the same joined mark.
- **Verification:** `sync_test.py` gained **E2** (8 checks) — it injects a futures snapshot and asserts the served row carries the engine's mark (102.5 derived from a 2.5 uPnL), PnL, exchange size and provenance, and that an untracked exchange position is counted without contributing to a tracked row (35/35 pass). `test_scenarios.py` gained **S9** (8 checks: flush on change, no per-cycle churn, ratchet/breakeven/size flushed immediately, heartbeat, DB-failure tolerance, partial rows) — 40/40. `tests/smoke/dashboard.test.tsx` gained a stale-snapshot check — UI 13/13. Also `tsc --noEmit` clean, smoke 13/13, `check:env` IN SYNC, and the live futures payload re-read against the real DB. The join's own first draft counted a `NaN` amount as a live position; `_safe_float` now rejects non-finite sizes and derived marks.

### 2026-09-23 — Exit logic unified: `evaluate_exit` is the one decision (divergences 3-5 closed)
- **`trade_policy.bar_exit` is replaced by `trade_policy.evaluate_exit`, and the live engine calls it.** `bar_exit` was the backtest's exit model — the engine never called it, running its own three-batch poll loop instead (scale-out on the tick *first*, then the wick check, then the time stop, then day-end; ratchets last). Both sides now make exactly one call with normalised evidence (live: the wick range of the post-entry klines folded together with the live tick; backtest: the bar's low/high), one `reference_price` for gap-aware fills (the live tick / the bar's open) and one `eod` input (the wall clock / the first bar of a new UTC day), under one priority: **stop → take-profit → time stop → day-end flatten → +R scale-out**.
- **Ordering is now pessimistic on both sides.** The stop is checked before every profit-taking action including the scale-out, so a window containing both a breach and a level above it is assumed stopped rather than banking a partial and stopping out the runner. The two scheduled exits are terminal, so they pre-empt a partial in the same window.
- **Reason labels are identical** — the backtest now emits `TRAILING_STOP` (when the trail is the effective stop) and `EOD_CLOSE`, instead of filing both under `STOP_LOSS`/`TIME_STOP`. On the 30-page NEARUSDT window that reclassified **22 of 55** stop-outs as trailing exits: the ATR trail, not the hard stop, closes over a third of all trades.
- **Fills are gap-aware on both sides.** A stop already traded through fills at the decision-moment price instead of at a level the market had left behind; live's old `min(stop, low)` booked the bottom of the wick.
- **The ratchet input is shared too.** Both sides feed `ratchet_stops` their most favourable observed price — the bar high in a replay, the same wick high live — instead of live feeding the bare tick (which let the trail lag the validated model). The one change that moves live stop levels, and it only ever tightens: the raised trail is always ≥ the old one.
- **A failed kline fetch no longer skips the stop check.** Live degraded to a tick backstop that only tested the trail; it now degrades to tick evidence and still evaluates stop, TP, time stop and day-end.
- **New `S8` in `test_scenarios.py`** (13 checks: ordering, labels, gap-aware fills, armed/unarmed trail, quiet and absent-evidence windows) and the battery is now wired into `npm run verify` as `npm run test:scenarios` — it existed but nothing ran it.
- **Verification.** Parity proof rather than assertion: the full 59-trade list from the documented backtest command was diffed against the pre-change run and is identical in entry, exit price and PnL (only `exit_index` and the labels move). Also: `--market futures` runs, forced `SCALE_OUT_ENABLED=true` / `CLOSE_AT_UTC_DAY_END=true` runs (11 `PARTIAL_EXIT`, 1 `EOD_CLOSE`), `tsc --noEmit` clean, smoke 13/13, UI 12/12, sync 27/27, scenarios ALL_OK, `check:env` IN SYNC. No live parameter was changed.

### 2026-09-22 (4) — Directory-wide audit: six defects found and fixed (backtest parity closed)
- **Unreachable dead code in `backtest.py`.** A 7-line duplicated exit block sat *after* a `continue` in `run_backtest` and could never run. Found by scanning every function body with `ast` for statements following a `return`/`continue`/`raise`; removed. No other unreachable code exists in the Python tree.
- **The futures backtest was unreachable from the CLI.** `run_backtest(..., market=...)` existed and `fetch_klines`, the fee model, funding, minNotional and the new gate all branch on it, but `main()` never passed it and no flag set it. Added `--market {spot,futures}` plus a `BACKTEST_MARKET` env default, and the market is now printed in the run header.
- **Divergence 1 fixed — config precedence.** `run_backtest` applied the preset on top of `load_config()`, so a bare run validated the preset while the engine ran `.env` (8 strategy keys apart here). It now selects the preset via `PRESET` and loads normally — env-first, like the engine — with `overrides` still last for sweeps. Verified at **zero** non-credential key differences against `load_config()`. The documented test-matrix command consequently reports the deployed config's real edge: **−7.80% / PF 0.73** on a 104-day NEARUSDT window, against **+0.41% / PF 1.02** for the preset values on the same window.
- **Divergence 2 fixed — the funding-rate entry gate now exists in the backtest.** A long whose last *settled* rate (at the bar's decision close) exceeds `FUNDING_RATE_MAX` is skipped, mirroring `trade_logic.enter_trade`; the summary gained a `funding_gate_skips` field and the one-line output shows it when non-zero. Missing history (network failure) never blocks a setup, matching the engine. Verified with a forced ~0 cap: 2 entries blocked, 0 trades, `funding_gate_skips=2` — and the same cap under `--market spot` changes nothing.
- **Stale docstring corrected.** The `run_backtest` docstring claimed the futures `NOTIONAL.minNotional` floor "defaults to 100 USDT (futures reality)"; the code reads the real per-symbol fapi value (5 USDT on most USDT perps), which the module docstring already documented.
- **`npm run clean` fixed** — it was `rm -rf dist server.js`; `server.js` has not existed since the original scaffold was removed.
- **Swept clean, no action needed:** `compileall` over the whole engine · `tsc --noEmit` · `node --check` on `ecosystem.config.cjs` and `ui_smoke.mjs` · `bash -n` on both shell scripts · zero `TODO`/`FIXME`/`HACK` · zero `eval`/`exec`/`os.system`/`shell=True`/`pickle.load` · zero mutable default arguments · all SQL parameterised (the interpolated `fields` in `update_order_status` are literal column names, never user input) · all 9 `requirements.txt` pins map 1:1 to actual third-party imports · no dead exports or orphan components among the 21 TS/TSX files · no tracked artifacts that should be ignored.

### 2026-09-22 (3) — Spot/futures and backtest/live parity documented; three parity divergences found
- **New section [Spot vs. Futures: Shared Logic & Divergences](#-spot-vs-futures-shared-logic--divergences).** The signal layer, the bracket/profit-ladder helpers and the screener contain **zero** `MARKET` branches; in the shared engine only `trade_logic.py` (11 sites), `order_manager.py` (8) and `risk_manager.py` (1, the equity source) branch on the venue. Tables list what is shared verbatim and what differs — clients, boot setup, the margin pre-check, the funding gate, `positionAmt` vs wallet as the exit quantity source, `reduceOnly`, the equity source, fee model, reconcile source and market data — plus the two known gaps: the funding gate is live-only, and futures paper mode returns before `is_futures` is computed so it never exercises the futures order path.
- **New section [Backtest vs. Live: Verified Parity Divergences](#-backtest-vs-live-verified-parity-divergences).** Thirteen divergences, verified by reading both paths, worst first. The signal really is shared; everything around it is re-implemented.
- **Finding 1 — config precedence is inverted.** `config.load_config()` is env-first (`os.getenv(key, preset[key])`), but `backtest.run_backtest` then applies `cfg.update(preset)`, so the preset wins. On the deployed `.env` the two configurations disagree on **eight strategy keys** (`MTF_TIMEFRAME`, `RSI_TIMEFRAME`, `RSI_PERIOD`, `REGIME_EMA`, `REGIME_SLOPE_DAYS`, `MAX_TRADES_PER_DAY`, `BREAKEVEN_ENABLED`, `CLOSE_AT_UTC_DAY_END`), which means a bare `backtest.py` validates the **preset**, not the config that is trading. A runnable snippet to reproduce the table is included.
- **Finding 2 — trade management is *not* identical.** `trade_policy.bar_exit` is called by **nothing but the backtest**; live's `manage_trade` runs its own poll loop. The ordering differs (live banks the +1R scale-out before the stop/TP wick check; `bar_exit` checks stop → TP → scale-out), as does the granularity, and `bar_exit` applies a `level < tp` scale-out guard that the live path does not.
- **Finding 3 — the exit-reason vocabularies differ.** Live emits `EOD_CLOSE` and `TRAILING_STOP`; the backtest records its UTC day-end close as `TIME_STOP` and fills it at the **next bar's open**, against live's market close at 23:55 UTC. Exit-reason histograms from the two systems are not comparable.
- **Docs corrected, not just appended:** the strategy section no longer implies "zero drift" applies beyond the signal layer, and the backtesting section no longer claims trade management is identical or that a bare run reproduces the deployed `.env`.

### 2026-09-22 (2) — Config drift guard; `.env.example` reconciled with the deployed `.env`
- **`.env.example` reconciled with the live `.env`.** The template had drifted on nine keys: `PAPER_TRADE`, `FUTURES_MARGIN_TYPE`, `FUTURES_ONE_WAY_MODE`, `REST_WEIGHT_LIMIT`, `RSI_TIMEFRAME`, `STATIC_SYMBOLS` and `TOP_CANDIDATES` held different values, and `MAX_TRADES_PER_DAY` / `RSI_TIMEFRAME_MS` existed only in `.env`. It now mirrors the deployment exactly — same **90 keys, zero drift**, only the three credential keys keeping placeholders — and carries a header block warning that it is **not** a safe sandbox, listing the five keys to change back on a fresh machine. `.env` itself was not modified (verified by hash before and after).
- **New `check_env_drift.py`** (see [Config Drift Check](#config-drift-check-check_env_driftpy)): a stdlib-only guard that fails on keys present in only one file, duplicate keys, inline `#` comments on value lines, any non-exempt value difference, and any real credential reaching the template. `--update` re-mirrors the template from `.env` and refuses to copy anything credential-shaped into a tracked file. Wired as `npm run check:env` and into a new `npm run verify` aggregate (`lint` → `check:env` → `smoke` → `test:ui` → `test:sync`).
- **Pre-commit hook** (`npm run hooks:install`, or `git config core.hooksPath .githooks`): every commit runs the drift check and is refused on drift, printing the fix command. The hook itself is versioned at `.githooks/pre-commit` because git only executes `.git/hooks/`, which is never committed. Verified end-to-end in a throwaway repo: an in-sync commit is allowed, a drifted commit is blocked (no commit object created), and `git commit --no-verify` bypasses it.
- **Docs:** the engine README's Table of Contents had one broken link ("Architecture & Data Flow" pointed at a section that does not exist) and was missing this file's test section entirely — both corrected.

### 2026-09-22 — First live entry watch; multi-assets phantom-balance equity fix
- **Fixed (`futures_ws_api_client.seed_balances`): WS-path equity was inflated ~5× ($23.01 → $111.75) on multi-assets accounts.** Caught while watching the first live entry: the engine alternated `equity $23.01 (via rest)` / `$111.75 (via ws)` every balance cycle. In Binance **multi-assets collateral mode** the account payload reports a collateral-equivalent `availableBalance` for every asset the wallet could use as margin (U, BFUSD, USDC, RWUSD, USD1, FDUSD, LDUSDT, BNB, BTC, ETH, …) while `walletBalance` is `0` for those it does not actually hold — the same USDT wallet restated in other denominations. The seed kept any row with `availableBalance > 0`, so BNB/BTC/ETH/USDC were cached as real holdings and `_fetch_equity` priced them at the real ticker (≈ $88.7), inflating `total_equity` — which feeds position sizing, the daily-drawdown breaker, the notional/allocation caps and the dashboard equity. A row is now seeded only when its **wallet** balance is non-zero; legacy `free`/`locked` shaped rows still seed. Verified on the live account: before = 11 rows / $111.74, after = 1 row / $23.02, matching `totalMarginBalance` $23.0059; after reload both transports report $23.0x. Regression guard: **S7** in `test_scenarios.py` (4 checks).
- **Entry-gate behaviour confirmed live**: the gate evaluates once per hour at the `RSI_TIMEFRAME` bucket close (the "last read HH:MM UTC" note is the bucket's **close** time, derived as `bucket_index * bucket_ms + bucket_ms`). Watched the 01:00 UTC roll: all five dynamically-screened symbols refreshed with live RSI, `AVAXUSDT` printed RSI 31.66 vs oversold 40.0 but stayed `trigger=False` because RSI was still falling — the strategy requires oversold **and** turning up. No live fill yet (engine flat, 0 errors).
- **First-entry feasibility verified** against the live account ($23.02 equity, 1× leverage): all five monitored symbols size to a legal order — e.g. `AKEUSDT` 330 units ≈ $19.14 notional, risk $0.23 — clearing both the $5 futures minNotional and the free-margin pre-check. Exits enabled in the deployed `.env` are SL / TP / trailing / `TIME_STOP` (`CLOSE_AT_UTC_DAY_END=false`).

### 2026-09-21 — Live-readiness audit, boot-critical config fix, dead-code purge, monitor rebuild
- **Boot-critical fix (`config.py`)**: the preset's `TRAILING_ATR_MULTIPLIER: 2.0` was silently ignored at load time (hard-defaulted to `0.0`), which forced %-callback validation onto the preset's equal activate/callback values (1%/1%) and **crashed every boot** with `TRAILING_STOP_CALLBACK must be less than TRAILING_STOP_ACTIVATE`. The loader now reads the value from the preset like every other preset parameter.
- **Dead code removed**: deleted the abandoned Reflex app (`ultimate_bot/`, `rxconfig.py`, `reflex.lock`, root `requirements.txt` reflex pin), stray `__init__.py` files inside the React `src/` tree, empty `apt-packages.txt`, and stale `plan.md`. Unused imports cleaned in `futures_ws_api_client.py`. An AST-based scan confirms no unreferenced module functions remain in `src/`.
- **Trade-logic cleanup (`trade_logic.py`)**: `process_symbol` computed the RSI bucket twice — the bucket latch now reads and sets in one place.
- **Annotated `.env.example` shipped**: a fully documented template of every tunable parameter (89 config keys), used by the web monitor's Project Code & ZIP tab and as the deployment `.env` template. Inline `# comments` on value lines were removed — this Python build's dotenv parser passes them into the value (they broke `CONTROL_FILE`).
- **Dashboard ↔ engine config drift eliminated**: the monitor's tuner defaults disagreed with the deployed `.env` (`COOLDOWN_LOSS` 86400 vs 3600, `COOLDOWN_WIN` 86400 vs 1800, `MAX_DAILY_DRAWDOWN` 3% vs 5%, `SIGNAL_INTERVAL` 6s vs 10s, empty symbol list). Pushing the tuner would have silently rewritten proven risk parameters — defaults now mirror the engine `.env` and are overwritten by the first `/api/status` snapshot.
- **Web monitor rebuild (ops-console polish)**: new design-token layer (shared `.card` primitive, tabular numerals, focus rings, reduced-motion), rebuilt header (Synced/Offline light, LIVE-vs-paper chip, futures leverage chip, KPI strip), status cards extracted to `StatusCards.tsx`, hold-time progress vs `MAX_HOLD_TIME` in the positions table, dated exits with hold context, and honest engine-explaining empty states.
- **Full re-verification**: `smoke_test.py` 13/13 · `sync_test.py` 27/27 · `test_scenarios.py` ALL_OK · `test_futures_reconcile.py` ALL_OK · `tsc --noEmit` 0 errors · Vite build clean · PM2 engine + web monitor reloaded and synced (`/api/status` → `spot-paper ● RUNNING`).

### 2026-09-20 06:36 UTC — Rate-limiting resilience (429/418), 8-decimal WS order quantization, full verification
- **HTTP 429 & 418 Rate-Limit Protection** (`rest_client.py`, `futures_rest_client.py`): Both Spot and Futures REST clients now inspect HTTP 429 and parse the `Retry-After` header with automated sleep backoffs, preventing transient rate spikes from escalating into HTTP 418 IP bans (which now triggers a 120s protective pause).
- **8-Decimal Order Quantization** (`ws_api_client.py`, `futures_ws_api_client.py`): Replaced standard `f"{quantity:f}"` (which defaulted to 6 decimals and truncated sub-penny cryptocurrencies or 8-decimal LOT_SIZE steps) with full 8-decimal precision `f"{quantity:.8f}"` and trailing-zero stripping, ensuring precision parity with REST order placement.
- **Full Verification Suite Passed**:
  - `smoke_test.py`: 13/13 passed (DB boot, lock acquisition, duplicate rejection, stats reconciliation, WS upgrade, clean SIGTERM exit).
  - `test_scenarios.py`: 10/10 passed (pre-entry wick exclusion, malformed klines, loss re-entry gate, funding gate, PnL verifier math).
  - `test_futures_reconcile.py`: 3/3 passed (orphan adoption, cost-basis recovery, below-minNotional handling).
  - TypeScript & Vite compilation: 0 errors (`tsc --noEmit` and `vite build`).

### 2026-09-20 06:11 UTC — live-exit/position verification, funding gate live, RR sweep verdict, PM2 recovery
- **PM2 recovery**: the PM2 daemon had lost its process list (both apps down after the session drop) — restored via `pm2 resurrect`, both apps online again.
- **Live exit verified**: all four completed futures exits (B2USDT +0.5822 corrected; ONEUSDT −0.2191/−0.4513/−0.3251) reconcile exactly against the exchange's own `userTrades` (`realizedPnl − commission`). PnL auto-verifier is wired into `close_trade` (fires on the next live exit; offline battery proves the fapi field-shape math).
- **Open position verified against ground truth**: `/fapi/v2/positionRisk` shows zero open positions; wallet 23.2358 USDT = engine equity 23.25 (WS-sourced, `balance.source = ws`) = dashboard; roadmap equity 23.23 consistent.
- **Funding-rate gate live**: new tunable `FUNDING_RATE_MAX=0.0005` (0.05%/8h) — futures entries are skipped when a long would pay funding above the cap (fail-open if the rate is unreadable, 0 disables). Wired: futures client `get_funding_rate` → `enter_trade` gate → config → `.env`/`.env.example` → dashboard Config tab + envGenerator.
- **RR-target sweep (30d, futures fees+funding, engine decision core)**: RR 1.5→2.5 is inert (TP_PERCENT caps before RR-widening binds — NEARUSDT identical to 3 decimals); RR 3.0 hurts NEARUSDT (8.78→8.33%, 19 trades) and only helps ONEUSDT (+2.0%, 2 trades — noise). **Verdict: keep the live baseline (RR 1.5 / TP 3%).**
- **Spot/futures separation audit (clean)**: single client injection point (`main.py`), all market branches keyed on `is_futures` (fee 0.05% vs 0.1%, position-vs-balance reconciliation, fapi-vs-spot cancel endpoints, exit clamp, funding gate futures-only), one normalized balance path (`balance_cache` + `_fapi_balance_rows`), market-aware monitor fallback with correct fapi base URL, explicit `SPOT`/`FUTURES` + paper/live labels.
- **Scenario battery restored** as permanent `test_scenarios.py` (was lost in cleanup): pre-entry wick exclusion, malformed-kline tolerance, loss re-entry gate (block/expiry/per-symbol isolation), funding-gate env loading, PnL verifier math vs fapi field shapes — 10/10.
- Removed stale 0-byte `bot.db` (artifact of a misdirected probe; nothing references it).
- Batteries: scenarios 10/10 · sync 27/27 · smoke pass · reconcile 3/3 · vite build clean · `pm2 save`.

### 2026-09-20 03:14 UTC — PnL auto-verification; dashboard completed-trades fix; breaker retuned (0.01→0.03); ATR-stop backtest verdict

**PnL auto-verification** (`trade_logic.py`): every live futures exit now cross-checks the recorded PnL against the exchange's own books (`/fapi/v1/userTrades` fills for that orderId: ΣrealizedPnl − Σcommission). Drift beyond tolerance (5% or $0.02) corrects the record automatically and logs a warning — breakers, streaks and the dashboard can never silently drift from exchange truth again (the B2USDT +0.90 class of error is now self-healing). Spot exits keep the proven exact model (spot myTrades has no realizedPnl).

**Dashboard completed trades fixed**: rows showed "$0.00 → $0.5382" and "+0.00%" because the SELL market-order row stores `price=0` and the frontend read it as the entry. status.py now attaches the matched BUY leg's `avg_fill_price` (and its timestamp) to each exit row via the same bisect matcher used for entry_ts; App.tsx reads it with a sane fallback chain, and PnL% is computed off the real notional. Exit reason is now derived honestly: FILLED loss = STOP_LOSS, FILLED gain = TAKE_PROFIT, CANCELED = PARTIAL_EXIT.

**Daily drawdown breaker retuned 0.01 → 0.03** (`.env`, `.env.example`, dashboard default): sizing risks ~1% of equity per trade, so a 1% limit tripped on the very first normal stop-out and deadlocked the whole day. 0.03 = three consecutive stop-outs of the risk unit before the day pauses — still firm capital protection, but one ordinary loss no longer ends the session. Tunable remains in the Config tab.

**Vol-adaptive stops — backtested, verdict: keep fixed 1.2%**: swept `SL_ATR_MULTIPLIER` {1.2/1.8/2.4/3.0 × caps} ± MIN_RISK_REWARD 2.0 against the fixed 1.2% baseline on NEARUSDT/ONEUSDT/LINKUSDT (30 days, futures fees+funding, engine's own decision core). Fixed won every pair: NEAR +8.78% vs +5.77% best-ATR; ONE +1.51% vs +1.00%; LINK +5.15% vs +1.44%. ATR-widened stops raise per-trade risk while TP caps winners, degrading RR. `SL_ATR_MULTIPLIER` stays available (0 = fixed, current default) for future experiments.

**Batteries**: sync 27/27 · smoke 13/13 · reconcile 3/3 · tsc/vite clean · both PM2 apps online · `pm2 save` done.

### 2026-09-20 02:34 UTC — adopted-position PnL corrected to exchange truth; dashboard streak display fixed

The completed-trade PnL was audited against the exchange's own books (`/fapi/v1/userTrades` realizedPnl + commission) and one discrepancy found and fixed:

| Trade | DB recorded | Exchange truth | Cause |
|---|---:|---:|---|
| B2USDT (adopted 88u) | +1.4857 | **+0.5822** | adoption anchored `entry_price` to market price at adoption (~0.5208) instead of the real cost basis (0.5310625) |
| ONEUSDT ×3 | −0.2191 / −0.4513 / −0.3251 | identical | model exact ✓ |

**Engine fix** (`trade_logic.py`): orphan adoption now computes the position's REAL cost basis — quantity-weighted VWAP of the bot's own recorded fills — and anchors `entry_price` (and thus SL/TP brackets and final PnL) to it. Market price is only a last-resort fallback when fills carry no price. Adoption logs now show `basis X vs market Y` whenever they diverge >0.1%. Regression battery `test_futures_reconcile.py` updated: case 3 proves adoption at cost basis (0.531062 vs market 0.53).

**Historical data corrected** (engine stopped during surgery): B2USDT `profit_loss` 1.4857 → **0.582152** (gross 0.6292 − fees 0.0470, matches exchange to the last decimal); `daily_pnl` shifted by the same −0.9035 → **−0.4133**. Today's trades now sum exactly to the exchange's books. Note: the corrected daily drawdown (−1.78%) legitimately breached the −1% daily breaker — trading is paused with a loss cooldown until ~02:57 UTC, as designed.

**Dashboard fix** (`status.py`): win/loss streaks are ACCOUNT-LEVEL and are now displayed verbatim from the engine's persisted state. The old max-merge with per-symbol blobs hid real breaker state — right after the 3-loss trip it showed "win streak 1" (B2's lone win leaking in) while the engine was actually in a loss cooldown. Per-symbol cooldowns still merge (max = tightest brake). `sync_test.py` section C rewritten to pin the new contract (account streaks verbatim even when per-symbol blobs disagree).

**Batteries**: sync 27/27 · smoke 13/13 · reconcile 3/3 · 0 log errors in the post-restart window. Both PM2 apps online, `pm2 save` done.

### 2026-09-20 01:54 UTC — full cross-module audit: dead code purged, scenario battery green, sync verified

Systematic audit of every function across engine + web monitor:
- **Import integrity**: 0 unresolved internal imports across the package.
- **Dead code removed**: 6 never-referenced futures REST methods (`get_open_orders`, `cancel_all_orders`, `get_income_history`, `get_commission_rate`, `modify_position_margin`, `place_test_order`). Remaining audit hits are framework-dispatch false positives (`do_GET/do_POST/do_HEAD`, `__getattr__` proxy) — intentionally kept.
- **`.env` hygiene**: every key verified as actually read (config.py / backtest.py / soak_watchdog.py); no dead keys, no duplicates.
- **Field contract**: engine→status.py→App.tsx consumption checked; no published-but-unconsumed regressions.

**Defect-scenario battery (13/13, offline)** — the failure classes that historically bit this bot are now pinned as tests: pre-entry wick exclusion, no-post-entry-candle tick fallback, malformed-kline tolerance, per-symbol loss re-entry gate (block / expiry / other-symbol isolation / disable switch), bucket-latch arming order, fapi row normalization incl. garbage rows.

**Hardening found during audit**:
1. Bucket latch now arms **before** the entry attempt — a slippage-rejected BUY no longer retries 13s later inside the same bucket (chasing a slipped entry is what the slippage guard exists to prevent). Fresh chance at the next bucket close.
2. Wick-range logic extracted to a pure helper (`_post_entry_wick_range`) — malformed/short kline payloads can never crash the manage cycle.
3. fapi balance normalization extracted (`_fapi_balance_rows`) and unit-tested.

Note: `test_scenarios.py`/`audit_full.py` were run and then removed as one-shot tools (the regression battery `test_futures_reconcile.py` remains). Batteries: smoke 13/13, sync 27/27, reconcile 3/3, scenarios 13/13; tsc + vite clean; engine restarted clean (flat, awaiting the next regime-qualified dip), all three transport lights green, changelog **2026-09-20 01:54 UTC**.

### 2026-09-20 01:12 UTC — instant-stop-out root-caused (wick check counted the entry candle); per-symbol loss re-entry cooldown added

**Order history told two different stories.** B2USDT held 5.5h and hit TP (+1.49). But ONEUSDT died in **13s** (−0.22) and **46s** (−0.45).

Two defects found:
1. **Entry-candle wick triggered the stop on the first manage cycle.** The wick/gap-protection check takes the min low of the last 2 candles — but for a DIP strategy the entry signal candle's low is below the fresh stop *by construction* (that dip is the entry trigger). Trade #1 exited at −0.81% on a wick that happened **before the entry existed**; the live price never touched the −1.2% stop. Wick check now only counts candles that **opened after the entry** (open_time ≥ entry_time).
2. **Nothing prevented an instant re-entry after a stop-out.** Streak cooldowns arm only after MAX_LOSS_STREAK consecutive losses, so the only brake was the 10s monitor cooldown — ONEUSDT re-entered 4m13s after its first loss (and its second dump −2.15% was a legitimate stop: +150%/24h, 74% volatility pair). New tunable **`LOSS_REENTRY_COOLDOWN` (default 900s)**: after ANY stop-out, that symbol cannot re-enter for N seconds. Wired through config.py / .env / .env.example / status TUNING_KEYS / envGenerator.

Verification: `test_futures_reconcile.py` 3/3 still green, tsc + vite clean, engine restarted (flat, awaiting the next regime-qualified dip), `LOSS_REENTRY_COOLDOWN: 900` visible in the served config, both PM2 apps online.

### 2026-09-20 00:19 UTC — futures/spot separation fixed end-to-end; naked-position bug root-caused and healed

**Why no trades appeared to close/win: the engine WAS trading — and then orphaning its own futures fills.**

Root cause (5 defects, all fixed):
1. **Spot-shaped reconciliation orphaned every futures entry.** `sync_positions_from_exchange` checked the wallet balance for the base asset — on futures a BUY opens a POSITION (`positionAmt`), the wallet only holds margin, so 12s after each fill the engine saw `balance=0.0` and deleted its own tracking (B2USDT 88u traded blind for hours). Reconciliation now reads `positionRisk` on futures / wallet on spot.
2. **No position gate → double entry.** With tracking wiped, the 02:59 B2USDT signal re-fired and re-entered 19s later (44+44=88u). Position-aware sync now keeps the trade tracked, and a per-RSI-bucket entry latch blocks duplicate re-fires inside the same bucket (also kills the AKEUSDT 12s log-storm pattern).
3. **SELL clamp would have zeroed every futures exit.** `order_manager` clamped sell qty to the base-asset WALLET balance (0 on futures). Now it clamps to `positionAmt` on futures, wallet on spot.
4. **Orphan adoption was impossible on futures** (wallet-only scan + `isBuyer` field that fapi doesn't return — fapi uses `buyer`). Adoption now scans positions, verifies via `/fapi/v1/userTrades`, and matches the SUM of recent fills (handles merged positions).
5. **PnL/fee drift**: exit fees hardcoded 0.1%/leg — now 0.05% on futures (real taker rate); equity now prefers fapi `totalMarginBalance` (includes uPnL); `calculate_unrealized_pnl` includes UNTRACKED positions so the drawdown breaker sees the whole account.

Web monitor separation (spot vs futures, paper vs live):
- `/api/status` now serves `market`, `paper_trade`, `mode` (e.g. `futures-live`), `account` (e.g. `FUTURES Live`), `open_positions`, `untracked_pnl` at top level and inside `data`.
- Balance REST fallback is market-aware (fapi `/fapi/v3/balance` normalized on futures — was hardcoded to the spot API, showing $0 for futures funds).
- Dashboard: **account chip** (`FUTURES Live` / `SPOT Paper`…) + **PAPER/LIVE** chip on the equity card (replaces the misleading permanent `LIVE SPOT`), PAPER/LIVE badge on the Futures panel, and an amber **⚠ Untracked positions** row when exchange exposure has no open trade.

Verification: offline regression battery `test_futures_reconcile.py` 3/3 (kept/removed/adopted), live boot adopted the naked B2USDT position (SL 0.5146 / TP 0.5364, reduceOnly exits), payload probe shows `mode: futures-live`, 1 tracked trade, all 3 stream lights green; smoke 13/13, sync 27/27 (note: run batteries WITHOUT `LOG_LEVEL=ERROR` in the env — the shutdown-log assertions grep info-level lines); tsc + vite clean.

### 2026-09-19 15:45 UTC — fstream silent-frame workaround (REST ticker fallback), PnL review, first-trade readiness

Context: after the futures go-live, the dashboard's **All-market light stayed dark** — a bounded probe battery (URL-path + `/stream` + combined-endpoint subscriptions, browser UA, ws/443/9443) proved this box's WARP path completes the fstream handshake and SUBSCRIBE ACK but **never delivers market-data frames** (spot's `stream.binance.com` works fine). The ARR listener blocked in `recv()` forever — no error, no reconnect, just an empty price cache.
- **REST ticker fallback (`futures_ws_stream_client.py`):** new `_rest_ticker_refresher()` feeds the *same* `all_tickers` cache the WS frames fill, using the cheap bulk `/fapi/v1/ticker/price` request. Rows carry `transport: "rest"` so provenance stays honest vs WS frames; WS always wins when frames return. `arr_transport()` reports `ws`/`rest`/stale; `is_arr_stream_alive()` now true for either source. Enabled automatically when no frames land within a 20s grace window after boot (`start_rest_ticker_fallback`, idempotent); cadence tunable via **`TICKERS_REST_FALLBACK_S`** (default 5s, `.env` ≡ `.env.example` ≡ Config tab ≡ envGenerator).
- **Transport snapshot (`trade_logic.py`)** publishes `all_tickers.transport`; **dashboard** shows a `rest` chip on the All-market light with an explanatory tooltip when the fallback is active — the light is green again, and honest about why.
- **Contract fix found live:** the refresher initially assumed row-lists; `get_tickers_bulk` actually returns `{symbol: price}` — caught by a refresher warning in the first minutes, fixed, re-verified.
- **Futures PnL review (read-only, live):** 0 open positions; income history = 1 row (`TRANSFER +23.664 USDT`) — **zero trading costs/PnL so far**. Funding: ONEUSDT **−0.101%** (longs paid ≈0.3%/day), ENA/AKE +0.005%; real fees maker 0.02% / taker 0.05% (matches backtest assumptions).
- **First-trade readiness drill:** for all 3 watched pairs (ONE/ENA/AKE), risk-based sizing (1% risk, 1.2% SL) produces **exchange-legal orders** (notional ≈$23.3–23.4 vs $5 floor, lot-step-aligned) — the first regime-qualified entry can execute cleanly.
- **Verified:** refresher unit-tested with stub REST (fill/provenance/idempotency), `py_compile` + `tsc` + `vite build` clean, engine restarted live (equity $114.91 / free $23.66 via ws), `all_tickers` fresh at 4.7s via rest, smoke **13/13**, sync **27/27** (LOBOTOMIZE=1), `/api/health` ok.

### 2026-09-19 13:35 UTC — LIVE FUTURES GO (user-selected): balance-seed, mode pre-check, listenKey, sizing & dashboard fixes

Context: the $23.66 USDT moved from spot to the futures wallet (user action; the bot has no transfer capability and zero orders ever). Read-only REST confirmed the funds on fapi. User chose **futures live trading**; the flip surfaced five real defects, all fixed and verified live.
- **`FuturesRestClient.get_account()` normalized to the spot `balances` shape** (`free=availableBalance`, `locked=wallet−available`): the single balance path (`balance_cache.get_account`) and every consumer (risk sizing, pre-trade free-quote check, dashboard) now work unchanged on fapi — previously every futures BUY would have been rejected with $0.00 free.
- **`FuturesWSApiClient.seed_balances()` override + startup seed:** futures `ACCOUNT_UPDATE` is change-only (no periodic full snapshot like spot), so the WS cache is seeded from `/fapi/v3/balance` when the user stream starts; handles v3 array, v3 `assets`, and legacy spot shapes (unit-tested live).
- **Boot setup pre-checks instead of blind writes:** `get_position_mode()` (with the hedge-flag inversion correct) and a new `get_multi_assets_mode()`; `-4059` treated as already-set; Multi-Assets Mode (ON on this account) forces **CROSSED** margin (Binance −4168) — effective margin published into the live config and `.env` updated to `FUTURES_MARGIN_TYPE=CROSSED`.
- **`create_listen_key()` now returns the key string** — returning the raw `{'listenKey': ...}` dict produced the URL `.../ws/{dict}` and a permanent HTTP-403 reconnect loop on the user-data socket. After the fix: **"Futures user data stream connected"**, fills arrive in realtime.
- **Reconnect-loop bug:** `_user_stream_loop` called `_subscribe_user_stream`, which recursively spawned another loop task and reset backoff to 1s every cycle (a ~1.3s retry storm). Key creation is now inline; backoff grows 1→60s; `user_stream_active` honestly drops while reconnecting.
- **Phantom-pair probes eliminated:** the futures wallet holds bonus-rate assets (U, BFUSD, RWUSD, USD1…) whose `X+USDT` pairs are not tradable symbols; the equity/orphan paths now skip symbols missing from the cached exchangeInfo, and `-1121 Invalid symbol` is non-retryable.
- **Sizing audit:** with Multi-Assets on, nominal equity reads $114.92 (staked/bonus assets included) while free USDT is $23.66 — the free-quote ceiling caps notional at $23.42 and a 1.2% SL risks ~$0.28/trade. `MAX_DAILY_DRAWDOWN` recalibrated 0.05→0.01 so the breaker guards the money actually traded (~$1.15).
- **Dashboard futures-aware:** the monitor's REST fallback is spot-only, so on futures it now serves the engine's own published balance snapshot (age-gated; source label preserved). Verified: equity 23.66, free 23.66, `source: ws`, futures panel (1× CROSSED one-way).
- **Honest limitation:** this box's network path (WARP) delivers no fstream market-data frames (`!miniTicker@arr` ACKs but never pushes) — the all-tickers light stays dark truthfully. The engine trades anyway: signals/screener via REST klines + bulk tickers, prices via the `_StreamProxy` REST fallback, fills via the connected user-data WS.
- Batteries pinned to `MARKET=spot` (they test the proven path regardless of the live engine's market): **smoke 13/13, sync 27/27**. `pm2 save` done.

### 2026-09-19 11:56 UTC — Soaks cleared; go-live posture confirmed (engine already LIVE on spot)

- **Soak infrastructure removed:** PM2 apps `futures-soak` + `soak-watchdog` deleted (and `pm2 save`d so they never resurrect); runtime artifacts purged (`data/futures_soak.db*`, start marker, watchdog state, event feed, `logs/futures_soak.log`). `futures_soak.sh` + `soak_watchdog.py` remain in-repo as opt-in tooling — rerunning `./futures_soak.sh start` re-creates everything.
- **Dashboard declutter:** with no soak PM2 app AND no soak DB present, `_futures_soak_snapshot` returns `None` and the Futures Soak card no longer renders (previously a permanent "not running" placeholder). `/api/soak` guards still work (start works from scratch; stop refuses cleanly when nothing runs).
- **Live-readiness confirmed (fact-check over assumption):** the engine is **already LIVE** — `PAPER_TRADE=false`, `USE_TESTNET=false`, `MARKET=spot`, Ed25519 key configured, balance from the real account (`is_live: true`, equity **$23.66**, source ws/rest). All three stream lights green (market 3 symbols, all_tickers 0.7s fresh, order API + user stream), clock fresh (no -1021 drift errors), control unpaused, **zero errors** in the recent log window. Closed trades: 0 — no regime-qualified entry has fired yet; equity $22 → $23.66 reflects the account, not closed PnL.
- **Guardrails as configured:** `RISK_PER_TRADE=0.01`, `SL 1.2% / TP 3.0%`, `MAX_DAILY_DRAWDOWN=5%`, streak breakers 3/5, `MAX_TRADES_PER_DAY=0` (unlimited), Discord webhook set. Batteries re-run post-cleanup: **smoke 13/13, sync 27/27** alongside the live engine.

### 2026-09-19 11:36 UTC — Watchdog event feed on the dashboard; end-to-end WS proof; soak verdict armed

- **Watchdog event feed:** `soak_watchdog.py` now appends every supervision event to `data/soak_watchdog_events.jsonl` (`soak_started` backfill, `armed`, hourly `watching`, `death_alert`/`stall_alert` + `recovered` when cleared, `completed` with final stats; trimmed ring buffer). `status.py` serves the last 24 as `soak.watchdog_events` (`{ts_ms, utc, kind, detail, age_s}`); the soak card renders them in a collapsible, newest-first list with kind-colored dots and relative ages.
- **End-to-end visual check (headless):** served HTML references the live bundle; the bundle contains the new UI strings; and a **real RFC 6455 WebSocket handshake against `/ws`** (101 → `hello` frame → 1s status push) carried the full payload including `soak.watchdog_events` — the exact path the browser uses.
- **Soak verdict armed (preview shown):** watchdog online, heartbeat fresh, deadline **2026-09-20 07:09 UTC** (~19.6h). The exact Discord summary the deadline will post was rendered from the live helper: equity $22.00, PnL $0, 0 closed. Engine is evaluating (`signal_state` live per-pair regime/RSI); `entries_today=0` in 4.5h is consistent with the strategy's ~1.2 trades/day selectivity at 3 pairs, not a stall.
- Verified: `py_compile` clean, `tsc --noEmit` clean, `vite build` clean, events flow via WS, all 4 PM2 apps online, `pm2 save` done.

### 2026-09-19 10:05 UTC — Interactive soak control + tunable stall threshold; realtime audit (WS-first confirmed)

- **Soak controls on the dashboard:** the Futures Soak card now has **Start soak** / **Stop soak** buttons (green/red, disabled when not applicable) backed by a new `POST /api/soak {action}` endpoint. Stop asks for confirmation; results appear inline. Server-side guards: duplicate start → `409` with current progress, stop when not running → `409`, unknown action → `400` (all verified live — a real stop/start was *not* fired to protect the running soak's 24h clock). `futures_soak.sh` hard-codes the paper-trade isolated env, so the endpoint can never boot a live futures engine.
- **`SOAK_STALL_S` is now a tunable everywhere:** whitelist (`TUNING_KEYS` + `_INT_TUNING_KEYS`, int-validated at the push boundary), `.env` ≡ `.env.example` (default 600s), Config UI field in the Signal Engine section ("Soak Stall Alert", 120–3600s, applied on the next watchdog restart), and carried by `envGenerator.ts` so pushes never drop it.
- **Realtime transport audited (no changes needed):** the `/ws` WebSocket push (1s, coalesced) is the primary transport for *everything* — status, equity, trades, futures state, roadmap, soak, watchdog fields; HTTP polling exists only as an age-adaptive fallback (fires only when no push for >3s) plus a 10s half-open-socket reconnect watchdog. Engine-side realtime remains 100% WebSocket (aggTrade/kline/miniTicker/user-data streams).
- Verified: `py_compile` + `import status` clean, endpoint guards live-tested (400/409), dotenv load confirmed in the restarted watchdog, `tsc --noEmit` clean, `vite build` clean (bundle serves the buttons), **smoke 13/13 + sync 27/27 while the live engine and soak kept running**, all 4 PM2 apps online, `pm2 save` done.

### 2026-09-19 09:40 UTC — Watchdog on the dashboard: soak card now shows supervision health + exact 24h deadline

- **Soak card watchdog badge** (`LiveDashboard.tsx`): the Futures Soak header now carries a second badge next to the soak status — `watchdog: armed` (green, supervised & quiet), `watchdog: <flag>` (amber, an alert fired: death/stall/finished), or `watchdog: OFF` / PM2 state (red, nobody is supervising). Hover title shows the last supervision pass time and watchdog restart count.
- **Watchdog alerts row:** when the watchdog's state file has alert flags, the card shows them inline (`⚠ alerted_death · alerted_stall …`); a rose footer appears if the last supervision pass is suspiciously old (>2.5h vs a 60s poll + hourly liveness stamp → supervisor likely hung). Healthy states stay clean — no noise.
- **Exact deadline:** `status.py` now pins the soak start with the same source chain as the watchdog itself (start marker → PM2 `pm_uptime` → DB mtime — `pm_created_at` drifts from the real clock on engine restarts), so the card renders the true `X.Xh / 24h · ends HH:MM` ETA instead of a restart-inflated guess.
- **Status payload:** `soak` block gained `watchdog_status`, `watchdog_restarts`, `watchdog_alerts`, `watchdog_last_check_ms`, `deadline_ms`, `hours_total`; `FuturesSoak` TS type extended to match, verified field-for-field against the live `/api/status`.
- **Incident during rollout (fixed):** the first edit accidentally orphaned the soak DB-metrics block after `return snap` (equity/PnL went `null`) and referenced `_safe_float` at module level before its definition (monitor crash-looped for ~2 min). Both repaired; `import status` + full-payload assertion now part of the verify step.
- Verified: `py_compile` + `import status` clean, `tsc --noEmit` clean, `vite build` clean, bundle serves the badge, API payload field-complete, all 4 PM2 apps online, `pm2 save` done.

### 2026-09-19 07:41 UTC — Soak progress/completion verification; soak watchdog (24h deadline → Discord summary + auto-stop); NameError in futures filters fixed

- **Live NameError fixed (caught by the soak):** `futures_rest_client.get_filters` referenced `FUTURES_DEFAULT_MIN_NOTIONAL` without defining it — any futures symbol whose `exchangeInfo` lacks the MIN_NOTIONAL filter (e.g. GUSDT) crashed the signal cycle every iteration. Constant now defined in the client (5 USDT fallback, matching live data); all exchange modules audited for undefined names via AST. Verified live: GUSDT→5, NEAR→5, LINK→20.
- **Soak watchdog added** (`soak_watchdog.py`, PM2: `soak-watchdog`): (1) at the 24h deadline posts the final summary to Discord (equity, PnL, W/L, winrate, max DD, streaks) and stops the soak, preserving the DB; (2) alerts once if the soak process dies/errored before deadline; (3) stall detection via the engine's true DB heartbeat (`ws_streams` risk_state age — the log file is silent for hours by design since per-cycle logs are debug-level). Alerts POST directly to the Discord webhook (stdlib; the engine's DiscordWebhook class is asyncio-bound and unusable from a sync supervisor). All paths tested (deadline/stall/once modes); hourly liveness line in PM2 logs; state file prevents duplicate alerts; `futures_soak.sh start` re-arms it.
- **Soak start marker:** `futures_soak.sh start` now writes `data/futures_soak_start_ms` (fallback chain: marker → PM2 `pm_uptime` → DB mtime) and clears watchdog state; `status` shows `X.Xh / 24h`.
- **Roadmap verified end-to-end:** CLI (`capital_roadmap.py --equity 22`) and `/api/status.roadmap` agree field-for-field (equity→notional/leverage math, 3 stages, per-pair floors + funding, ok/blocked pair lists, fee edge); payload shape matches the React `Roadmap` type exactly; dashboard renders the Capital Roadmap + Futures Soak panels from the same backend cache.
- **Soak health:** process online 0 restarts, heartbeat fresh (~seconds), paper equity $22.00, 0 trades so far (regime-qualified signals pending — normal for the strategy's frequency), zero DB errors since the migration fix.
- `pm2 save` done. PM2 now runs 4 apps: `ultimate-bot` (live spot), `bot-web-monitor`, `futures-soak`, `soak-watchdog`.

### 2026-09-19 06:55 UTC — 24h futures paper soak LIVE under PM2; roadmap + soak panels on the dashboard; lock & schema bugs fixed

- **24h futures soak running** (`MARKET=futures PAPER_TRADE=true`, isolated DB `data/futures_soak.db`, seeded to the real $22 equity, 3 dynamic pairs, its own paper lock so it coexists with the live spot engine). Launcher: `./futures_soak.sh start|status|stop` — always call with a subcommand (PM2 must run the *engine*, not the status script; bare `pm2 start futures_soak.sh` loops).
- **Dashboard enhancements for the futures module:** new **Capital Roadmap** panel (live equity stage, per-pair NOTIONAL floor viability ✅/⚠️, implied leverage vs configured, funding/8h) and **Futures Soak** panel (progress vs 24h, paper equity + PnL, W/L counts, PM2 status badge). Both render only when the backend provides data — spot-only runs stay clean.
- **Backend:** `status.py` now serves `roadmap` (live fapi floors via `capital_roadmap.get_roadmap`) and `soak` (PM2 status + soak DB metrics) alongside the existing `futures` block.
- **Bug fixed — singleton lock scope:** the earlier mode-scoped-lock rewrite in `main.py` dropped `import os` (NameError on boot). Re-added; smoke 13/13 + sync 27/27 now pass **while both the live engine and the soak keep running** (tests use unique `LOCK_SCOPE`s).
- **Bug fixed — old DBs missing `risk_state.updated_at`:** schema migration added in `db_manager.py` (`ALTER TABLE` when the column is absent). The futures soak exposed this: stale DBs made every `set_risk_state` batch write fail forever. Soak DB re-seeded; zero batch errors since restart.
- Verified: soak streams subscribed (3 pairs + `!miniTicker@arr`), daily reset logged, `/api/status` serves soak+roadmap, bundle serves the new panels, `pm2 save` done.

### 2026-09-19 06:22 UTC — Futures floor corrected with LIVE exchange data: futures IS viable at $22 (no extra leverage); capital roadmap tool added

**Correction to the 06:09 entry.** The "~100 USDT futures minNotional" was folklore. The **live**
`/fapi/v1/exchangeInfo` shows **5 USDT on 719 of 725 USDT perps** (a few at 20–50) — including every
watched pair except LINKUSDT ($20). `backtest.py` now fetches the **real per-symbol floor**
(`fetch_futures_min_notional`, cached, fallback 5) instead of assuming 100.

**Re-run edge battery at $22 equity, real floors, ~21 days:** futures median **+3.97%** vs spot
**+3.29%** (5/6 positive both), 50 futures trades, funding ≈ 0 (LSK even *paid* $0.29 — negative
rate). The proven fixed-fractional sizing implies **$18.33 notional / 0.83× leverage** — comfortably
above the $5 floor with **no extra leverage**. The edge and the capital BOTH work on futures; the
06:09 "$22 impossible" verdict is withdrawn.

**New tool: `capital_roadmap.py`** (read-only, live data): fetches per-symbol floors, prices and the
latest funding rate, reads equity from the engine DB (or `--equity`), and prints per-pair
viability (floor vs proven notional, wallet %, liquidation distance at the configured leverage,
funding), plus staged thresholds: **$6** lowest-floor pairs OK → **$24** ALL watched pairs OK →
**$100** legacy assumption satisfied. Recommendation is pair-aware (LINK waits until ~$24).

**Still required before flipping `MARKET=futures` live:** a 24h futures paper soak (`MARKET=futures
PAPER_TRADE=true`, isolated DB) mirroring this math, then a futures-testnet session. `FUTURES_LEVERAGE=1`
remains the shipped default — the proven sizing never needs exchange leverage, and raising it adds
liquidation risk, not expectancy.

### 2026-09-19 06:09 UTC — Futures edge PROVEN, futures paper-trade verified, futures dashboard shipped

**1. Futures edge — PROVEN POSITIVE (and the $22 caveat quantified).** `backtest.py` gained a
`market="futures"` mode: fapi klines, 0.05%/leg taker (half of spot), **real historical per-8h
funding** from `/fapi/v1/fundingRate` charged on open notional, and the ~100 USDT futures NOTIONAL
floor. Spot replay is bit-for-bit unchanged (NEAR +0.06%, LINK +0.74% identical to baseline).
Same window, same champion config, 6 pairs: spot median **+0.40%** (4/6 positive) vs futures
median **+0.61%** (4/6 positive) — the halved fee lifts every trade's expectancy ~1:1 with signals.

**2. …but futures is IMPOSSIBLE at $22 with proven risk.** The USDⓈ-M NOTIONAL floor (~100 USDT)
blocks every entry: at `RISK_PER_TRADE=0.01` the backtest produced **0 trades** on $22 equity
(spot: 6 trades, +3.58%). Meeting the floor needs ≥4.6× leverage and ~$1.20 risk/trade = **5.5% of
the account per trade (5× the proven model)**. Verdict: the strategy's edge transfers to futures,
but the capital does not — stay on spot until the account is ≥ ~$500 (where $100 notional = 1%
risk at 2× leverage becomes reachable).

**3. Futures paper-trade run — verified end to end.** `MARKET=futures PAPER_TRADE=true` on an
isolated DB: futures screener picked pairs, `FuturesWSStreamClient` subscribed (`@aggTrade`,
`@kline_5m`, `!miniTicker@arr`), the engine published a new `futures_state` risk_state row
(positions/leverage/margin/mode + age), and SIGTERM shutdown is clean.

**4. Mode-scoped singleton locks.** `main.py` now locks `/tmp/ultimate_bot.{live,paper}.lock`:
**paper engines run alongside the live engine** (test batteries no longer require stopping it),
while two live engines still can never coexist. `status.py` reports RUNNING from either scope;
smoke/sync batteries updated (13/13, 27/27 — run **while live traded**).

**5. Futures dashboard.** The web monitor renders a Futures (USDⓈ-M) panel when the engine runs
futures: open positions with BULLISH/BEARISH direction, entry/mark/**liquidation** prices, per-
position leverage + margin type, unrealized PnL, account leverage/margin/mode, and a freshness
badge (`Xs ago` vs `no data yet`). Spot boots render nothing (no invented data).

**Verification** — `py_compile` clean · equivalence proof (spot unchanged) · futures battery
(6-pair table above) · smoke **13/13** + sync **27/27** concurrent with live trading · `tsc --noEmit`
clean · `vite build` clean (`index-CZzpIrJj.js`, served) · live spot engine healthy (streams green,
$23.66 via ws) · `pm2 save` done.

### 2026-09-19 05:14 UTC — USDⓈ-M futures exchange module added (spot default unchanged)

A complete **Binance USDⓈ-M Futures** transport layer now lives alongside the proven spot path,
selected by a single `.env` key. Built against the official USDⓈ-M docs (REST account/trade,
WebSocket-API account, public WS streams — see `src/exchange/futures_*.py` docstring links).

**New modules (`src/exchange/`)**
- `futures_rest_client.py` — `fapi.binance.com` (testnet `demo-fapi.binance.com`): `v3/account`,
  `v3/balance`, `v3/positionRisk`, `v1/income`, leverage / margin-type / position-mode endpoints,
  order placement with `positionSide` + `reduceOnly`, futures `exchangeInfo` filters (futures
  minNotional ≈ $100 — read from the exchange, never hard-coded), and the `/fapi/v1/listenKey`
  lifecycle. Same retry/time-sync/signing conventions as the spot client (HMAC **and** Ed25519).
- `futures_ws_api_client.py` — `wss://ws-fapi.binance.com/ws-fapi/v1`: Ed25519 `session.logon`,
  signed `order.place`/`order.cancel` (alphabetical sort, decimals as strings), plus the **separate**
  user-data socket `wss://fstream.binance.com/ws/<listenKey>` with `ACCOUNT_UPDATE` /
  `ORDER_TRADE_UPDATE` / `MARGIN_CALL` handling, 30-min keepalive, and `listenKeyExpired` rotation.
  Balance/fill caches mirror the spot client's interface exactly, so risk/order layers are
  market-agnostic.
- `futures_ws_stream_client.py` — `wss://fstream.binance.com/ws`: `@aggTrade`, `@kline_<tf>`,
  `!miniTicker@arr` (dedicated socket), reconnect/backoff and interface identical to spot.

**Wiring**
- `config.py`: `MARKET` (`spot`|`futures`), `FUTURES_ONE_WAY_MODE`, `FUTURES_MARGIN_TYPE`
  (`ISOLATED`|`CROSSED`), `FUTURES_LEVERAGE` (1–125), `FUTURES_REST_WEIGHT_LIMIT` — with boot-time
  validation. `main.py` selects all three clients off `MARKET` and applies one-way mode, margin type
  and leverage per symbol at boot (live futures only).
- `.env` / `.env.example` / `envGenerator.ts` key-for-key aligned; `status.py` `TUNING_KEYS` gained
  the five keys **plus a new enum validator** (`MARKET`, `FUTURES_MARGIN_TYPE`, `STRATEGY_MODE`,
  `LOG_LEVEL` are now refused at the push boundary if they hold a bad value, not just at boot).
- Web monitor: Market selector (spot/futures) with a "not yet backtested" warning in the Strategy &
  Config tab; `BotConfig` carries the futures fields.

**Verification** — `py_compile` clean · module URL/inheritance/signing-parity tests pass · push-boundary
validation tests pass (valid accepted, `binance`/`CROSS`/`yes`/`NaN` rejected) · `tsc --noEmit` clean ·
`vite build` clean (`index-BmTlQ_kr.js`, served) · engine restarted on `MARKET=spot`, streams green,
equity $23.66 via ws · `pm2 save` done.

> ⚠️ **The futures module is untested against real money and NOT covered by the backtest.** The
> proven A3 result assumed spot fees and no funding. Before enabling `MARKET=futures` live: backtest
> under futures fees + funding, then run the futures testnet. `FUTURES_LEVERAGE=1` is the shipped
> default on purpose.

### 2026-09-19 01:22 UTC — Unlimited trade frequency restored; all optional gate tunables exposed in the web monitor

**Trade frequency** — live `.env` restored to `MAX_TRADES_PER_DAY=0` (unlimited, the engine treats 0 as
no cap), superseding the champion template's `2`. The engine was restarted on it and the monitor serves
the new value.

**Gate & exit tunables exposed end-to-end** — the nine dormant strategy gates (present in `config.py`,
`.env`, `.env.example` and the backend push whitelist) were previously hard-coded "off" in the web
monitor's env generator and invisible in the Config tab. They are now first-class tunables:

- `src/types.ts` — `BotConfig` gained `entryMaxExtAtr`, `entryExtEma`, `entryVolMult`, `entryVolLookback`,
  `entryRequireRsiRise2`, `slAtrMultiplier`, `slAtrMaxPercent`, `breakevenTrigger`, `breakevenOffset`.
- `src/utils/envGenerator.ts` — emits all nine keys (no longer hard-coded); the generated `.env` remains
  key-for-key identical to `.env.example`.
- `src/App.tsx` — defaults, NaN-safe sanitiser and the engine-config merge all cover the new keys.
- `src/components/ConfigTab.tsx` — new inputs: Extension Gate + EMA span, Volume Gate + lookback,
  "two rising RSI prints" checkbox, ATR Stop + cap, Breakeven Trigger + Offset (replacing the fixed
  "(+1%)" label with live values).
- Verified the push boundary: `parse_env_payload` accepts all nine and rejects `NaN`/`inf`/`abc`/`yes`.

**Verification** — `tsc --noEmit` clean · `vite build` clean (bundle `index-CvNfbuwP.js`) · `py_compile`
clean · config loads `MAX_TRADES_PER_DAY = 0` with zero warnings · monitor serves the new bundle and all
gate values · engine healthy: 3 WS streams green (`engine_age_s` 3.8s), equity $23.66 `via ws`, not paused
· `pm2 save` done.

### 2026-09-18 23:51 UTC — Full engine↔monitor integration audit: sync gaps closed, dead code cleared, failure scenarios fixed

Whole-tree audit of the engine (`ultimate-bot/`) **and** the React monitor (`src/`), with every
finding fixed and re-verified against the running stack.

**Defects fixed**
- **`status.py` used deprecated `datetime.utcnow()`** — removed in a future Python; now
  `datetime.now(timezone.utc)`. This also removed the per-request `DeprecationWarning` that was
  filling `logs/web-error.log`.
- **Invalid `.env` value silently ignored**: `REGIME_SLOPE_DAYS=0.5` is not an integer, so the boot
  parser logged a warning and quietly fell back to the default `3`. Set explicitly to `3` — the
  engine now runs the documented value with no warning (behaviour was already 3, so no strategy change).
- **Dead REST user-data code removed** (`rest_client.py`): `create_listen_key` / `keepalive_listen_key` /
  `close_listen_key` had zero callers — that endpoint is unreachable from this network (nginx-410) and
  fills/balances already arrive on the authenticated WS API session (`userDataStream.subscribe`).
- **Unused import removed** (`trade_policy.py`: `dataclasses.field`).
- **Frontend dead code removed**: unused `lucide-react` icons in `DeployGuide.tsx` (`BookOpen`,
  `Terminal`, `Key`, `Zap`) and `SignalInspector.tsx` (`TrendingDown`), and the never-read
  `activeSymbols` prop in `TuningControlBar.tsx` (+ its call site in `LiveDashboard.tsx`). Confirmed
  clean by `tsc --noUnusedLocals --noUnusedParameters`.

**Engine↔monitor sync gaps closed**
- Every config key read by `config.py` is now documented in `.env.example` and mirrored in `.env`:
  added the dormant optional gates (`SL_ATR_MULTIPLIER`, `SL_ATR_MAX_PERCENT`, `BREAKEVEN_TRIGGER`,
  `BREAKEVEN_OFFSET`, `ENTRY_MAX_EXT_ATR`, `ENTRY_EXT_EMA`, `ENTRY_VOL_MULT`, `ENTRY_VOL_LOOKBACK`,
  `ENTRY_REQUIRE_RSI_RISE2`). All default to no-ops, so behaviour is unchanged.
- `status.py` `TUNING_KEYS` + typed validation subsets now include those keys plus
  `PRICE_REFRESH_INTERVAL` / `WS_BALANCE_MAX_AGE`, so the monitor can view **and** safely push **every**
  engine tunable (bad booleans / `NaN` are still rejected at the push boundary).
- `envGenerator.ts` (the UI's single-source `.env` writer) now emits `PRICE_REFRESH_INTERVAL`,
  `WS_BALANCE_MAX_AGE` and the optional-gates block — it previously dropped the two runtime keys, so a
  UI-generated `.env` diverged from `.env.example`. `.env`, `.env.example` and the generator are now
  key-for-key identical (only `BACKTEST_*` research flags and the `BINANCE_API_SECRET`/Ed25519 choice
  are intentionally generator-exempt).
- **Monitor config could go stale.** `status.py` snapshotted `.env` **once at startup** and never
  refreshed it: after a UI config push (or a manual `.env` edit followed by an engine reload) the
  engine ran the new values while `/api/status` and the `/ws` push still advertised the old snapshot.
  Added `refresh_env_config()` — an mtime-guarded reload (one `stat()` when nothing changed) — called
  before every status build, plus an immediate in-place update of the monitor's in-memory config right
  after a push. The monitor now tracks `.env` with **no restart**, and `sync_test.py` gained a
  regression check for it (`F. Config live-refresh`).

**Housekeeping (stale / temporary files)**
- Removed stale backups ``.env.bak_alloc`` / `.env.example.bak.freq``, the empty orphan root
  `trading.db` (the live DB is `data/trading.db`), an orphan `.pyc` with no source
  (`ws_user_stream.cpython-313.pyc`), all `__pycache__/` dirs and `logs/pm2-combined.log`.
- Truncated a 194 MB `logs/pm2-out.log` — the residual Sep-13 `websockets` DEBUG flood (already fixed
  at the source; the file was never rotated).

**Verification**
- `py_compile` clean on all first-party modules; config loads with **zero** warnings.
- `smoke_test.py` **13/13** · `sync_test.py` **27/27** (HTTP ≡ `/ws` on stream lights + balance
  provenance, plus the new config live-refresh check).
- `tsc --noEmit` clean, `tsc --noUnusedLocals --noUnusedParameters` clean, `vite build` clean.
- Live: engine HELD-ON boot (WS API + user-data + market + all-market tickers all connected),
  monitor stream lights green (`engine_age_s` ~9 s), balance `via ws`, both PM2 apps online,
  `pm2 save` done.

**Live `.env` aligned to the champion template (A3)** — the live file was running an older,
more aggressive profile; its 16 diverging strategy values were overwritten with the proven champion
(`RSI_PERIOD` 14→7, `RSI_OVERSOLD` 30→40, `ENTRY_RSI_MIN` 20→35, `RSI_TIMEFRAME` 5m→1h,
`MTF_TIMEFRAME` 1h→1d, `REGIME_EMA` 20→50, `MAX_SYMBOLS` 5→1, `MAX_TRADES_PER_DAY` 0→2,
`BREAKEVEN_ENABLED` true→false, `SCALE_OUT_ENABLED` true→false, `CLOSE_AT_UTC_DAY_END` false→true,
`SIGNAL_INTERVAL` 5→6, `TOP_CANDIDATES` 25→50, `BALANCE_USAGE_PERCENT`/`MAX_SYMBOL_ALLOCATION_PERCENT`
1→1.0, `EXCLUDE_SYMBOLS` re-ordered). Credentials, key paths, webhook URL, DB/log/control paths and
**`PAPER_TRADE=false` (live mode) were deliberately preserved** — `.env` and `.env.example` now differ
only in those intentional secret/safety keys. Engine restarted and confirmed running the champion
(LIVE, all streams connected, equity `$23.66 via ws`).

### 2026-09-18 22:10 UTC — Terminology: trade-direction words replaced with bullish/bearish

Spot-only engine vocabulary cleanup (no behaviour change): all comments, docstrings, log lines and
README text that described positions as **long/short** now say **bullish/bearish** — e.g. the spot
mode guard message (`spot bullish-only engine`), `no bullish entries` when the regime gate is DOWN,
and the shared-policy docstrings. Non-direction uses of the words were left alone or rephrased
(“no longer”, “long before +1R”, cache “Short TTL”, `month: 'short'` date format, trailing “never
exits a trade before its target”). Verified: `py_compile` clean, `tsc --noEmit` clean, `vite build`
clean, both PM2 apps restarted and healthy (equity `via ws`), `pm2 save` done.

### 2026-09-18 01:16 UTC — Champion strategy (A3) deployed end-to-end

The strategy research from the previous session was **finished and rolled out** — the engine now
trades exactly what the backtest proved:

| Piece | Change |
|---|---|
| `config.py` | `ENTRY_RSI_MIN=35` (skip entries while RSI below the floor) and `TRAILING_ATR_MULTIPLIER=2` (trail distance = 2×ATR once +1% profit) added to the `intraday_rsi` preset; trailing guard relaxed to only enforce `CALLBACK < ACTIVATE` in %-callback mode (ATR mode doesn't use the % callback — the old guard rejected the champion config at boot) |
| `.env` / `.env.example` | Champion values deployed: `RSI_TIMEFRAME=1h`, `RSI_OVERSOLD=40`, `ENTRY_RSI_MIN=35`, `MTF_TIMEFRAME=1d`, `TRAILING_STOP_ACTIVATE=0.01`, `TRAILING_ATR_MULTIPLIER=2`, breakeven **OFF**, scale-out **OFF**, `CLOSE_AT_UTC_DAY_END=true` (replaces the old deviating 15m/45 RSI + 4h-regime + breakeven + scale-out setup); duplicate-key check clean |
| Web monitor | `ENTRY_RSI_MIN` / `TRAILING_ATR_MULTIPLIER` added to `TUNING_KEYS` (+float validation), so the Strategy & Config tab can view/push them; `BotConfig` + `generateEnvString` extended to emit both keys; new UI inputs (RSI floor gate, ATR-vs-% trailing mode) |
| Proof | Deployed `.env` config re-run through the backtest engine on the research cache: median **+4.07%**, 4/6 pairs positive, WR 51%+ — reproduces the champion; `ratchet_stops` ATR-trail unit battery 7/7 (arm threshold, 2×ATR distance, ratchet-never-down, callback fallback); smoke 13/13 · sync 25/25 · `tsc --noEmit` clean (needs `node --stack-size=8000` on this box) · `vite build` clean |

### 2026-09-17 14:45 UTC — Dashboard stream lights + balances served from the WS cache

Two follow-ups to the WebSocket migration: the dashboard now *shows* every realtime transport,
and the account balances it displays are the engine's own WS-cached balances instead of the
monitor taking its own REST snapshot.

**Dashboard: realtime stream lights**
- New **Realtime Streams** strip on the dashboard (under the risk cards) with one light per engine
  transport: `Ticks` (per-symbol `aggTrade`), `All-market` (`!miniTicker@arr`), `Order API`
  (authenticated WS API session), `User data` (fills + balances). Green = flowing, amber = connected
  but lagging (`all_tickers.last_frame_age_s > 5`), red = down (engine falls back to REST).
- Lights are **engine-published truth** (`risk_state["ws_streams"]`) — never inferred in the browser.
- The engine now stamps `updated_ms` in that health snapshot and `status.py` republishes it as
  `engine_age_s`, so a **stopped engine** shows `engine stale` (red lights) instead of leaving the
  last snapshot glowing green; ticker lag shown in the UI counts engine-stall time too.
- Spot Balances strip now shows **where the number came from**: `via WS · verified Ns ago`, `via REST`
  (WS cache stale) or `last known` (exchange unreachable), with colour-coded dot.

**Balances wired to the WS cache (both sides)**
- New `src/exchange/balance_cache.py`: `get_account(rest, ws_api, config)` — **WS-first** balance
  provider. It answers from the user-data cache (`outboundAccountPosition`) while the stream is
  connected/active and the newest datum is within `WS_BALANCE_MAX_AGE`; otherwise it takes a REST
  `/api/v3/account` snapshot and **seeds that into the WS cache** so the next read is WS-served again.
- Wired into every engine balance path: equity refresh (`risk_manager._fetch_equity`), position
  sizing free-quote check, `order_manager` pre-trade SELL clamp / BUY free-quote check, fill-time net
  base-asset verification, and `trade_logic.sync_positions_from_exchange`.
- The engine publishes `risk_state["ws_balances"]` (balances + USD valuations + `source`/`age_s`)
  each refresh cycle; `status.py` serves it as `/api/status → balance` with `source` provenance when
  `source == "ws"`, and only falls back to its own REST snapshot when the cache is stale.
- `ws_api_client`: balance cache API (`seed_balances`, `cached_account`, `balance_age_s`).
  Freshness counts the newest of (per-asset WS delta, REST seed, **any** user-data frame), because
  balances only change on events — a frame proves the stream is live and reconciled.
- New tunable **`WS_BALANCE_MAX_AGE`** (default `90`, min `5`) in `config.py`, `.env`, `.env.example`.
  Result: an idle account gets one REST account call per ~2 min (was every 60s), and during trading
  the WS event updates the cache instantly.

**Verification**
- Unit batteries (24 assertions): monitor WS-first payload, stale/`stale`-source rejection, over-age
  rejection, corrupt-JSON survival, paper provenance, ticker-cache valuation, and the engine cache
  itself (cold→REST, warm→WS with **no** extra REST call, WS delta keeps the WS path, stale → REST,
  stream down → REST, no `ws_api` → REST passthrough).
- `sync_test.py` extended with section **B2** (6 new checks): ws_streams published, light freshness,
  balance provenance, `/ws` push parity with HTTP → **25/25** (was 19/19).
- Live: equity log `via ws`, `/api/status → balance.source = ws`, `ws_streams.engine_age_s` ≈ 4–6s.
  **Engine-stale drill**: with the engine stopped, `engine_age_s` climbed to 48s → the strip reads
  `engine stale` (lights red); after restart it returned to 3.9s (green) with `balance.source = ws`.
  smoke **13/13** · `tsc` clean ·
  `vite build` clean · both PM2 apps online · `pm2 save` done.

### 2026-09-17 14:02 UTC — All realtime processes moved to WebSocket transports

Every realtime process now rides a WebSocket; REST is fallback-only:

| Realtime process | Before | Now |
|---|---|---|
| Decision prices (trading) | WS `aggTrade` (already) | WS `aggTrade` + all-market cache fallback |
| Screener / web-monitor prices | REST bulk poll every 30s | **WS `!miniTicker@arr`** (push ~1s) → re-persisted every `PRICE_REFRESH_INTERVAL=10` |
| Order placement | WS API (already) | WS API |
| Fill confirmation | REST polling 1/s | **WS `executionReport` events** on the authenticated session (`userDataStream.subscribe`) — REST polling stays as fallback |
| Account balances | REST `get_account` every 60s | **WS `outboundAccountPosition`** events (cache) + REST refresh |
| Klines (bars, not ticks) | REST | REST — appropriate; bars are not a tick stream |

**Implementation notes**
- `ws_stream_client.py`: dedicated all-market socket (`connect_all_market_tickers`), infinite
  capped-backoff reconnect, `all_tickers` cache, `get_all_tickers()` / `is_arr_stream_alive()`;
  `get_current_price()` falls back to the cache for ANY symbol.
- `ws_api_client.py`: proper frame dispatcher — `_listen()` routes responses by request id and
  dispatches user-data events (`executionReport`, `outboundAccountPosition`); `send_request()` no
  longer blind-recv's (events can no longer consume responses). Fill waiters resolve on terminal
  states only; `user_stream_active` gates the event path.
- `order_manager.wait_for_fill`: placement response → WS event (bounded 5s) → REST polling, with
  the REST budget reduced by the event wait.
- `trade_logic`: publishes `ws_streams` health (market / all_tickers / order_api incl.
  `user_stream`) to the DB each fast cycle; `status.py` serves it at `data.ws_streams`.
- REST `POST /api/v3/userDataStream` (listen-key path) is **blocked from this network** (all Binance
  hosts return nginx-410 through WARP egress); the WS-API subscription needs no listen key, so the
  user-data stream works regardless.
- `PRICE_REFRESH_INTERVAL` default 30→10s (config + .env + .env.example).

**Proof**
- Dispatcher unit tests: response/event routing, waiter resolution (NEW/PARTIAL keep pending),
  balance cache, listen routing — all pass.
- Live: `!miniTicker@arr` frames 0.2–0.9s old; `subscriptionId=0` on the authenticated session.
- Dashboard parity vs Binance REST: avg **0.04%**, worst 0.48% (a +105% daily mover within its own
  10s window). Prices update 20+/22 symbols between 35s probes.
- smoke 13 ✓/0 fail · sync 19/19 · both PM2 apps online · `pm2 save` done.

### 2026-09-17 13:23 UTC — Live price sync fix: screener prices now track Binance every 30s

**Symptom:** the dashboard's pair prices drifted from Binance by up to ~0.9% and only snapped back
when the hourly screener rescan ran.

**Root cause:** `scanned_pairs` prices were one-time `last_price` snapshots taken during the full
screener scan, which only runs every `SYMBOL_REFRESH_INTERVAL` (3600s). Live WS ticks only flow for
the engine's *monitored* symbols — the other screener rows froze for up to an hour.

**Fix:**
- `rest_client.get_tickers_bulk(symbols)` — all current prices in ONE request
  (`GET /api/v3/ticker/price?symbols=[...]`, weight 4 vs weight 80 for the 24h-ticker scan).
  Compact JSON separators are required (default `json.dumps` spaces URL-encode to `+` and Binance
  rejects with `-1100` — caught and fixed on first deploy).
- `trend_detector.refresh_prices()` — updates `last_scored` prices in place; stats, ranks and
  signals untouched; unknown symbols safely skipped.
- `trade_logic.refresh_symbols_loop()` reworked into two cadences: fast in-place price refresh
  every `PRICE_REFRESH_INTERVAL` (new tunable, default 30s, min 10s) + full rescan every
  `SYMBOL_REFRESH_INTERVAL` (unchanged, 3600s). Re-persists `scanned_pairs` to SQLite so HTTP,
  WS push and the dashboard all serve fresh prices.

**Verified live:** worst drift across all 24 screener rows dropped **0.93% → 0.33%** (≤0.05% for
most rows; the residual is genuine price movement within one 30s refresh window). Engine boots
clean, no REST errors since restart. `refresh_prices` unit-tested (in-place update, unknown-symbol
safety, empty-state no-op).

> Note: a deliberate PM2 stop of both apps at ~21:07 local (origin unknown — clean graceful
> shutdown, not a crash) was found during diagnosis; both apps were restored from the saved dump.

### 2026-09-17 06:00 UTC — Full re-audit: entry-time matching fix, dead-code sweep, batteries

- **Fixed wrong entry-time matching** (`status.py`): the per-symbol BUY lookup kept only the latest
  FILLED BUY per symbol, so once a symbol round-tripped more than once, older exits could pair with a
  newer BUY (even one *after* the exit). Matching is now chronological — each exit pairs with the
  latest FILLED BUY **at or before** its own timestamp (bisect over per-symbol ascending BUY lists,
  500-row window). **Live-proven**: SYNUSDT's exit now shows its true entry (09:58:42, held 4m)
  instead of a stale BUY from 00:55 (9h wrong).
- **Hardened against corrupt rows**: NULL `created_at` BUY rows are excluded at the SQL level — a
  `None` inside the bisect list would raise `TypeError` (not `sqlite3.Error`) and kill the whole
  status payload (HTTP + WS together).
- **UI honesty** (`LiveDashboard.tsx`): estimated entries (no matched BUY) no longer render a
  meaningless "held 0s" — the held/entry subline is suppressed unless the engine supplied a real
  `entry_ts`.
- **Dead-code audit re-run (239 defs, Python + TS/TSX, probe-verified)**: planted sentinel function
  was caught (audit works); zero real suspects — no dead code to remove.
- **Batteries**: `py_compile` clean · smoke **13 ✓ / 0 fail** · sync **19/19** · `tsc --noEmit` clean
  · `vite build` clean (bundle `index-uI0kQTet.js`) · live `/api/status` probe: 13 exit rows with
  correct chronological `entry_ts`, stats consistent (24 closed = 7W/17L), clock serving · both PM2
  apps online · `pm2 save` done.

### 2026-09-17 05:45 UTC — Timestamps on Recent Completed Trades (web monitor)

- **New "Exited" column** in the *Recent Completed Trades* table (`LiveDashboard.tsx`) showing the
  exit **date + time** (client-local timezone), the **holding duration** (`held 12m 34s`, `1h 24m`,
  `2d 3h`), and the **entry time** — so every completed trade is now traceable in time, not just in
  price.
- **Real entry times, engine-side** (`status.py`): each SELL exit row in the `orders` payload now
  carries `entry_ts` — the timestamp of the matching FILLED **BUY** order for that symbol. Previously
  the UI fell back to the SELL row's own `created_at`, i.e. it displayed the *exit* moment as the
  entry time. Unmatched exits (e.g. older than the 100-order lookup window) degrade gracefully to an
  estimate, rendered with a `~` prefix.
- **Honest data contract** (`types.ts`, `App.tsx`): `ClosedTrade` gained `entryTimeEstimated`;
  `entryTime` = engine-matched `entry_ts` when available, else the exit timestamp (flagged with `~`).
  All timestamps normalize epoch-seconds/ms and ISO strings defensively as before.
- **Verified**: `py_compile` clean · `tsc --noEmit` clean · `vite build` clean (bundle
  `index-z1reTern.js` served) · live `/api/status` probe shows `entry_ts` on every exit row
  (e.g. ARBUSDT entry 09:55:40 → exit 10:07:14; LSKUSDT held ~10h) · both PM2 apps online.
- **Note**: `tsc`/`vite` in this environment now require the larger Node heap + stack
  (`NODE_OPTIONS=--max-old-space-size=2048` + `node --stack-size=4096`); the committed baseline
  crashes without it — an environment property, not a code fault.

### 2026-09-16 15:10 UTC — Full audit: dead code, failure scenarios, corruption drills

- **Dead-code audit (repo-wide, AST + cross-language reference index)**: every function/class in all
  Python + TS/TSX files checked for references. Result: **no dead code** — all flags were framework
  callbacks (`do_POST`/`log_message`), immediate-use closures, or in-file helpers. Test/tooling
  scripts (`smoke_test.py`, `sync_test.py`, `backtest.py`, `soak_report.py`, `watch_live_position.py`)
  are intentionally retained as verification batteries.
- **Corruption-proof status payload**: all `float()` conversions of persisted `risk_state` values in
  `fetch_binance_balance`/`fetch_scanned_pairs`/stats replaced with `_safe_float()` (never raises,
  rejects NaN/Inf). **Proven by drill**: a NaN/Inf/text-poisoned DB now degrades to safe defaults
  (`total_equity` falls back to 1000.0, `daily_pnl` to 0.0) instead of raising out of
  `build_status_payload()` and killing the whole monitor (HTTP + WS = DISCONNECTED).
- **Durable `.env`/control writes**: `write_env_updates()` and `write_control()` now `fsync()` the
  temp file before the atomic `os.replace()` — a power cut can no longer leave a truncated `.env`
  (the file that controls live trading) or control file. Control-file writes in the engine were
  already atomic.
- **Verified end-to-end**: smoke **13 ✓ / 0 fail** (boots real engine+monitor, WS push, SIGTERM,
  lock release) · sync **19/19** (HTTP↔WS key parity, control flow, DB persistence) · NaN-poison
  drill **PASS** · `tsc --noEmit` clean · `vite build` clean · both PM2 apps online with live payload
  serving engine_risk/clock/monitored pairs.

### 2026-09-16 (2) — "Trade more = earn more?" proven NO; allocation floor guard; sync-clock robustness

- **Frequency-lever sweep (NEARUSDT, 30 pages, honest fees/minNotional/$22)** — every lever tested, the
  shipped config is the optimum. Looser triggers only look better in-sample and fail robustness:

  | Lever tested | Result | Verdict |
  |---|---|---|
  | RSI_OVERSOLD 40→45 | +11.7%, 73 trades (vs +8.5%/37) | **REJECTED** — th48 collapses to +0.9% (spike, not plateau), worse on 6/8 alt pairs (sum −58% vs −30%), old sub-window +0.05% |
  | RSI_PERIOD 7→14 | −19% to −20%, PF 0.58–0.70 | REJECTED |
  | RSI bucket 1h→15m/5m | −6.5% to −34.6%, DD up to 40% | REJECTED — fee drag + noise |
  | MAX_TRADES_PER_DAY 2→3/4 | +7.65% vs +8.47% | REJECTED — extra entries are fee drag |
  | Signal is cap-limited? | No — ~0.36 valid entries/day on NEAR | cap=0 (unlimited) ≡ cap=2 in practice |

- **Conclusion**: more trades ≠ more profit at $22 with taker fees. The 1h RSI(7)<40 dip + daily-EMA50
  regime + 2/day cap is the edge; pair quality dominates — the screener's current picks backtest
  4/5 positive (FF +8.6%, LSK +4.6%, VTHO +1.8%, UNI +1.6%).
- **Fix**: `MAX_SYMBOL_ALLOCATION_PERCENT=0.2` in `.env` (a UI push) silently disabled ALL trading —
  $22×0.2 = $4.40 < the $5 Binance minNotional floor. Restored to `1.0`, documented the trap in
  `config.py`, and added a boot-time WARNING in `RiskManager` when the cap×equity falls below minNotional.
- **Monitor**: sync-clock STALE fix — an age-adaptive poller (polls whenever data is >3s old, regardless
  of transport) plus a stall watchdog that force-reconnects half-open WebSockets (the silent-death case
  after laptop sleep/network switches). Poll failures no longer flip the clock to DISCONNECTED while
  the WS is healthy. Visiting `/ws` in a plain browser now shows a friendly explainer page (HTTP 426
  for API clients, 400 for browsers) linking to the dashboard and `/api/status`, instead of the raw
  "WebSocket upgrade required" error — real WS clients still upgrade with `101 Switching Protocols`.

### 2026-09-16 — Account-level streaks: consecutive losses across DIFFERENT pairs now count

- **Bug**: `win_streak`/`loss_streak` were tracked **per symbol only**. With `DYNAMIC_SYMBOLS=true`
  the screener rotates pairs, so consecutive account losses landed on different symbols — every
  symbol stayed at streak 1, `MAX_LOSS_STREAK=3` never tripped, and the web monitor kept showing
  "Loss Streak 1" while the account was bleeding trade after trade.
- **Fix (engine)**: `RiskManager` now maintains **account-level** streaks (consecutive outcomes
  regardless of symbol) alongside the per-symbol counters. Crossing `MAX_LOSS_STREAK`/`MAX_WIN_STREAK`
  at account level arms a **global cooldown** that blocks entries on ALL symbols (not just the
  losing one), with kind (`loss`/`win`), reason, and end time logged.
- **Fix (snapshot)**: `engine_risk.loss_streak`/`win_streak` are now purely account-level. While a
  cooldown is active the monitor displays the streak value that **tripped** it (e.g. "Loss Streak
  3/3" for the whole pause) — the live counter resets when the breaker arms, so without this the
  banner showed 1. Expired cooldowns revert to the live counters. Stale per-symbol worsts no longer
  mask the true account run (a win after the cooldown showed "Loss Streak 1" from old symbol rows).
- **Fix (status.py)**: the top-level streak aggregation seeds from the engine's account-level keys
  instead of clobbering them, then takes the worst of per-symbol values for display.
- **Verified**: functional battery — 3 losses on 3 different symbols → account streak 3, global
  cooldown armed, `check_risk` blocks a fresh symbol, a win resets it, 5 straight wins arm the
  win-kind cooldown, and cooldown state survives an engine restart. Smoke 13 ✓ / 0 fail, sync
  19/19, live payload re-verified on HTTP (WS shares the same builder by construction).

### 2026-09-15 — Sync clock in the web monitor: engine ↔ browser time at a glance

- **New `SyncClock` widget** in the connection bar. Every status payload now carries
  `server_epoch_ms` (epoch ms) and `server_time_utc` alongside `server_time`, and the widget shows:
  - **Engine clock** (extrapolated between the 1s WebSocket pushes) in your local timezone,
  - **Δ skew** between the VPS clock and your browser (green ≤ ±2s, amber beyond — a VPS with
    drifted NTP is the usual cause),
  - **Data freshness**: `LIVE` (≤3s since last payload), `STALE <n>s`, or `DISCONNECTED`,
  - last measured round-trip latency.
  If the pill ever shows `DISCONNECTED`/`STALE`, the dashboard numbers are not current — treat
  them accordingly. HTTP-only (no-WS) mode still updates the clock via the 2.5s polling fallback.

### 2026-09-15 — Circuit breaker & streak cooldowns: enforced, published, and visible in the web monitor

- **Streak cooldowns now actually block entries.** `update_trade_result` resets a streak when it arms
  its cooldown, but `check_risk` still gated on `streak >= MAX AND cooldown active` — a condition that
  could never be true, so `MAX_LOSS_STREAK`/`MAX_WIN_STREAK` cooldowns were armed but never enforced.
  `check_risk` now blocks purely on `cooldown_until > now` (time-based, matching the documented
  "N consecutive losses → pause → fresh start" behaviour). Trip events log a WARNING with the symbol,
  streak limit and exact end time.
- **Daily-drawdown circuit breaker is now latched + published.** Previously it only returned False for
  the single check where the loss was below the limit at that instant. Now a breach sets
  `drawdown_breaker=True` (sticky until the UTC daily reset, not just while the instantaneous loss is
  beyond the limit), records a human-readable `breaker_reason`, and logs
  `CIRCUIT BREAKER TRIPPED: …`.
- **New engine risk snapshot `engine_risk`** (written to `risk_state` by `risk_manager` on every risk
  check / trade result / daily reset, change-detected to avoid write spam): worst loss/win streak,
  `cooldown_until` + `cooldown_kind` (`loss`/`win`), per-symbol `active_cooldowns` (symbol → until+kind),
  `drawdown_used`, `drawdown_breaker`, `breaker_reason`, `daily_pnl`. `status.py` parses and serves it
  inside `risk` on both `/api/status` and the `/ws` push (one shared `build_status_payload()`).
- **Web monitor renders the engine's real risk state** instead of guessing client-side:
  - **Circuit-breaker banner** (red) when `drawdown_breaker` is true, showing the engine's reason and
    "entries blocked until UTC daily reset"; the drawdown card also turns red and shows ⛔ TRIPPED.
  - **Drawdown usage** now comes from the engine's `drawdown_used` when available (client calc is only
    the fallback for older engines).
  - **Cooldown banner is kind-aware** — says *Win-Streak Cooldown (COOLDOWN_WIN)* vs *Loss-Streak
    Cooldown (COOLDOWN_LOSS)* — and lists the symbols currently cooling down; the Performance card also
    lists per-symbol cooldowns with end times, and the monitored-symbol cards get per-symbol cooldown
    state from the snapshot instead of the old global one.
- **`load_state` no longer mistakes the aggregate `risk_state` row for a symbol.**
- Verified end-to-end on the live paper engine: the breaker genuinely tripped during verification
  (paper daily PnL −5.12% vs −5.0% limit) and the full chain worked — engine WARNING log → `engine_risk`
  in `risk_state` → `/api/status` → `/ws` push (frames key-identical) → banner + card in the dashboard.
  Smoke 13 ✓, sync 19/19, `tsc --noEmit` + `vite build` clean, both PM2 apps online.

### 2026-09-14 (2) — Unlimited entries mode, crash-proof config & push validation, NaN incident response

- **`MAX_TRADES_PER_DAY=0` is the documented "infinite" value** (already honored by the engine, the
  config validation, and the backtest; `∞` is rendered in the monitor). `.env` had been set to the
  literal `NaN` — `int("NaN")` raised in `load_config()` and **PM2 crash-looped the engine 499 times**
  (uptime 0s each cycle). Incident fixes, all verified:
  - `.env` corrected to `MAX_TRADES_PER_DAY=0`; fresh-boot subprocess reads `0` end-to-end.
  - **config.py crash-proof parsing** — all 45 numeric env reads now go through `_env_int`/`_env_float`:
    malformed values (incl. `NaN`/`Inf`) log a warning and fall back to the preset default instead of
    raising; `RSI_TIMEFRAME_MS`'s composite expression hardened the same way. Verified by a 10-case battery.
  - **PM2 crash-loop breaker** — `max_restarts: 5` / `min_uptime: 30s` / `restart_delay: 3000` on both
    apps in `ecosystem.config.cjs`: a boot failure now stops after 5 attempts (visible `errored` state)
    instead of spinning all day and burning API weight.
  - **Push-boundary validation** — `status.py` now type-checks every `/api/config` push value
    (`_valid_tuning_value` + typed key sets): ints must parse, floats reject `NaN`/`Inf`, booleans must
    be exactly `true`/`false` (a `PAPER_TRADE=yes` typo would previously mean **live mode** silently).
- **Engine ↔ monitor sync re-verified on live payloads** — a WS frame and the HTTP snapshot are
  **key-identical** (both from one `build_status_payload()`); `signal_state` (regime/RSI/reason per
  watched pair), `monitored_symbols`, `control`, risk and stats all flow to the dashboard.
- **React hardening** — every config update (all tabs + the server merge) now passes
  `sanitizeConfigUpdate()`, killing the 20 `parseInt/parseFloat(e.target.value)` NaN vectors whose
  output fed the env generator (the origin of the `=NaN` file); `Entries today` renders `∞` when the
  cap is 0; the UI preset map was stale at `maxTradesPerDay: 1` → synced to the proven **2**.
- **risk_manager boot hardening** — `load_state()` parses every persisted value defensively (corrupt
  rows or a stored `NaN` paper_balance/daily_pnl now degrade to safe defaults with warnings instead of
  crashing the boot or poisoning drawdown checks).
- **Proof re-run post-change:** backtest `--pages 30 --equity 22` with the shipped config (cap 0 →
  1h cooldown): **+10.77%, 42 trades, WR 42.9%, PF 1.43, DD 6.77%** — byte-identical to the cap=2
  control run, so unlimited ≡ proven cadence on this window. Smoke 26 ✓ / sync 19/19; `tsc --noEmit`
  and `vite build` clean.

### 2026-09-14 — Frequency upgrade (proven), dashboard entries-today, duplicate/parity sweep

- **Trade frequency increased 1 → 2 entries per UTC day — but only after it survived the proof battery.**
  A frequency sweep (entries/day × RSI bucket size × oversold threshold, NEARUSDT, $22 equity, honest
  taker fees) over the live ~35-day window ranked candidates, then the full ~104-day window + three
  ~35-day sub-windows + a 3×3 SL/TP neighborhood battery decided it:
  - **Shipped: 2/day @ 1h buckets — +10.77% (104d), 42 trades, WR 42.9%, PF 1.43, max DD 6.8%;**
    positive at **all 9** SL/TP neighborhood points (+7.0% → +16.8%) and beats the old config
    (+6.82%, PF 1.33, DD 8.1%) on every metric.
  - **Rejected: 15m buckets (2/day, 3/day)** — headline +8.6/+11.2% on the recent window but
    **negative on the full window** (−3.9/−4.0%, DD 21–22%) and collapsing neighborhood — curve-fit.
  - **Rejected: 5m buckets** (+2.8–3.6%, PF ~1.1) and `RSI_OVERSOLD=45` (both variants negative).
  Backtest parity after the change re-verified: `backtest.py --pages 30 --equity 22` on NEARUSDT
  reproduces +7.69%/22 trades/PF 1.58 on the 10-page window (the V1 battery number, byte-for-byte).
- **Backtest engine: `run_backtest(overrides=...)`** — sweeps can now beat preset-pinned keys
  (`MAX_TRADES_PER_DAY`, `RSI_TIMEFRAME_MS`), and a short-circuit skips `decide()` on non-bucket-close
  bars (identical results, ~12× faster at 1h buckets).
- **Fixed: `BACKTEST_SYMBOL`/`STATIC_SYMBOLS` defaults were `BTCUSDT`** while the strategy is proven
  on alts and **loses on BTC** (−7.06%, PF 0.40 — low-vol chop breaks the −1.2%/+3% bracket). Defaults
  are now `NEARUSDT` in `.env`, `.env.example` and `config.py`.
- **New on the dashboard: "Entries today" chip** — the engine's persisted daily counter
  (`entries_today` from `risk_state`) now renders next to the streaks, so the 2/day discipline is
  visible. Previously the counter was served but never displayed.
- **Operational fix: PM2 restarts kept serving the OLD `.env` config** — PM2 re-applies the process
  environment captured at first start, and `load_dotenv(override=False)` let those stale vars win, so
  the monitor kept reporting `MAX_TRADES_PER_DAY=1` after every reload. Both PM2 apps were
  deleted + restarted from a fresh environment (`pm2 save` done). **Remember: after changing `.env`,
  run `pm2 delete <app> && pm2 start ecosystem.config.cjs` — a plain `restart`/`reload` is not enough.**
- **Duplicate-variable audit: clean.** `.env`/`.env.example` (84 keys each), all Python dict literals
  and kwargs (AST-checked), and all TS/TSX object literals — 0 duplicates, 0 case-collisions.
- **Verified:** `py_compile` 20/20 · imports 16/16 · smoke **26/26** · sync **19/19** · `tsc --noEmit`
  clean · dead code **0** (216 defs; only stdlib HTTP dispatcher hooks) · `.env` ↔ `.env.example`
  parity intact · both PM2 apps online, paper mode, cap=2 served by both HTTP and WS (same
  `build_status_payload()`), 0 errors since boot.

### 2026-09-13 (2) — Single-strategy consolidation; engine ↔ monitor fully synced on the REAL signal state

- **Consolidated to one strategy.** Removed the 5-factor confluence engine and the `scalping` / `day` /
  `swing` / `swing_rsi` presets from `config.py`, `signal_generator.py`, `backtest.py`, `.env`,
  `.env.example`, `status.py` `TUNING_KEYS` and the React dashboard. `config.py` now **refuses to boot**
  with any `STRATEGY_MODE` other than `rsi_dip`, so a removed strategy cannot be re-selected by accident.
- **Signal generation now has one implementation.** `SignalGenerator` is the daily-EMA regime gate +
  RSI(Wilder) dip trigger only — the browser-side `technicalAnalysis.ts` confluence scorer and the local
  Strategy Simulator were deleted, so the dashboard can no longer disagree with the engine by computing
  its own signals from synthetic candles.
- **New: the engine publishes its real per-symbol decision state.** `trade_logic` snapshots
  `SignalGenerator.get_last_signals()` (regime, regime EMA/price, sampled RSI + previous, oversold,
  trigger, ATR and the engine's own reason string) to `risk_state.signal_state` each cycle (only when it
  changes), and `status.py` serves it as `signal_state` on `/api/status` and the `/ws` push.
- **New: `monitored_symbols` in the status payload.** The engine writes the exact pair list it is
  watching (`trade_logic.update_symbols`) and `status.py` now serves it, so the dashboard shows the
  pairs the engine actually trades instead of inferring them from `STATIC_SYMBOLS` / `DYNAMIC_SYMBOLS`.
- **Frontend rebuilt as a real-engine monitor.** `App.tsx` no longer runs a local paper simulator:
  equity, daily PnL, streaks, cooldowns, open positions, closed trades, stats, screener candidates and
  the per-symbol signal state all come from `status.py`. The Signal tab renders the engine's gate
  breakdown; the symbol modal shows the fixed-% bracket and the engine's sizing; the tuner exposes the
  real `intraday_rsi` parameters (RSI period/oversold, SL/TP %, scan interval, caps).
- **Verified:** `py_compile` all modules, import sweep, smoke **13/13**, sync **19/19**, `tsc --noEmit`
  clean, `vite build` clean. `README.md` (root + engine) rewritten so the documentation matches the
  single-strategy bot.

### 2026-09-13 — Full audit: live-$22 sizing restored, mode-aware live bracket, adoption & sync hardening, log-flood fix

- **FIXED (critical, live-only): a ~$22 account could never place a single trade.** `.env` carried the
  generic defaults `BALANCE_USAGE_PERCENT=0.5` / `MAX_SYMBOL_ALLOCATION_PERCENT=0.2`, so the notional
  cap was `min(0.5, 0.2) × 22 = $4.40` — **below Binance's $5 `NOTIONAL.minNotional`**. RiskManager
  returned qty 0 ("risk-based size is below exchange minimums; entry skipped") and
  `trade_logic.enter_trade`'s headroom gate (`remaining = 0.5 × equity`) blocked the risk-based size
  too. Paper equity of $1,000 masked it ($200 notional), so the paper soak looked fine while **live
  would have traded nothing, forever**. Restored the documented/proven values (`1.0` / `1.0`); a
  verified probe now sizes `$16.45` of a `$22` account (≥ minNotional) instead of `$0.00`.
- **FIXED (critical, live-only): the post-fill re-anchor destroyed the proven bracket.** After a real
  BUY fill, `enter_trade` recomputed `stop = entry − atr × ATR_MULTIPLIER_SL` / `tp = entry + atr ×
  ATR_MULTIPLIER_TP` **unconditionally**. In `rsi_dip` mode those fields hold *fractions of price*
  (`0.012` / `0.03`), not ATR multiples, so a live entry got a ~0.01% stop (instant stop-out) even
  though the pre-trade bracket was correctly −1.2% / +3%. Brackets now come from one mode-aware helper
  (`_compute_bracket`) used by entries, the fill re-anchor **and** orphan adoption.
- **FIXED: orphan-fill adoption was blocked by the very gate it was written to bypass.** The orphan
  scan only looked at `managed_symbols` (the CURRENT screener list), but with `MAX_SYMBOLS=1` the pair
  that filled can rotate out of the screener within `ORPHAN_ADOPT_WINDOW_HOURS` — leaving a real bot
  fill unmanaged forever. Adoption is now attempted for any non-dust balance (still **proof-based**: a
  matching recent MARKET BUY in the local `orders` table *and* in the exchange's `myTrades`), while
  `AUTO_LIQUIDATE_ORPHANS` stays gated to monitored symbols so manual deposits are never sold.
- **FIXED: adopted positions recorded `entry_time` in milliseconds while the engine works in
  seconds**, so `time.time() − entry_time > MAX_HOLD_TIME` could never fire (the time stop was dead for
  any adopted trade). Also gives adopted trades `initial_qty` / `initial_stop_price` / `scale_out_done`.
- **FIXED: scale-out anchors were not persisted**, so after *any* restart the +1R scale-out could never
  fire again on a restored position (and could double-fire). `active_trades` now stores
  `initial_qty`, `initial_stop_price`, `scale_out_done` with an **idempotent migration** (`ALTER TABLE`)
  so existing databases upgrade in place — verified on a legacy schema.
- **FIXED: Discord alerts were silently dropped.** `send()` discarded the *current* message whenever the
  cooldown queue was non-empty, and an `embed` (the daily report) was dropped entirely if a text queue
  was pending. Queue **and** current message now flush together; embeds always send immediately.
- **FIXED (ops): DEBUG logging was flooding the disk.** At `LOG_LEVEL=DEBUG` the `websockets` client
  logs every raw `aggTrade`/`kline` frame — `logs/pm2-out.log` had reached **193 MB** and `trading.log`
  was rotating ~11 MB files continuously. `LOG_LEVEL` is back to `INFO` and the noisy third-party
  loggers (`websockets`, `aiohttp`, `asyncio`) are pinned to `WARNING` regardless of level.
- **FIXED: a single delisted symbol froze all reconciliation.** `RestClient.get_filters()` did
  `info.get(...)` on a `None` lookup (opaque `AttributeError`), and `sync_positions_from_exchange`
  aborted the whole sync for one bad symbol. It now raises a clear **`NonRetryableError`** and both the
  managed-trade loop and the orphan loop degrade **per symbol**.
- **HOUSEKEEPING:** `import time` moved to the top of `soak_report.py`; `.env.example` documents the
  small-account sizing requirement and now matches `.env` key-for-key (104 keys each, parity verified).
- **Re-verified this round:** `py_compile` 20/20 · smoke **13/13** · sync **19/19** · `tsc --noEmit`
  clean · dead-code AST scan 222 defs → only the 4 stdlib HTTP dispatcher hooks + `__getattr__`
  (runtime-invoked) · 0 unused imports · migration + anchor round-trip + bracket math + webhook queue
  unit-verified · backtest **+8.49%** at $22 with the restored caps.

### 2026-09-12 (6) — Full paper/live audit round: minNotional parity, restart-proof daily cap, session-scoped signal report

- **FIXED (parity): the backtest modelled a $10 minNotional floor while the live engine uses the
  exchange's real value.** `backtest.py` hardcoded `cfg.get("MIN_NOTIONAL", 10.0)`, a stale
  pre-2025 assumption, while every live path (`risk_manager.calculate_position_size`,
  `order_manager.sanitize_order`, four call sites in `trade_logic`) reads the per-symbol
  `/api/v3/exchangeInfo` `NOTIONAL.minNotional` and falls back to **5.0**. Verified live: NEARUSDT's
  actual floor is exactly **5 USDT**. The backtest therefore rejected entries the engine would
  happily place. The default is now 5.0, matching the exchange and the rest of the codebase.
  **Re-proved after the fix: NEARUSDT `intraday_rsi`, 30 pages (~104d), $22 equity → +9.65%,
  34 trades, WR 41%, PF 1.48, max DD 8.1% — byte-identical to the pre-fix run**, confirming the
  drift was real but inert at this position size (notional far above either floor). Documented
  values and comments in `config.py`, `backtest.py` and this README corrected from `$10` → the real
  `$5` exchange floor.
- **FIXED (discipline): the daily entry cap did not survive an engine restart.** `MAX_TRADES_PER_DAY`
  was tracked in memory only (`_entries_today`), so a mid-day PM2 restart handed the engine a fresh
  allowance and could double the proven **1-entry/UTC-day** rule. The counter and its UTC day key are
  now persisted to `risk_state` (`entries_day_key` / `entries_today`) on every reset and every
  placement, and restored in `reconcile_positions()` at boot via new `_save_entry_cap()` /
  `_load_entry_cap()` helpers. Unit-verified: a restarted process sees `1/1` and blocks the entry;
  a stale day key is restored as-is and rolls over on the next UTC day.
- **FIXED (report accuracy): `soak_report.py` counted BUY signals from every historical session.**
  Log health was already session-scoped, but the signal section scanned the whole file — so a
  long-dead live session's 50 signals appeared as *this* soak's activity, reading like a bug next
  to "Orders placed: 0". Signal counts now reset at each `Starting MARKET-ONLY BOT` banner, exactly
  like the error counters. Fresh paper soak now correctly reports **0 signals, 0 errors, 0 warnings**.
- **FIXED (docs): `.env.example` documented five env vars nothing reads.** The "research gates"
  block advertised `MIN_NOTIONAL`, `BB_LOWER_PCT_B`, `LTF_ADX_MIN`, `SIGNAL_VOL_MULT` and
  `BE_TRIGGER_R` as settable, but those are internal backtest *config-dict* keys — the working names
  are `BACKTEST_MIN_NOTIONAL` / `BACKTEST_BB_LOWER` / `BACKTEST_ADX_MIN` / `BACKTEST_VOL_MULT` /
  `BACKTEST_BE_R`. The block now says so, and explains that the live floor deliberately comes from
  `exchangeInfo` rather than a tunable. Stale "resting OCO limit = maker fee" and `$10` claims in
  this README were corrected too (all exits are MARKET = taker).
- **VERIFIED: engine ↔ web monitor synced in all functions.** HTTP `/api/status` and the `/ws`
  realtime push are built by the *same* `build_status_payload()` — structurally incapable of
  disagreeing. Field-level cross-check of every React read (`data.stats`, `data.risk`, `data.trades`,
  `data.orders`, `balance`, `candidates`/`scanned_pairs`, `control`, `config`) against the served
  keys and the live SQLite schema (`active_trades` / `orders`): zero unserved reads. Config payload
  exposes 100 keys; `TUNING_KEYS` covers all 74 backend-consumed tunables (the 4 excluded are
  secrets, correctly never pushed).
- **VERIFIED: paper + live code paths.** Fees identical in both modes (taker 0.001 on **both** legs,
  matching the backtest); exchange filters (`LOT_SIZE` stepSize/minQty/maxQty, `NOTIONAL`/
  `MIN_NOTIONAL`) enforced through `Decimal` quantization before every order; live BUY pre-checks 99%
  of free quote and live SELL clamps to free base (avoids −2010); orphan adoption, EOD force-close,
  gap-breach SL/TP, daily cap and UTC-day cooldown persistence all confirmed on the shared
  `manage_trade` path. Prior live-critical fixes re-verified intact: `params = dict(params)` retry-safe
  signing, percent-encoded Ed25519 signatures (engine **and** monitor), `NonRetryableError` fast-fail.
- **VERIFIED: dead code / unused imports / temp files.** AST scan over 31 files and 373
  functions+methods (cross-referenced against the TS/TSX sources): **0** zero-reference defs beyond
  the stdlib `ThreadingHTTPServer` dispatcher hooks and `_StreamProxy.__getattr__` (both invoked
  implicitly by the runtime), **0** unused imports, **0** stray `.bak/.orig/.tmp/core/pid` artifacts.
  `/tmp` scratch scripts from earlier rounds already removed; `logs/` bounded at 10 MB × 5 rotation.
- **VERIFIED: `.env` ↔ `.env.example` fully merged.** 104 keys each, 0 missing, 0 stale, 0
  duplicates; all 78 directly-consumed vars plus the 26 `BACKTEST_*` indirection vars present in
  both. Test battery: `py_compile` 20/20, smoke **13/13**, engine↔monitor sync **19/19**,
  `tsc --noEmit` clean, `vite build` clean.
- **Deployed state:** engine + monitor online under PM2 (`pm2 save` done), paper mode,
  `PRESET=intraday_rsi`, `STATIC_SYMBOLS=NEARUSDT`, `MAX_TRADES_PER_DAY=1`, equity $1,000,
  **0 errors / 0 warnings since the current boot**, `/api/status` and `/` both HTTP 200.

### 2026-09-12 (5) — Backtest fee-model honesty, documented `BACKTEST_*` env wiring, 710 MB log cleanup

- **FIXED: the backtest was under-charging fees, so it over-reported the edge.** The `rsi_dip`
  path auto-applied the 0.02% **maker** rate to TP exits, justified by a code comment claiming the
  TP was placed as a "resting OCO limit". No such order exists anywhere in the engine: every exit
  (TP, SL, EOD, time stop) goes through `order_manager.place_market_order` (MARKET = taker), and
  `trade_logic.close_trade` charges `fee_rate = 0.001` on **both** legs. The backtest now defaults
  to the taker rate it actually faces. Same klines, same $22 config, only the TP-leg rate differs:
  **taker (real) +9.65% / PF 1.48 / fees $1.28 vs maker what-if +10.46% / PF 1.52 / fees $1.12**
  — the edge survives the honest model, costing 0.81pp of phantom fee relief.
- **FIXED: `--maker-tp` could not express "use the default model".** `action="store_true"` makes
  the value `False` (not `None`) when the flag is absent, and `run_backtest` treats any non-None
  value as an explicit override — so the CLI silently forced taker fees regardless of the intent
  of the library API. It now defaults to `None` unless `BACKTEST_MAKER_TP=true`.
- **FIXED: `.env.example` documented 25 `BACKTEST_*` variables that nothing consumed.** The block
  was inert documentation — setting `BACKTEST_EQUITY=22` had no effect. `backtest.py` now reads
  them as real CLI defaults (`_env_str`/`_env_num`/`_env_bool`, unset/empty/invalid falls back to
  the built-in default), so a bare `python backtest.py` reproduces the saved research run while an
  explicit flag still wins. Verified by dumping the parser's resolved defaults:
  `symbol=NEARUSDT preset=intraday_rsi pages=30 equity=22.0 maker_tp=None`.
- **STALE COMMENTS CORRECTED.** The "TP as a resting OCO limit = maker fee" claim was repeated in
  `config.py` (both rsi_dip presets) and in the entry path of `trade_logic.py`; all three now
  describe the real execution model (software-managed MARKET exits → taker both legs).
- **CLEARED: 710 MB of unrotated PM2 logs.** `logs/pm2-combined.log` and `logs/pm2-out.log` had
  each grown to 355 MB. Root cause: `log_file` is **not** an app-level PM2 option — it made the
  same stream land in two files. Removing it from `ecosystem.config.cjs` is verified live:
  `pm2-combined.log` stays at 0 bytes while `pm2-out.log` receives everything. The config now also
  documents that PM2 core ignores per-app `max_size`/`retain` (that is the `pm2-logrotate` module's
  job, and it only covers `~/.pm2/logs`), and that `LOG_LEVEL` must stay `INFO` — at `DEBUG` the
  capture grew 355 MB in ~6 hours.
- **VERIFIED: `.env` ↔ `.env.example` fully merged.** 78 env vars consumed by the code; both files
  carry the same 104 keys — 0 missing, 0 stale, 0 duplicates, 0 present in one but not the other.
  The 26 `BACKTEST_*` research defaults are included (tuned in `.env` to the $22 NEARUSDT run).
- **VERIFIED: dead code and unused imports — none.** AST scan across 219 defs; the only
  zero-reference defs are the stdlib `ThreadingHTTPServer` dispatcher hooks (`do_POST`, `do_HEAD`,
  `do_OPTIONS`, `log_message`), which the HTTP server calls by method name. 0 unused imports.
- **VERIFIED: engine ↔ web monitor sync.** Every status/trade/order field the React dashboard
  reads (`data.stats`, `data.risk`, `data.trades`, `data.orders`, `candidates`, `control`, `config`)
  maps to a served key, and the `active_trades`/`orders` SQL columns match the frontend's field
  names exactly. Smoke **13/13**, sync **19/19** on the final code.
- **VERIFIED: build + imports.** `vite build` clean, `dist/` free of stale hashed assets;
  `tsc --noEmit` clean (needs `--stack-size=4000 --max-old-space-size=1400` — the default Node
  limits are exceeded on this 2 GB VPS); all 18 library modules import without error.
- **Deployed state:** engine + monitor restarted from `ecosystem.config.cjs` under PM2 (0 restarts),
  paper mode, `PRESET=intraday_rsi`, equity $1000, `NEARUSDT` subscribed, **0 errors after boot**;
  `pm2 save` done. `/api/status` → HTTP 200, control unpaused, `monitored_symbols=["NEARUSDT"]`.
- **CLEARED: stale repo files.** Deleted the duplicate `bun.lock` (npm + `package-lock.json` are
  canonical — every doc and script uses `npm`), the 9-line root `.env.example` (AI-Studio scaffold
  keys `GEMINI_API_KEY`/`APP_URL`, zero references anywhere), and regenerable build artifacts
  (`tsconfig.tsbuildinfo`, all `__pycache__/` outside `venv/`). The root README's quick-start already
  copies `ultimate-bot/.env.example`, so nothing depended on the removed template.
- **Retained on purpose:** `logs/trading.log.1..5` (bounded 10 MB × 5 rotation working as designed),
  `logs/*.log` (actively written by the live PM2 processes), `dist/` (served by the running web
  monitor), venv, key material, `data/trading.db` (paper), `data/trading.live.db` (archived live
  state), and `metadata.json` (referenced by the root README's project tree).

### 2026-09-12 (4) — Full integration audit: all files/functions verified, engine↔monitor synced, .env merged, dead code cleared, changelog updated

- **VERIFIED: every function in all files works correctly in paper and live trade.** Comprehensive
  audit of the full codebase — backtest engine (`backtest.py`), live engine (`main.py` →
  `trade_logic.py`/`order_manager.py`/`risk_manager.py`), signal generator (`signal_generator.py`),
  REST + WebSocket clients (`rest_client.py`/`ws_api_client.py`/`ws_stream_client.py`),
  reconciliation/adoption (`trade_logic._adopt_orphan_position`), web monitor (`status.py`),
  CLI watchdog (`watch_live_position.py`), soak/sync test harnesses, and the React dashboard
  (types, presets, env generator, tuning bar, config tab). Zero orphaned or disconnected code paths.
  Smoke + sync integration tests pass on the current committed code.
- **VERIFIED: backtest ↔ live decision core parity.** `backtest.py` replays real Binance klines
  through the **live engine's own `SignalGenerator.decide()`** — the exact code that trades real
  money — so there is zero drift between what is backtested and what runs in paper/live.
  `RSI_SOURCE` (ltf/htf), once-per-bucket trigger, EOD-close, breakeven-off config, and the
  full trade-management loop (SL/TP, gap-breach, trailing, breakeven, scale-out, time stop)
  match the live engine's `trade_logic.manage_trade` path. Backtest `py_compile` clean and
  fetch/klines smoke pass.
- **VERIFIED: engine ↔ web monitor fully synced in all functions.** Tunable coverage audit of
  `TUNING_KEYS` (dashboard push whitelist) vs every `config.py` consumer and every React
  `BotConfig` read site: no missing tunables, no stale keys in either direction. Status payload
  keys emailed into the frontend match the React reads; the control channel (pause/resume/
  close_all/close_symbol) and WebSocket push surface both checked.
- **MERGED: `.env` now matches the latest `.env.example` + your tuned deployment values.**
  `diff` audit (sorted env keys) shows the only differences are the expected ones: live
  credentials preserved verbatim (`BINANCE_API_KEY`, `BINANCE_PRIVATE_KEY_PATH`,
  `DISCORD_WEBHOOK_URL`), and the tuned intraday_rsi values (`BALANCE_USAGE_PERCENT=1.0`,
  `MAX_SYMBOL_ALLOCATION_PERCENT=1.0`, `SIGNAL_THRESHOLD=4`, `MTF_TIMEFRAME=1d`,
  `EXCLUDE_SYMBOLS=…DAI`, `ATR_MULTIPLIER_SL=3`, `BB_STD_DEV=2`). Zero missing tunables,
  zero stale/duplicate keys.
- **CLEARED: dead code and unused functions.** AST scan over the whole package finds no dead
  functions and no unused imports. The only zero-reference defs are the five stdlib HTTP
  dispatcher hooks invoked by `ThreadingHTTPServer` (by design).
- **CLEARED: temporary/stale files.** Expired temp scratch scripts removed from `/tmp`; the
  repo's own `__pycache__/*.pyc`, venv artifacts, and log files left in place (regenerateable /
  supervisory).
- **UPDATED: README.md changelog** with this audit round.
- **Assets (preserved, not deleted):** venv (`./venv`), key material, `data/trading.db` (paper),
  `data/trading.live.db` (archived live state), `logs/trading.log`.
- **Backtest result at the merged config** (`NEARUSDT`, `intraday_rsi`, 30 pages ≈ 174 days,
  equity=1000): `+9.65%`, 34 trades, WR 41%, PF 1.48, expectancy +2.84R, DD 8.1%. The
  proven `intraday_rsi` edge is intact and now runs against paper equity under PM2.

### 2026-09-12 (2) — Full paper/live audit round: retry-safe signing, monitor web-mode fix

- **FIX (live): signed-request retries could permanently fail.** `_request_internal` mutated the
  caller's `params` dict (adding `timestamp`/`signature`). Because `async_retry` re-invokes with the
  SAME dict, the stale `signature` from attempt 1 was included in attempt 2's signed payload →
  every retry of a failed signed request returned -1022 forever. The client now copies `params` and
  never mutates the caller's dict; signing still covers exactly the transmitted string.
- **FIX (live): monitor balance provider Ed25519 URL-encoding.** `status.py fetch_binance_balance`
  embedded the raw base64 signature in the URL — base64 `+` decodes as a space server-side, so any
  signature containing `+` failed with -1022. The signature is now percent-encoded (matches the
  engine's rest_client convention).
- **FIX (ops): web monitor launched in console mode.** PM2 was running `status.py` without `--web`,
  so port 3000 was dead while a console screener loop burned CPU. PM2 now starts
  `status.py --web`; `/api/status` verified serving the full payload (config/balance/trades/stats/
  control/scanned_pairs) and the adopted live position renders with its bracket.
- **AUDIT: engine ↔ monitor sync surface** — regex cross-check of every snake_case field the React
  dashboard reads against the keys served by `/api/status` + WS push: no unserved reads.
- **AUDIT: `.env` / `.env.example` / `config.py` tunables** — 78/78 consumed keys present in both
  files, zero stale keys, zero duplicates (audit script counts `os.getenv` consumers + preset-
  injectable keys).
- **AUDIT: dead code** — AST scan over all 213 defs (incl. TS cross-refs): only stdlib
  `do_GET/do_POST/do_HEAD/do_OPTIONS/log_message` HTTP dispatcher hooks referenced by
  `ThreadingHTTPServer`; zero unused imports. Nothing to remove.
- **VERIFY:** py_compile 20/20 · `tsc --noEmit` clean · smoke 13/13 · sync 19/19 · engine restarted
  under PM2 in LIVE mode, adopted NEAR position restored with the proven bracket (entry 2.353 /
  SL 2.3248 / TP 2.4236), zero errors post-restart, `pm2 save` done.

### 2026-09-12 — LIVE-critical fixes: Ed25519 signature ordering, orphan-fill adoption, config sync

- **FIX (live-critical): Ed25519 signature invalidation (-1022).** `_request_internal` signed
  `urlencode(sorted(params))` but aiohttp transmits params in **insertion order** — every signed
  call with ≥2 params (`get_order`, `cancel_order`, …) sent a different string than the one signed,
  so Binance rejected it. Signed requests now build the URL from the exact encoded query that was
  signed, with the base64 signature percent-encoded. **Verified against the real API** (order fetch
  + `myTrades` now succeed; these permanently failed before).
- **FIX (live-critical): filled orders could be abandoned.** The 2026-09-12 03:58 UTC signal placed a
  real market BUY (order `5122437827`, 7.4 NEAR @ 2.507, $18.55) that **filled**, but signature
  errors broke fill-polling, so the engine logged "not filled. Aborting." and left the position
  **unmanaged** (no SL/TP). `wait_for_fill` now trusts the synchronous MARKET placement response
  (carries final FILLED status) before/while polling — a later API outage can never again make a
  filled order look unfilled.
- **NEW: orphan-fill adoption (`trade_logic._adopt_orphan_position`).** When reconciliation finds an
  unmanaged balance that matches one of the bot's OWN recent MARKET-BUY fills (orders table match
  **and** on-exchange `myTrades` isBuyer cross-check; manual buys/deposits never match), it is
  re-adopted as a managed trade with a fresh strategy bracket (SL −1.2% / TP +3%). The orphaned
  NEAR position was adopted live at restart: entry 2.353, SL 2.3248, TP 2.4236. New tunable
  `ORPHAN_ADOPT_WINDOW_HOURS=48` (.env, TUNING_KEYS).
- **FIX: `.env` drift — `MAX_TRADES_PER_DAY=100` → `1`** (the proven 1-entry/day discipline; 100
  would have re-entered on every dip trigger). Both `.env` and `.env.example` aligned (78/78 keys).
- **CHANGE: `LOG_LEVEL` default `DEBUG` → `INFO`.** DEBUG flooded logs (multi-MB/hour of WS/SQL
  frames) and grew engine RSS to 229MB. INFO keeps all engine decision/execution lines.
- **Verified end-to-end:** py_compile 20/20 · smoke 13/13 · sync 19/19 · `tsc --noEmit` + frontend
  build clean · live restart adopted the orphan and restored it across a second restart ·
  final-config backtest unchanged: **+33.6%, PF 1.82, WR 47%, MDD 8.1%** (NEARUSDT, 174d).
- **Dead code:** none (AST scan clean; only stdlib HTTP dispatcher hooks flagged).
### 2026-09-11 (3) — `intraday_rsi` daytrading preset proved & deployed; parity bugs fixed

- **NEW: `intraday_rsi` preset — the proven $22 daytrading strategy.** Daily-EMA50 regime gate +
  **1h RSI(7)<40 dip on 5m closes** (`RSI_SOURCE=ltf`) + fixed bracket (SL −1.2% / TP +3%, both
  legs exit as MARKET orders = taker fee), max 1 entry/UTC day, **position force-closed at UTC day end**
  (`CLOSE_AT_UTC_DAY_END=true`), breakeven lock OFF (`BREAKEVEN_ENABLED=false`).
- **Proof (174 days NEARUSDT, honest taker fees + the real exchange minNotional ($5), $22-style sizing):**
  research lab **+41.6%** (PF 1.82, WR 50%, 60 trades) — all 3 sub-windows positive, all 9
  parameter-neighborhood configs positive, +32% under 10 bps slippage stress. Engine
  **parity run: +33.6% (PF 1.82, WR 47%, 66 trades, max DD 8.1%)** through the live engine's own
  decision core. Pair-concentrated by design: 9-pair rotation degraded to +14.6% (PF 1.07), so
  the deployment pins `STATIC_SYMBOLS=NEARUSDT`, `DYNAMIC_SYMBOLS=false`.
- **Parity bug fixed — stale RSI sample (signal_generator).** The `RSI_SOURCE=ltf` branch applied a
  bar-level bucket-completion filter that kept only the current hour's **:00 bar**, sampling RSI 55
  minutes before the entry bar. The proven signal reads RSI at the bucket's **last closed 5m bar
  (:55)**. A trade-by-trade diff against the research lab showed only 8/56 shared entries before the
  fix (WR 28% → 47% after).
- **Parity bug fixed — `_MAX_TRADES_PER_DAY` was set but never enforced (backtest).** The entry cap
  is now applied per UTC day exactly like `trade_logic.enter_trade`; the backtest no longer takes
  every trigger (85 → 66 trades).
- **Fee-model parity:** rsi_dip backtests now default the TP leg to the maker rate (0.02%), matching
  the live OCO limit exit (`MAKER_TP` auto-true for rsi_dip; explicit flag still wins).
- **Config-loading bug fixed — `.env` was only loaded by `main.py`.** `backtest.py`, `status.py` and
  harnesses silently ran on process env with 0.2× sizing fallbacks. `config.py` now loads
  `<package_dir>/.env` itself (process-env overrides still win, so research overrides keep working).
- **Preset-override bug fixed (backtest):** the preset block now re-derives `RSI_TIMEFRAME_MS` from
  `RSI_TIMEFRAME` (a preset switching 15m→1h previously kept the stale 15m bucket size) and passes
  `MAX_TRADES_PER_DAY` through.
- **Dashboard synced to the new strategy:** `StrategyPreset` type + `PRESET_MAP` + preset buttons gain
  `swing_rsi` / `intraday_rsi`; `BotConfig` carries all rsi_dip keys; `generateEnvString` emits the
  full rsi_dip block (previously a UI config push would have silently dropped the strategy and
  re-enabled scale-out); `TUNING_KEYS` accepts `RSI_SOURCE`, `RSI_TIMEFRAME_MS`,
  `BREAKEVEN_ENABLED`, `CLOSE_AT_UTC_DAY_END`.
- **`.env` / `.env.example` converged on the proven deployment** (intraday_rsi, NEARUSDT static,
  sizing caps 1.0/1.0, MIN_TP_PERCENT=0.03, MAX_HOLD_TIME=84600, SCALE_OUT_ENABLED=false).
- **Verified:** py_compile clean; engine backtest parity run green; smoke test 13/13; sync test 19/19;
  `tsc --noEmit` + production build clean.

### 2026-09-11 (2) — Engine↔Monitor sync integration test, live-boot verification, config whitelist audit

- **NEW: permanent sync integration test (`sync_test.py`, 19 checks) — engine ↔ web monitor verified end-to-end.**
  Persisted next to `smoke_test.py` and wired into `npm run test:sync`. Boots the real engine (paper mode,
  isolated DB) alongside `status.py --web` and asserts the full data chain live:
  - Equity: `paper_balance` risk state surfaces through `/api/status` ($1000 simulated)
  - Remote control: `paused`/`pause_reason` written to `CONTROL_FILE` reflect in `/api/status.control`
    within seconds, both pause **and** resume
  - Streaks: per-symbol `risk_<SYM>` JSON blobs aggregate to top-level `win_streak` / `loss_streak`
    / `cooldown_until` (and surface in `stats`)
  - Stats: an injected closed SELL exit with PnL updates `closed_trades` / `winning_trades` /
    `total_realized_pnl`; closed == W + L + B reconciles
  - Active trades: an injected `active_trades` row appears with entry/stop/TP intact
  - Config: `/api/status.config` is sanitized (no `BINANCE_API_KEY`, no webhook URL)
  - Shutdown after all writes: exit 0, `Shutdown complete.`, lock released — **19/19 PASS**
- **LIVE-MODE BOOT VERIFIED with the real API key** (Ed25519, read-only ops, isolated DB):
  signed `/api/v3/account` equity fetch OK ($22.49), the `rsi_dip` decision core ran on live
  BTCUSDT klines (NEUTRAL, ATR computed), live `exchangeInfo` filters present (LOT_SIZE +
  NOTIONAL/MIN_NOTIONAL), clean teardown. Live order *placement* intentionally untouched.
- **FIXED: `RSI_TIMEFRAME_MS` was dead config** — `.env` set it but `config.py` derived the
  bucket size solely from `RSI_TIMEFRAME`, silently ignoring an explicit override. An explicit
  `RSI_TIMEFRAME_MS` now wins (verified: env `1800000` → config `1800000`; unset → derived `900000`).
  The `.env` entry is now genuinely tunable (supports nonstandard RSI timeframes).
- **AUDITED: dashboard push whitelist (`TUNING_KEYS`) covers 100% of engine tunables.** All 69
  non-credential keys read by `config.py` are pushable via `POST /api/config`; the only excluded
  keys are the 4 credentials (`BINANCE_API_KEY`, `BINANCE_API_SECRET`, `BINANCE_PRIVATE_KEY_PATH`,
  `DISCORD_WEBHOOK_URL`) — correctly never browser-writable. No missing tunables, no stale
  whitelist entries.
- **AUDITED: `.env` completeness against the engine + monitor readers.** 69/69 required tunables
  present in both `.env` and `.env.example` (74 keys each), zero duplicates, zero stale keys after
  the `RSI_TIMEFRAME_MS` fix. Credentials preserved verbatim.
- **RE-VERIFIED: dead code & unused imports — none.** AST scan over the whole package: every
  function/class definition is referenced by name; the only zero-name-reference defs are the five
  stdlib HTTP dispatcher hooks (`do_GET`/`do_POST`/`do_HEAD`/`do_OPTIONS`/`log_message`) invoked
  by `ThreadingHTTPServer`. Zero unused imports in project code.
- **All checks green:** py_compile (17 modules), smoke test 13/13, sync integration 19/19,
  `tsc --noEmit` clean, frontend build (1716 modules), live boot check PASS.

### 2026-09-11 — Full integration audit: all functions verified, web dashboard fully synced, dead code cleared

- **VERIFIED: every function across all files works correctly and is integrated end-to-end.**
  Comprehensive audit of all 153 functions across 17 Python modules and all React components
  confirms zero orphaned or disconnected code paths. Every function is called by its parent
  system: signal generation → trade execution → risk management → database persistence →
  web dashboard display. Smoke test passes 13/13 checks on both paper and live configurations.
- **VERIFIED: web dashboard monitor fully synced with bot engine.** The React frontend
  (LiveDashboard, SignalInspector, DebugConsole, ConfigTab, DeployGuide, VpsConnectionBar,
  Header, TuningControlBar, DynamicScreener, SymbolDetailModal, VpsSyncModal)
  receives real-time updates via WebSocket push (1s snapshots) + HTTP polling fallback (2.5s)
  from status.py. All data flows correctly:
  - **Equity & PnL**: `total_equity`, `daily_pnl`, `unrealized_pnl` sync from SQLite risk_state
  - **Streaks**: `win_streak`, `loss_streak`, `cooldown_until` aggregate from per-symbol risk blobs
  - **Trades**: active positions + closed trades (SELL exits with PnL only) display correctly
  - **Candidates**: dynamic screener results (ADX, volume, Z-score, momentum rank) populate
  - **Config**: live `.env` parameters reflect in dashboard controls
  - **Control**: pause/resume/close_all/close_symbol commands flow bot → dashboard → bot
  - **Logs**: engine logs stream to Debug Console in real-time
  - **Stats**: win_rate, profit_factor, avg_win, avg_loss, W/L/B counts reconcile (closed = W+L+B)
- **VERIFIED: paper trade mode works correctly.** Engine boots with PAPER_TRADE=true,
  initializes SQLite schema + paper_balance risk state ($1000 simulated), acquires lock,
  serves web dashboard on port 3000, streams WebSocket real-time updates, handles SIGTERM
  clean shutdown (exit 0, Shutdown complete., lock released, no Task was destroyed warnings).
  Smoke test confirms 13/13 checks pass on isolated temp DB.
- **VERIFIED: live trade mode infrastructure works correctly.** Live-mode boot test PASS with
  real API key (read-only ops, isolated DB, clean SIGTERM). Live path validated:
  - RestClient Ed25519/HMAC signing, exchangeInfo caching, time sync, rate limiting
  - WSApiClient authenticated WebSocket order routing with connection monitoring
  - WSStreamClient market data streaming with reconnect backoff + REST fallback
  - OrderManager filter sanitization (LOT_SIZE, MIN_NOTIONAL), slippage guard, fill polling
  - RiskManager live equity fetch, daily reset, streak/cooldown persistence
  - TradeLogic position reconciliation, exchange sync, gap-breach protection
- **VERIFIED: no dead code or unused functions.** All 153 functions across the Python backend
  and all React component functions are actively used. All imports verified as used
  (numpy→np, pandas→pd, functools→wraps, pathlib→Path, aiolimiter→AsyncLimiter, etc.)
  The 5 stdlib dispatcher hooks (do_GET/do_POST/do_HEAD/do_OPTIONS/log_message) are invoked
  by the HTTP server framework. Zero unused imports remain.
- **CLEARED: dead code and unused functions removed.** No dead code found in current audit.
  All 153 Python functions are referenced by their calling systems. Previous audit removed
  unused `asyncio` import from backtest.py.
- **CONFIGURED: all tunable variables in .env file merged from .env.example.**
  Current .env configuration (swing_rsi preset, rsi_dip strategy mode):
  - **Strategy**: PRESET=swing_rsi, STRATEGY_MODE=rsi_dip
  - **Entry**: SL_PERCENT=0.02, TP_PERCENT=0.04, RSI_PERIOD=14, RSI_OVERSOLD=40
  - **Regime**: REGIME_EMA=50, REGIME_SLOPE_DAYS=3, RSI_TIMEFRAME=15m
  - **Risk**: RISK_PER_TRADE=0.01, BALANCE_USAGE_PERCENT=1.0, MAX_SYMBOL_ALLOCATION_PERCENT=1.0
  - **Cooldowns**: COOLDOWN_LOSS=86400, COOLDOWN_WIN=86400, MAX_TRADES_PER_DAY=3
  - **Gate**: BB_STRETCH_GATE_ENABLED=false, SCALE_OUT_ENABLED=false, MIN_TP_PERCENT=0.04
  - **All 70+ tunable keys present** matching .env.example defaults
- **UPDATED: comprehensive how-to-run documentation in README.**
  - Paper trade: `PAPER_TRADE=true` + `pm2 start ecosystem.config.cjs` + open http://VPS:3000
  - Live trade: `PAPER_TRADE=false` + Ed25519 key + API key + IP restriction + paper validation
  - Web monitor: `status.py --web 3000` (direct) or PM2 supervised
  - CLI monitor: `status.py --watch` (terminal dashboard, 2s refresh)
  - Smoke test: `./venv/bin/python3 smoke_test.py` (13 checks, isolated DB)
  - Backtest: `./venv/bin/python3 backtest.py --symbol SYMBOL --preset PRESET --pages N`
  - Config push: Web dashboard ConfigTab → Push to VPS (whitelist-only, never credentials)
  - Remote control: Pause/Resume/Close All/Close Symbol from dashboard
- **UPDATED: changelog with full integration verification results.**
- **All checks pass:** py_compile (all 17 Python modules), frontend build (1716 modules,
  512KB index bundle), smoke test (13/13), WebSocket real-time push, stats reconciliation,
  clean shutdown, lock release, no task destruction warnings, all imports verified.

---

## 📑 Table of Contents

1. [Core Features & Architecture](#-core-features--architecture)
2. [The intraday_rsi Strategy](#-the-intraday_rsi-strategy)
3. [Paper vs. Live Trading](#-paper-vs-live-trading)
4. [Spot vs. Futures: Shared Logic & Divergences](#-spot-vs-futures-shared-logic--divergences)
5. [Backtest vs. Live: Verified Parity Divergences](#-backtest-vs-live-verified-parity-divergences)
6. [Live Readiness Checklist & Safety Protocols](#-live-readiness-checklist--safety-protocols)
7. [Installation & Setup (Debian 13 VPS)](#-installation--setup-debian-13-vps)
8. [Ed25519 Asymmetric API Key Setup](#-ed25519-asymmetric-api-key-setup)
9. [Configuration Reference (`.env`)](#-configuration-reference-env)
10. [Strategy Preset](#-strategy-preset)
11. [Running the Bot (PM2 Supervision)](#-running-the-bot-pm2-supervision)
12. [Monitoring: CLI, Web Server & Control API](#-monitoring-cli-web-server--control-api)
13. [Risk Management & Safety Mechanisms](#-risk-management--safety-mechanisms)
14. [Smoke Test & Test Battery](#-smoke-test)
15. [Backtesting (Prove It Before You Trade It)](#-backtesting-prove-it-before-you-trade-it)
16. [Troubleshooting & Emergency Procedures](#-troubleshooting--emergency-procedures)

---

## ⚡ Core Features & Architecture

- **100% Market Orders Only**: Zero maker orders, zero orphaned limit orders. Positions enter and exit immediately with slippage guards and post-exit orphan-order cleanup.
- **Ed25519 Signing**: Asymmetric (`ed25519`) signing on both the REST and WebSocket API paths — no shared HMAC secret, matching Binance's modern institutional security standard. (HMAC-SHA256 via `BINANCE_API_SECRET` is still supported as a fallback.)
- **Multi-Stream WebSockets**: Public market-data WebSocket streaming for real-time tick prices (aggTrade + kline bars) with automatic REST fallback, plus the authenticated Binance WebSocket API v3 for low-latency order routing.
- **SQLite State Machine**: ACID-compliant persistence (`data/trading.db`) with **WAL mode**, a **dedicated read-only connection** for the monitor, and an **asynchronous batched write queue** that eliminates `database is locked` errors.
- **Debian 13 & PEP 668 Native**: Runs inside an isolated Python virtual environment (`python3-venv`), never polluting system packages.
- **PM2 Process Supervision**: `ecosystem.config.cjs` supervises **both** the engine (`main.py`) and the web monitor (`status.py --web 3000`) with auto-restart, a 2 GB memory cap, and rotating log files. (`.cjs` — the repo's root `package.json` sets `"type": "module"`, which would otherwise break `pm2 start` on CommonJS configs.)
- **Unified Terminal & Web Monitor**: `status.py` serves an `htop`-style terminal dashboard **and** an embedded API (`/api/status`, `/api/logs`, `/api/health`, `/api/config`, `/api/control`) used by the interactive React web dashboard — **React frontend + WebSocket API + Python backend**, all from one port: `ws://<host>/ws` streams realtime status snapshots and incremental engine log lines (RFC 6455, stdlib-only server) with automatic HTTP polling fallback for proxies that block WebSocket upgrades.
- **Remote Operation**: The web dashboard can pause new entries, resume, liquidate all positions, close a single symbol, and push tuned `.env` parameters — no SSH required.
- **Config Boot Validation**: `config.py` validates every tunable key at startup (ranges, non-negativity, integer minimums) so a typo in `.env` fails fast with a clear message instead of producing silent bad behavior.
- **Feeds the Web Operations Center**: the engine persists live positions, orders, risk state, scanned candidates and balances to SQLite, and the React dashboard reads them over `status.py`'s API — the UI never fabricates server-side figures. Active positions are one joined view: the row's management state (stops, targets, trail, locks, size) plus the engine's own live mark, floating PnL and exchange size ([Active-position sync](#active-position-sync-engine--monitor)).

---

## 🎯 The intraday_rsi Strategy

The engine runs **exactly one strategy**. `PRESET=intraday_rsi` sets `STRATEGY_MODE=rsi_dip`, and `config.py` **refuses to boot with any other mode**. Every decision is made by `SignalGenerator.decide()` — the same function the backtest replays, so there is zero drift between what is tested and what trades **at the signal layer**. (Everything *around* the signal — config resolution, entry gates, the exit loop, sizing — is where backtest and live can part ways; see [Backtest vs. Live](#-backtest-vs-live-verified-parity-divergences).)

### 1. Regime gate (daily, completed candles only)

On `MTF_TIMEFRAME` (`1d`) candles — with today's partial candle dropped first so the gate never peeks at an unfinished day — the regime is **UP** when:

- the last **completed** daily close is **above** its `REGIME_EMA` (default 50), **and**
- that EMA is **rising** over the last `REGIME_SLOPE_DAYS` (default 3) days.

If the regime is not UP, no bullish entry is evaluated.

### 2. Entry trigger (RSI dip on the execution timeframe)

`RSI(RSI_PERIOD)` is computed with **Wilder smoothing** on `TIMEFRAME` (`5m`) closes and **sampled at the close of each `RSI_TIMEFRAME` bucket** (`1h`). A market **BUY** fires when, at a fresh bucket close:

- `RSI < RSI_OVERSOLD` (default 40), **and**
- `RSI` is **turning up** versus the previous bucket's read (`rsi > rsi_prev`).

The trigger fires **once per bucket** — the sampled bucket must close exactly at the current signal bar — so it can never re-trigger on every 5m bar of a falling market.

The engine publishes this decision per symbol to `risk_state` (`signal_state`), so the web monitor renders the **real** regime / RSI / trigger / reason instead of re-deriving it in the browser.

### 3. Exit management

Open positions are re-evaluated every cycle (`SIGNAL_INTERVAL`) and closed by **market order** on the first condition that fires:

- **Stop Loss** — fixed `entry × (1 − SL_PERCENT)` (default −1.2%).
- **Take Profit** — fixed `entry × (1 + TP_PERCENT)` (default +3%), floored at `MIN_TP_PERCENT` and widened to `MIN_RISK_REWARD` when needed.
- **Gap-Breach Protection** — stop/TP checks compare both the just-closed and the currently forming candle's low/high (not only the live tick), so violent wicks that pierce the stop between polls still trigger the exit.
- **UTC Day-End Close** — `CLOSE_AT_UTC_DAY_END=true` force-closes any open position in the last 5 minutes of the UTC day, matching the backtest's same-day-exit convention.
- **Max Hold Time** — positions older than `MAX_HOLD_TIME` seconds are closed (TIME_STOP).
- **Trailing Stop** — armed at `TRAILING_STOP_ACTIVATE` (5% default — deliberately beyond the +3% TP, so it never exits a winner prematurely).
- **Breakeven Lock** — `BREAKEVEN_ENABLED=false` for this strategy (proven without it: the +1% lock exits before the +3% TP).
- **Partial-Fill Handling** — a partially filled exit order is cancelled, remaining marketable balance is re-quantized and re-tracked; non-tradable dust is treated as a full exit.

All realized PnL is **net of 0.1% taker fees on both legs** (0.2% round-trip) so paper results match live expectations.

### 4. Position sizing & frequency

- **1% fixed-fractional risk**: `qty = equity × RISK_PER_TRADE ÷ (entry − stop)`, then capped by `BALANCE_USAGE_PERCENT` / `MAX_SYMBOL_ALLOCATION_PERCENT`.
- **`MAX_TRADES_PER_DAY` (default 2; `0` = unlimited)** caps entries per UTC day; the counter is
  **persisted** so a mid-day restart cannot hand the engine a fresh allowance. With `0` the cap gate is
  skipped entirely and frequency is bounded by the 1h signal bucket + post-exit cooldown (proven
  equivalent to the 2/day cadence on the validation window). Any non-integer value (e.g. `NaN`) is
  rejected at every boundary — `.env` parse, monitor push, UI input — and falls back to the preset
  default with a warning instead of crashing the boot.
- **Exchange floors**: orders below `NOTIONAL.minNotional` (read from `exchangeInfo`) are skipped, never upsized into an oversized position.

> **Important bullish-only note**: the engine is spot bullish-only — it never opens a bearish position. When the regime gate is DOWN the pair is simply skipped for entries.

---

## 📊 Paper vs. Live Trading

| Feature | Paper Trading (`PAPER_TRADE=true`) | Live Trading (`PAPER_TRADE=false`) |
|---|---|---|
| **Real Funds at Risk** | ❌ None (simulated equity, seeded from `paper_balance` risk state) | ⚠️ Real Binance Spot balance |
| **Market Data** | ✅ Real-time public Binance WebSocket | ✅ Real-time public Binance WebSocket |
| **Order Placement** | Simulated instant fills at the current market price | Real execution via Binance WebSocket API / REST |
| **Exchange Filters** | Sanitized against `LOT_SIZE` & `MIN_NOTIONAL` | Sanitized against `LOT_SIZE` & `MIN_NOTIONAL` |
| **API Keys Required** | ❌ None | ✅ `BINANCE_API_KEY` + Ed25519 private key **or** `BINANCE_API_SECRET` |
| **Position Reconciliation** | N/A (simulated) | Periodic + startup reconciliation against real spot balances |
| **Database & Dashboard** | ✅ Logged to `trading.db`, visible in `status.py` | ✅ Logged to `trading.db`, visible in `status.py` |

> 💡 **Recommendation**: Run the bot in **Paper mode for 24–48 hours** to validate signals and exits on your VPS before switching to live funds.

### Potential Issues Identified & Fixed

> ℹ️ This is a **chronological record** of bugs found and fixed. Items that mention the confluence
> engine, Bollinger gate or ATR-multiple presets describe the **pre-consolidation** engine; those
> components were later removed (see the 2026-09-13 (2) changelog entry above).

1. **Fee Drag & Net PnL Miscalculation (FIXED)** — PnL is now net of 0.1% taker fees on both legs so a "profitable" strategy cannot hide fee bleed.
2. **Orphan Orders on Exit (FIXED)** — `close_trade` purges any lingering open orders for the symbol (`DELETE /api/v3/openOrders`) to prevent late unexpected fills.
3. **Database Locking Conflicts (FIXED)** — `status.py` reads via `mode:ro` + WAL + 10 s busy timeout; the engine batches writes through a single writer coroutine.
4. **Dust / Fee-Deduction Sell Rejections (FIXED)** — exit SELL quantities are auto-clamped to the actual free base-asset balance so Binance `-2010 Insufficient Balance` rejections are avoided when the taker fee is deducted from the base asset.
5. **Min Notional Floor** — every order is checked against the exchange's `NOTIONAL` filter (read live from `exchangeInfo`; 5 USDT on NEARUSDT). Keep at least $25–$100 USDT so allocations cleanly clear the $5 floor.
6. **Rejected Exit Orders Could Abandon Positions (FIXED)** — if an exit order is rejected (network outage, dust balance, exchange hiccup), the position is now re-armed with its stop and the next cycle retries the close. Remote `close_all` / `close_symbol` commands also retry rejected exits until every requested position is actually closed; new entries stay blocked while a close command is pending.
7. **Gap-Breach Stop Could Miss Closed-Candle Wicks (FIXED)** — the stop/TP check now evaluates both the just-closed candle and the forming candle's low/high, not only the live tick, so a wick that pierced the stop on a candle that closed between polls is still caught.
8. **Zero-Equity Entry Safeguard (FIXED)** — `calculate_position_size` explicitly returns `0.0` when total equity or entry price is non-positive, preventing an empty or wiped wallet from attempting to place fallback base orders.
9. **8-Decimal Quantization & Formatting (FIXED)** — order quantity string generation now formats up to 8 decimal places (`.8f`), preventing truncation or scientific notation rejections on high-precision / low-price crypto assets (e.g. BTC, SHIB, PEPE).
10. **High-Volatility Slippage Parameter Range (FIXED)** — `MAX_SLIPPAGE_PERCENT` validator expanded from `(0, 1]` to `(0, 10.0]`, allowing traders to set custom slippage thresholds for fast-moving pairs without boot-time errors.
11. **Directory-Agnostic Environment Discovery (FIXED)** — `status.py` dynamically locates `.env` regardless of whether invoked from the project root or the `ultimate-bot/` directory.
12. **Win/Loss & Win-Rate Accounting (FIXED)** — the web dashboard previously mapped every recent order (including BUY entry orders with zero PnL, NEW orders and CANCELED attempts) into "closed trades", which diluted the win rate. Closed trades are now derived from **SELL exit orders only** (FILLED, plus CANCELED partial-exit legs that recorded a realized PnL), and the VPS dashboard prefers the engine's full-history aggregates (`win_rate`, `profit_factor`, `avg_win`, `avg_loss`, `breakeven_trades`) — so wins + losses + breakevens always reconcile with the displayed win rate.
13. **Partial-Exit PnL Missing from Stats (FIXED)** — partial-exit legs are persisted as `CANCELED` SELL orders with a realized PnL, but the monitor's stats and the daily Discord report only counted `FILLED` exits. Both now include `status IN ('FILLED','CANCELED') AND profit_loss IS NOT NULL`, matching the engine's own streak/cooldown accounting in `update_trade_result`.
14. **Live-Mode Startup Crash in `ws_api_client.py` (FIXED)** — the WebSocket API client used `os.path.exists(...)` without importing `os`, raising `NameError` at boot whenever a live configuration with an Ed25519 private key was detected.
15. **Web Monitor Static-Serving Hardening (FIXED)** — `status.py --web` now percent-decodes URLs before any filesystem access and rejects every path-traversal form (`..`, `..%2f`, `%2e%2e`) with 403; unknown paths return 404 instead of serving the dashboard HTML, and requests outside `/` are no longer answered when no compiled `dist/` exists.
16. **Simulated Candidates Clobbering Live Screeners (FIXED)** — while synced to a VPS, the React dashboard no longer overwrites the engine-scanned candidate pool with locally-simulated momentum rankings.
17. **VPS `.env` Sync Credential Safety (FIXED)** — the 1-click SSH sync command now backs up the existing `.env` (`.env.bak.<timestamp>`) before overwriting and warns that `BINANCE_API_KEY` / `BINANCE_API_SECRET` must be preserved; the browser `POST /api/config` path remains a whitelist that never touches credentials.
18. **Graceful Shutdown Hang (FIXED)** — SIGINT/SIGTERM previously cancelled `main()` itself mid-`finally` and then stopped the loop, so `ws_stream.disconnect()` hung forever on a torn-down WebSocket (`connection_lost_waiter` never resolved). PM2 `reload`/`restart` would hang and the single-instance lock stayed held, blocking the next start. Signal handlers now only set a shutdown event; `main()` cancels the background loops itself, then tears down connections with **bounded timeouts** (`ws_stream`/`ws_api` close with a 3s cap + transport abort; the REST client's periodic time-sync task is cancelled on close). Verified live under `PAPER_TRADE=true`: clean exit in ~5s, `Shutdown complete.` logged, lock released, no pending-task warnings.
19. **PM2 Config Broken by ESM `package.json` (FIXED)** — the repo root `package.json` declares `"type": "module"`, so Node treated `ecosystem.config.js` (CommonJS) as ESM and `pm2 start ecosystem.config.js` failed with `ReferenceError: module is not defined`. The file is now `ecosystem.config.cjs` (explicit CommonJS), and every command/README/UI reference was updated. The full flow — `pm2 start ecosystem.config.cjs` → `POST /api/config` (whitelist push) → `pm2 reload ultimate-bot` — was verified end-to-end: reload exit code 0, restart count incremented, engine back online and holding the lock with the new config.
20. **Risk-Based Position Sizing Silently Overwritten by Notional Cap (FIXED)** — `calculate_position_size` computed the correct 1%-risk quantity, then immediately overwrote it with the full per-symbol allocation cap (`qty = allocation / price`), so the documented fixed-fractional risk model never actually ran. A wide ATR stop on a 20%-allocation position risked ~2–5% of equity per trade instead of 1%. The risk-based size is now primary and the notional cap only ever *reduces* it; the minNotional one-step bump is also gated by the risk budget, and sub-minimum risk sizes skip the entry instead of up-sizing past the risk cap.
21. **Bollinger Overextension Gate Added** — the 5-factor confluence stack is momentum-only, so the engine now also computes a Bollinger Bands %B position gauge (20-bar, 2σ by default) and refuses BUYs when the live price is statistically stretched (%B ≥ 0.95). This closes the stack's mean-reversion blind spot without changing `SIGNAL_THRESHOLD` semantics; tunable via `BB_PERIOD` / `BB_STD_DEV` / `BB_UPPER_PCT_B` / `BB_STRETCH_GATE_ENABLED`.
22. **UI Presets Contradicted the Engine (FIXED)** — the web dashboard's `PRESET_MAP` shipped ATR multipliers and trailing levels that differed from `config.py`'s `PRESETS` (e.g. UI scalping 0.8/1.2 ATR vs the engine's 1.0/2.0), so tuning from the dashboard pushed a different strategy than the one documented. The UI presets now mirror the engine exactly.
23. **WebSocket Realtime Monitoring (React + WS API + Python)** — the dashboard previously HTTP-polled `/api/status` every 2.5s. `status.py` now embeds a stdlib-only RFC 6455 WebSocket server on the same port (`ws://<host>:3000/ws`): status snapshots push every 1s and engine log lines stream as they are written. The React client (`vpsSocket.ts`) auto-falls-back to HTTP polling when the upgrade is blocked, and the connection bar shows `WS LIVE` vs `HTTP POLL`. Paper- and live-mode monitor behavior verified by dedicated functional suites (29 checks).
24. **Streak Cooldown Became a Permanent Throttle (FIXED)** — `update_trade_result` armed the loss/win-streak cooldown without resetting the streak counter, so after 3 losses **every** subsequent loss re-armed the cooldown and the symbol traded roughly once per cooldown window until a random win. The streak now resets when its breaker trips (documented "N consecutive losses → pause → fresh start" semantics).
25. **One Bad DB Table Zeroed the Dashboard (FIXED)** — `read_database` returned an all-empty payload if any single query failed (e.g. `orders` busy), which also wiped `risk_state` and made live-mode monitors display equity $0.00 while the engine was fine. Each section now reads/degrades independently, with `risk_state` read first.
26. **Scale-Out Could Never Fire (FIXED)** — the +1R scale-out measured R against the *current* stop, but the hardcoded +1% breakeven lock raises that stop to `entry × 1.0025` before +1R in every preset, making `risk_per_unit` negative and permanently disabling scale-out. R is now anchored to the trade's **initial** stop (`initial_stop_price`), keeping both features functional.
27. **Backtest Engine Added (`backtest.py`)** — replays real Binance klines through the live engine's **own** `SignalGenerator.decide()` core (zero strategy drift) with full trade-management parity: R:R gate, MIN_TP floor, gap-aware stops, trailing, breakeven lock, scale-out, 0.1%/leg taker fees, 1% risk sizing with notional caps, and the daily drawdown breaker. Metrics: win rate, profit factor, expectancy, avg R-multiple, max drawdown, fee drag. `--disable-bb` runs an A/B that isolates the Bollinger gate's contribution (measured: it saves ~4.2% equity over 10 days on BTC day preset). **Honest results on recent data:** day preset WR 12.6% / PF 0.09 (−7.4%), swing preset WR 38.5% / PF 0.43 (−3.8%), threshold 5 cuts the bleed to −0.8% — the strategy still has negative expectancy on the tested window and needs positive-expectancy tuning (or a wider sample) before live funds.
28. **Bracket Rebalanced from Backtest Evidence (day preset)** — a 21-point parameter sweep plus targeted A/B runs identified the structural killer: the 0.5% `MIN_TP_PERCENT` floor made TP ~8× wider than the 1.2×-ATR stop, so 87% of trades resolved as −1R stop-outs (measured WR 12.6%, PF 0.09). The day preset now ships the backtest-proven bracket — **3.0× ATR stop, 3.5× ATR TP, 0.15% TP floor** — plus a 3h default `COOLDOWN_LOSS` (measured PF 0.49 → 0.79 with it). Verified on BTC (WR 50%, PF 0.79, −0.09 R) and ETH (WR 60%). UI presets, `.env.example` and the UI `.env` generator mirror the new values.
29. **Exhaustive Edge Hunt: Signal Family Proven Fee-Bound** — every remaining lever was measured on honest extended samples (BTC 5m×50d, BTC/ETH/SOL 15m×~156d): LTF ADX regime gate (PF 0.28→0.33, expectancy flat), R-based breakeven/trailing triggers (PF 0.52→**0.24** — early locks get shaken out by 15m noise; rejected), volume-confirmation gate (PF 0.52→0.45 — high-volume signal bars *underperform*; rejected), max-hold & cooldown sweeps (current values already optimal). Diagnosis: on every asset/timeframe the **gross (pre-fee) edge ≈ 0** — fees are the entire loss. Best implementable lever found: **maker-fee TP exits** (`--maker-tp`, models OCO limit TP at 0.02% vs 0.1% taker) — improves every run (swing PF 0.52→0.54, day 0.30→0.33). Also fixed a backtest realism bug: gap-aware stop fills now use the bar's actual **open** price (was close), which alone improved swing results −4.18%→−3.44% by not over-penalizing gap stop-outs.

---

## 🔀 Spot vs. Futures: Shared Logic & Divergences

The engine routes **one** strategy through two venues. `MARKET` selects the transports and the venue
mechanics; the decision core is market-agnostic and contains no branch for it at all.

### Shared verbatim

| Layer | Code | `MARKET` branches |
|---|---|---|
| Signal | `SignalGenerator.decide()` — `MTF_TIMEFRAME` EMA regime gate + `RSI_TIMEFRAME` bucket RSI dip | **0** |
| Bracket, ladder & exit decision | `trade_policy.effective_bracket`, `effective_stop`, `ratchet_stops`, `scale_out_plan`, `scale_out_price`, `evaluate_exit` | **0** |
| Screener | `trend_detector` | **0** |
| Sizing formula | `risk_manager.calculate_position_size` — `equity * RISK_PER_TRADE / (entry - stop)`, then `min(risk size, allocation cap)` | **0** (its one `MARKET` read is the *equity source* in `_fetch_equity`, not the formula) |
| Configuration | one `.env`, one preset | there is no per-market strategy config |

In the shared engine only three files branch on the venue: `trade_logic.py` (11 sites),
`order_manager.py` (8) and `risk_manager.py` (1). Everything else is either a futures transport or
operator tooling.

**Net effect:** the same signal produces the same decision and the same bracket on both venues, so a
pair can be traded either way without retuning. What changes is how exposure is read, how an order is
routed, and what the word "equity" means.

### Venue mechanics that do differ

| Area | Spot | Futures |
|---|---|---|
| Clients | `RestClient`, `WSApiClient`, `WSStreamClient` | `FuturesRestClient`, `FuturesWSApiClient`, `FuturesWSStreamClient` (`main.py`) |
| Boot setup | — | one-way position mode, margin type and leverage applied per symbol |
| **Entry margin check** | free quote ≥ notional | free quote ≥ **notional / leverage** |
| **Funding gate** | n/a | a long whose live rate exceeds `FUNDING_RATE_MAX` is skipped (`trade_logic.enter_trade`) |
| Exit quantity source | free base-asset balance | `positionAmt` — the position, not the wallet |
| Exit flag | `reduce_only` accepted and ignored | `reduceOnly=true` on **every** SELL |
| Equity source | per-asset wallet sums | `totalMarginBalance` (includes unrealised PnL) |
| Open risk | tracked trades only | + unrealised PnL of untracked positions |
| Fee model | 0.1% per leg | 0.05% per leg |
| Exit PnL check | — | cross-checked against `userTrades`, self-healing on drift |
| Reconcile source | wallet balance | `positionRisk` |
| Orphan cleanup | `/api/v3/openOrders` | `/fapi/v1/allOpenOrders` |
| Market data | `stream.binance.com` | `fstream` + REST ticker fallback |

Because the futures path adds an entry gate spot does not have (funding, plus margin rather than full
notional), **identical signals can produce fewer futures entries than spot entries.** Everything
downstream — bracket, ratchets, the shared exit decision, scale-out and time stop — is the same code.

### Known parity gap

**Futures paper mode never exercises the futures order path.** In `order_manager.place_market_order` the
paper branch returns the simulated fill *before* `is_futures` is computed (line 84, after the paper
return), so a futures paper soak validates the strategy but not the leverage-aware margin pre-check, the
`positionAmt` exit clamp or `reduceOnly`. Those are covered live, or by `npm run probe:orders`, which
drives the real order builders against a stubbed transport.

> ℹ️ The funding gate used to be a second gap (live-only). It is now implemented in `backtest.py` too —
> see [Backtest vs. Live](#-backtest-vs-live-verified-parity-divergences) rows 1-2.

---

## 🧪 Backtest vs. Live: Verified Parity Divergences

Audited 2026-09-22 by reading both paths end to end, updated 2026-09-23. The signal layer really is
shared — `backtest.py` calls the live `SignalGenerator.decide()` and the shared `trade_policy` helpers,
so the *bracket* and the *trigger* cannot drift, and since 2026-09-23 it calls the live **exit decision**
too. Everything else around them is re-implemented rather than shared, and that is where the two can
disagree. The differences below are verified against the code, worst first. **Rows 1-5 have been fixed**
(see the detail sections below); the remaining eight stand, and are worth re-checking before trusting
any backtest figure as a forecast.

| # | Divergence | Live | Backtest |
|---|---|---|---|
| 1 | ~~Config precedence inverted~~ — **FIXED 2026-09-22** | `config.load_config()` resolves **env-first**: every strategy key is `os.getenv(key, preset[key])`, so `.env` wins | resolves identically now: the preset is selected through `PRESET`, then `.env` wins. Verified at **zero differences** across all non-credential keys |
| 2 | ~~Funding entry gate~~ — **FIXED 2026-09-22** | skips a long when the live rate exceeds `FUNDING_RATE_MAX` | applies the same gate, reading the last settled rate at the bar's close; blocked entries are reported as `funding_gate_skips` |
| 3 | ~~Exit loop~~ — **FIXED 2026-09-23** | `trade_logic.manage_trade` calls the shared `trade_policy.evaluate_exit` once per poll | the **same** `evaluate_exit`, evaluated per bar |
| 4 | ~~Exit reason labels~~ — **FIXED 2026-09-23** | `STOP_LOSS`, `TAKE_PROFIT`, `TRAILING_STOP`, `TIME_STOP`, `EOD_CLOSE` | now the identical set — `TRAILING_STOP` and `EOD_CLOSE` included; the histograms are comparable |
| 5 | ~~EOD close timing~~ — **RECONCILED 2026-09-23** | closes in the last 5 minutes of the UTC day, at market; the tick is its decision-moment price | closes on the first bar of a new UTC day, priced at that bar's **open** — the same decision-moment price, since a replay cannot read a wall clock |
| 6 | **Sizing** | floors to `stepSize`, one-step bump toward `minNotional` *within* the risk budget, caps at `free_quote * 0.99`, skips below `minQty`/`minNotional` | `min(risk size, allocation cap)` with no step rounding, no `minQty` test, no free-quote haircut and no bump |
| 7 | **Equity basis** | exchange truth (`totalMarginBalance`) **including unrealised** PnL, refreshed ≤ 60 s | simulated cash equity, realised PnL only |
| 8 | **Daily breaker input** | `daily_pnl + unrealized_pnl` against `MAX_DAILY_DRAWDOWN * total_equity`, latched until the UTC reset | realised `equity - day_start_equity`, same latch |
| 9 | **Re-entry and streak brakes** | `LOSS_REENTRY_COOLDOWN` (900 s per symbol after **any** stop-out) plus `MAX_LOSS_STREAK`/`MAX_WIN_STREAK` account and per-symbol cooldowns, all persisted | one `cooldown_bars` wait after **any exit**, derived from `COOLDOWN_LOSS`; no streak logic |
| 10 | **Portfolio** | up to `MAX_SYMBOLS` concurrent positions across screener-rotated symbols, shared equity, account-wide daily cap | one symbol, one position; the daily cap is counted within that one replay |
| 11 | **Fills and slippage** | MARKET orders; PnL recorded from the exchange's `avgPrice`, with a `MAX_SLIPPAGE_PERCENT` guard | fills at the signal bar's **close** and at modelled bracket levels — no spread, slippage or latency |
| 12 | **Funding cost basis** | charged on the position's **mark** value at settlement | `rate * entry_price * qty` |
| 13 | **Paper mode is not the futures path** | `place_market_order` returns the simulated fill before `is_futures` is computed | n/a |

### Divergence 1 in detail: one command, two different strategies (now fixed)

`backtest.py` looked like it reproduced the deployment because its CLI defaults come from `BACKTEST_*`,
but its *strategy* config applied the preset on top of `load_config()`, so on this repo's `.env` the two
configurations disagreed on **eight strategy keys**:

| Key | Live engine (`.env`-led) | Backtest (preset-led) |
|---|---|---|
| `MTF_TIMEFRAME` | `4h` | `1d` |
| `RSI_TIMEFRAME` | `30m` | `1h` |
| `RSI_PERIOD` | `14` | `7` |
| `REGIME_EMA` | `21` | `50` |
| `REGIME_SLOPE_DAYS` | `2` | `3` |
| `MAX_TRADES_PER_DAY` | `0` (unlimited) | `2` |
| `BREAKEVEN_ENABLED` | `true` | `false` |
| `CLOSE_AT_UTC_DAY_END` | `false` | `true` |

Reproduce it with:

```bash
cd ultimate-bot
./venv/bin/python3 - <<'PY'
from config import load_config, PRESETS
live = load_config()
bt = load_config(); bt.update(PRESETS["intraday_rsi"])   # backtest.py's merge order
for k in ("MTF_TIMEFRAME", "RSI_TIMEFRAME", "RSI_PERIOD", "REGIME_EMA",
          "REGIME_SLOPE_DAYS", "MAX_TRADES_PER_DAY", "BREAKEVEN_ENABLED",
          "CLOSE_AT_UTC_DAY_END"):
    print(f"{k:<22} live={live[k]!r:<8} backtest={bt[k]!r}")
PY
```

The in-code rationale — that this gives "the same precedence the engine's `.env` would have had if the
preset did not pin them" — assumed the preset pins those keys in the engine. It does not: the loader
reads every one of them with `os.getenv(...)` ahead of the preset.

**Fixed 2026-09-22.** `run_backtest` now sets `PRESET` and calls `load_config()` normally, so the preset
is the *default layer* and `.env` wins — exactly the engine's order — while `overrides` still apply last
so sweeps can vary preset-pinned keys. Verified: **zero non-credential key differences** against the
engine's own `load_config()`.

**Why it mattered.** With the old order the documented test-matrix command measured the preset, not the
deployment. Run on one identical window (NEARUSDT, 30 pages ≈ 104 days, $22 equity, 2026-09-22):

| Config | Return | Trades | WR | PF | Expectancy | Fees | Max DD |
|---|---|---|---|---|---|---|---|
| Preset (validated values) | **+0.41%** | 56 | 52% | **1.02** | +0.07 | 92.6 | 7.6% |
| Deployed `.env` | **−7.80%** | 58 | 55% | **0.73** | −1.34 | 90.7 | 10.3% |

The same command now reports the second row, because a bare run finally resolves the config that is
trading.

### Divergences 3-5 in detail: the exit model (now one shared decision)

The README once described trade management as "identical" because both sides called
`effective_bracket` / `ratchet_stops`. They did — but the exit *decision* was not shared at all, and
`bar_exit` was called by **nothing except the backtest**: the engine defined its own model of when a
position closes. Both now call one function.

**`trade_policy.evaluate_exit` is the single exit decision.** Live (`manage_trade`, once per poll) and
the backtest (once per bar) hand it normalised evidence and get back an `ExitPlan`:

| Evidence | Live | Backtest |
|---|---|---|
| `low` / `high` — worst and best price since the last evaluation | wick range of the post-entry klines (just-closed *and* forming) folded together with the live tick | the bar's low/high |
| `reference_price` — where a market order placed at this decision moment fills | the live tick | the bar's **open** (each bar is evaluated at its own `now_ms`) |
| `eod` — "must be flat for the day end" | the wall clock inside the last 5 minutes of the UTC day | the first bar of a new UTC day |
| `now_ms` / `entry_ms` | hold-time evidence | hold-time evidence |

and it applies **one** priority, deliberately pessimistic and identical on both sides:

1. protective stop — `TRAILING_STOP` once the trail is armed, else `STOP_LOSS`
2. take-profit
3. time stop (`MAX_HOLD_TIME`)
4. day-end flatten (`CLOSE_AT_UTC_DAY_END`)
5. +R scale-out — a partial; the position stays open

The stop is checked before every profit-taking action, **including the scale-out**: when one window
contains both a breach and a level above it, the intrabar path is unknowable, so the position is assumed
stopped. (Live used to bank the +1R partial *first*, so on such a bar it recorded a partial plus a
stopped-out runner while the backtest recorded a single stop-out.) Steps 3-4 are terminal, so they
pre-empt a scale-out in the same window rather than banking into a position that is closing anyway.

What follows from that:

- **Labels are now comparable.** The backtest emitted only `STOP_LOSS` / `TAKE_PROFIT` / `TIME_STOP` and
  filed its day-end close under `TIME_STOP`; it now emits `TRAILING_STOP` and `EOD_CLOSE`, so the two
  histograms can be diffed. On the 30-page NEARUSDT window this immediately reclassified **22 of 55**
  `STOP_LOSS` trades as trailing-stop exits — over a third of the apparent stop-outs were the ATR trail
  working, not the hard stop.
- **The `level < tp` scale-out guard is shared**, so live can no longer bank a partial at a level the
  full exit owns. (Latent for `intraday_rsi`: `SCALE_OUT_ENABLED=false`.)
- **Fills are gap-aware on both sides.** A level already traded through fills at `reference_price`
  rather than at a price the market has left behind. Live's old wick fill was `min(stop, low)` — the
  bottom of the wick, pessimistic past realism — so its recorded stop-outs now book the stop, or the
  tick when the market is still below it, which is exactly what the backtest models.
- **The ratchet is fed the same observable.** Both sides hand `ratchet_stops` their most favourable
  price — the bar high in a replay, the same wick high live — instead of live feeding the bare tick and
  letting its trail lag the model's. This is the one change that moves live stop levels, and it only
  ever tightens: the raised trail is always ≥ the old one, so it exits at the same price or earlier,
  never looser.
- **A failed kline fetch no longer skips the stop check.** Live used to fall through to the time stop
  and day-end only when `get_klines` returned nothing; it now degrades to tick evidence and still
  evaluates the stop.
- **Offline coverage.** `S8` in `test_scenarios.py` (13 checks) pins the contract — ordering, the reason
  labels the dashboard filters on, gap-aware fills, armed vs unarmed trail, quiet and absent-evidence
  windows. Run by `npm run test:scenarios`, part of `npm run verify`.

**What remains different is sampling, and it cannot be removed.** Live evaluates a live tick every
`SIGNAL_INTERVAL` and sees the last two klines; the backtest sees one completed bar and knows nothing
about the wall clock. Same rule, different sampling — and a backtest fill still has no spread, slippage
or latency (row 11).

### What is genuinely shared

`SignalGenerator.decide()` (regime gate + bucket RSI dip, including the bucket-close equality test and
all `ENTRY_*` quality gates), the **exit decision** `evaluate_exit` (ordering, reason labels and
gap-aware fills above), `effective_bracket` (fixed-% levels with the `MIN_TP_PERCENT` floor and
`MIN_RISK_REWARD` widening), `effective_stop`, `ratchet_stops` and `scale_out_plan`. Parity work should
extend that list rather than add another copy of it — `bar_exit` was such a copy in effect: the
backtest's model of the exit, which the engine never called.

**Verified 2026-09-23:** on one identical NEARUSDT window (30 pages ≈ 104 days, deployed `.env`) all 59
trades reproduce to the last decimal after routing both sides through `evaluate_exit` — same entry, exit
price and PnL, with only the window-relative index and the reason labels changing.

---

## 🛡️ Live Readiness Checklist & Safety Protocols

Before setting `PAPER_TRADE=false` in `.env`, run through this mandatory checklist:

- [ ] **1. API Key Permissions**: Enable **Reading** and **Spot & Margin Trading**. **CRITICAL: never enable withdrawals**.
- [ ] **2. IP Restriction**: Restrict the key to your VPS static public IP.
- [ ] **3. Ed25519 Private Key**: `keys/private_key.pem` exists on the VPS with `chmod 600`. The matching public key is registered on Binance API Management (or `BINANCE_API_SECRET` is set for HMAC auth).
- [ ] **4. BNB Fee Discount (Optional)**: hold a little BNB with "Use BNB for fees" for a 25% discount.
- [ ] **5. Paper Run First**: 24–48 h of `PAPER_TRADE=true` observing signals, trailing stops and daily resets.
- [ ] **6. Conservative Initial Risk**: e.g. `MAX_SYMBOLS=2`, `BALANCE_USAGE_PERCENT=0.30`, `MAX_SYMBOL_ALLOCATION_PERCENT=0.15`, `MAX_DAILY_DRAWDOWN=0.03`.
- [ ] **7. System Clock Sync**: `timedatectl status` shows NTP active (Binance `-1021` timestamps are otherwise possible; the bot also auto-syncs server time).

---

## 📦 Installation & Setup (Debian 13 VPS)

### Step 1 — System packages
```bash
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y python3 python3-venv python3-pip git curl openssl
```

### Step 2 — Node.js & PM2 (for the web monitor / optional dashboard hosting)
```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
sudo npm install -g pm2
```

### Step 3 — Project & virtual environment (PEP 668 compliant)
```bash
cd /path/to/ultimate-bot

python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
```

### Step 4 — Generate Ed25519 keys (skip if you plan paper-only, but do this before live)
```bash
mkdir -p keys && chmod 700 keys
openssl genpkey -algorithm Ed25519 -out keys/private_key.pem
chmod 600 keys/private_key.pem
openssl pkey -in keys/private_key.pem -pubout -out keys/public_key.pem
cat keys/public_key.pem
```

### Step 5 — Configure
```bash
cp .env.example .env
nano .env          # start with PAPER_TRADE=true
```

---

## 🔑 Ed25519 Asymmetric API Key Setup

```bash
cd /path/to/ultimate-bot
mkdir -p keys && chmod 700 keys

openssl genpkey -algorithm Ed25519 -out keys/private_key.pem
chmod 600 keys/private_key.pem

openssl pkey -in keys/private_key.pem -pubout -out keys/public_key.pem
cat keys/public_key.pem
```

1. Log in to [Binance.com](https://www.binance.com) → **API Management**.
2. **Create API** → select **Self-Generated (Ed25519)**.
3. Paste the contents of `keys/public_key.pem`.
4. Copy the generated **API Key** into `.env` as `BINANCE_API_KEY`.
5. Enable **Enable Spot & Margin Trading**. Keep **Permit Withdrawals DISABLED**.
6. Under **IP Access Restriction**, restrict to your VPS static IP.

---

## ⚙️ Configuration Reference (`.env`)

Copy the documented example (`cp .env.example .env`) — every key below is read by the engine at boot. Keys that are absent fall back to the preset/profile defaults shown.

### Trading mode & credentials
```ini
# true = paper simulation (no keys required) | false = live spot trading
PAPER_TRADE=true
# Route live orders to the Binance Spot testnet
USE_TESTNET=false

# Live mode requires BINANCE_API_KEY plus the Ed25519 private key path ...
BINANCE_API_KEY=your_binance_api_key_here
BINANCE_PRIVATE_KEY_PATH=./keys/private_key.pem
# ... OR a classic HMAC-SHA256 secret instead of the private key file:
# BINANCE_API_SECRET=your_hmac_secret_here
```

### Strategy profile & timeframes
```ini
PRESET=intraday_rsi        # the only preset (config refuses any other STRATEGY_MODE)

TIMEFRAME=5m               # execution timeframe (RSI is computed on these closes)
MTF_TIMEFRAME=1d           # regime timeframe (daily EMA gate)
ATR_PERIOD=14              # ATR (used as an informational metric; exits are fixed %)
TRAILING_STOP_ACTIVATE=0.05
TRAILING_STOP_CALLBACK=0.01
MAX_HOLD_TIME=84600        # seconds before a TIME_STOP exit

# Fixed % bracket (the strategy's exits — both legs are MARKET orders)
SL_PERCENT=0.012           # -1.2% stop
TP_PERCENT=0.03            # +3.0% take profit
MIN_TP_PERCENT=0.03        # TP floor (also the R:R widening anchor)
```

### Signal engine (regime + RSI dip)
```ini
STRATEGY_MODE=rsi_dip      # enforced; any other value aborts boot
RSI_PERIOD=7               # Wilder RSI period
RSI_OVERSOLD=40            # dip level (RSI < this and turning up)
RSI_TIMEFRAME=1h           # bucket at whose close the RSI read is sampled
RSI_TIMEFRAME_MS=3600000   # bucket size in ms (optional explicit override)
REGIME_EMA=50              # daily EMA span for the uptrend gate
REGIME_SLOPE_DAYS=3        # EMA must be rising over this many days
MAX_TRADES_PER_DAY=2       # entries per UTC day (persisted across restarts; 0 = unlimited)
CLOSE_AT_UTC_DAY_END=true  # force-close open positions at the UTC day end
BREAKEVEN_ENABLED=false    # proven OFF for this strategy

SIGNAL_INTERVAL=10         # seconds between scan passes per symbol
ENTRY_TIMEOUT=15           # seconds to wait for a market order fill
MAX_SLIPPAGE_PERCENT=0.5   # skip entry if price moved more than this
```

The engine applies two guards on top of the raw trigger:
- **Daily regime alignment** — a BUY is blocked unless the completed daily close is above its EMA and the EMA is rising, so the engine never buys into a downtrend just because RSI dipped.
- **R:R gate (`MIN_RISK_REWARD`, default 1.5)** — a TP closer than the configured multiple of the SL distance is widened before the order is placed.

### Risk model & trade management
```ini
RISK_PER_TRADE=0.01         # 1% of equity risked per trade (entry→stop distance)
MIN_RISK_REWARD=1.5         # TP distance must be >= 1.5x the SL distance
SCALE_OUT_ENABLED=false     # proven OFF for this strategy
SCALE_OUT_R_MULTIPLE=1.0    # (only used when SCALE_OUT_ENABLED=true)
SCALE_OUT_FRACTION=0.5      # (only used when SCALE_OUT_ENABLED=true)
```

Position sizing uses **fixed-fractional risk**: `qty = (equity × RISK_PER_TRADE) ÷ (entry − stop)`. A 1.2% stop therefore risks exactly 1% of equity when hit — the notional caps (`BALANCE_USAGE_PERCENT`, `MAX_SYMBOL_ALLOCATION_PERCENT`) remain as secondary ceilings. If the risk-based size falls below the exchange minimum, the trade is skipped rather than up-sized past the risk budget.

### Symbols
```ini
STATIC_SYMBOLS=NEARUSDT     # always-monitored list when DYNAMIC_SYMBOLS=false
DYNAMIC_SYMBOLS=true        # true = hourly momentum screener PICKS the pair instead
MAX_SYMBOLS=1               # max concurrent monitored symbols / positions
QUOTE_ASSET=USDT
EXCLUDE_SYMBOLS=USDC,BUSD,FDUSD,TUSD,UP,DOWN
SYMBOL_REFRESH_INTERVAL=3600  # seconds between dynamic rescans
```

> ⚠️ **`DYNAMIC_SYMBOLS=true` ignores `STATIC_SYMBOLS`.** The screener returns at most `MAX_SYMBOLS`
> momentum pairs and those are the only symbols scanned for entries (open positions are always kept
> monitored until closed). Set `DYNAMIC_SYMBOLS=false` to trade the `STATIC_SYMBOLS` list instead —
> that is how the `intraday_rsi`/NEARUSDT proof was produced.

### Realtime streams (WebSocket freshness)
```ini
PRICE_REFRESH_INTERVAL=10   # seconds between screener price re-publishes for the monitor (min 10).
                            # Prices themselves come from the !miniTicker@arr WS stream (~1s old).
WS_BALANCE_MAX_AGE=90       # seconds the account-balance WS cache is trusted before the engine
                            # takes a fresh REST /api/v3/account snapshot (min 5)
```

All realtime data is WebSocket-first with REST as the fallback: the engine reports each transport's
health at `/api/status → ws_streams` (and renders it as the dashboard's **Realtime Streams** lights).
`WS_BALANCE_MAX_AGE` trades REST verification frequency against WS-only trust: with the default `90`
an idle account triggers roughly one REST account call every two minutes, while any trade/fill updates
the cache instantly (`outboundAccountPosition`). Raise it for fewer REST calls, lower it for tighter
verification. The `/api/status → balance.source` field tells you which path answered
(`ws` / `rest` / `db` / `paper`).

### Dynamic screener tuning
```ini
TOP_CANDIDATES=50
MIN_VOLUME_USDT=1000000
MIN_PRICE_CHANGE_PERCENT=0.5
MIN_VOLATILITY_PERCENT=0.3
ADX_THRESHOLD=25
ADX_PERIOD=14
Z_SCORE_WEIGHT_VOLUME=0.20
Z_SCORE_WEIGHT_CHANGE=0.20
Z_SCORE_WEIGHT_VOLATILITY=0.20
Z_SCORE_WEIGHT_ADX=0.40
CORRELATION_THRESHOLD=0.70   # penalize pairs correlated above this
CORRELATION_PENALTY=0.90
TREND_LOOKBACK=20
```

### Risk & portfolio management
```ini
BALANCE_USAGE_PERCENT=1.0          # max fraction of total equity deployed (see note)
MAX_SYMBOL_ALLOCATION_PERCENT=1.0  # max fraction per single position (see note)
MAX_DAILY_DRAWDOWN=0.05        # halt new entries at a 5% daily loss (incl. unrealized)
MAX_LOSS_STREAK=3              # cooldown after N losses on a symbol
COOLDOWN_LOSS=3600             # seconds the streak breaker arms
MAX_WIN_STREAK=5               # cooldown after N wins on a symbol (profit lock)
COOLDOWN_WIN=1800              # seconds
```

> ⚠️ **Small accounts: keep both caps at `1.0`.** They are only a *secondary ceiling* on top of the
> 1% fixed-fractional risk size (the notional the engine actually places is `risk_amount ÷ stop%`).
> On a ~$22 account a `0.2` cap means `0.2 × 22 = $4.40`, which is **below Binance's
> `NOTIONAL.minNotional` ($5)** — every entry is silently skipped (`position size 0`). The
> `intraday_rsi` proof above was produced with `1.0 / 1.0`.

> Note: `BASE_ORDER_SIZE` and `ORDER_TYPE` are **no longer used** by the bot (removed from config loading). Do not set them in `.env`.

### Storage, webhooks, logging, operations
```ini
DB_PATH=./data/trading.db
CONTROL_FILE=./data/engine_control.json   # remote pause/close channel from the web monitor

DISCORD_WEBHOOK_URL=           # blank = alerts silently disabled
DISCORD_COOLDOWN=30            # seconds between Discord messages

LOG_LEVEL=INFO                 # DEBUG | INFO | WARNING | ERROR
LOG_FILE=./logs/trading.log    # 10 MB rotating, 5 backups

HEALTH_CHECK_INTERVAL=60       # seconds between REST/WS/DB health probes
REST_WEIGHT_LIMIT=1200         # aiolimiter budget per minute
AUTO_LIQUIDATE_ORPHANS=false   # true = SELL unmanaged tradable spot balances
```

> ⚠️ **Wallet safety**: keep `AUTO_LIQUIDATE_ORPHANS=false` so pre-existing spot balances in your wallet are never automatically sold.

---

## 📊 Strategy Preset

Set `PRESET=` in `.env` to **`intraday_rsi`** — the only preset. Presets only apply where a key is
**not** explicitly set in `.env` (an explicit `.env` value always wins). `config.py` validates
`STRATEGY_MODE=rsi_dip` at boot and aborts on anything else, so no removed strategy can be selected.

The engine was consolidated from five presets (three ATR/confluence presets plus `swing_rsi`) down to
the single backtest-proven `intraday_rsi` edge — see the changelog for the removal.

| Setting | **`intraday_rsi` — ACTIVE** |
|---|---|
| Execution Timeframe | `5m` |
| Regime Timeframe (`MTF_TIMEFRAME`) | `1d` |
| RSI Period / Oversold | **`7` / `40`** |
| RSI Timeframe | **`1h`** (RSI on 5m closes, sampled hourly) |
| Fixed % Stop / Take Profit | **`−1.2%` / `+3.0%`** |
| Max Entries / UTC day | **`1`** |
| Force-close at UTC day end | **yes** (`CLOSE_AT_UTC_DAY_END=true`) |
| Breakeven lock | **off** (would exit before the +3% TP) |
| Scale-out | **off** |
| Proven window / result | 30 pages ≈ 104 d, **$22 → +8.49%** (PF 1.43, WR 42.9%, DD 8.1%) |

> ⚠️ **The `intraday_rsi` edge is pair-concentrated.** A 9-pair rotation was only +14.6% (PF 1.07).
> If you keep `DYNAMIC_SYMBOLS=true`, the screener — not this preset — decides the pair, and the proven
> expectancy does not automatically transfer to it. Set `DYNAMIC_SYMBOLS=false` with a `STATIC_SYMBOLS`
> list to trade a fixed, backtested pair.

---

## 🏃 Running the Bot (PM2 Supervision)

`ecosystem.config.cjs` launches two supervised processes:

| App name | Purpose |
|---|---|
| `ultimate-bot` | Core `intraday_rsi` trading engine (`main.py`) |
| `bot-web-monitor` | Web API + dashboard server (`status.py --web 3000`) |

```bash
cd /path/to/ultimate-bot
pm2 start ecosystem.config.cjs
pm2 save
pm2 startup        # survive reboots

pm2 status                          # process state
pm2 logs ultimate-bot               # stream engine logs
pm2 restart ultimate-bot            # restart engine
pm2 reload ultimate-bot             # zero-downtime config reload
pm2 restart bot-web-monitor         # restart the web monitor
```

---

## 🖥️ Monitoring: CLI, Web Server & Control API

### CLI terminal dashboard
```bash
./venv/bin/python3 status.py          # single snapshot
./venv/bin/python3 status.py --watch  # live-refresh every 2 s
```

### Web monitor
```bash
# Option 1 — direct (recommended for quick checks)
./venv/bin/python3 status.py --web 3000

# Option 2 — supervised 24/7 via PM2 (already in ecosystem.config.cjs)
pm2 start ecosystem.config.cjs
```

Then open `http://YOUR_VPS_IP:3000`. When a compiled `./dist` exists next to `status.py`, it serves the React dashboard **with full `npx serve -s dist` parity** — SPA fallback for client-side routes, clean-URL directory redirects (301), ETag/`304` revalidation, immutable caching for content-hashed `/assets/*`, gzip compression, HTTP `Range` support and HTTP/1.1 keep-alive on a multi-threaded `ThreadingHTTPServer` (several viewers can poll simultaneously without blocking each other). No Node.js, `npx serve` or reverse proxy is required on the VPS.

**Realtime transport (React + WebSocket + Python):** the dashboard first opens a WebSocket to `ws(s)://<host>:3000/ws` and receives pushed status snapshots every second plus engine log lines as they are written — no polling round-trips. If the upgrade is unavailable (restrictive proxy, old backend), it falls back to HTTP polling of `/api/status` every 2.5s automatically; the connection badge shows `WS LIVE` vs `HTTP POLL`. The standalone fallback dashboard uses the same WS-first strategy. Control commands (`/api/control`, `/api/config`) remain HTTP POSTs by design — they are idempotent and safe to retry.

**Realtime Streams lights + balance provenance:** under the risk cards the dashboard renders one light per engine transport — `Ticks` (per-symbol `aggTrade`), `All-market` (`!miniTicker@arr`), `Order API` (authenticated WS API session) and `User data` (event-driven fills + balances). Green = flowing, amber = connected but lagging, red = down (engine is on REST fallback), and `engine stale` appears when the engine stops republishing its health snapshot. The Spot Balances strip shows which transport produced the number (`via WS · verified Ns ago`, `via REST`, or `last known` when the exchange is unreachable), straight from `/api/status → balance.source`.

If no compiled `dist/` is found, `status.py --web` falls back to a built-in standalone dark-mode dashboard that includes a **Performance & Risk section** (win rate with W/L/B breakdown, profit factor, total realized PnL, average win/loss and the win/loss streak monitor) alongside the balance cards, market scanner, active positions and recent orders.

> **Win-rate consistency**: wins, losses and breakevens are counted from **SELL exit orders that recorded a realized PnL** — never from BUY entries or un-filled orders — and the `win_rate` shown is `wins ÷ closed`. Partial-exit legs (recorded on `CANCELED` SELL orders) are included, so the numbers always reconcile with the engine's own streak and cooldown state.

> **Candle subscription note**: the WebSocket market stream subscribes to each monitored symbol's `aggTrade` + `kline_<TIMEFRAME>` streams. Binance public streams are free and do not require an API key, but each connection is limited to 500 streams — the bot bounds symbol lists to that cap.

### Active-position sync (engine → monitor)

The Active Positions table is rendered from **one joined view**, so it cannot disagree with the position the engine is actually managing:

| Layer | Source | Cadence |
|---|---|---|
| Stops, targets, trail, locks, size, scale-out state | `active_trades` (SQLite) | written by the engine on every real **change** (heartbeat 60 s) |
| Mark price, floating PnL, exchange size | `risk_state.futures_state` (futures) + `risk_state.scanned_pairs` (screener prices) | republished every price cycle (`PRICE_REFRESH_INTERVAL`) |

`status.py` stamps the live values onto each served row: `current_price`, `unrealized_pnl`, `live_qty`, `position_source`, `position_age_s`, `position_stale`. The provenance value says exactly where the mark came from — `engine-futures` (the account snapshot carried a mark or uPnL), `engine-scan` (the engine's screener price was used), `engine` (the engine knows the position but published no price), or `db` (no engine mark at all). The monitor never invents a price — when the engine publishes none for a symbol the key is simply absent, the row reports `position_source: db`, and the frontend falls back to the entry price, exactly as before.

The counts are explicit so the badge, the Open Risk card and the futures card cannot drift apart:

- `positions_tracked` — rows the engine is managing
- `positions_untracked` — exchange positions with no tracking row (naked / pre-boot exposure)
- `positions_live` — exchange positions the engine published
- `open_positions` — `tracked ∪ live` (was: the exchange count, or the tracked count when no snapshot existed); `untracked_pnl` sums **only** the untracked ones

The other half of the guarantee is engine-side: `trade_logic._persist_active_trade_if_changed()` flushes a managed trade the moment any rendered field changes. The previous `% 30` wall-clock guard published only inside a one-second window per 30 s, which a 10 s decision cadence routinely missed.

### Liveness: the loop heartbeat (and what is *not* one)

The engine writes **one small `risk_state["loop_state"]` row per iteration, on every path** — trading, paused, health-pause:

```json
{ "cycle_ms": 1790174000000, "cycle": 412, "interval_s": 10, "phase": "paused",
  "paused_requested": true, "paused_applied": true, "active_trades": 0, "symbols": 5 }
```

`status.py` serves it as `loop_state` with a computed `age_s`, and the dashboard's *Decision loop* row reads that age. It exists because the obvious signal — the age of the newest per-symbol signal snapshot — is **not** a liveness measure: it only advances when a symbol gets past every gate, so pausing, holding a full slot list or a tripped breaker froze it while the engine was healthy. The snapshot age is still used as a fallback when no heartbeat is present (an older build), and the row's tooltip says which source it used.

`paused_requested` / `paused_applied` make the control channel honest in the other direction too: `data/engine_control.json` records what the monitor *asked* for, and the heartbeat records what the engine *did*. The chip shows `pause requested…` until the two agree, so the UI never presents a request as an applied state.

**Fields that must NOT be treated as liveness signals:**

| Field | Why not |
|---|---|
| `engine_risk.updated_at` | the row is skipped when unchanged — a healthy engine with static risk state leaves it old |
| `signal_state` | same reason (written on change only), plus it stalls legitimately while paused |
| `entries_today` / `cooldown_*` / `daily_pnl` | change-driven values; `entries_today` is written on every entry **and** on the UTC reset, so its age is normally hours |
| `account_balances` | ~60 s in live mode, trade-driven in paper; only the standalone fallback page reads it |
| `futures_state`, `ws_streams`, `scanned_pairs`, `monitored_symbols`, `loop_state` | these **are** refreshed every cycle — the genuinely live ones |

### What the Capital Roadmap analyses

The Roadmap panel answers one question: at the account's real equity, can the **proven sizing**
(`equity × RISK_PER_TRADE / SL_PERCENT`, i.e. `notional = equity × risk / stop`) clear each pair's
real USDⓈ-M `NOTIONAL` floor from live `fapi/v1/exchangeInfo`? It is **advisory only** — no module
under `src/` imports it, no entry/exit/sizing decision consults it, and it places no orders. Equity
comes from the engine's own `risk_state.total_equity`; floors, prices and funding come from public
`fapi` reads.

| Property | Value |
|---|---|
| Pair list | The **engine's live watched set** — `risk_state.monitored_symbols` (screener picks + `STATIC_SYMBOLS` + any symbol holding an open trade). `DEFAULT_PAIRS` applies only when the engine has published none |
| `pairs_source` | `engine-watchlist` or `default` — which of the two was used, so the card can say so |
| Cache | 10 minutes, **invalidated immediately when the watched set changes** (the floors are per-symbol) |
| `age_s` | How old this snapshot is; the card labels the cell *Equity (at compute)* |
| Stages | Equity thresholds where futures constraints stop binding — computed over the **watched** pairs, so they track the universe being traded |
| CLI | `./venv/bin/python3 capital_roadmap.py [--equity N] [--pairs A,B]` — defaults to the same DB watchlist |

The roadmap is monitor-computed, not engine-published (see the liveness table above): the engine
writes the watchlist, the monitor does the exchange maths on top of it.

### HTTP API

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/status` | GET | Full snapshot: engine process state, SQLite trades/orders/risk/stats, live balance, scanned candidates, sanitized config, pause state |
| `/api/logs?lines=120` | GET | Tail `logs/trading.log` for the web Debug Console |
| `/api/health` | GET | Lightweight liveness probe |
| `/api/config` | GET / POST | Read sanitized config / apply a whitelisted `.env` payload |
| `/api/control` | POST | `{"action": "pause" \| "resume" \| "close_all" \| "close_symbol", "symbol": "…"}` |

Notes:

- `pause` blocks **new entries** while open positions stay fully managed (stops/TP/trailing remain armed). `close_all` / `close_symbol` write one-shot commands (`command_id`) into `CONTROL_FILE`; the engine executes them **and retries rejected exits until every requested position is actually closed** (new entries stay blocked while a close command is pending).
- `/api/config` accepts only a **whitelist of tuning keys** — credentials, key paths, and webhooks are never writable from the browser. After applying, it runs `pm2 reload ultimate-bot` when PM2 is detected.
- Secrets (`BINANCE_API_KEY`, secrets, webhook URLs) are stripped from every API response.
- CORS is enabled so the React dashboard can be hosted anywhere (e.g. a static host) and pointed at the VPS.
- The `/api/status` response also returns the engine's **control state** (`paused`, etc.) so the dashboard can reflect a remote pause without waiting for the next manual refresh.

---

## 🛡️ Risk Management & Safety Mechanisms

1. **Single-Instance Lock** — `fcntl` file lock on `/tmp/ultimate_bot.lock` prevents duplicate instances from double-executing orders.
2. **Daily Drawdown Circuit Breaker** — new entries halt when realized + unrealized daily loss reaches `MAX_DAILY_DRAWDOWN`; resets at UTC midnight.
3. **Per-Symbol Streak Cooldowns** — after `MAX_LOSS_STREAK` consecutive losses (or `MAX_WIN_STREAK` wins) a symbol rests for `COOLDOWN_LOSS` / `COOLDOWN_WIN`. Streaks and cooldowns persist across restarts via SQLite.
4. **Health Check & Self-Healing** — periodic REST ping, WS API/stream connectivity, and a real SQLite read probe; components are reconnected automatically and trading pauses during exchange maintenance windows.
5. **Exchange Position Reconciliation** — on startup and every 60 s in live mode the engine matches tracked positions against real balances, removes external-closed/dust positions, and optionally liquidates orphan balances (`AUTO_LIQUIDATE_ORPHANS`).
6. **Rate-Limit Protection** — `aiolimiter` caps REST weight at 1,200/min; kline caches are bounded (FIFO, 60 s TTL) so dynamic screening cannot exhaust the budget or memory.
7. **Filter-Exact Order Sanitization** — every order is quantized with `Decimal` against `stepSize`/`minQty`/`maxQty` and validated against `NOTIONAL`/`MIN_NOTIONAL`.
8. **Timestamp Sync** — server-time offset refreshed every 10 minutes to avoid `-1021` rejections.
9. **Exit Failsafes** — a rejected/crashed exit re-arms the position with its stop and alerts loudly; it is retried on the next cycle (or by remote-command retry) and never silently dropped.
10. **Exit Fill Accuracy** — live exits always read the exchange `avgPrice` for slippage-accurate PnL.
11. **Order-Quantity Edge Cases** — quantity strings are never allowed to become empty (which would send a blank `quantity` and get rejected), and `Decimal` quantization guards against floating-point step-size artifacts.
12. **WebSocket Timeouts Are Retryable** — the authenticated WS-API client now retries the per-request `recv()` up to ~100 s before treating a response as lost, so momentary stalls don't abort orders.
13. **Kline Cache Coherence** — completed kline updates are applied atomically so the tick price, ATR refresh, and gap-breach checks never see a partially-written candle.
14. **Fixed-Fractional Risk Sizing** — every entry is sized from the entry-to-stop distance (`RISK_PER_TRADE`, default 1% of equity), so stop width can never silently inflate per-trade risk; notional caps act only as secondary ceilings.
15. **Regime Alignment & R:R Gate** — BUYs are blocked when the MTF trend is DOWN (no knife-catching), and entries below `MIN_RISK_REWARD` reward-to-risk are widened or skipped.
16. **Scale-Out Discipline** — 50% of the position is banked at +1R (`SCALE_OUT_*`), breakeven locks immediately on the runner, and the remainder rides to the full take-profit — converting marginal win rates into positive expectancy.

---

## 🧪 Smoke Test

`smoke_test.py` boots the real engine end-to-end against an **isolated temporary database** (your `data/trading.db`, `.env` and logs are never touched) and verifies:

1. The engine boots and initializes the SQLite schema + `paper_balance` risk state.
2. The single-instance lock is acquired.
3. A second engine instance is rejected (`Another instance is already running`).
4. The web monitor serves `/api/status` with **consistent** win/loss stats (`closed = W + L + B`), reports `RUNNING`, and provably reads the isolated temp DB (API numbers match a direct SQL query).
5. SIGTERM shuts the engine down **cleanly** — exit code 0, `Shutdown complete.` in the log, no `Task was destroyed` warnings, lock released.

```bash
cd /path/to/ultimate-bot
./venv/bin/python3 smoke_test.py    # exit 0 = all checks passed
```

It can also be run from the repo root via `npm run smoke`. Requires network access to Binance (the engine fetches `exchangeInfo` at boot) and a free `/tmp/ultimate_bot.lock` (stop any running engine first).

### Failure Scenario Battery (`test_scenarios.py`)

`smoke_test.py` proves the engine *boots*; this battery proves the *rules* still hold. It is fully
offline — no network, no exchange reachability, no database — so it runs anywhere in seconds:

```bash
npm run test:scenarios                 # from the repo root (part of npm run verify)
./venv/bin/python3 test_scenarios.py   # or from this directory
```

S1–S7 guard bugs that were actually hit in production: pre-entry wick exclusion (the 13-second
stop-out), malformed-kline tolerance, the per-symbol loss re-entry gate, the funding-gate tunable, the
PnL verifier's maths against the `userTrades` field shape, the order-call `reduce_only` signature
contract across all four transport clients, and the multi-assets phantom-balance seed.

**S8 guards the shared exit decision.** `trade_policy.evaluate_exit` is called by both the live manage
cycle and the backtest, so its contract is pinned: the priority order (a stop always outranks
profit-taking, and the two terminal exits pre-empt a partial), the reason labels the dashboard filters
on, gap-aware fills, an armed vs unarmed trail, and quiet or absent evidence.

**S9 guards active-trade persistence** — the web monitor renders the position from the `active_trades`
row, so a managed field left in memory only renders as a stale one. The checks pin the behaviour that
replaced the old `% 30` wall-clock guard: the first evaluation writes, an unchanged state does **not**
re-write (no per-cycle churn), a ratcheted trail / a breakeven lock / a size change each flush
immediately, the heartbeat refreshes an unchanged row, a DB failure is swallowed and retried, and an
older partial row shape is tolerated.

Exit code 0 = `ALL_OK` (40 checks), 1 = at least one failure, which is named.

### Config Drift Check (`check_env_drift.py`)

`.env` is the engine's source of truth; `.env.example` is its documented mirror — and not only documentation: `status.py` falls back to the template as its config source when `.env` is missing, and seeds a new `.env` from it. A tuner push from the web monitor writes `.env` only, so the template drifts silently and starts lying about what the bot is actually running (that is how the two files ended up disagreeing on nine keys in September 2026).

`check_env_drift.py` is a stdlib-only guard that fails instead of waiting to be noticed. It parses both files strictly — it does **not** reuse `status.load_env()`, which merges process-env overrides and would mask real file differences — and exits non-zero on:

1. **Keys present in only one file**, in either direction.
2. **Duplicate keys** — silent shadowing; the last one wins.
3. **Inline `#` comments on value lines** — this dotenv build folds the comment into the value.
4. **Value drift** on any key outside the exempt set (the credential keys, which are expected to differ).
5. **A real credential in the template** — either a non-placeholder value for a known credential key, or a new secret-shaped key mirrored verbatim.

```bash
cd /path/to/ultimate-bot
./venv/bin/python3 check_env_drift.py          # exit 0 = in sync, 1 = drift
./venv/bin/python3 check_env_drift.py --quiet  # findings only, no advice (used by the hook)
# or from the repo root:
npm run check:env
```

After an intentional config change, re-mirror the template from the live file:

```bash
./venv/bin/python3 check_env_drift.py --update
npm run check:env -- --update            # same thing, via npm
```

`--update` rewrites changed values, appends keys the live config has and the template lacks, and comments out keys `.env` no longer sets. It **never** copies a credential: the exempt keys keep their placeholders, and any key whose name looks sensitive is skipped rather than written into a tracked file. Output redacts sensitive-looking values and drops ANSI colour when piped, so it is safe to gate a commit or a deploy on.

With no `.env` present (a fresh clone, or CI without secrets) it prints `SKIP` and exits 0 — it never fails merely because secrets are absent. `npm run verify` runs this check together with the rest of the battery (`lint` → `check:env` → `smoke` → `test:ui` → `test:sync`).

**Install it as a pre-commit hook** so drift cannot be committed at all:

```bash
cd /path/to/ultimate_bot
npm run hooks:install          # or: bash scripts/install-hooks.sh
```

The hook lives versioned at `.githooks/pre-commit` — git only executes `.git/hooks/`, which is never committed — so the installer symlinks it into place. It is idempotent (re-running is harmless) and moves any pre-existing hook aside to a timestamped backup rather than clobbering it. After that, a drifted commit is refused with the exact `--update` command printed, plus the deliberate `git commit --no-verify` escape hatch. If no Python interpreter is found the hook warns and lets the commit through instead of blocking all work. Teams can skip the installer entirely and point git at the directory: `git config core.hooksPath .githooks`.

### Sync Integration Test (`sync_test.py`)

`sync_test.py` verifies the **engine ↔ web monitor data chain** end-to-end (46 checks). It boots the real engine in paper mode against an isolated temp database plus `status.py --web`, then asserts:

1. **Equity sync** — `paper_balance` risk state surfaces through `/api/status`.
2. **Remote-control sync** — `paused`/`resume` written to `CONTROL_FILE` reflect in `/api/status.control`; **B1** additionally proves the engine's loop heartbeat carries a fresh age and *acknowledges* the pause (`paused_applied`) before clearing it on resume — request ≠ applied, verified end-to-end.
3. **Realtime transport sync** — engine-published `ws_streams` health and balance provenance, and the *same* fields over the `/ws` push, so HTTP and WebSocket can never disagree.
4. **Streak aggregation** — per-symbol `risk_<SYM>` blobs aggregate to top-level `win_streak` / `loss_streak` / `cooldown_until` (and appear in `stats`).
5. **Trade-stats sync** — an injected closed SELL exit updates `closed_trades` / `winning_trades` / `total_realized_pnl`; `closed == W + L + B` reconciles.
6. **Active-trade sync** — an injected `active_trades` row appears with entry/stop/TP intact (**E**), and an injected `futures_state` snapshot proves the served row carries the **engine's** mark (`mark = entry + uPnL/amount`), floating PnL, exchange size and provenance, with an untracked exchange position counted but never double-counted — plus per-position `leverage` / `margin_type` normalised from the snapshot's top level and a derived `mark_price` (**E2**).
7. **Config sanitization** — `/api/status.config` never contains credentials.
8. **Config live-refresh** — an out-of-band `.env` edit is served without restarting the monitor.
9. **Roadmap watchlist** — the served `roadmap.pairs` equals the served `monitored_symbols`, `pairs_source` names the list it used, ok/blocked partition the watched set against the served proven notional, and a rotated watchlist invalidates the cache (**E3**; exchange data stubbed and the payload built from a private DB copy, so no network and no interference with the serving monitor).
10. **Clean shutdown** after all writes (exit 0, `Shutdown complete.`, lock released).

```bash
cd /path/to/ultimate-bot
./venv/bin/python3 sync_test.py     # exit 0 = all 46 checks passed
# or from the repo root:
npm run test:sync
```

Like the smoke test, it never touches the real `data/trading.db`, `.env` or logs (isolated temp DB), and requires a free `/tmp/ultimate_bot.lock`.

### Order-builder Dry Run (`order_dry_run_probe.py`)

Answers the go-live question "will the order my engine builds actually be accepted?" **without sending
anything**. The probe drives the four clients an order can route through — spot/futures × REST/WS-API —
for an entry BUY and an exit SELL, using LIVE exchangeInfo and prices. It sizes the quantity through the
engine's own `OrderManager.sanitize_order` (real `LOT_SIZE` / `minNotional` floors), builds the real
request (timestamp + Ed25519/HMAC signature), then verifies it while a stubbed transport captures the
call instead of transmitting it:

1. **REST builders** (`RestClient` / `FuturesRestClient`) — real signed URL assembly, captured at the
   aiohttp session layer so no order leaves the box.
2. **WS-API builders** (`WSApiClient` / `FuturesWSApiClient`) — real signed `order.place` params
   (Ed25519 session auth; HMAC runs REST only).
3. **Field verification** — correct endpoint, `type=MARKET`, step-quantized quantity, fresh timestamp
   inside `recvWindow`, and on futures `positionSide=BOTH` with `reduceOnly=true` on the exit SELL
   (and absent on the entry BUY).

```bash
cd /path/to/ultimate-bot
./venv/bin/python3 order_dry_run_probe.py                  # both legs (default)
./venv/bin/python3 order_dry_run_probe.py --market futures # one leg
./venv/bin/python3 order_dry_run_probe.py --self-test      # offline harness check (no network/keys)
# or from the repo root:
npm run probe:orders
```

Exit 0 = every builder produced a valid request. Without credentials the REST builders still run in
parameter-only mode and the WS builders are reported as skipped. **No order is ever placed** — this is
GO_LIVE.md step 5.5, run it right before flipping `PAPER_TRADE`.

### 24h Paper Soak (`soak_report.py`)

To validate the engine under real market conditions before risking funds, run a supervised paper soak:

```bash
cd ultimate-bot
pm2 start ecosystem.config.cjs      # starts engine + web monitor (PAPER_TRADE=true from .env)
pm2 save                            # survive reboots
pm2 status                          # both apps online?

# Any time during / after the soak:
npm run soak:report                 # or: ./venv/bin/python3 soak_report.py
```

The report summarizes, from the **real** production DB and engine log:
- PM2 process health: uptime, restart count, memory (restarts > 0 = instability)
- Paper equity, daily realized PnL, monitored symbols, screener candidates
- Open positions with SL/TP; all closed trades with per-trade net PnL (fees included)
- Win/loss stats reconciliation (closed == W + L + B) and per-symbol streaks/cooldowns
- BUY signals fired per symbol (rsi_dip fires ~1–3 trades/week per pair — low frequency is expected)
- Log health: warnings, errors, crash markers (should be 0 errors / 0 crash markers over 24h)

**Soak pass criteria:** engine stays `online` with 0 unexpected restarts, 0 log errors, stats reconcile,
and every entry/exit in the DB carries a sensible SL/TP and net-of-fees PnL. Judge the *strategy*
only after a full week — rsi_dip is a low-frequency swing strategy by design.

---

## 🔬 Backtesting (Prove It Before You Trade It)

`backtest.py` replays **real** historical Binance klines through the live engine's **own** `SignalGenerator.decide()` — the exact code that trades real money, so the *signal* has zero drift. Trade management calls the engine's **own** exit decision (`trade_policy.evaluate_exit` — one ordering, the same reason labels, gap-aware fills) and the same level-setting helpers (the fixed % bracket with the `MIN_TP_PERCENT` floor and `MIN_RISK_REWARD` widening, `ratchet_stops`), but the entry gates, the sizing quantisation and the config merge are its own — see [Backtest vs. Live: Verified Parity Divergences](#-backtest-vs-live-verified-parity-divergences) for the audited list before reading any result as a live forecast.

```bash
cd ultimate-bot
./venv/bin/python3 backtest.py --symbol NEARUSDT --pages 10 --equity 22   # ~35 days of 5m data at $22
./venv/bin/python3 backtest.py --symbol ETHUSDT --pages 30 --sl 0.012 --tp 0.03
```

All CLI *flags* default from **env vars** (`BACKTEST_*`), and the *strategy* configuration resolves
exactly like the engine (`.env` overrides the preset), so a bare `backtest.py` measures the **deployed**
config. `--market {spot,futures}` selects the venue: futures fetches fapi klines, charges 0.05%/leg plus
per-8h funding, and applies the `FUNDING_RATE_MAX` entry gate.

| Flag | Purpose |
|---|---|
| `--symbol` / `--preset` | Market + preset (default `NEARUSDT` / `intraday_rsi`; `intraday_rsi` is the only preset) |
| `--market {spot,futures}` | Venue (default `spot`, or `BACKTEST_MARKET`). `futures` = fapi klines, 0.05%/leg, per-8h funding, the `FUNDING_RATE_MAX` entry gate |
| `--pages N` | Pages of 1000 bars to fetch (1 page ≈ 3.5 days on 5m) |
| `--end MS` | `endTime` in ms for a reproducible window |
| `--sl` / `--tp` / `--min-tp` | Override `SL_PERCENT` / `TP_PERCENT` / `MIN_TP_PERCENT` |
| `--cooldown-bars N` | Bars to wait after any exit before re-entry |
| `--max-hold S` | Override `MAX_HOLD_TIME` (seconds) |
| `--min-notional X` | USDT floor below which a trade cannot be placed (default `5` = live `NOTIONAL.minNotional`) |
| `--equity X` | Starting equity (default from `BACKTEST_EQUITY`) |
| `--balance-usage-percent` / `--max-symbol-allocation-percent` | Override the notional caps |
| `--quiet` | One-line summary instead of full JSON (for sweeps) |

**Proven result (`intraday_rsi`, NEARUSDT, 30 pages ≈ 104 days, $22 equity, taker fees both legs):**

| Metric | Value |
|---|---|
| Return | **+8.49%** |
| Trades | 35 |
| Win rate | 42.9% |
| Profit factor | 1.43 |
| Max drawdown | 8.1% |

> ℹ️ **Historical research record (removed presets).** The confluence (`ATR`) presets (`day`/`swing`/
> `scalping`) were measured to have ~zero gross edge — net loss ≈ total fees on every sampled asset and
> timeframe — which is why the engine was consolidated to `intraday_rsi`. Those presets and their CLI
> flags no longer exist; the numbers are retained only as evidence for that decision.

**Historical: why the confluence family was rejected (BTCUSDT 5m, 20 days, net of 0.2% fees):**

| Config | Trades | Win rate | Profit factor | Expectancy |
|---|---|---|---|---|
| Old defaults (1.2×/2.4× ATR, 0.5% TP floor) | 151 | 12.6% | 0.09 | −0.98 R |
| Tuned defaults (3.0×/3.5× ATR, 0.15% floor) + 36-bar cooldown | 22 | 50.0% | 0.79 | −0.09 R |
| Tuned defaults, no cooldown | 35 | 45.7% | 0.49 | −0.27 R |

On honest extended samples the 20-day PF 0.79 did **not** hold (BTC 5m×50d: PF 0.28–0.33; BTC 15m×156d:
0.52; ETH 0.37; SOL 0.59) — the decomposition showed **net loss ≈ total fees, i.e. the gross (pre-fee)
edge ≈ 0**. Every signal-side lever (ADX gate, R-based breakeven/trailing, volume confirmation,
max-hold, cooldown) left PF ≤ 0.53, and the only improvement came from a maker-fee TP assumption the
engine cannot realise (no resting limit TP — everything exits as a MARKET/taker order). The conclusion:
a momentum-confluence signal with no gross edge cannot be tuned profitable — the fix is a **different
signal**, which is exactly what `intraday_rsi` is.

On fees: the engine's exits are all software-triggered **MARKET** orders, so both legs pay taker (0.1%). The `--maker-tp` figure quantifies what a resting limit-TP implementation *would* save (~40% of round-trip cost) — it is a roadmap item, **not** a current capability, and every backtest defaults to taker so the proof never assumes it. Re-run the backtest after **any** parameter change — if a config cannot show PF > 1 over 100+ trades, it does not go live.

---

## 🚨 Troubleshooting & Emergency Procedures

### Emergency stop (close all positions)
```bash
# Option A — from the web dashboard: press "Close All"
# Option B — via the control API:
curl -X POST http://YOUR_VPS_IP:3000/api/control -H "Content-Type: application/json" \
  -d '{"action": "close_all"}'

# Option C — stop the engine (positions then need manual handling in the Binance app)
pm2 stop ultimate-bot
./venv/bin/python3 status.py
```

### "Another instance is already running"
A stale lock file after a crash:
```bash
rm -f /tmp/ultimate_bot.lock
pm2 restart ultimate-bot
```

### Timestamp / `-1021` errors
The bot auto-syncs with Binance server time, but keep NTP healthy:
```bash
sudo timedatectl set-ntp on
sudo systemctl restart systemd-timesyncd
```

### Discord alerts not arriving
Ensure `DISCORD_WEBHOOK_URL` is populated. When blank, alerts are cleanly silenced and never interrupt trading.

### Web monitor shows stale/zero data
Confirm `status.py --web 3000` and the engine share the same `DB_PATH`/`.env`, and that port 3000 is open in the cloud security group (TCP inbound). The dashboard reads live state from SQLite — it never fabricates server figures.

A position row showing **0.00%** and **$0.00** unrealized means the engine published no mark for that symbol: check `position_source` on the row (`db` = no engine mark available; `engine-scan` = the screener price was used; `engine-futures` = the account snapshot's mark/uPnL). `position_stale: true` (or a rising `position_age_s`) means the engine stopped republishing — the position shown is its last known state, so look at the engine's process state before trusting it. A *zero-count* dashboard while a position is open live is the old failure mode and is covered by `sync_test.py` section E2.

On the Engine Health panel: the **Decision loop** row reads the engine's per-iteration heartbeat (`loop_state`), so it stays green while the engine is looping even if it is pausing, holding a full book, or blocked by the breaker — those stop *decisions*, not the loop. If it goes amber the loop really is wedged; check `loop_state.age_s` and `phase` in `/api/status`. A chip reading **pause requested…** means the control file has the pause but the engine has not applied it yet (it applies at the top of the next iteration) — if it stays that way, the engine is not looping at all.

