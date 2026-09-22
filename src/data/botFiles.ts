// Single source of truth for the "Project Code & ZIP" tab.
// Every file below is imported RAW from the real `ultimate-bot/` engine in this
// repository, so the ZIP a user downloads is byte-identical to the audited,
// production-grade engine that lives in `ultimate-bot/`. No duplicated stubs,
// no drift between what the web monitor displays and what actually runs.
import README_MD from '../../ultimate-bot/README.md?raw';
import ENV_EXAMPLE from '../../ultimate-bot/.env.example?raw';
import CONFIG_PY from '../../ultimate-bot/config.py?raw';
import MAIN_PY from '../../ultimate-bot/main.py?raw';
import STATUS_PY from '../../ultimate-bot/status.py?raw';
import BACKTEST_PY from '../../ultimate-bot/backtest.py?raw';
import ECOSYSTEM_JS from '../../ultimate-bot/ecosystem.config.cjs?raw';
import REQUIREMENTS_TXT from '../../ultimate-bot/requirements.txt?raw';
import INIT_PY_RAW from '../../ultimate-bot/src/__init__.py?raw';
import BACKOFF_PY from '../../ultimate-bot/src/core/backoff.py?raw';
import ERROR_HANDLER_PY from '../../ultimate-bot/src/core/error_handler.py?raw';
import HEALTH_CHECK_PY from '../../ultimate-bot/src/core/health_check.py?raw';
import DB_MANAGER_PY from '../../ultimate-bot/src/database/db_manager.py?raw';
import REST_CLIENT_PY from '../../ultimate-bot/src/exchange/rest_client.py?raw';
import WS_API_CLIENT_PY from '../../ultimate-bot/src/exchange/ws_api_client.py?raw';
import WS_STREAM_CLIENT_PY from '../../ultimate-bot/src/exchange/ws_stream_client.py?raw';
import DISCORD_WEBHOOK_PY from '../../ultimate-bot/src/reporting/discord_webhook.py?raw';
import RISK_MANAGER_PY from '../../ultimate-bot/src/risk/risk_manager.py?raw';
import SIGNAL_GENERATOR_PY from '../../ultimate-bot/src/strategies/signal_generator.py?raw';
import TREND_DETECTOR_PY from '../../ultimate-bot/src/strategies/trend_detector.py?raw';
import ORDER_MANAGER_PY from '../../ultimate-bot/src/trade/order_manager.py?raw';
import TRADE_LOGIC_PY from '../../ultimate-bot/src/trade/trade_logic.py?raw';
import HELPERS_PY from '../../ultimate-bot/src/utils/helpers.py?raw';

// Raw content of the package __init__.py so the ZIP download ships the real file
// (previously re-exported an empty string — the downloaded package was broken).
export const INIT_PY = INIT_PY_RAW;

export interface BotFileDefinition {
  path: string;
  name: string;
  category: 'root' | 'core' | 'exchange' | 'database' | 'risk' | 'strategies' | 'trade' | 'reporting' | 'utils';
  description: string;
  enhancements: string[];
  content: string;
}

