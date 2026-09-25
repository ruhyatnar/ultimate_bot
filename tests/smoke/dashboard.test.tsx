/**
 * Dashboard UI smoke tests — render the REAL component tree
 * (react-dom/server) with engine-shaped fixtures and assert that every
 * section the operator relies on actually renders.
 *
 * Covers the LiveDashboard sections plus the App shell (tab navigation), so
 * removing or rewiring a tab cannot silently break the served UI.
 *
 * Run with: npm run test:ui  (see scripts/ui_smoke.mjs)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import App from '../../src/App';
import { LiveDashboard } from '../../src/components/LiveDashboard';
import { SignalInspector } from '../../src/components/SignalInspector';
import { DebugConsole } from '../../src/components/DebugConsole';
import { ConfigTab } from '../../src/components/ConfigTab';
import { DeployGuide } from '../../src/components/DeployGuide';
import type {
  BotConfig,
  ActiveTrade,
  ClosedTrade,
  MarketSymbolData,
  EngineRiskState,
  FuturesState,
  LogMessage,
  Roadmap,
} from '../../src/types';
import { ENGINE_FRESHNESS_S } from '../../src/utils/freshness';
import { createEngineLogSink, parseEngineLogLine } from '../../src/utils/engineLog';

// ─── Fixtures shaped exactly like the engine publishes (status.py) ──────────

const baseConfig: BotConfig = {
  preset: 'intraday_rsi',
  timeframe: '15m',
  mtfTimeframe: '1d',
  atrPeriod: 14,
  trailingStopActivate: 0.01,
  trailingStopCallback: 0.01,
  maxHoldTime: 84600,
  minTpPercent: 0.006,
  signalInterval: 10,
  strategyMode: 'rsi_dip',
  slPercent: 0.012,
  tpPercent: 0.03,
  rsiPeriod: 14,
  rsiOversold: 40,
  entryRsiMin: 0,
  trailingAtrMultiplier: 2.0,
  entryMaxExtAtr: 0,
  entryExtEma: 20,
  entryVolMult: 0,
  entryVolLookback: 20,
  entryRequireRsiRise2: false,
  slAtrMultiplier: 0,
  slAtrMaxPercent: 0.03,
  breakevenTrigger: 0.01,
  breakevenOffset: 0.001,
  rsiTimeframe: '1h',
  rsiTimeframeMs: 3600000,
  regimeEma: 20,
  regimeSlopeDays: 3,
  maxTradesPerDay: 6,
  breakevenEnabled: false,
  closeAtUtcDayEnd: true,
  maxDailyDrawdown: 0.05,
  maxLossStreak: 3,
  maxWinStreak: 4,
  cooldownLoss: 3600,
  lossReentryCooldown: 900,
  cooldownWin: 1800,
  balanceUsagePercent: 1.0,
  maxSymbolAllocationPercent: 100,
  staticSymbols: ['NEARUSDT'],
  dynamicSymbols: false,
  maxSymbols: 3,
  adxThreshold: 25,
  adxPeriod: 14,
  paperTrade: true,
  useTestnet: false,
  market: 'spot',
  futuresOneWayMode: true,
  futuresMarginType: 'ISOLATED',
  futuresLeverage: 3,
  fundingRateMax: 0.0005,
  soakStallSeconds: 900,
  tickersRestFallbackSeconds: 60,
  discordWebhookUrl: '',
  logLevel: 'INFO',
};

const signal = (over: Partial<MarketSymbolData['signal']> = {}) => ({
  symbol: 'NEARUSDT',
  regime: 'UP' as const,
  rsi: 31.2,
  rsi_prev: 29.8,
  trigger: false,
  signal: 'NEUTRAL' as const,
  reason: 'waiting for RSI dip',
  time: Date.now() - 3000,
  ...over,
});

const symbolData: MarketSymbolData[] = [
  {
    symbol: 'NEARUSDT',
    name: 'NEAR Protocol',
    price: 3.21,
    priceChange24h: 2.4,
    high24h: 3.3,
    low24h: 3.0,
    volume24h: 123_000_000,
    signal: signal(),
    sparkline: [3.0, 3.05, 3.1, 3.21],
    inCooldown: false,
  },
];

const activeTrade: ActiveTrade = {
  id: 'db_NEARUSDT_1',
  symbol: 'NEARUSDT',
  side: 'BUY',
  entryPrice: 3.0,
  currentPrice: 3.15,
  quantity: 30,
  notional: 90,
  entryTime: Date.now() - 600_000,
  stopPrice: 2.964,
  takeProfit: 3.09,
  atr: 0.05,
  trailingActive: true,
  trailingStop: 3.05,
  breakevenActivated: false,
  unrealizedPnl: 4.5,
  unrealizedPnlPct: 5.0,
};

const closedTrade: ClosedTrade = {
  id: 'db_NEARUSDT_2',
  symbol: 'NEARUSDT',
  side: 'BUY',
  entryPrice: 3.0,
  exitPrice: 3.09,
  quantity: 30,
  pnl: 2.4,
  pnlPct: 2.7,
  entryTime: Date.now() - 7_200_000,
  entryTimeEstimated: false,
  exitTime: Date.now() - 3_600_000,
  exitReason: 'TAKE_PROFIT',
};

const engineRisk: EngineRiskState = {
  loss_streak: 0,
  win_streak: 1,
  cooldown_until: 0,
  cooldown_kind: 'loss',
  active_cooldowns: {},
  drawdown_used: 0.012,
  drawdown_breaker: false,
  breaker_reason: '',
  daily_pnl: 2.1,
};

const futuresState: FuturesState = {
  positions: [],
  leverage: 3,
  margin_type: 'ISOLATED',
  one_way: true,
};

const roadmap: Roadmap = {
  equity: 1000,
  risk_per_trade: 0.01,
  sl_percent: 0.012,
  proven_notional: 833,
  implied_leverage: 1.2,
  futures_leverage_cfg: 3,
  pairs: [],
  ok_pairs: [],
  blocked_pairs: [],
  stages: [{ threshold: 1000, label: 'start', reached: true }],
  fee_edge_pct: 0.05,
};

const baseProps = {
  equity: 1000,
  dailyRealizedPnl: 2.1,
  unrealizedPnl: 4.5,
  activeTrades: [] as ActiveTrade[],
  closedTrades: [] as ClosedTrade[],
  symbolsData: symbolData,
  candidates: [],
  config: baseConfig,
  onUpdateConfig: () => {},
  onApplyPreset: () => {},
  onCloseTrade: () => {},
  onCloseAllTrades: () => {},
  winStreak: 1,
  lossStreak: 0,
  entriesToday: 1,
  vpsRiskAvailable: true,
  isLossCooldown: false,
  cooldownEndsAt: 0,
  cooldownKind: 'loss',
  engineRisk,
  vpsConnected: true,
  vpsBalance: null,
  onPushConfigToVps: async () => ({ ok: true, message: '' }),
  vpsPushResult: null,
  controlPaused: false,
  onToggleVpsPause: () => {},
  engineRunning: true,
  serverStats: null,
  wsStreams: null,
  loopState: null,
  futuresState,
  untrackedPnl: null,
  roadmap,
  soak: null,
  onSoakControl: async () => true,
};

const render = (over: Partial<typeof baseProps> = {}) =>
  renderToStaticMarkup(<LiveDashboard {...baseProps} {...over} />);

// ─── Tests ───────────────────────────────────────────────────────────────────

test('renders all six dashboard sections', () => {
  const html = render({ activeTrades: [activeTrade], closedTrades: [closedTrade] });
  // §1 controls
  assert.match(html, /Active Strategy/);
  assert.match(html, /Sync to engine \.env/);
  // §2 KPI row
  assert.match(html, /Daily Net PnL/);
  assert.match(html, /Drawdown Breaker/);
  assert.match(html, /Open Risk/);
  // §3 engine health
  assert.match(html, /Engine Health/);
  assert.match(html, /Realtime transports/);
  assert.match(html, /Decision loop/);
  // §4 market
  assert.match(html, /Monitored Symbols \(1\)/);
  // §5 positions
  assert.match(html, /Active Positions/);
  assert.match(html, /Bracket Ladder/);
  // §6 history
  assert.match(html, /Recent Completed Trades/);
});

test('capital roadmap states which universe it analyses', () => {
  // The roadmap used to analyse a list baked into capital_roadmap.py, so the card
  // described pairs the engine was not trading. It must present the engine's live
  // watched set — and say that is what it is — falling back only when the engine
  // has published no watchlist yet.
  const watched = render({
    roadmap: {
      ...roadmap,
      pairs_source: 'engine-watchlist',
      pairs: [
        { symbol: 'TAKEUSDT', price: 4.1, floor: 5, ok: true, funding_rate: 0.0001 },
        { symbol: 'BCHUSDT', price: 300, floor: 20, ok: false, funding_rate: 0.0001 },
      ],
      ok_pairs: ['TAKEUSDT'],
      blocked_pairs: ['BCHUSDT'],
    },
  });
  assert.match(watched, /2 engine-watched pairs/);
  assert.match(watched, /Capital Roadmap/);

  const fallback = render({ roadmap: { ...roadmap, pairs_source: 'default' } });
  assert.match(fallback, /0 fallback pairs/);
});

test('a blocked-pair chip shows its own computed threshold, not a literal', () => {
  // The chip used to render a hardcoded '$24' — a number that was only correct
  // while the watched set's highest floor happened to be $20, and which would
  // silently mislead the moment the screener rotated in a different pair. It
  // must print the per-pair requirement the engine computed.
  const html = render({
    roadmap: {
      ...roadmap,
      pairs_source: 'engine-watchlist',
      pairs: [
        { symbol: 'TAKEUSDT', price: 4.1, floor: 5, ok: true, required_equity: 6, funding_rate: 0.0001 },
        { symbol: 'BCHUSDT', price: 300, floor: 50, ok: false, required_equity: 60, funding_rate: 0.0001 },
      ],
      ok_pairs: ['TAKEUSDT'],
      blocked_pairs: ['BCHUSDT'],
    },
  });
  assert.match(html, /BCHUSDT @ \$60/);
  assert.doesNotMatch(html, /@ \$24/);
});

test('a blocked-pair chip omits the threshold when the payload predates it', () => {
  // A payload from an older engine carries no required_equity. The chip must
  // then show the symbol alone rather than resurrect the baked-in number.
  const html = render({
    roadmap: {
      ...roadmap,
      pairs_source: 'engine-watchlist',
      pairs: [
        { symbol: 'BCHUSDT', price: 300, floor: 20, ok: false, funding_rate: 0.0001 },
      ],
      ok_pairs: [],
      blocked_pairs: ['BCHUSDT'],
    },
  });
  assert.match(html, /BCHUSDT/);
  assert.doesNotMatch(html, /BCHUSDT @/);
});

test('active position shows bracket ladder prices and market close', () => {
  const html = render({ activeTrades: [activeTrade] });
  assert.match(html, /SL 2\.9640/);
  assert.match(html, /TP 3\.0900/);
  assert.match(html, /Market Close/);
  assert.match(html, /Trailing/);
  assert.match(html, /of 3 slots/);
});

test('a stale engine position snapshot is flagged beside the mark', () => {
  // The mark comes from the engine's own published snapshot; when that snapshot
  // stops being refreshed the row must say so rather than presenting its last
  // known state as a live price.
  const stale = render({
    activeTrades: [{ ...activeTrade, positionStale: true, positionAgeS: 412 }],
  });
  assert.match(stale, /engine stale/);
  assert.match(stale, /412s old/);
  const fresh = render({ activeTrades: [{ ...activeTrade, positionStale: false }] });
  assert.doesNotMatch(fresh, /engine stale/);
});

test('the decision-loop row reads the engine heartbeat, not a frozen snapshot', () => {
  // Per-symbol decisions only advance when a symbol gets past every gate, so
  // pausing or holding a full book freezes that timestamp while the engine keeps
  // looping. The row must report the engine's heartbeat (and say which source it used).
  const withBeat = render({
    loopState: {
      cycle_ms: Date.now() - 3_000,
      cycle: 42,
      interval_s: 10,
      phase: 'paused',
      paused_requested: true,
      paused_applied: true,
      active_trades: 0,
      symbols: 5,
      age_s: 3,
    },
  });
  assert.match(withBeat, /decision-loop heartbeat/);
  assert.doesNotMatch(withBeat, /per-symbol signal snapshot/);
  // No heartbeat (an older engine) -> fall back to the snapshot, and say so.
  const noBeat = render({ loopState: null });
  assert.match(noBeat, /per-symbol signal snapshot/);
});

test('a pause reads as applied only once the engine acknowledges it', () => {
  const beat = (applied: boolean) => ({
    cycle_ms: Date.now(),
    cycle: 7,
    interval_s: 10,
    phase: applied ? 'paused' : 'trading',
    paused_requested: applied,
    paused_applied: applied,
    active_trades: 0,
    symbols: 5,
    age_s: 1,
  });
  const pending = render({ controlPaused: true, loopState: beat(false) });
  assert.match(pending, /pause requested/);
  assert.doesNotMatch(pending, /entries paused/);

  const applied = render({ controlPaused: true, loopState: beat(true) });
  assert.match(applied, /entries paused/);
  assert.doesNotMatch(applied, /pause requested/);

  // Not paused -> no chip at all.
  const running = render({ controlPaused: false, loopState: beat(false) });
  assert.doesNotMatch(running, /pause requested/);
  assert.doesNotMatch(running, /entries paused/);
});

test('breaker banner + card state when the engine trips the breaker', () => {
  const html = render({
    engineRisk: { ...engineRisk, drawdown_breaker: true, breaker_reason: 'daily drawdown 5.1% > 5.0%' },
  });
  assert.match(html, /Circuit Breaker Tripped/);
  assert.match(html, /daily drawdown 5\.1%/);
  assert.match(html, /TRIPPED/);
});

test('loss cooldown banner shows a relative end time', () => {
  const in47m = Date.now() + 47 * 60_000;
  const html = render({ isLossCooldown: true, cooldownEndsAt: in47m, cooldownKind: 'loss' });
  assert.match(html, /Loss-Streak Cooldown/);
  assert.match(html, /in 4[67]m/);
});

test('flat state is honest and explains the decision cadence', () => {
  const html = render();
  assert.match(html, /No open positions/);
  assert.match(html, /every USDT is spendable/);
  assert.match(html, /No closed trades yet/);
});

test('engine-authoritative profit factor and all-time realized PnL surface', () => {
  const html = render({
    closedTrades: [closedTrade],
    serverStats: {
      closed_trades: 12,
      winning_trades: 7,
      losing_trades: 5,
      breakeven_trades: 0,
      win_rate: 58.3,
      profit_factor: 2.06,
      total_realized_pnl: 41.2,
    },
  });
  assert.match(html, /2\.06/); // engine PF preferred over local derivation
  assert.match(html, /All-time realized/);
  assert.match(html, /\$41\.20/);
  assert.match(html, /7W/);
  assert.match(html, /5L/);
});

// ─── Recent Completed Trades: order, count and reason provenance ────────────

const closedAt = (symbol: string, exitTime: number, over: Partial<ClosedTrade> = {}): ClosedTrade => ({
  ...closedTrade,
  id: `order_${symbol}_${exitTime}`,
  symbol,
  exitTime,
  entryTime: exitTime - 600_000,
  ...over
});

test('recent completed trades are newest-first whatever order the payload arrives in', () => {
  // The table used to slice the LAST eight of a newest-first list and reverse
  // them, so it printed the OLDEST exits in the window under a "newest first"
  // subtitle and the freshest exits never appeared at all.
  const now = Date.now();
  const ascending = Array.from({ length: 10 }, (_, i) =>
    closedAt(`SYM${i}USDT`, now - (9 - i) * 60_000) // SYM0 oldest … SYM9 newest
  );
  const html = render({ closedTrades: ascending });

  const body = html.slice(html.indexOf('Recent Completed Trades'));
  const seen = [...body.matchAll(/SYM(\d)USDT/g)].map(m => Number(m[1]));
  // Newest eight, newest first — and the two oldest are not shown.
  assert.deepEqual(seen.slice(0, 8), [9, 8, 7, 6, 5, 4, 3, 2]);
  assert.ok(!seen.includes(0) && !seen.includes(1), `oldest exits leaked in: ${seen}`);
  // Rendering order must be descending in exitTime, not the array's own order.
  const exitTimes = ascending.slice(0, 8).map(t => t.exitTime);
  assert.ok(exitTimes[0] < exitTimes[7], 'fixture must start oldest-first');
});

test('"Total closed" reports the engine trade count, not the served order window', () => {
  // The chip counted the rows in the 25-order window (~a dozen exits) while the
  // win-rate card beside it reported the engine's full-history trade count — two
  // numbers labelled the same thing.
  const allTime = render({
    closedTrades: [closedTrade],
    serverStats: {
      closed_trades: 42,
      winning_trades: 25,
      losing_trades: 17,
      breakeven_trades: 0,
      win_rate: 59.5,
      profit_factor: 1.8,
      total_realized_pnl: 120.4,
      exit_legs: 57,
    },
  });
  // Assert the CHIP, not just that "42" appears somewhere on the page: the
  // win-rate card beside it renders the same engine count, so a loose match
  // would pass even while the chip kept printing the window length.
  assert.match(allTime, /Total closed \(all-time\)<\/span><strong[^>]*>42<\/strong>/);
  const windowed = render({ closedTrades: [closedTrade] });
  assert.match(windowed, /Total closed \(window\)<\/span><strong[^>]*>1<\/strong>/);
});

test('an inferred exit reason is marked; an engine-recorded one is not', () => {
  // status.py can only serve a LABEL the engine actually recorded for rows
  // written after the exit_reason column existed. For older rows it still
  // infers from the PnL sign — and a profitable protective stop then looks like
  // a take-profit win. Those rows must be marked as guesses.
  const html = render({
    closedTrades: [
      closedAt('ENGINEUSDT', Date.now(), { exitReason: 'TRAILING_STOP' }),
      closedAt('LEGACYUSDT', Date.now() - 1000, { exitReason: 'TAKE_PROFIT', exitReasonInferred: true }),
    ],
  });
  const marks = html.match(/Inferred from the realized PnL sign/g) ?? [];
  assert.equal(marks.length, 1, 'exactly the legacy row is flagged as inferred');
  assert.match(html, /TRAILING STOP/);
  // The trailing-stop branch is reachable now that the engine's own reason is
  // served — it was unreachable while every label was inferred from the sign.
  assert.match(html, /border-amber-500\/30 bg-amber-500\/20 text-amber-300/);
});

test('the engine stats streaks are numbers and reach the streak row', () => {
  // status.py served win_streak/loss_streak straight from risk_state — strings.
  // The KPI row coerces, but a served NUMBER is the contract; without an
  // engine_risk snapshot (older engine) the served counts are what it has.
  const html = render({
    engineRisk: null,
    winStreak: 0,
    lossStreak: 0,
    serverStats: {
      closed_trades: 9,
      winning_trades: 6,
      losing_trades: 3,
      breakeven_trades: 0,
      win_rate: 66.7,
      profit_factor: 1.5,
      total_realized_pnl: 12.5,
      win_streak: 2,
      loss_streak: 1,
    },
  });
  assert.match(html, /Win <b[^>]*>2<\/b>/);
  assert.match(html, /Loss <b[^>]*>1<\/b>/);
});

test('the Process row follows the engine\'s own status, not a freshness guess', () => {
  const streams = {
    market: { connected: true, symbols: 5 },
    all_tickers: { connected: true, last_frame_age_s: 2 },
    order_api: { connected: true, user_stream: true },
    engine_age_s: 2
  };
  // STOPPED process with a FRESH last snapshot: the old code called this
  // RUNNING because it only looked at the snapshot's age.
  const down = render({ engineRunning: false, wsStreams: streams });
  assert.match(down, /DOWN/);
  assert.match(down, /does not report RUNNING/);
  assert.doesNotMatch(down, />RUNNING</);

  // RUNNING process with a STALE snapshot: alive but not publishing.
  const hung = render({ engineRunning: true, wsStreams: { ...streams, engine_age_s: 412 } });
  assert.match(hung, />RUNNING</);
  assert.match(hung, /may be hung mid-iteration/);
});

test('the Decision loop row shows the engine\'s published cadence, not the client config', () => {
  // SIGNAL_INTERVAL lives in the ENGINE's .env; the browser copy can be stale.
  // When the heartbeat carries a cadence, that is the one the loop actually runs.
  const beat = (interval_s: number) => ({
    cycle_ms: Date.now() - 2_000,
    cycle: 9,
    interval_s,
    phase: 'trading',
    paused_requested: false,
    paused_applied: false,
    active_trades: 0,
    symbols: 5,
    age_s: 2
  });
  const published = render({
    config: { ...baseConfig, signalInterval: 10 },
    loopState: beat(45),
  });
  assert.match(published, /every 45s/);
  assert.doesNotMatch(published, /every 10s/);
  assert.match(published, /own published cadence/);

  // No heartbeat (older engine) -> the configured interval, said to be config.
  const configured = render({ loopState: null });
  assert.match(configured, /every 10s/);
});

test('Risk guardrails name an active streak cooldown and say what it blocks', () => {
  // The row claimed "breaker and streak cooldown state" but rendered only the
  // breaker; a cooldown that blocks every entry was invisible here.
  const in30m = Math.floor((Date.now() + 30 * 60_000) / 1000);
  const cooling = render({
    engineRisk: { ...engineRisk, cooldown_until: in30m, cooldown_kind: 'win' },
  });
  assert.match(cooling, /win cooldown/);
  assert.match(cooling, /Entries blocked by the win-streak cooldown/);
  assert.match(cooling, /Open positions stay managed/);

  const expired = render({
    engineRisk: { ...engineRisk, cooldown_until: Math.floor(Date.now() / 1000) - 60, cooldown_kind: 'loss' },
  });
  assert.doesNotMatch(expired, /loss cooldown</);

  // Per-symbol cooldowns count as active even with no account-wide one.
  const perSymbol = render({
    engineRisk: { ...engineRisk, active_cooldowns: { SOLUSDT: { until: in30m, kind: 'loss' } } },
  });
  assert.match(perSymbol, /loss cooldown/);
  assert.match(perSymbol, /symbols: SOLUSDT/);

  // No risk snapshot at all: unknown, not clear.
  const unknown = render({ engineRisk: null });
  assert.match(unknown, /UNKNOWN, not clear/);
});

test('the freshness budget is one shared constant, not a per-component 30', () => {
  // Three surfaces each hardcoded 30s, so a tuned budget would leave the stream
  // lights and the health card contradicting each other about one payload.
  const streams = (age: number) => ({
    market: { connected: true, symbols: 5 },
    all_tickers: { connected: true, last_frame_age_s: 1 },
    order_api: { connected: true, user_stream: true },
    engine_age_s: age
  });
  const atBudget = render({ wsStreams: streams(ENGINE_FRESHNESS_S) });
  assert.doesNotMatch(atBudget, /may be hung mid-iteration/);
  const overBudget = render({ wsStreams: streams(ENGINE_FRESHNESS_S + 1) });
  assert.match(overBudget, /may be hung mid-iteration/);
});

test('dynamic screener empty state when enabled but no candidates', () => {
  const html = render({ config: { ...baseConfig, dynamicSymbols: true }, candidates: [] });
  assert.match(html, /no candidates yet/);
});

test('untracked positions warning surfaces floating PnL outside management', () => {
  const html = render({ untrackedPnl: -1.25 });
  assert.match(html, /Untracked positions/);
  assert.match(html, /not SL\/TP-managed/);
});

test('futures account chip appears in futures market mode', () => {
  const html = render({ config: { ...baseConfig, market: 'futures', paperTrade: false } });
  assert.match(html, /FUTURES/);
  assert.match(html, /LIVE/);
});

// ─── App shell: tab navigation (no Project Code tab) ────────────────────────

test('app shell renders exactly the five surviving tabs', () => {
  const html = renderToStaticMarkup(<App />);
  const ids = [...html.matchAll(/id="tab-([a-z]+)"/g)].map((m) => m[1]);
  assert.deepEqual(ids, ['dashboard', 'signals', 'debug', 'config', 'deploy']);
  // The removed feature must not leave a dead tab behind.
  assert.doesNotMatch(html, /Project Code|tab-code|Download ZIP|ultimate-bot\.zip/);
});

test('app shell opens on the trading desk, rendered through App', () => {
  const html = renderToStaticMarkup(<App />);
  assert.match(html, /id="tab-dashboard"[^>]*aria-current="page"/);
  assert.equal((html.match(/aria-current="page"/g) ?? []).length, 1);
  assert.match(html, /Active Strategy/);
  assert.match(html, /Monitored Symbols/);
  assert.match(html, /Engine Health/);
});

test('every remaining tab body still renders', () => {
  const logLine: LogMessage = {
    id: 'log_1',
    timestamp: new Date().toISOString(),
    level: 'INFO',
    category: 'SIGNAL',
    message: 'NEARUSDT regime=UP rsi=31.2 -> NEUTRAL',
    symbol: 'NEARUSDT',
  };

  assert.match(
    renderToStaticMarkup(<SignalInspector symbolsData={symbolData} config={baseConfig} />),
    /Engine Signal State/
  );
  assert.match(
    renderToStaticMarkup(
      <DebugConsole logs={[logLine]} onClearLogs={() => {}} signalInterval={10} />
    ),
    /Execution Engine &amp; Debug Terminal/
  );
  assert.match(
    renderToStaticMarkup(
      <ConfigTab
        config={baseConfig}
        onUpdateConfig={() => {}}
        onApplyPreset={() => {}}
        vpsConnected
        onPushToVps={async () => ({ ok: true, message: '' })}
      />
    ),
    /Strategy Preset/
  );
  const deploy = renderToStaticMarkup(<DeployGuide />);
  assert.match(deploy, /Production VPS Deployment &amp; Live Readiness/);
  // The ZIP-era deploy step is gone with the feature.
  assert.doesNotMatch(deploy, /unzip|\.zip/);
});

// ─── Engine Log: line identity, clock, and tracebacks ───────────────────────

/** A line exactly as the engine's RotatingFileHandler writes it. */
const engineLine = (clock: string, message: string, level = 'INFO'): string =>
  `2026-09-24 ${clock} - src.risk.risk_manager - ${level} - ${message}\n`;

