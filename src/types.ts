/** The bot runs exactly one strategy. */
export type StrategyPreset = 'intraday_rsi';

export interface BotConfig {
  preset: StrategyPreset;
  timeframe: string;
  mtfTimeframe: string;       // regime timeframe (daily)
  atrPeriod: number;
  trailingStopActivate: number;
  trailingStopCallback: number;
  maxHoldTime: number;        // seconds
  minTpPercent: number;       // TP floor as a fraction
  signalInterval: number;     // seconds between scan passes
  // --- rsi_dip strategy (the only signal engine) ---
  strategyMode: 'rsi_dip';
  slPercent: number;          // fixed SL bracket, fraction (0.012 = -1.2%)
  tpPercent: number;          // fixed TP bracket, fraction (0.03 = +3%)
  rsiPeriod: number;
  rsiOversold: number;
  entryRsiMin: number;        // RSI floor gate: skip entries when RSI < this (0 = off)
  trailingAtrMultiplier: number; // ATR-scaled trail distance (0 = use % callback)
  // --- optional entry quality gates (all off by default; see SignalGenerator) ---
  entryMaxExtAtr: number;     // skip entries when close > N×ATR above the extension EMA (0 = off)
  entryExtEma: number;        // EMA span used by the extension measure
  entryVolMult: number;       // require confirmation volume ≥ N× rolling average (0 = off)
  entryVolLookback: number;   // rolling window (bars) for the volume average
  entryRequireRsiRise2: boolean; // require two consecutive rising RSI prints before entry
  // --- volatility-adaptive stop (0 = fixed SL_PERCENT, the proven baseline) ---
  slAtrMultiplier: number;    // stop distance = N×ATR instead of the fixed %
  slAtrMaxPercent: number;    // clamp on the ATR stop, fraction of entry
  // --- breakeven lock levels (used when breakevenEnabled) ---
  breakevenTrigger: number;   // profit fraction that arms the lock
  breakevenOffset: number;    // stop offset above entry once armed (covers fees)
  rsiTimeframe: string;       // RSI sampling bucket ('1h')
  rsiTimeframeMs: number;     // bucket size in ms (derived; explicit wins)
  regimeEma: number;          // daily EMA span for the regime gate
  regimeSlopeDays: number;    // EMA rising-over-N-days requirement
  maxTradesPerDay: number;    // entries per UTC day (0 = unlimited)
  breakevenEnabled: boolean;  // +1% BE lock (must stay OFF for this strategy)
  closeAtUtcDayEnd: boolean;  // force-close open positions at UTC day end
  // --- risk ---
  maxDailyDrawdown: number;
  maxLossStreak: number;
  maxWinStreak: number;
  cooldownLoss: number;       // seconds
  lossReentryCooldown: number; // per-symbol wait (s) after any stop-out
  cooldownWin: number;        // seconds
  balanceUsagePercent: number;
  maxSymbolAllocationPercent: number;
  // --- symbols / screener ---
  staticSymbols: string[];
  dynamicSymbols: boolean;
  maxSymbols: number;
  adxThreshold: number;
  adxPeriod: number;
  // --- runtime ---
  paperTrade: boolean;
  useTestnet: boolean;
  // --- market selection (spot = proven default; futures = USDⓈ-M module) ---
  market: 'spot' | 'futures';
  futuresOneWayMode: boolean;
  futuresMarginType: 'ISOLATED' | 'CROSSED';
  futuresLeverage: number;
  /** Funding-rate gate (futures): skip longs when live rate > this per 8h interval (0 = off). */
  fundingRateMax: number;
  /** 24h futures soak watchdog: heartbeat stall alert threshold in seconds. */
  soakStallSeconds: number;
  /** All-market ticker REST fallback cadence (seconds) when fstream frames are dropped. */
  tickersRestFallbackSeconds: number;
  discordWebhookUrl: string;
  logLevel: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR';
}