export const BOT_FILES: BotFileDefinition[] = [
  {
    path: 'main.py',
    name: 'main.py',
    category: 'root',
    description: 'System master loop: async lifecycle, single-instance lock, graceful signal shutdown, engine wiring.',
    enhancements: [
      'File-lock via fcntl prevents duplicate bot instances from double-executing orders',
      'Graceful SIGINT/SIGTERM shutdown that cancels background tasks and releases the lock',
      'Paper mode boots with zero credentials; live mode requires Ed25519 key + API key',
      'Public WS stream used in BOTH modes for real-time tick prices (REST fallback if it fails)'
    ],
    content: MAIN_PY,
  },
  {
    path: 'config.py',
    name: 'config.py',
    category: 'root',
    description: 'Central configuration manager and strict startup validation. Single strategy: intraday_rsi.',
    enhancements: [
      'Refuses to boot live trading without BINANCE_API_KEY and the Ed25519 private key on disk',
      'One preset (intraday_rsi) mirrored by the web tuner so the dashboard never drifts from the engine',
      'Mathematical constraint validation (TP > SL, callback < activate, RSI bounds, etc.)'
    ],
    content: CONFIG_PY,
  },
  {
    path: 'status.py',
    name: 'status.py',
    category: 'root',
    description: 'CLI terminal dashboard AND the embedded web monitor: React frontend + WebSocket API + Python backend on one port — /ws realtime push (RFC 6455, stdlib) with /api/status HTTP polling fallback, plus the dashboard UI (SPA fallback, gzip, ETag/304, Range, keep-alive).',
    enhancements: [
      'Read-only SQLite access (mode:ro, WAL, 10s busy timeout) so it never locks the engine',
      'CORS-enabled /api/status, /api/health and /api/config endpoints for the web dashboard',
      'WebSocket /ws streams status snapshots (1s) + incremental log tails with auto HTTP-polling fallback',
      '--web PORT serves the React dashboard from ./dist or a built-in fallback UI',
      'Secrets (API keys, webhooks) are stripped from every API response'
    ],
    content: STATUS_PY,
  },
  {
    path: 'backtest.py',
    name: 'backtest.py',
    category: 'root',
    description: 'Strategy backtest engine: replays REAL Binance klines through the live engine\'s own decision core (SignalGenerator.decide) with identical trade management — fixed % SL/TP, EOD close, fees, risk sizing, drawdown breaker.',
    enhancements: [
      'Zero drift: backtests the exact SignalGenerator.decide code the live engine trades with',
      'Full trade-management parity: MIN_TP floor, R:R widening, gap-aware stops, taker fees on both legs',
      'Honest metrics: win rate, profit factor, expectancy, avg R-multiple, max drawdown, fee drag',
      'Env-driven CLI defaults (BACKTEST_*) so a run reproduces the deployed .env'
    ],
    content: BACKTEST_PY,
  },
  {
    path: 'ecosystem.config.cjs',
    name: 'ecosystem.config.cjs',
    category: 'root',
    description: 'PM2 production process supervisor config: auto-restart, memory cap, log routing.',
    enhancements: [
      'Runs under the project virtualenv python interpreter',
      'Memory limit restart threshold (2GB) and rotating log files'
    ],
    content: ECOSYSTEM_JS,
  },
  {
    path: 'requirements.txt',
    name: 'requirements.txt',
    category: 'root',
    description: 'Pinned Python 3 dependency specifications for modern async crypto trading.',
    enhancements: [
      'aiohttp, websockets, aiosqlite for non-blocking I/O',
      'cryptography for Ed25519 signing keys; pandas/numpy/scipy for indicators',
      'aiolimiter for Binance REST rate-limit safety'
    ],
    content: REQUIREMENTS_TXT,
  },
  {
    path: '.env.example',
    name: '.env.example',
    category: 'root',
    description: 'Template configuration with every tunable parameter and safe defaults (PAPER_TRADE=true).',
    enhancements: [
      'Documented parameter definitions and preset selection',
      'AUTO_LIQUIDATE_ORPHANS=false protects pre-existing wallet balances'
    ],
    content: ENV_EXAMPLE,
  },
  {
    path: 'README.md',
    name: 'README.md',
    category: 'root',
    description: 'Full deployment, configuration, Ed25519 setup, and monitoring guide for Debian 13 VPS.',
    enhancements: [
      'Step-by-step Debian 13 PEP 668 venv and PM2 setup',
      'Live-readiness checklist and paper-vs-live audit table',
      'Web monitor launch instructions (status.py --web 3000)'
    ],
    content: README_MD,
  },
  {
    path: 'src/core/backoff.py',
    name: 'backoff.py',
    category: 'core',
    description: 'Async retry decorator with exponential backoff + jitter for transient exchange errors.',
    enhancements: ['Randomized backoff avoids thundering-herd retries after exchange hiccups'],
    content: BACKOFF_PY,
  },
  {
    path: 'src/core/error_handler.py',
    name: 'error_handler.py',
    category: 'core',
    description: 'Top-level error capture with rate-limited Discord alerting.',
    enhancements: ['60s webhook cooldown prevents alert floods during outages'],
    content: ERROR_HANDLER_PY,
  },
  {
    path: 'src/core/health_check.py',
    name: 'health_check.py',
    category: 'core',
    description: 'REST/WS/DB health monitoring with automatic component reconnection and maintenance pause.',
    enhancements: [
      'Detects exchange maintenance windows and pauses trading until they end',
      'Self-heals REST sessions, WS API and WS streams with backoff-guarded reconnects'
    ],
    content: HEALTH_CHECK_PY,
  },
  {
    path: 'src/database/db_manager.py',
    name: 'db_manager.py',
    category: 'database',
    description: 'ACID SQLite manager: WAL journal, batched write queue, separate read-only connection.',
    enhancements: [
      'Asynchronous batched write queue prevents "database is locked" errors',
      'Dedicated query-only read connection for the status API',
      'Tables: orders (fills, fees, PnL), active_trades, risk_state'
    ],
    content: DB_MANAGER_PY,
  },
  {
    path: 'src/exchange/rest_client.py',
    name: 'rest_client.py',
    category: 'exchange',
    description: 'Async Binance REST client: Ed25519 signing, weight-limiter, time-sync, retry with backoff.',
    enhancements: [
      'aiolimiter keeps calls below the 1200 weight/min IP ceiling (429-ban protection)',
      'Server-time offset sync every 10min kills -1021 timestamp rejections',
      'exchangeInfo cache auto-refreshes every 24h (delistings, filter changes)',
      'Retry with exponential backoff on transient failures'
    ],
    content: REST_CLIENT_PY,
  },
  {
    path: 'src/exchange/ws_api_client.py',
    name: 'ws_api_client.py',
    category: 'exchange',
    description: 'Low-latency Binance WebSocket API client for authenticated order placement and cancellation.',
    enhancements: [
      'Ed25519 session.logon authentication',
      'Asyncio lock serializes request/response ID matching (no cross-talk races)',
      'Connection monitor auto-reconnects dropped sessions'
    ],
    content: WS_API_CLIENT_PY,
  },
  {
    path: 'src/exchange/ws_stream_client.py',
    name: 'ws_stream_client.py',
    category: 'exchange',
    description: 'Public market-data WebSocket streaming aggTrade ticks and kline bars.',
    enhancements: [
      'Dynamic subscribe/unsubscribe without restarting the connection',
      'Reconnect loop with exponential backoff builds a fresh socket each attempt',
      'Works on modern (v12+) and legacy websockets libraries via state detection',
      'Stale-tick protection: prices older than 5s return None so callers fall back to REST'
    ],
    content: WS_STREAM_CLIENT_PY,
  },
  {
    path: 'src/reporting/discord_webhook.py',
    name: 'discord_webhook.py',
    category: 'reporting',
    description: 'Discord alerting with cooldown batching, chunked messages, embeds, and retry.',
    enhancements: [
      'Queue + flush loop batches rapid-fire alerts within the cooldown window',
      'Silently no-ops when no webhook is configured (alerts never block trading)'
    ],
    content: DISCORD_WEBHOOK_PY,
  },
  {
    path: 'src/risk/risk_manager.py',
    name: 'risk_manager.py',
    category: 'risk',
    description: 'Real-time equity tracker, daily drawdown circuit breaker, and streak-based cooldowns.',
    enhancements: [
      'Simulated $1000 equity in PAPER_TRADE; live Binance equity (quote + converted assets) otherwise',
      'Drawdown enforced on realized + unrealized daily PnL with UTC daily reset',
      'Per-symbol loss/win streak cooldowns (revenge-trade protection)',
      'Position sizing: allocation caps, free-quote clamp, LOT_SIZE step/min/max, minNotional bump'
    ],
    content: RISK_MANAGER_PY,
  },
  {
    path: 'src/strategies/signal_generator.py',
    name: 'signal_generator.py',
    category: 'strategies',
    description: 'Single signal engine: daily EMA regime gate + RSI(Wilder) dip trigger sampled per closed bucket.',
    enhancements: [
      '60s kline cache (bounded FIFO) cuts REST weight usage by ~90% per scan loop',
      'Regime uses COMPLETED daily candles only, so it never peeks at an unfinished day',
      'Fires once per bucket (no re-triggering every bar) with a rising-RSI confirmation',
      'Publishes a per-symbol decision snapshot (regime/RSI/trigger/reason) for the web monitor'
    ],
    content: SIGNAL_GENERATOR_PY,
  },
  {
    path: 'src/strategies/trend_detector.py',
    name: 'trend_detector.py',
    category: 'strategies',
    description: 'Dynamic screener: volume/change/volatility/ADX Z-score ranking with correlation penalty.',
    enhancements: [
      'Real ADX(14) qualification filter (threshold 25) on MTF candles',
      'Pearson correlation matrix prevents stacking correlated pairs',
      'Breakout and EMA trend-direction score adjustments'
    ],
    content: TREND_DETECTOR_PY,
  },
  {
    path: 'src/trade/order_manager.py',
    name: 'order_manager.py',
    category: 'trade',
    description: 'Market-order execution engine: filter sanitization, slippage guard, fill polling, paper fills.',
    enhancements: [
      'Decimal-exact LOT_SIZE quantization (no scientific notation, no -1013 rejections)',
      'BUY: pre-checks free quote with a 1% fee/slippage buffer (fail fast, no -2010)',
      'SELL: auto-clamps quantity to the free base-asset balance (fee-deduction safe)',
      'WS-API first, REST fallback; timeout path cancels partial fills and reports them'
    ],
    content: ORDER_MANAGER_PY,
  },
  {
    path: 'src/trade/trade_logic.py',
    name: 'trade_logic.py',
    category: 'trade',
    description: 'Core execution controller: signal loop, market entries, fixed-% bracket exits, reconciliation, daily reports.',
    enhancements: [
      'Gap-breach stop checks use bar LOW (wick protection), not just the live tick',
      'Net PnL always nets 0.1% taker fees on both legs — paper matches live',
      'Fixed % bracket (SL_PERCENT / TP_PERCENT) with MIN_TP floor and MIN_RISK_REWARD widening',
      'Persisted daily entry cap and UTC-day-end force close for intraday discipline',
      'Exit-order rejection failsafe re-arms the stop and retries instead of dropping the position',
      'Live mode purges orphan open orders on close; periodic exchange position reconciliation',
      'WS price reads transparently fall back to REST via a stream proxy'
    ],
    content: TRADE_LOGIC_PY,
  },
  {
    path: 'src/utils/helpers.py',
    name: 'helpers.py',
    category: 'utils',
    description: 'Logging setup: stdout stream + rotating file handler honoring LOG_LEVEL/LOG_FILE.',
    enhancements: ['10MB rotating log files with 5 backups keep VPS disk usage bounded'],
    content: HELPERS_PY,
  },
];