test('a log line carries the ENGINE clock, not the browser arrival time', () => {
  const frame = parseEngineLogLine(
    engineLine('17:02:10,629', 'Live account equity updated: $23.47 USDT')
  );
  assert.equal(frame?.kind, 'line');
  if (!frame || frame.kind !== 'line') throw new Error('expected a line frame');
  assert.equal(frame.record.timestamp, '17:02:10');
  assert.equal(frame.record.level, 'INFO');
  assert.equal(frame.record.message, 'Live account equity updated: $23.47 USDT');
  // Identity is the whole raw line, timestamp included.
  assert.match(frame.record.key, /^2026-09-24 17:02:10,629 - /);
});

test('a repeated log line is not collapsed into its first occurrence', () => {
  // The live log is ~91% repeats (the equity line alone appears 264x), so the old
  // level + message[:80] key showed 376 of 3,985 lines and never tailed.
  const sink = createEngineLogSink();
  const batch = [
    engineLine('17:02:10,629', 'Live account equity updated: $23.47 USDT (Free USDT: $23.47, via ws)'),
    engineLine('17:03:15,299', 'Live account equity updated: $23.47 USDT (Free USDT: $23.47, via ws)'),
    engineLine('17:04:20,004', 'Live account equity updated: $23.46 USDT (Free USDT: $23.47, via rest)'),
  ];
  assert.equal(sink.push(batch).length, 3);
  // …while the same raw lines are still deduped (the poller re-reads them).
  assert.equal(sink.push(batch).length, 0);
});