/**
 * The engine's REAL per-symbol decision state, published each cycle via
 * risk_state -> /api/status (`signal_state`) and the /ws push. The dashboard
 * renders exactly this — it never synthesises signal factors in the browser.
 */
export interface SignalState {
  symbol: string;
  time?: number;
  regime: 'UP' | 'DOWN' | 'NOT_READY' | 'NO_DATA';
  regime_ema?: number;
  regime_price?: number;
  regime_ema_value?: number;
  rsi: number | null;
  rsi_prev: number | null;
  oversold?: number;
  rsi_period?: number;
  rsi_timeframe?: string;
  trigger: boolean;
  signal: 'BUY' | 'NEUTRAL';
  atr?: number;
  reason: string;
}

export interface ActiveTrade {
  id: string;
  symbol: string;
  side: 'BUY' | 'SELL';
  entryPrice: number;
  currentPrice: number;
  quantity: number;
  notional: number;
  entryTime: number;
  stopPrice: number;
  takeProfit: number;
  atr: number;
  trailingActive: boolean;
  trailingStop: number;
  breakevenActivated: boolean;
  unrealizedPnl: number;
  unrealizedPnlPct: number;
  /**
   * Provenance of `currentPrice` / `unrealizedPnl`, stamped by the monitor from
   * the engine's own published position state (see the engine README's
   * "Active-position sync"). `db` means no engine mark was available and the row
   * falls back to the entry price.
   */
  positionSource?: 'engine-futures' | 'engine-scan' | 'engine' | 'db';
  /** Exchange-side position size — can differ from `quantity` before reconcile. */
  liveQty?: number;
  /** Age (seconds) of the engine snapshot behind these numbers; null if none. */
  positionAgeS?: number | null;
  /** The snapshot is older than the freshness budget: the engine stopped publishing. */
  positionStale?: boolean;
}

export interface ClosedTrade {
  id: string;
  symbol: string;
  side: 'BUY' | 'SELL';
  entryPrice: number;
  exitPrice: number;
  quantity: number;
  pnl: number;
  pnlPct: number;
  /**
   * Timestamps (client-epoch ms). exitTime = the exit order's fill/update time;
   * entryTime = the matched BUY entry order's time when the engine provides one
   * (entry_ts), otherwise an estimate — shown as '~' in the UI.
   */
  entryTime: number;
  entryTimeEstimated: boolean;
  exitTime: number;
  /** Exit labels as persisted/mapped from the engine's orders table. */
  exitReason:
    | 'STOP_LOSS'
    | 'TAKE_PROFIT'
    | 'TRAILING_STOP'
    | 'TIME_STOP'
    | 'EOD_CLOSE'
    | 'MANUAL'
    | 'EMERGENCY_CLOSE'
    | 'REMOTE_CLOSE'
    | 'REMOTE_CLOSE_ALL'
    | 'PARTIAL_EXIT'
    | 'MARKET_EXIT';
  /**
   * True when the engine never recorded a reason for this row (it predates the
   * exit_reason column) and the label was INFERRED from the PnL sign. The UI
   * marks those, because an inferred 'TAKE_PROFIT' means "profitable exit", not
   * "the target was reached". Absent on rows the engine labelled itself.
   */
  exitReasonInferred?: boolean;
}

export interface LogMessage {
  id: string;
  timestamp: string;
  level: 'DEBUG' | 'INFO' | 'WARN' | 'ERROR' | 'SUCCESS';
  category: 'SIGNAL' | 'ORDER' | 'RISK' | 'HEALTH' | 'SYS';
  message: string;
  symbol?: string;
  details?: Record<string, any>;
}

/** The engine's published risk snapshot (risk_state["engine_risk"]). */
export interface EngineRiskState {
  loss_streak: number;
  win_streak: number;
  cooldown_until: number;               // unix seconds, 0 = none
  cooldown_kind: 'loss' | 'win' | string;
  active_cooldowns: Record<string, { until: number; kind: string }>;
  drawdown_used: number;                // fraction of equity (0.05 = 5%)
  drawdown_breaker: boolean;            // daily-drawdown circuit breaker tripped
  breaker_reason: string;
  daily_pnl: number;
}

