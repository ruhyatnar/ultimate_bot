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
  serverStats: null,
  wsStreams: null,
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

test('active position shows bracket ladder prices and market close', () => {
  const html = render({ activeTrades: [activeTrade] });
  assert.match(html, /SL 2\.9640/);
  assert.match(html, /TP 3\.0900/);
  assert.match(html, /Market Close/);
  assert.match(html, /Trailing/);
  assert.match(html, /of 3 slots/);
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