test('two records sharing an 80-char prefix stay distinct', () => {
  const sink = createEngineLogSink();
  const prefix = 'Exit filled for BTCUSDT: reduceOnly market order executed at price 60.25, final reduction leg at ';
  assert.ok(prefix.length > 80, 'the prefix must exceed the old 80-char key window');
  const frames = sink.push([
    engineLine('17:00:00,001', `${prefix}.11`),
    engineLine('17:00:00,002', `${prefix}.99`),
  ]);
  assert.equal(frames.length, 2);
});

test('the seen-set evicts oldest-first instead of clearing wholesale', () => {
  const sink = createEngineLogSink(8);
  const many = Array.from({ length: 8 }, (_, i) => engineLine(`17:00:0${i},000`, `record ${i}`));
  assert.equal(sink.push(many).length, 8);
  assert.equal(sink.push(many.slice(-2)).length, 0, 'recent lines stay deduped');
  assert.equal(sink.push([engineLine('17:00:09,000', 'record 9')]).length, 1);
  assert.equal(sink.push([many[0]]).length, 1, 'the oldest line was forgotten, not the whole set');
  assert.equal(sink.push([many[7]]).length, 0, 'clear() would have re-admitted the newest line');
});

test('a traceback stays attached to its ERROR line instead of being dropped', () => {
  const sink = createEngineLogSink();
  const error = engineLine(
    '17:05:00,000',
    'Futures REST error 400 [POST /fapi/v1/order]: {"code":-2010}',
    'ERROR'
  );
  const frames = sink.push([
    error,
    'Traceback (most recent call last):\n',
    '  File "src/trade/trade_logic.py", line 42, in _enter\n',
  ]);
  assert.deepEqual(frames.map((f) => f.kind), ['line', 'continuation', 'continuation']);
  const second = frames[1];
  assert.equal(
    second.kind === 'continuation' ? second.text : '',
    'Traceback (most recent call last):'
  );
  // A rotated/truncated log re-sends its tail — the stack must not duplicate.
  assert.equal(
    sink.push([error, 'Traceback (most recent call last):\n']).length,
    0
  );
});