export interface MarketSymbolData {
  symbol: string;
  name: string;
  price: number;
  priceChange24h: number;
  high24h: number;
  low24h: number;
  volume24h: number;
  /** Engine-published signal state (real) or an explicit "engine offline" placeholder. */
  signal: SignalState;
  sparkline: number[];
  inCooldown: boolean;
  cooldownEndsAt?: number;
}

export interface CandidateSymbol {
  symbol: string;
  name: string;
  price: number;
  priceChange24h: number;
  volume24h: number;
  volatility: number;
  adx: number;
  zScore: number;
  isSelected: boolean;
  momentumRank: number;
}

export interface VpsBotStatus {
  connected: boolean;
  endpoint: string;
  lastSyncTime?: string;
  latencyMs?: number;
  engineStatus: string;
  engineRunning: boolean;
  error?: string;
  serverTime?: string;
  serverEpochMs?: number;
  /** Client-side Date.now() when the last engine payload arrived (drives data-age). */
  lastPayloadAt?: number;
  stats?: {
    total_orders: number;
    closed_trades: number;
    winning_trades: number;
    losing_trades: number;
    breakeven_trades: number;
    total_realized_pnl: number;
    win_rate: number;
    profit_factor: number;
    avg_win: number;
    avg_loss: number;
  };
}

export interface VpsControlState {
  paused: boolean;
  pauseReason?: string;
}

export interface VpsBalanceData {
  totalEquity: number;
  freeQuote: number;
  lockedQuote: number;
  quoteAsset: string;
  isLive: boolean;
  /** Human account label from the monitor: e.g. 'FUTURES Live', 'SPOT Paper'. */
  account?: string;
  dailyPnl?: number;
  /**
   * Which transport answered the balance read (engine-published):
   * 'ws' = user-data stream cache (outboundAccountPosition), 'rest' = REST
   * /api/v3/account snapshot, 'db' = last equity published by the engine,
   * 'paper' = simulated paper balance.
   */
  source?: 'ws' | 'rest' | 'db' | 'paper' | string;
  /** Age (seconds) of the WS balance datum when source === 'ws'. */
  ageS?: number | null;
  balances?: Array<{
    asset: string;
    free: number;
    locked: number;
    total?: number;
    usd_value?: number;
  }>;
}

/**
 * Realtime transport health published by the engine (risk_state["ws_streams"]).
 * Every realtime path is a WebSocket stream; the dashboard lights these so a
 * silently dead feed is visible instead of inferred from stale numbers.
 */
export interface WsStreams {
  /** Per-symbol public stream (aggTrade ticks → trading decisions). */
  market?: { connected: boolean; symbols?: number };
  /** All-market !miniTicker@arr stream (screener + monitor prices). transport:
   * 'ws' frames flowing, 'rest' fallback refresher, '' stale/empty. */
  all_tickers?: { connected: boolean; last_frame_age_s?: number | null; transport?: string };
  /** Authenticated WS API session (order placement) + user-data subscription. */
  order_api?: {
    connected: boolean;
    user_stream?: boolean;
    /** Engine's measured clock offset vs the exchange (ms); null until first sync. */
    time_offset_ms?: number | null;
    /** True while the measurement is fresh (within two sync intervals). */
    clock_synced?: boolean;
  };
  /**
   * Age (seconds) of the engine's publication, computed by the monitor. Lets the
   * dashboard distinguish "stream down" from "engine stopped publishing" — a
   * dead engine leaves its last snapshot behind and would keep lights green.
   */
  engine_age_s?: number | null;
}

