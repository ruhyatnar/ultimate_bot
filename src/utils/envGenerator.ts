import { BotConfig } from '../types';

/** Binance Spot NOTIONAL.minNotional floor — orders below this are rejected live.
 *  MUST match the engine's fallback (risk_manager/order_manager/trade_logic = 5.0)
 *  and the real exchange value; a mismatch here misreports trade viability. */
export const MIN_NOTIONAL_USDT = 5;

/**
 * Single source of truth for the bot's .env file.
 * Used by the Strategy & Config tab, the VPS Sync modal, and the bundled
 * `.env` file shipped in the downloadable ZIP — so all three always match
 * the keys the engine actually reads (config.py + status.py TUNING_KEYS).
 */
export function generateEnvString(config: BotConfig, apiKey: string = 'your_binance_api_key_here'): string {
  return `# =================================================================
# BINANCE ULTIMATE BOT — TUNED CONFIGURATION
# Generated via Interactive Web Monitor
# Single strategy: intraday_rsi (daily-EMA50 regime + RSI dip, fixed % bracket)
# =================================================================

# --- Trading Mode ---
# SAFETY DEFAULT: 'true' = zero-risk paper simulation with real-time market data.
# The engine will NOT boot into live mode unless this is explicitly 'false'.
PAPER_TRADE=${config.paperTrade}
USE_TESTNET=${config.useTestnet}
# Market: 'spot' (the proven engine path) or 'futures' (USDⓈ-M via fapi).
MARKET=${config.market}
# Futures settings (only used when MARKET=futures)
FUTURES_ONE_WAY_MODE=${config.futuresOneWayMode}
FUTURES_MARGIN_TYPE=${config.futuresMarginType}
FUTURES_LEVERAGE=${config.futuresLeverage}
FUTURES_REST_WEIGHT_LIMIT=2400
# All-market ticker feed: REST fallback cadence (seconds) when the fstream
# data frames are silently dropped by the network path. Lower = fresher prices,
# higher REST weight usage.
TICKERS_REST_FALLBACK_S=${config.tickersRestFallbackSeconds}
# 24h futures paper soak: watchdog stall-alert threshold (seconds without a
# DB heartbeat while PM2 reports the soak online).
SOAK_STALL_S=${config.soakStallSeconds}

# --- Binance Credentials ---
# ⚠️ Placeholders below are for NEW setups only. If you are pushing this over an
# EXISTING VPS deployment, preserve your current BINANCE_API_KEY / BINANCE_API_SECRET
# (the web monitor's POST /api/config whitelist never touches credentials, and the
# engine requires the real key to boot in live mode).
BINANCE_API_KEY=${apiKey}
BINANCE_PRIVATE_KEY_PATH=./keys/private_key.pem

# --- Strategy (the only one) ---
PRESET=${config.preset}
STRATEGY_MODE=${config.strategyMode}
# Signal = daily EMA-50 regime gate + RSI dip trigger, fixed % bracket exits.
SL_PERCENT=${config.slPercent}
TP_PERCENT=${config.tpPercent}
RSI_PERIOD=${config.rsiPeriod}
RSI_OVERSOLD=${config.rsiOversold}
# RSI floor: skip entries while RSI < ENTRY_RSI_MIN (0 = gate off). Champion value 35.
ENTRY_RSI_MIN=${config.entryRsiMin}
RSI_TIMEFRAME=${config.rsiTimeframe}
RSI_TIMEFRAME_MS=${config.rsiTimeframeMs}
REGIME_EMA=${config.regimeEma}
REGIME_SLOPE_DAYS=${config.regimeSlopeDays}
# Entries allowed per UTC day. 0 = unlimited (the engine treats 0 as no cap).
MAX_TRADES_PER_DAY=${config.maxTradesPerDay}
# +1% breakeven lock — proven OFF (it exits before the +3% TP)
BREAKEVEN_ENABLED=${config.breakevenEnabled}
BREAKEVEN_TRIGGER=${config.breakevenTrigger}
BREAKEVEN_OFFSET=${config.breakevenOffset}
# Force-close open positions at UTC day end (intraday discipline)
CLOSE_AT_UTC_DAY_END=${config.closeAtUtcDayEnd}
MIN_TP_PERCENT=${config.minTpPercent}

# --- Execution ---
TIMEFRAME=${config.timeframe}
MTF_TIMEFRAME=${config.mtfTimeframe}
ATR_PERIOD=${config.atrPeriod}
SIGNAL_INTERVAL=${config.signalInterval}
# Trailing defaults sit beyond the +3% TP so trailing never exits a trade before its target.
# Trailing: ATR-scaled distance when TRAILING_ATR_MULTIPLIER > 0 (0 = % callback).
# Champion A3: 2x ATR trail activating at +1%.
TRAILING_ATR_MULTIPLIER=${config.trailingAtrMultiplier}
TRAILING_STOP_ACTIVATE=${config.trailingStopActivate}
TRAILING_STOP_CALLBACK=${config.trailingStopCallback}
# Volatility-adaptive stop: when > 0 the stop is N×ATR (clamped by max %),
# otherwise the fixed SL_PERCENT above. Proven OFF (0).
SL_ATR_MULTIPLIER=${config.slAtrMultiplier}
SL_ATR_MAX_PERCENT=${config.slAtrMaxPercent}
# Funding-rate gate (futures): skip long entries when the pair's live rate
# exceeds this per-interval fraction (a long PAYS positive funding). 0 = off.
FUNDING_RATE_MAX=${config.fundingRateMax}
MAX_HOLD_TIME=${config.maxHoldTime}
# Realtime scan/management pass interval (seconds). Prices are WS-fed; this only
# paces the strategy loop, it is NOT the tick cadence (10s is the proven value).
PRICE_REFRESH_INTERVAL=10
# Max age (seconds) of a WS user-data balance snapshot before a REST refresh.
WS_BALANCE_MAX_AGE=90

# --- Risk Model ---
# 1% fixed-fractional risk per trade (qty sized from the entry-to-stop distance)
RISK_PER_TRADE=0.01
MIN_RISK_REWARD=1.5
# Notional caps — keep at 1.0 on small accounts: 0.2 x $22 = $4.40 is below the
# exchange's $5 NOTIONAL.minNotional and EVERY entry would be skipped.
BALANCE_USAGE_PERCENT=${config.balanceUsagePercent}
MAX_SYMBOL_ALLOCATION_PERCENT=${config.maxSymbolAllocationPercent}
MAX_DAILY_DRAWDOWN=${config.maxDailyDrawdown}
MAX_LOSS_STREAK=${config.maxLossStreak}
MAX_WIN_STREAK=${config.maxWinStreak}
COOLDOWN_LOSS=${config.cooldownLoss}
COOLDOWN_WIN=${config.cooldownWin}
LOSS_REENTRY_COOLDOWN=${config.lossReentryCooldown}
MAX_SLIPPAGE_PERCENT=0.5
ENTRY_TIMEOUT=15
# Scale-out: proven OFF for this strategy (the tested edge had none).
SCALE_OUT_ENABLED=false
SCALE_OUT_R_MULTIPLE=1.0
SCALE_OUT_FRACTION=0.5

# --- Optional entry quality gates (all proven OFF for the current champion) ---
# No-ops at these defaults; kept as tunables so the decision core can be
# re-tested without a code change.
ENTRY_MAX_EXT_ATR=${config.entryMaxExtAtr}
ENTRY_EXT_EMA=${config.entryExtEma}
ENTRY_VOL_MULT=${config.entryVolMult}
ENTRY_VOL_LOOKBACK=${config.entryVolLookback}
ENTRY_REQUIRE_RSI_RISE2=${config.entryRequireRsiRise2}

# --- Symbols ---
DYNAMIC_SYMBOLS=${config.dynamicSymbols}
MAX_SYMBOLS=${config.maxSymbols}
STATIC_SYMBOLS=${config.staticSymbols.join(',')}
QUOTE_ASSET=USDT
EXCLUDE_SYMBOLS=USDC,BUSD,UP,DOWN,FDUSD,TUSD,DAI
SYMBOL_REFRESH_INTERVAL=3600

# --- Dynamic Screener ---
ADX_THRESHOLD=${config.adxThreshold}
ADX_PERIOD=${config.adxPeriod}
TOP_CANDIDATES=50
MIN_VOLUME_USDT=1000000
MIN_PRICE_CHANGE_PERCENT=0.5
MIN_VOLATILITY_PERCENT=0.3
Z_SCORE_WEIGHT_VOLUME=0.20
Z_SCORE_WEIGHT_CHANGE=0.20
Z_SCORE_WEIGHT_VOLATILITY=0.20
Z_SCORE_WEIGHT_ADX=0.40
CORRELATION_THRESHOLD=0.70
CORRELATION_PENALTY=0.90
TREND_LOOKBACK=20

# --- Database, Webhooks, Logging ---
DB_PATH=./data/trading.db
CONTROL_FILE=./data/engine_control.json
DISCORD_WEBHOOK_URL=${config.discordWebhookUrl}
DISCORD_COOLDOWN=30
# DEBUG logs every raw WebSocket frame (~10 MB/min) — keep INFO unless debugging.
LOG_LEVEL=${config.logLevel}
LOG_FILE=./logs/trading.log
HEALTH_CHECK_INTERVAL=60
REST_WEIGHT_LIMIT=1200

# --- Wallet Safety ---
# If false, existing spot balances in your wallet are left completely untouched
AUTO_LIQUIDATE_ORPHANS=false
ORPHAN_ADOPT_WINDOW_HOURS=48
`;
}

/** Compute the effective per-trade allocation: min(total-usage, per-symbol cap) with 1% fee buffer. */
export function effectiveAllocation(config: BotConfig, equity: number): number {
  return Math.min(equity * config.balanceUsagePercent, equity * config.maxSymbolAllocationPercent) * 0.99;
}