test('levels map to the console vocabulary and junk never becomes a line', () => {
  const warn = parseEngineLogLine(engineLine('17:06:00,000', 'listenKey expired', 'WARNING'));
  assert.equal(warn?.kind === 'line' ? warn.record.level : '', 'WARN');
  const crit = parseEngineLogLine(engineLine('17:06:01,000', 'IP ban (HTTP 418)', 'CRITICAL'));
  assert.equal(crit?.kind === 'line' ? crit.record.level : '', 'ERROR');
  const legacy = parseEngineLogLine('2026-09-24 17:06:02,000 - INFO - no logger name\n');
  assert.equal(legacy?.kind === 'line' ? legacy.record.message : '', 'no logger name');
  assert.equal(parseEngineLogLine('\n'), null);
  assert.equal(parseEngineLogLine('   \n'), null);
  assert.equal(parseEngineLogLine('INFO - not a record\n')?.kind, 'continuation');
});

test('a multi-line record renders as lines in the console', () => {
  const traceback: LogMessage = {
    id: 'log_tb',
    timestamp: '17:05:00',
    level: 'ERROR',
    category: 'SYS',
    message: 'Futures REST error 400\nTraceback (most recent call last):\n  File "x.py", line 1',
  };
  const html = renderToStaticMarkup(
    <DebugConsole logs={[traceback]} onClearLogs={() => {}} signalInterval={10} />
  );
  assert.match(html, /whitespace-pre-wrap/);
  assert.match(html, /Futures REST error 400/);
  assert.match(html, /Traceback \(most recent call last\)/);
  assert.match(html, /File &quot;x\.py&quot;, line 1/);
});