/**
 * Engine decision-loop heartbeat (risk_state["loop_state"]), written once per
 * iteration on EVERY path — trading, paused, health-pause.
 *
 * The dashboard's loop light must read this rather than the age of the newest
 * per-symbol signal snapshot: that snapshot only advances when a symbol gets past
 * every gate, so pausing, holding a full book or a tripped breaker froze it while
 * the engine was healthy. `paused_applied` is the engine acknowledging the pause
 * the monitor asked for, so the UI can show an applied pause instead of echoing
 * its own request.
 */
export interface LoopState {
  cycle_ms: number;
  cycle: number;
  interval_s: number;
  phase: 'trading' | 'paused' | 'health-pause' | string;
  paused_requested: boolean;
  paused_applied: boolean;
  active_trades: number;
  symbols: number;
  /** Monitor-computed age (seconds) of the heartbeat. */
  age_s?: number | null;
}

/** Engine-published USDⓈ-M futures account state (risk_state["futures_state"]). */
export interface FuturesPosition {
  symbol: string;
  amount: number;             // contracts; negative = bearish position
  entry_price: number;
  unrealized_pnl?: number | null;
  leverage?: number | string | null;
  margin_type?: string | null;
  liquidation_price?: number | null;
  mark_price?: number | null;
  paper?: boolean;            // true = mirrored from paper trades, not the exchange
}

export interface FuturesState {
  positions: FuturesPosition[];
  leverage: number;
  margin_type: string;
  one_way: boolean;
  updated_ms?: number;
  age_s?: number | null;
}

/** Capital-roadmap payload (status.py `roadmap` — live floors/prices/funding). */
export interface RoadmapStage {
  threshold: number;
  label: string;
  reached: boolean;
}

export interface RoadmapPair {
  symbol: string;
  price?: number | null;
  floor: number;
  ok: boolean;
  /**
   * Equity at which proven sizing clears this pair's own NOTIONAL floor
   * (`floor * sl_percent / risk_per_trade`), computed server-side per pair —
   * so a blocked chip never inherits a threshold that only held for a
   * different watchlist.
   */
  required_equity?: number | null;
  funding_rate?: number | null;
}

export interface Roadmap {
  equity: number;
  risk_per_trade: number;
  sl_percent: number;
  proven_notional: number;
  implied_leverage: number;
  futures_leverage_cfg: number;
  /** Monitor-computed age (seconds) of this snapshot — floors/prices are cached 10 min. */
  age_s?: number | null;
  /**
   * Which list `pairs` describes: the ENGINE's live watched symbols (rotated by
   * the dynamic screener) or the fallback default list, used only when the
   * engine has published no watchlist yet.
   */
  pairs_source?: 'engine-watchlist' | 'default';
  pairs: RoadmapPair[];
  ok_pairs: string[];
  blocked_pairs: string[];
  stages: RoadmapStage[];
  fee_edge_pct: number;
  generated_ms?: number;
}

/** One watchdog supervision event (soak_watchdog.py JSONL feed). */
export interface WatchdogEvent {
  ts_ms: number;
  utc: string;
  kind: string;
  detail: string;
  age_s: number;
}

/** 24h futures paper-soak health (status.py `soak`). */
export interface FuturesSoak {
  running: boolean;
  status: string;
  db: string;
  started_ms?: number;
  uptime_h?: number;
  /** 24h deadline (started_ms + SOAK_HOURS) when the soak start could be pinned. */
  deadline_ms?: number;
  hours_total?: number;
  equity?: number | null;
  paper_balance?: number | null;
  closed?: number;
  wins?: number;
  losses?: number;
  pnl?: number | null;
  last_trade_ms?: number | null;
  /** PM2 status of the soak-watchdog supervisor (not running = nobody watching). */
  watchdog_status?: string;
  watchdog_restarts?: number;
  /** Once-per-event alert flags set in the watchdog's state file. */
  watchdog_alerts?: string[];
  /** Recent supervision events (newest last from backend; UI reverses). */
  watchdog_events?: WatchdogEvent[];
  /** Last supervision pass (watchdog liveness stamp, ms epoch). */
  watchdog_last_check_ms?: number;
}

export interface PushResult {
  ok: boolean;
  message: string;
}
