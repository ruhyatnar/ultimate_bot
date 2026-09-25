import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  BotConfig,
  StrategyPreset,
  ActiveTrade,
  ClosedTrade,
  MarketSymbolData,
  LogMessage,
  CandidateSymbol,
  VpsBotStatus,
  VpsControlState,
  PushResult,
  VpsBalanceData,
  SignalState,
  EngineRiskState,
  WsStreams,
  LoopState,
  FuturesState,
  Roadmap,
  FuturesSoak
} from './types';
import { generateEnvString } from './utils/envGenerator';
import { VpsSocket, WsTransport } from './utils/vpsSocket';
import { EngineLogSink, createEngineLogSink } from './utils/engineLog';
import { Header } from './components/Header';
import { VpsConnectionBar } from './components/VpsConnectionBar';
import { LiveDashboard } from './components/LiveDashboard';
import { SignalInspector } from './components/SignalInspector';
import { DebugConsole } from './components/DebugConsole';
import { ConfigTab } from './components/ConfigTab';
import { DeployGuide } from './components/DeployGuide';

// ---------------------------------------------------------------------------
// The bot runs exactly ONE strategy: intraday_rsi (daily-EMA50 regime gate +
// RSI dip trigger, fixed % bracket). These defaults mirror config.py's
// PRESETS['intraday_rsi'] and the deployed .env so the tuner starts in sync.
// ---------------------------------------------------------------------------
const PRESET_MAP: Record<StrategyPreset, Partial<BotConfig>> = {
  intraday_rsi: {
    timeframe: '5m',
    mtfTimeframe: '1d',
    maxHoldTime: 84600,
    strategyMode: 'rsi_dip',
    slPercent: 0.012,
    tpPercent: 0.03,
    rsiPeriod: 7,
    rsiOversold: 40,
    rsiTimeframe: '1h',
    rsiTimeframeMs: 3600000,
    regimeEma: 50,
    regimeSlopeDays: 3,
    // Keep in lockstep with config.py PRESETS.intraday_rsi.MAX_TRADES_PER_DAY
    // (the proven frequency). 0 = unlimited.
    maxTradesPerDay: 2,
    breakevenEnabled: false,
    closeAtUtcDayEnd: true,
    minTpPercent: 0.03,
    // Champion A3: RSI floor 35 + ATR trail (2x, activate +1%)
    entryRsiMin: 35,
    trailingAtrMultiplier: 2,
    trailingStopActivate: 0.01,
    trailingStopCallback: 0.01,
    // Optional gates — all proven OFF (no-ops at these defaults)
    entryMaxExtAtr: 0,
    entryExtEma: 20,
    entryVolMult: 0,
    entryVolLookback: 20,
    entryRequireRsiRise2: false,
    slAtrMultiplier: 0,
    slAtrMaxPercent: 0.024,
    breakevenTrigger: 0.01,
    breakevenOffset: 0.0025
  }
};

const DEFAULT_CONFIG: BotConfig = {
  preset: 'intraday_rsi',
  timeframe: '5m',
  mtfTimeframe: '1d',
  atrPeriod: 14,
  trailingStopActivate: 0.01,
  trailingStopCallback: 0.01,
  maxHoldTime: 84600,
  minTpPercent: 0.03,
  // Keep in lockstep with ultimate-bot/.env (the engine's deployed values) —
  // these defaults seed the tuner before the first engine snapshot arrives and
  // are overwritten by the /api/status config echo immediately after.
  signalInterval: 10,
  strategyMode: 'rsi_dip',
  slPercent: 0.012,
  tpPercent: 0.03,
  rsiPeriod: 7,
  rsiOversold: 40,
  entryRsiMin: 35,
  trailingAtrMultiplier: 2,
  entryMaxExtAtr: 0,
  entryExtEma: 20,
  entryVolMult: 0,
  entryVolLookback: 20,
  entryRequireRsiRise2: false,
  slAtrMultiplier: 0,
  slAtrMaxPercent: 0.024,
  fundingRateMax: 0.0005,
  breakevenTrigger: 0.01,
  breakevenOffset: 0.0025,
  rsiTimeframe: '1h',
  rsiTimeframeMs: 3600000,
  regimeEma: 50,
  regimeSlopeDays: 3,
  maxTradesPerDay: 2,
  breakevenEnabled: false,
  closeAtUtcDayEnd: true,
  // Risk values mirror ultimate-bot/.env exactly (engine is the source of truth).
  maxDailyDrawdown: 0.05,
  maxLossStreak: 3,
  maxWinStreak: 5,
  cooldownLoss: 3600,
  cooldownWin: 1800,
  lossReentryCooldown: 900,
  balanceUsagePercent: 1.0,
  maxSymbolAllocationPercent: 1.0,
  staticSymbols: ['NEARUSDT'],
  dynamicSymbols: false,
  maxSymbols: 5,
  adxThreshold: 25,
  adxPeriod: 14,
  paperTrade: true,
  useTestnet: false,
  market: 'spot' as const,
  futuresOneWayMode: true,
  futuresMarginType: 'ISOLATED' as const,
  futuresLeverage: 1,
  soakStallSeconds: 600,
  tickersRestFallbackSeconds: 5,
  discordWebhookUrl: '',
  logLevel: 'INFO'
};

// Friendly display names for the common pairs; unknown pairs fall back to the
// base asset ticker. Purely cosmetic — no prices are invented here.
const SYMBOL_NAMES: Record<string, string> = {
  BTCUSDT: 'Bitcoin',
  ETHUSDT: 'Ethereum',
  SOLUSDT: 'Solana',
  BNBUSDT: 'BNB',
  NEARUSDT: 'NEAR Protocol',
  AVAXUSDT: 'Avalanche',
  SUIUSDT: 'Sui',
  DOGEUSDT: 'Dogecoin',
  LINKUSDT: 'Chainlink',
  OPUSDT: 'Optimism',
  APTUSDT: 'Aptos',
  ARBUSDT: 'Arbitrum',
  INJUSDT: 'Injective',
  TONUSDT: 'Toncoin',
  DOTUSDT: 'Polkadot',
  LSKUSDT: 'Lisk'
};

const symbolName = (sym: string): string =>
  SYMBOL_NAMES[sym] || sym.replace(/(USDT|BUSD|USDC|FDUSD)$/, '');

/** Explicit placeholder used when the engine has not published a snapshot yet. */
const offlineSignal = (symbol: string): SignalState => ({
  symbol,
  regime: 'NO_DATA',
  rsi: null,
  rsi_prev: null,
  trigger: false,
  signal: 'NEUTRAL',
  reason: 'engine has not published a signal snapshot for this pair yet'
});

/**
 * Guard every config update (all tabs push through here): a cleared number
 * input yields NaN via parseInt/parseFloat, which used to flow into the env
 * generator and produced MAX_TRADES_PER_DAY=NaN — a value that crashed the
 * engine on boot (fixed server-side too, but never emit it in the first
 * place). Non-finite numeric fields fall back to the current value.
 */
const NUMERIC_CONFIG_KEYS: ReadonlyArray<keyof BotConfig> = [
  'atrPeriod', 'trailingStopActivate', 'trailingStopCallback', 'maxHoldTime',
  'minTpPercent', 'signalInterval', 'slPercent', 'tpPercent', 'rsiPeriod',
  'rsiOversold', 'rsiTimeframeMs', 'regimeEma', 'regimeSlopeDays',
  'entryRsiMin', 'trailingAtrMultiplier',
  'entryMaxExtAtr', 'entryExtEma', 'entryVolMult', 'entryVolLookback',
  'slAtrMultiplier', 'slAtrMaxPercent', 'fundingRateMax', 'breakevenTrigger', 'breakevenOffset',
  'maxTradesPerDay', 'maxDailyDrawdown', 'maxLossStreak', 'maxWinStreak',
  'cooldownLoss', 'cooldownWin', 'balanceUsagePercent',
  'lossReentryCooldown',
  'maxSymbolAllocationPercent', 'maxSymbols', 'adxThreshold', 'adxPeriod',
  'futuresLeverage', 'soakStallSeconds', 'tickersRestFallbackSeconds'
];

export const sanitizeConfigUpdate = (next: BotConfig, current: BotConfig): BotConfig => {
  const out = { ...next };
  for (const key of NUMERIC_CONFIG_KEYS) {
    const v = (out as any)[key];
    if (typeof v !== 'number' || !Number.isFinite(v)) {
      (out as any)[key] = (current as any)[key];
    }
  }
  return out;
};

const asServerNum = (v: any): number => {
  const n = parseFloat(v);
  return isNaN(n) ? 0 : n;
};

export default function App() {
  const [config, setConfig] = useState<BotConfig>(() => {
    try {
      const saved = localStorage.getItem('ultimate_bot_config');
      if (saved) return { ...DEFAULT_CONFIG, ...JSON.parse(saved) };
    } catch {
      // localStorage unavailable — fall through to defaults
    }
    return DEFAULT_CONFIG;
  });

  const [activeTab, setActiveTab] = useState<'dashboard' | 'signals' | 'debug' | 'config' | 'deploy'>('dashboard');

  // Equity / daily PnL / streaks / cooldowns are engine-owned. They are ONLY ever
  // populated from the server snapshot, never synthesised in the browser.
  const [equity, setEquity] = useState<number>(0);
  const [dailyRealizedPnl, setDailyRealizedPnl] = useState<number>(0);
  const [vpsRiskAvailable, setVpsRiskAvailable] = useState<boolean>(false);
  const [winStreak, setWinStreak] = useState<number>(0);
  const [lossStreak, setLossStreak] = useState<number>(0);
  const [entriesToday, setEntriesToday] = useState<number>(0);
  // Engine's authoritative risk snapshot (breaker, streaks, per-symbol cooldowns).
  const [engineRisk, setEngineRisk] = useState<EngineRiskState | null>(null);
  const [isLossCooldown, setIsLossCooldown] = useState<boolean>(false);
  const [cooldownEndsAt, setCooldownEndsAt] = useState<number>(0);
  // 'loss' | 'win' — which streak breaker armed the active cooldown
  const [cooldownKind, setCooldownKind] = useState<string>('loss');

  const [vpsEndpoint, setVpsEndpoint] = useState<string>(() => {
    try {
      return localStorage.getItem('ultimate_bot_vps_endpoint') || '';
    } catch {
      return '';
    }
  });
  const [isPollingVps, setIsPollingVps] = useState<boolean>(false);
  // Monitoring transport: 'websocket' = realtime /ws push from status.py,
  // 'polling' = HTTP fallback when the upgrade is blocked or the backend restarts.
  const [wsTransport, setWsTransport] = useState<WsTransport>('connecting');
  const [vpsStatus, setVpsStatus] = useState<VpsBotStatus>({
    connected: false,
    endpoint: vpsEndpoint || (typeof window !== 'undefined' ? window.location.origin : ''),
    engineStatus: 'CHECKING...',
    engineRunning: false
  });
  const [vpsControl, setVpsControl] = useState<VpsControlState>({ paused: false });
  const [vpsPushResult, setVpsPushResult] = useState<PushResult | null>(null);
  const [vpsBalance, setVpsBalance] = useState<VpsBalanceData | null>(null);
  // Engine-published realtime transport health (WS stream lights).
  const [wsStreams, setWsStreams] = useState<WsStreams | null>(null);
  const [loopState, setLoopState] = useState<LoopState | null>(null);
  const [futuresState, setFuturesState] = useState<FuturesState | null>(null);
  const [roadmap, setRoadmap] = useState<Roadmap | null>(null);
  const [soak, setSoak] = useState<FuturesSoak | null>(null);
  const [untrackedPnl, setUntrackedPnl] = useState<number | null>(null);

  const [activeTrades, setActiveTrades] = useState<ActiveTrade[]>([]);
  const [closedTrades, setClosedTrades] = useState<ClosedTrade[]>([]);
  const [logs, setLogs] = useState<LogMessage[]>([]);

  const [symbolsData, setSymbolsData] = useState<MarketSymbolData[]>([]);
  const [candidates, setCandidates] = useState<CandidateSymbol[]>([]);

  const vpsControlRef = useRef<VpsControlState>({ paused: false });
  vpsControlRef.current = vpsControl;
  const vpsStatusRef = useRef<VpsBotStatus>(vpsStatus);
  vpsStatusRef.current = vpsStatus;
  const configRef = useRef<BotConfig>(config);
  configRef.current = config;
  const equityRef = useRef<number>(equity);
  equityRef.current = equity;
  const cooldownUntilRef = useRef<number>(0);
  const cooldownKindRef = useRef<string>('loss');
  const engineRiskRef = useRef<EngineRiskState | null>(null);
  const wsTransportRef = useRef<WsTransport>('connecting');
  wsTransportRef.current = wsTransport;
  const vpsEndpointRef = useRef<string>(vpsEndpoint);
  vpsEndpointRef.current = vpsEndpoint;

  const addLog = useCallback((
    level: LogMessage['level'],
    category: LogMessage['category'],
    message: string,
    symbol?: string,
    /** Engine clock for the line (`HH:MM:SS`); defaults to the browser's now. */
    timestamp?: string
  ) => {
    const newLog: LogMessage = {
      id: `log_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`,
      timestamp: timestamp || new Date().toISOString().substring(11, 19),
      level,
      category,
      message,
      symbol
    };
    setLogs(prev => [...prev.slice(-300), newLog]);
  }, []);

  /** Attach an unstructured line (e.g. a traceback frame) to the last record,
   *  so an ERROR's stack is readable instead of silently dropped. */
  const appendLogText = useCallback((text: string) => {
    setLogs(prev => {
      if (prev.length === 0) return prev;
      const last = prev[prev.length - 1];
      return [...prev.slice(0, -1), { ...last, message: `${last.message}\n${text}` }];
    });
  }, []);

  // One log sink for the whole app: the /ws incremental push and the HTTP
  // fallback both feed it, so a line can never be shown twice (or be shown with
  // the browser's arrival time instead of the engine's own clock).
  const engineLogSinkRef = useRef<EngineLogSink | null>(null);
  if (!engineLogSinkRef.current) engineLogSinkRef.current = createEngineLogSink();
  const ingestEngineLogLines = useCallback(
    (lines: string[]) => {
      const frames = engineLogSinkRef.current?.push(lines) ?? [];
      for (const frame of frames) {
        if (frame.kind === 'line') {
          addLog(frame.record.level, 'SYS', frame.record.message, undefined, frame.record.timestamp);
        } else {
          appendLogText(frame.text);
        }
      }
    },
    [addLog, appendLogText]
  );
  // The socket is built once and kept for the session, so it reaches the
  // current ingester through a ref (same pattern as applyVpsPayloadRef).
  const ingestEngineLogLinesRef = useRef(ingestEngineLogLines);
  ingestEngineLogLinesRef.current = ingestEngineLogLines;

  // Persist config + VPS settings to localStorage
  useEffect(() => {
    try {
      localStorage.setItem('ultimate_bot_config', JSON.stringify(config));
      localStorage.setItem('ultimate_bot_vps_endpoint', vpsEndpoint);
    } catch {
      // storage unavailable (private mode) — non-fatal
    }
  }, [config, vpsEndpoint]);

  // -------------------------------------------------------------------------
  // Realtime WebSocket channel (React frontend + WS API + Python backend).
  // status.py streams /api/status snapshots + engine log lines over
  // ws://<host>/ws; the HTTP poll below is the automatic fallback.
  // -------------------------------------------------------------------------
  const applyVpsPayloadRef = useRef<(json: any, latency?: number) => void>(() => {});
  const lastWsTransportRef = useRef<WsTransport>('connecting');
  const vpsSocketRef = useRef<VpsSocket | null>(null);
  if (typeof window !== 'undefined' && !vpsSocketRef.current) {
    vpsSocketRef.current = new VpsSocket({
      onSnapshot: (payload) => applyVpsPayloadRef.current(payload),
      onLogLines: (lines) => {
        // Same parser + deduper as the HTTP poller (utils/engineLog): WS pushes
        // only new lines, but a rotated/truncated log resends its tail.
        ingestEngineLogLinesRef.current(lines);
      },
      onStatus: (update) => {
        setWsTransport(update.transport);
        if (lastWsTransportRef.current !== update.transport) {
          lastWsTransportRef.current = update.transport;
          if (update.transport === 'websocket') {
            addLog('SUCCESS', 'SYS', `Realtime WebSocket connected (${vpsEndpointRef.current || 'same-origin'}/ws) — live push active, HTTP polling paused.`);
          } else if (update.transport === 'polling') {
            addLog('WARN', 'SYS', `${update.error || 'WebSocket unavailable'} — falling back to HTTP polling (2.5s).`);
          }
        }
        if (update.transport === 'websocket') {
          setVpsStatus(prev => ({
            ...prev,
            connected: true,
            endpoint: vpsEndpointRef.current || prev.endpoint,
            latencyMs: update.latencyMs ?? prev.latencyMs,
            error: undefined
          }));
        } else if (update.transport === 'polling') {
          // The HTTP fallback poll (still running) sets engine state on its next result.
          setVpsStatus(prev => ({ ...prev, connected: false, error: update.error }));
        }
      }
    });
  }

  // UTC-midnight reset of the cooldown banner mirror (the engine owns the real counter)
  useEffect(() => {
    let lastDay = new Date().toISOString().slice(0, 10);
    const tick = () => {
      const key = new Date().toISOString().slice(0, 10);
      if (key !== lastDay) {
        lastDay = key;
        addLog('INFO', 'RISK', 'UTC midnight rollover: engine daily counters reset.');
      }
    };
    const interval = setInterval(tick, 30_000);
    return () => clearInterval(interval);
  }, [addLog]);

  // Track cooldown state for the UI banner (loss OR win streak breaker)
  useEffect(() => {
    const tick = () => {
      const active = Date.now() < cooldownUntilRef.current;
      setIsLossCooldown(active);
      if (active) setCooldownKind(cooldownKindRef.current);
    };
    tick();
    const interval = setInterval(tick, 1000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    addLog('INFO', 'SYS', 'Ultimate Binance Bot monitor connected — reading the engine via status.py (/api/status + /ws).');
  }, [addLog]);

  /**
   * Single status processor. BOTH the WebSocket push path and the HTTP polling
   * fallback funnel every /api/status payload through this exact mapping, so the
   * two transports can never disagree about how a snapshot maps to app state.
   */
  const processVpsStatus = useCallback((json: any, latency?: number) => {
    const targetBase = (vpsEndpoint || (typeof window !== 'undefined' ? window.location.origin : '')).replace(/\/+$/, '');
    try {
      const rawProcess = String(json.process || '');
      const isEngineRunning = rawProcess.includes('RUNNING') || rawProcess.includes('ONLINE');
      const stats = json.data?.stats;

      setVpsStatus({
        connected: true,
        endpoint: targetBase,
        latencyMs: latency ?? vpsStatusRef.current.latencyMs,
        engineStatus: rawProcess || 'RUNNING',
        engineRunning: isEngineRunning,
        lastSyncTime: new Date().toLocaleTimeString(),
        lastPayloadAt: Date.now(),
        serverTime: json.server_time || json.timestamp,
        serverEpochMs: typeof json.server_epoch_ms === 'number' ? json.server_epoch_ms : vpsStatusRef.current.serverEpochMs,
        stats
      });

      if (json.control && typeof json.control.paused === 'boolean') {
        setVpsControl({
          paused: json.control.paused,
          pauseReason: json.control.pause_reason || undefined
        });
      }

      // ---- decision-loop heartbeat (engine-published, every iteration) ----
      const loop = json.loop_state ?? json.data?.loop_state ?? null;
      if (loop && typeof loop === 'object' && !Array.isArray(loop)) {
        setLoopState({
          cycle_ms: Number(loop.cycle_ms) || 0,
          cycle: Number(loop.cycle) || 0,
          interval_s: Number(loop.interval_s) || 0,
          phase: String(loop.phase ?? ''),
          paused_requested: Boolean(loop.paused_requested),
          paused_applied: Boolean(loop.paused_applied),
          active_trades: Number(loop.active_trades) || 0,
          symbols: Number(loop.symbols) || 0,
          age_s: typeof loop.age_s === 'number' ? loop.age_s : null
        });
      } else {
        setLoopState(null);
      }

      // ---- realtime transport health (engine-published WS stream lights) ----
      const ws = json.ws_streams ?? json.data?.ws_streams ?? null;
      if (ws && typeof ws === 'object' && !Array.isArray(ws)) {
        setWsStreams({
          market: ws.market ? { connected: Boolean(ws.market.connected), symbols: Number(ws.market.symbols) || 0 } : undefined,
          all_tickers: ws.all_tickers
            ? { connected: Boolean(ws.all_tickers.connected), last_frame_age_s: ws.all_tickers.last_frame_age_s ?? null }
            : undefined,
          order_api: ws.order_api
            ? { connected: Boolean(ws.order_api.connected), user_stream: Boolean(ws.order_api.user_stream) }
            : undefined,
          engine_age_s: typeof ws.engine_age_s === 'number' ? ws.engine_age_s : null
        });
      } else {
        setWsStreams(null);
      }

      // ---- futures account state (engine-published; null on spot) ----
      const fut = json.futures ?? json.data?.futures ?? null;
      if (fut && typeof fut === 'object' && Array.isArray(fut.positions)) {
        setFuturesState({
          positions: fut.positions.map((p: any) => ({
            symbol: String(p.symbol ?? ''),
            amount: Number(p.amount) || 0,
            entry_price: Number(p.entry_price) || 0,
            unrealized_pnl: p.unrealized_pnl == null ? null : Number(p.unrealized_pnl),
            leverage: p.leverage ?? null,
            margin_type: p.margin_type ?? null,
            liquidation_price: p.liquidation_price == null ? null : Number(p.liquidation_price),
            mark_price: p.mark_price == null ? null : Number(p.mark_price),
            paper: Boolean(p.paper)
          })),
          leverage: Number(fut.leverage) || 1,
          margin_type: String(fut.margin_type ?? 'ISOLATED'),
          one_way: Boolean(fut.one_way),
          updated_ms: fut.updated_ms,
          age_s: typeof fut.age_s === 'number' ? fut.age_s : null
        });
      } else {
        setFuturesState(null);
      }

      // ---- capital roadmap + futures soak (monitor-computed, 10min/1min fresh) ----
      setRoadmap(json.roadmap && typeof json.roadmap === 'object' ? json.roadmap : null);
      setSoak(json.soak && typeof json.soak === 'object' ? json.soak : null);

      // ---- untracked exposure: exchange positions without an open trade ----
      const rawUntracked = json.untracked_pnl ?? json.data?.untracked_pnl;
      setUntrackedPnl(rawUntracked === undefined || rawUntracked === null ? null : Number(rawUntracked) || 0);

      // ---- balance / equity / risk (engine authoritative) ----
      const r = json.data?.risk || {};
      const b = json.balance || json.data?.balance;
      if (b) {
        setVpsBalance({
          totalEquity: asServerNum(b.total_equity ?? b.equity),
          freeQuote: asServerNum(b.free_quote ?? b.total_equity),
          lockedQuote: asServerNum(b.locked_quote),
          quoteAsset: b.quote_asset || 'USDT',
          account: typeof b.account === 'string' ? b.account : undefined,
          isLive: Boolean(b.is_live),
          dailyPnl: b.daily_pnl !== undefined ? asServerNum(b.daily_pnl) : undefined,
          source: typeof b.source === 'string' ? b.source : undefined,
          ageS: b.age_s === undefined || b.age_s === null ? null : asServerNum(b.age_s),
          balances: Array.isArray(b.balances) ? b.balances : []
        });
      }

      const rawEq = b?.total_equity ?? b?.equity ?? r?.total_equity ?? r?.live_equity ?? r?.equity ?? r?.paper_balance;
      const hasBalance = rawEq !== undefined && rawEq !== null && !isNaN(parseFloat(rawEq));
      const hasDaily = (r && r.daily_pnl !== undefined && !isNaN(parseFloat(r.daily_pnl))) ||
                       (b && b.daily_pnl !== undefined && !isNaN(parseFloat(b.daily_pnl)));
      setVpsRiskAvailable(Boolean(hasDaily || hasBalance));
      if (hasBalance) setEquity(parseFloat(rawEq));
      if (hasDaily) {
        setDailyRealizedPnl(r?.daily_pnl !== undefined ? parseFloat(r.daily_pnl) : parseFloat(b.daily_pnl));
      }
      // ---- engine risk snapshot (authoritative breaker/streak/cooldown state) ----
      const er = r.engine_risk;
      if (er && typeof er === 'object' && !Array.isArray(er)) {
        setEngineRisk({
          loss_streak: Number(er.loss_streak) || 0,
          win_streak: Number(er.win_streak) || 0,
          cooldown_until: Number(er.cooldown_until) || 0,
          cooldown_kind: er.cooldown_kind || 'loss',
          active_cooldowns: er.active_cooldowns && typeof er.active_cooldowns === 'object' ? er.active_cooldowns : {},
          drawdown_used: Number(er.drawdown_used) || 0,
          drawdown_breaker: Boolean(er.drawdown_breaker),
          breaker_reason: String(er.breaker_reason || ''),
          daily_pnl: Number(er.daily_pnl) || 0
        });
        setWinStreak(Number(er.win_streak) || 0);
        setLossStreak(Number(er.loss_streak) || 0);
        const cuMs = (Number(er.cooldown_until) || 0) * 1000;
        if (cuMs > Date.now()) {
          cooldownUntilRef.current = cuMs;
          cooldownKindRef.current = er.cooldown_kind === 'win' ? 'win' : 'loss';
          setCooldownKind(cooldownKindRef.current);
          setCooldownEndsAt(cuMs);
        } else if (cooldownUntilRef.current && cuMs === 0) {
          cooldownUntilRef.current = 0;
          setCooldownEndsAt(0);
        }
      } else {
        // Legacy fallback: aggregated streak strings (older engine without engine_risk)
        if (r.win_streak !== undefined) {
          const val = parseInt(r.win_streak || '0', 10);
          if (!isNaN(val)) setWinStreak(val);
        }
        if (r.loss_streak !== undefined) {
          const val = parseInt(r.loss_streak || '0', 10);
          if (!isNaN(val)) setLossStreak(val);
        }
      }
      if (r.entries_today !== undefined) {
        const val = parseInt(r.entries_today || '0', 10);
        if (!isNaN(val)) setEntriesToday(val);
      }
      if (!er && r.cooldown_until !== undefined) {
        const cu = parseInt(r.cooldown_until || '0', 10) * 1000;
        if (!isNaN(cu) && cu > Date.now() && cu > cooldownUntilRef.current) {
          cooldownUntilRef.current = cu;
          setCooldownEndsAt(cu);
        }
      }

      // ---- config drift (engine .env is the source of truth) ----
      if (json.config && Object.keys(json.config).length > 0) {
        const c = json.config;
        const staticList = c.STATIC_SYMBOLS
          ? c.STATIC_SYMBOLS.split(',').map((s: string) => s.trim().toUpperCase()).filter(Boolean)
          : null;
        const dynamicSym = c.DYNAMIC_SYMBOLS !== undefined
          ? String(c.DYNAMIC_SYMBOLS).toLowerCase() === 'true'
          : null;
        // Sanitize the server merge too: the monitor itself is protected from
        // NaN (defense in depth alongside config.py's _env_int/_env_float).
        setConfig(prev => sanitizeConfigUpdate({
          ...prev,
          paperTrade: c.PAPER_TRADE !== undefined ? String(c.PAPER_TRADE).toLowerCase() === 'true' : prev.paperTrade,
          useTestnet: c.USE_TESTNET !== undefined ? String(c.USE_TESTNET).toLowerCase() === 'true' : prev.useTestnet,
          market: c.MARKET === 'futures' ? 'futures' : c.MARKET === 'spot' ? 'spot' : prev.market,
          futuresOneWayMode: c.FUTURES_ONE_WAY_MODE !== undefined ? String(c.FUTURES_ONE_WAY_MODE).toLowerCase() === 'true' : prev.futuresOneWayMode,
          futuresMarginType: c.FUTURES_MARGIN_TYPE === 'CROSSED' ? 'CROSSED' : c.FUTURES_MARGIN_TYPE === 'ISOLATED' ? 'ISOLATED' : prev.futuresMarginType,
          futuresLeverage: Number.isFinite(parseInt(c.FUTURES_LEVERAGE, 10)) ? parseInt(c.FUTURES_LEVERAGE, 10) : prev.futuresLeverage,
          soakStallSeconds: Number.isFinite(parseInt(c.SOAK_STALL_S, 10)) ? parseInt(c.SOAK_STALL_S, 10) : prev.soakStallSeconds,
          tickersRestFallbackSeconds: Number.isFinite(parseFloat(c.TICKERS_REST_FALLBACK_S)) ? parseFloat(c.TICKERS_REST_FALLBACK_S) : prev.tickersRestFallbackSeconds,
          preset: (c.PRESET as StrategyPreset) || prev.preset,
          strategyMode: 'rsi_dip',
          timeframe: c.TIMEFRAME || prev.timeframe,
          mtfTimeframe: c.MTF_TIMEFRAME || prev.mtfTimeframe,
          atrPeriod: c.ATR_PERIOD ? parseInt(c.ATR_PERIOD, 10) : prev.atrPeriod,
          slPercent: c.SL_PERCENT ? parseFloat(c.SL_PERCENT) : prev.slPercent,
          tpPercent: c.TP_PERCENT ? parseFloat(c.TP_PERCENT) : prev.tpPercent,
          rsiPeriod: c.RSI_PERIOD ? parseInt(c.RSI_PERIOD, 10) : prev.rsiPeriod,
          rsiOversold: c.RSI_OVERSOLD ? parseFloat(c.RSI_OVERSOLD) : prev.rsiOversold,
          rsiTimeframe: c.RSI_TIMEFRAME || prev.rsiTimeframe,
          rsiTimeframeMs: c.RSI_TIMEFRAME_MS ? parseInt(c.RSI_TIMEFRAME_MS, 10) : prev.rsiTimeframeMs,
          regimeEma: c.REGIME_EMA ? parseInt(c.REGIME_EMA, 10) : prev.regimeEma,
          regimeSlopeDays: c.REGIME_SLOPE_DAYS ? parseInt(c.REGIME_SLOPE_DAYS, 10) : prev.regimeSlopeDays,
          // Never let a malformed server value become NaN: fall back to the
          // previous known-good number (NaN would render as "NaN" and poison
          // comparisons like maxTradesPerDay > 0).
          maxTradesPerDay: Number.isFinite(parseInt(c.MAX_TRADES_PER_DAY, 10)) ? parseInt(c.MAX_TRADES_PER_DAY, 10) : prev.maxTradesPerDay,
          breakevenEnabled: c.BREAKEVEN_ENABLED !== undefined ? String(c.BREAKEVEN_ENABLED).toLowerCase() === 'true' : prev.breakevenEnabled,
          closeAtUtcDayEnd: c.CLOSE_AT_UTC_DAY_END !== undefined ? String(c.CLOSE_AT_UTC_DAY_END).toLowerCase() === 'true' : prev.closeAtUtcDayEnd,
          minTpPercent: c.MIN_TP_PERCENT ? parseFloat(c.MIN_TP_PERCENT) : prev.minTpPercent,
          signalInterval: c.SIGNAL_INTERVAL ? parseInt(c.SIGNAL_INTERVAL, 10) : prev.signalInterval,
          trailingStopActivate: c.TRAILING_STOP_ACTIVATE ? parseFloat(c.TRAILING_STOP_ACTIVATE) : prev.trailingStopActivate,
          trailingStopCallback: c.TRAILING_STOP_CALLBACK ? parseFloat(c.TRAILING_STOP_CALLBACK) : prev.trailingStopCallback,
          entryRsiMin: c.ENTRY_RSI_MIN !== undefined && c.ENTRY_RSI_MIN !== '' ? parseFloat(c.ENTRY_RSI_MIN) : prev.entryRsiMin,
          trailingAtrMultiplier: c.TRAILING_ATR_MULTIPLIER !== undefined && c.TRAILING_ATR_MULTIPLIER !== '' ? parseFloat(c.TRAILING_ATR_MULTIPLIER) : prev.trailingAtrMultiplier,
          entryMaxExtAtr: c.ENTRY_MAX_EXT_ATR !== undefined && c.ENTRY_MAX_EXT_ATR !== '' ? parseFloat(c.ENTRY_MAX_EXT_ATR) : prev.entryMaxExtAtr,
          entryExtEma: c.ENTRY_EXT_EMA ? parseInt(c.ENTRY_EXT_EMA, 10) : prev.entryExtEma,
          entryVolMult: c.ENTRY_VOL_MULT !== undefined && c.ENTRY_VOL_MULT !== '' ? parseFloat(c.ENTRY_VOL_MULT) : prev.entryVolMult,
          entryVolLookback: c.ENTRY_VOL_LOOKBACK ? parseInt(c.ENTRY_VOL_LOOKBACK, 10) : prev.entryVolLookback,
          entryRequireRsiRise2: c.ENTRY_REQUIRE_RSI_RISE2 !== undefined ? String(c.ENTRY_REQUIRE_RSI_RISE2).toLowerCase() === 'true' : prev.entryRequireRsiRise2,
          slAtrMultiplier: c.SL_ATR_MULTIPLIER !== undefined && c.SL_ATR_MULTIPLIER !== '' ? parseFloat(c.SL_ATR_MULTIPLIER) : prev.slAtrMultiplier,
          fundingRateMax: c.FUNDING_RATE_MAX !== undefined && c.FUNDING_RATE_MAX !== '' ? parseFloat(c.FUNDING_RATE_MAX) : prev.fundingRateMax,
          slAtrMaxPercent: c.SL_ATR_MAX_PERCENT !== undefined && c.SL_ATR_MAX_PERCENT !== '' ? parseFloat(c.SL_ATR_MAX_PERCENT) : prev.slAtrMaxPercent,
          breakevenTrigger: c.BREAKEVEN_TRIGGER !== undefined && c.BREAKEVEN_TRIGGER !== '' ? parseFloat(c.BREAKEVEN_TRIGGER) : prev.breakevenTrigger,
          breakevenOffset: c.BREAKEVEN_OFFSET !== undefined && c.BREAKEVEN_OFFSET !== '' ? parseFloat(c.BREAKEVEN_OFFSET) : prev.breakevenOffset,
          maxHoldTime: c.MAX_HOLD_TIME ? parseInt(c.MAX_HOLD_TIME, 10) : prev.maxHoldTime,
          maxDailyDrawdown: c.MAX_DAILY_DRAWDOWN ? parseFloat(c.MAX_DAILY_DRAWDOWN) : prev.maxDailyDrawdown,
          maxLossStreak: c.MAX_LOSS_STREAK ? parseInt(c.MAX_LOSS_STREAK, 10) : prev.maxLossStreak,
          maxWinStreak: c.MAX_WIN_STREAK ? parseInt(c.MAX_WIN_STREAK, 10) : prev.maxWinStreak,
          cooldownLoss: c.COOLDOWN_LOSS ? parseInt(c.COOLDOWN_LOSS, 10) : prev.cooldownLoss,
          lossReentryCooldown: c.LOSS_REENTRY_COOLDOWN !== undefined && c.LOSS_REENTRY_COOLDOWN !== ''
            ? parseInt(c.LOSS_REENTRY_COOLDOWN, 10) || 900 : 900,
          cooldownWin: c.COOLDOWN_WIN ? parseInt(c.COOLDOWN_WIN, 10) : prev.cooldownWin,
          balanceUsagePercent: c.BALANCE_USAGE_PERCENT ? parseFloat(c.BALANCE_USAGE_PERCENT) : prev.balanceUsagePercent,
          maxSymbolAllocationPercent: c.MAX_SYMBOL_ALLOCATION_PERCENT ? parseFloat(c.MAX_SYMBOL_ALLOCATION_PERCENT) : prev.maxSymbolAllocationPercent,
          maxSymbols: c.MAX_SYMBOLS ? parseInt(c.MAX_SYMBOLS, 10) : prev.maxSymbols,
          staticSymbols: staticList ?? prev.staticSymbols,
          dynamicSymbols: dynamicSym !== null ? dynamicSym : prev.dynamicSymbols,
          adxThreshold: c.ADX_THRESHOLD ? parseFloat(c.ADX_THRESHOLD) : prev.adxThreshold,
          adxPeriod: c.ADX_PERIOD ? parseInt(c.ADX_PERIOD, 10) : prev.adxPeriod,
          logLevel: (c.LOG_LEVEL as BotConfig['logLevel']) || prev.logLevel
        }, prev));
      }

      // ---- active positions (SQLite is the engine's own persisted state) ----
      const tradeRows = json.data?.trades;
      if (Array.isArray(tradeRows)) {
        const mappedActive: ActiveTrade[] = tradeRows.map((t: any) => {
          const entryPrice = asServerNum(t.entry_price);
          const qty = asServerNum(t.quantity);
          // Prefer the engine's mark (current_price) when present; otherwise mark
          // the position at entry rather than inventing a price.
          const curPrice = asServerNum(t.current_price) || entryPrice;
          const unPnl = (curPrice - entryPrice) * qty;
          const unPnlPct = entryPrice > 0 ? ((curPrice - entryPrice) / entryPrice) * 100 : 0;
          return {
            id: `db_${t.symbol}_${t.order_id || t.entry_time || Date.now()}`,
            symbol: t.symbol,
            side: (t.side || 'BUY') as 'BUY' | 'SELL',
            entryPrice,
            currentPrice: curPrice,
            quantity: qty,
            notional: entryPrice * qty,
            entryTime: t.entry_time ? (t.entry_time < 1e12 ? t.entry_time * 1000 : t.entry_time) : Date.now(),
            stopPrice: asServerNum(t.stop_price),
            takeProfit: asServerNum(t.take_profit),
            atr: asServerNum(t.atr),
            trailingActive: Boolean(t.trailing_active),
            trailingStop: asServerNum(t.trailing_stop) || asServerNum(t.stop_price),
            breakevenActivated: Boolean(t.breakeven_activated),
            unrealizedPnl: unPnl,
            unrealizedPnlPct: unPnlPct,
            // Provenance of the mark above: stamped by status.py from the engine's
            // own published position state, never derived here.
            positionSource: (t.position_source as ActiveTrade['positionSource']) ?? undefined,
            liveQty: t.live_qty === undefined || t.live_qty === null ? undefined : asServerNum(t.live_qty),
            positionAgeS: t.position_age_s === undefined || t.position_age_s === null ? null : asServerNum(t.position_age_s),
            positionStale: Boolean(t.position_stale)
          };
        });
        setActiveTrades(mappedActive);
      }

      // ---- closed trades: every EXIT the engine recorded ----
      // This must use the SAME exit predicate as status.py's stats, or the table
      // and the win-rate card describe different sets. An exit is identified by
      // the reason the engine wrote on the order (trade_policy -> close_trade),
      // or — for rows predating that column — by a non-zero realized PnL.
      // Keying on `side === 'SELL'` was wrong twice over: a futures short closes
      // with a BUY (invisible here), and it counted a FILLED leg with no PnL as a
      // closed trade while the engine's own stats did not.
      const orderRows = json.data?.orders;
      if (Array.isArray(orderRows)) {
        const isExitOrder = (o: any): boolean => {
          if (typeof o.exit_reason === 'string' && o.exit_reason.length > 0) return true;
          const pnl = o.profit_loss;
          if (pnl === null || pnl === undefined || pnl === '') return false;
          return parseFloat(pnl) !== 0;
        };
        const exitOrders = orderRows.filter(isExitOrder);
        const mappedOrders: ClosedTrade[] = exitOrders.map((o: any) => {
          // Entry price: the engine attaches the matched BUY leg's avg_fill_price
          // as `entry_price` (market SELL rows store price=0, and `??` would not
          // fall through on that 0 — this once rendered "$0.00 → $0.5382").
          const entryP = asServerNum(o.entry_price) || asServerNum(o.avg_fill_price) || asServerNum(o.price);
          const exitP = asServerNum(o.avg_fill_price ?? o.price);
          const q = asServerNum(o.executed_qty ?? o.quantity);
          const pnl = asServerNum(o.profit_loss);
          const notional = (entryP > 0 ? entryP : exitP) * q;
          const pnlPct = notional > 0 ? (pnl / notional) * 100 : 0;
          const parseTs = (val: any, fallback: number): number => {
            if (!val) return fallback;
            if (typeof val === 'number') return val < 1e11 ? val * 1000 : val;
            const num = Number(val);
            if (!isNaN(num)) return num < 1e11 ? num * 1000 : num;
            const d = new Date(val).getTime();
            return isNaN(d) ? fallback : d;
          };
          // Prefer the engine-matched BUY entry (entry_ts) — the SELL row's own
          // created_at is the EXIT moment, not the position entry.
          const entryTs = parseTs(o.entry_ts, NaN);
          const entryEstimated = !isFinite(entryTs);
          const exitTs = parseTs(o.updated_at ?? o.created_at, Date.now());
          const status = String(o.status || '').toUpperCase();
          return {
            id: `order_${o.order_id}`,
            symbol: o.symbol,
            side: (o.side || 'SELL') as 'BUY' | 'SELL',
            entryPrice: entryP,
            exitPrice: exitP,
            quantity: q,
            pnl,
            pnlPct,
            entryTime: isFinite(entryTs) ? entryTs : exitTs,
            entryTimeEstimated: entryEstimated,
            exitTime: exitTs,
            // The engine's OWN reason when it recorded one; otherwise the label
            // status.py inferred from the PnL sign, flagged as inferred so the
            // table can mark a guess instead of colouring it as a win.
            exitReason: (o.exit_reason || (status === 'CANCELED' ? 'PARTIAL_EXIT' : 'MARKET_EXIT')) as ClosedTrade['exitReason'],
            exitReasonInferred: Boolean(o.exit_reason_inferred)
          };
        });
        setClosedTrades(mappedOrders);
      }

      // ---- engine-scanned screener pool ----
      const rawCandidates = json.candidates || json.scanned_pairs || json.data?.scanned_pairs;
      const priceBySym: Record<string, number> = {};
      const changeBySym: Record<string, number> = {};
      if (Array.isArray(rawCandidates) && rawCandidates.length > 0) {
        const mappedCandidates: CandidateSymbol[] = rawCandidates.map((c: any, idx: number) => {
          const sym = String(c.symbol || '').toUpperCase();
          const price = asServerNum(c.price ?? c.last_price);
          const change = asServerNum(c.price_change_24h ?? c.priceChangePercent ?? c.raw_price_change);
          if (sym) {
            priceBySym[sym] = price;
            changeBySym[sym] = change;
          }
          return {
            symbol: sym,
            name: symbolName(sym),
            price,
            priceChange24h: change,
            volume24h: asServerNum(c.volume_24h ?? c.volume),
            volatility: asServerNum(c.volatility),
            adx: asServerNum(c.adx),
            zScore: asServerNum(c.z_score ?? c.final_score),
            isSelected: Boolean(c.is_selected),
            momentumRank: c.momentum_rank || (idx + 1)
          };
        });
        setCandidates(mappedCandidates);
      }

      // ---- the engine's REAL per-symbol signal state ----
      const rawSignals: any[] = Array.isArray(json.signal_state)
        ? json.signal_state
        : (Array.isArray(json.data?.signal_state) ? json.data.signal_state : []);
      const signalBySym: Record<string, SignalState> = {};
      rawSignals.forEach((s: any) => {
        if (!s || !s.symbol) return;
        const sym = String(s.symbol).toUpperCase();
        signalBySym[sym] = {
          symbol: sym,
          time: s.time,
          regime: (s.regime || 'NO_DATA') as SignalState['regime'],
          regime_ema: s.regime_ema,
          regime_price: s.regime_price,
          regime_ema_value: s.regime_ema_value,
          rsi: s.rsi === null || s.rsi === undefined ? null : Number(s.rsi),
          rsi_prev: s.rsi_prev === null || s.rsi_prev === undefined ? null : Number(s.rsi_prev),
          oversold: s.oversold,
          rsi_period: s.rsi_period,
          rsi_timeframe: s.rsi_timeframe,
          trigger: Boolean(s.trigger),
          signal: s.signal === 'BUY' ? 'BUY' : 'NEUTRAL',
          atr: s.atr,
          reason: s.reason || ''
        };
      });

      // ---- monitored pairs: exactly what the engine is watching ----
      const rawMonitored = json.monitored_symbols || json.data?.monitored_symbols;
      const monitored: string[] = Array.isArray(rawMonitored) && rawMonitored.length > 0
        ? rawMonitored.map((s: any) => String(s).toUpperCase())
        : (Object.keys(signalBySym).length > 0
            ? Object.keys(signalBySym)
            : (json.config?.STATIC_SYMBOLS
                ? String(json.config.STATIC_SYMBOLS).split(',').map((s: string) => s.trim().toUpperCase()).filter(Boolean)
                : []));
      const entryPriceBySym: Record<string, number> = {};
      const openSymbols: string[] = [];
      (Array.isArray(tradeRows) ? tradeRows : []).forEach((t: any) => {
        if (!t?.symbol) return;
        const sym = String(t.symbol).toUpperCase();
        entryPriceBySym[sym] = asServerNum(t.entry_price);
        openSymbols.push(sym);
      });
      const finalList = Array.from(new Set([...monitored, ...openSymbols]));

      const builtSymbols: MarketSymbolData[] = finalList.map(sym => {
        const signal = signalBySym[sym] || offlineSignal(sym);
        const price = priceBySym[sym] || entryPriceBySym[sym] || signal.regime_price || 0;
        const change = changeBySym[sym] ?? 0;
        // Per-symbol cooldown from the engine snapshot when available; the legacy
        // global fallback keeps behaviour identical for older engines.
        const symCd = engineRiskRef.current?.active_cooldowns?.[sym];
        const cdUntil = symCd ? symCd.until * 1000 : cooldownUntilRef.current;
        return {
          symbol: sym,
          name: symbolName(sym),
          price,
          priceChange24h: change,
          high24h: price,
          low24h: price,
          volume24h: 0,
          signal,
          sparkline: [price, price],
          inCooldown: Date.now() < cdUntil,
          cooldownEndsAt: cdUntil > Date.now() ? cdUntil : undefined
        };
      });
      setSymbolsData(builtSymbols);
    } catch (err: any) {
      // Processing errors must never kill the push/poll loop itself.
      console.error('Status processing failed:', err);
    }
  }, [vpsEndpoint]);

  const fetchVpsData = useCallback(async () => {
    // Over WebSocket the snapshot already arrives via push — only flag the
    // spinner when this call performs an actual HTTP round-trip.
    if (wsTransportRef.current === 'websocket') {
      applyVpsPayloadRef.current = processVpsStatus;
    } else {
      setIsPollingVps(true);
    }
    const targetBase = (vpsEndpoint || (typeof window !== 'undefined' ? window.location.origin : '')).replace(/\/+$/, '');
    const url = targetBase.endsWith('/api/status') ? targetBase : `${targetBase}/api/status`;
    const startTime = performance.now();
    try {
      const res = await fetch(url, { mode: 'cors' });
      const latency = wsTransportRef.current === 'websocket'
        ? (vpsStatusRef.current.latencyMs ?? Math.round(performance.now() - startTime))
        : Math.round(performance.now() - startTime);
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
      processVpsStatus(await res.json(), latency);
    } catch (err: any) {
      setVpsRiskAvailable(false);
      // Only mark disconnected if the realtime channel is down too — with
      // age-adaptive polling, a transient poll error must not override a
      // healthy WebSocket feed.
      setVpsStatus(prev => ({
        ...prev,
        connected: wsTransportRef.current === 'websocket' ? true : false,
        error: err.message,
        engineStatus: wsTransportRef.current === 'websocket' ? prev.engineStatus : 'DISCONNECTED',
        engineRunning: wsTransportRef.current === 'websocket' ? prev.engineRunning : false
      }));
    } finally {
      if (wsTransportRef.current !== 'websocket') setIsPollingVps(false);
    }
  }, [vpsEndpoint, processVpsStatus]);

  // Adaptive freshness loop — keeps the Sync Clock LIVE:
  //  - healthy WS pushes (1s) keep data age ≤1s → no polling needed;
  //  - whenever age exceeds 3s (push stall, half-open socket), HTTP polling
  //    at 2.5s covers the gap until fresh WS payloads land again.
  useEffect(() => {
    const tick = () => {
      const lp = vpsStatusRef.current.lastPayloadAt;
      const age = lp ? Date.now() - lp : Infinity;
      if (age > 3000) fetchVpsData();
    };
    fetchVpsData();
    const interval = setInterval(tick, 2500);
    return () => clearInterval(interval);
  }, [fetchVpsData]);

  // WS stall watchdog: a half-open socket (no TCP FIN — sleep/wake, NAT drop)
  // leaves transport 'websocket' with no frames flowing. If no payload has
  // arrived for 10s, force a reconnect so pushes resume immediately.
  useEffect(() => {
    const interval = setInterval(() => {
      if (wsTransportRef.current !== 'websocket') return;
      const lp = vpsStatusRef.current.lastPayloadAt;
      if (lp && Date.now() - lp > 10_000) {
        const sock = vpsSocketRef.current;
        if (sock) {
          sock.disconnect();
          sock.connect(vpsEndpoint || (typeof window !== 'undefined' ? window.location.origin : ''));
        }
      }
    }, 5000);
    return () => clearInterval(interval);
  }, [vpsEndpoint]);

  // WebSocket lifecycle
  useEffect(() => {
    const sock = vpsSocketRef.current;
    if (!sock) return;
    applyVpsPayloadRef.current = processVpsStatus;
    sock.connect(vpsEndpoint || (typeof window !== 'undefined' ? window.location.origin : ''));
    return () => {
      // keep the socket across renders
    };
  }, [vpsEndpoint, processVpsStatus]);

  // Send a remote control command to the live engine via status.py (POST /api/control)
  const sendVpsControl = useCallback(async (action: 'pause' | 'resume' | 'close_all' | 'close_symbol', symbol?: string): Promise<boolean> => {
    const targetBase = (vpsEndpoint || (typeof window !== 'undefined' ? window.location.origin : '')).replace(/\/+$/, '');
    try {
      const res = await fetch(`${targetBase}/api/control`, {
        method: 'POST',
        mode: 'cors',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, symbol })
      });
      const json = await res.json();
      if (!res.ok || !json.ok) throw new Error(json.message || `HTTP ${res.status}`);
      if (json.control && typeof json.control.paused === 'boolean') {
        setVpsControl({
          paused: json.control.paused,
          pauseReason: json.control.pause_reason || undefined
        });
      }
      return true;
    } catch (err: any) {
      addLog('ERROR', 'RISK', `Remote control failed (${action}): ${err.message}`);
      return false;
    }
  }, [vpsEndpoint, addLog]);

  const handleToggleVpsPause = useCallback(async () => {
    const willPause = !vpsControlRef.current.paused;
    const ok = await sendVpsControl(willPause ? 'pause' : 'resume');
    if (ok) {
      addLog('WARN', 'RISK', `Remote engine ${willPause ? 'PAUSED' : 'RESUMED'} from web monitor. ${willPause ? 'New entries blocked — open positions still managed.' : ''}`);
    }
  }, [sendVpsControl, addLog]);

  // Start/stop the 24h futures paper soak (POST /api/soak → futures_soak.sh).
  // Server-side guards refuse duplicate starts / stops of nothing (409).
  const handleSoakControl = useCallback(async (action: 'start' | 'stop'): Promise<boolean> => {
    const targetBase = (vpsEndpoint || (typeof window !== 'undefined' ? window.location.origin : '')).replace(/\/+$/, '');
    try {
      const res = await fetch(`${targetBase}/api/soak`, {
        method: 'POST',
        mode: 'cors',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action })
      });
      const json = await res.json();
      if (!res.ok || !json.ok) throw new Error(json.message || `HTTP ${res.status}`);
      addLog('WARN', 'SYS', `Futures soak ${action}ed from web monitor.`);
      setTimeout(() => fetchVpsData(), 3000);
      return true;
    } catch (err: any) {
      addLog('ERROR', 'SYS', `Soak control failed (${action}): ${err.message}`);
      return false;
    }
  }, [vpsEndpoint, addLog, fetchVpsData]);

  // Push the tuned web configuration to the live engine (POST /api/config)
  const pushConfigToVps = useCallback(async (): Promise<PushResult> => {
    const targetBase = (vpsEndpoint || (typeof window !== 'undefined' ? window.location.origin : '')).replace(/\/+$/, '');
    try {
      const res = await fetch(`${targetBase}/api/config`, {
        method: 'POST',
        mode: 'cors',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ env_file: generateEnvString(configRef.current) })
      });
      const json = await res.json();
      if (!res.ok || !json.ok) throw new Error(json.message || `HTTP ${res.status}`);
      const reloadNote = json.reload?.exit_code === 0
        ? 'Engine reloaded via PM2 — changes live now.'
        : (json.reload?.hint || (json.reload?.error ? `Reload note: ${json.reload.error}` : ''));
      const message = `Applied ${json.count} tunable keys to the engine .env. ${reloadNote}`;
      setVpsPushResult({ ok: true, message });
      addLog('SUCCESS', 'SYS', `Engine config pushed: ${(json.applied || []).join(', ')}`);
      vpsSocketRef.current?.refresh();
      setTimeout(() => fetchVpsData(), 2500);
      return { ok: true, message };
    } catch (err: any) {
      const message = `Push failed: ${err.message}`;
      setVpsPushResult({ ok: false, message });
      addLog('ERROR', 'SYS', message);
      return { ok: false, message };
    }
  }, [vpsEndpoint, addLog, fetchVpsData]);

  // Stream the real engine log into the Debug Console (HTTP fallback; the /ws
  // push above is the primary transport and shares this ingester).
  useEffect(() => {
    if (!vpsStatus.connected) return;
    const targetBase = (vpsEndpoint || (typeof window !== 'undefined' ? window.location.origin : '')).replace(/\/+$/, '');
    let cancelled = false;
    const fetchEngineLogs = async () => {
      if (cancelled) return;
      try {
        const res = await fetch(`${targetBase}/api/logs?lines=120`, { mode: 'cors' });
        if (!res.ok) return;
        const json = await res.json();
        ingestEngineLogLines(Array.isArray(json.lines) ? json.lines : []);
      } catch {
        // Engine log endpoint unreachable — the realtime channel will retry.
      }
    };
    fetchEngineLogs();
    const interval = setInterval(fetchEngineLogs, 5000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [vpsStatus.connected, vpsEndpoint, ingestEngineLogLines]);

  // Apply the (single) preset
  const handleApplyPreset = useCallback((preset: StrategyPreset) => {
    const presetOverrides = PRESET_MAP[preset] || PRESET_MAP.intraday_rsi;
    setConfig(prev => sanitizeConfigUpdate({ ...prev, preset, ...presetOverrides }, prev));
    addLog('INFO', 'SYS', `Applied ${preset.toUpperCase()} strategy profile. Timeframe: ${presetOverrides.timeframe}, Regime TF: ${presetOverrides.mtfTimeframe}`);
  }, [addLog]);

  // Close a live position through the engine's remote control channel
  const handleCloseTrade = useCallback((symbol: string) => {
    sendVpsControl('close_symbol', symbol).then(ok => {
      if (ok) addLog('WARN', 'ORDER', `Market close requested for ${symbol} on the engine.`, symbol);
    });
  }, [sendVpsControl, addLog]);

  // Emergency close-all — liquidates every open engine position
  const handleCloseAllTrades = useCallback(async () => {
    const ok = await sendVpsControl('close_all');
    if (ok) addLog('WARN', 'RISK', 'EMERGENCY LIQUIDATION requested on the engine — closing all open positions.');
  }, [sendVpsControl, addLog]);

  const totalUnrealizedPnl = useMemo(
    () => activeTrades.reduce((acc, t) => acc + t.unrealizedPnl, 0),
    [activeTrades]
  );

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col selection:bg-amber-500/30 selection:text-amber-200">
      <Header
        isRunning={vpsStatus.engineRunning}
        onToggleRunning={handleToggleVpsPause}
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        config={config}
        activeTradesCount={activeTrades.length}
        unrealizedPnl={totalUnrealizedPnl}
        totalEquity={equity + totalUnrealizedPnl}
        vpsConnected={vpsStatus.connected}
        isLossCooldown={isLossCooldown}
      />

      <VpsConnectionBar
        vpsStatus={vpsStatus}
        vpsEndpoint={vpsEndpoint}
        onUpdateVpsEndpoint={setVpsEndpoint}
        onRefreshVps={fetchVpsData}
        isPolling={isPollingVps}
        controlPaused={vpsControl.paused}
        onToggleVpsPause={handleToggleVpsPause}
        wsTransport={wsTransport}
      />

      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {activeTab === 'dashboard' && (
          <LiveDashboard
            equity={equity + totalUnrealizedPnl}
            dailyRealizedPnl={dailyRealizedPnl}
            unrealizedPnl={totalUnrealizedPnl}
            activeTrades={activeTrades}
            closedTrades={closedTrades}
            symbolsData={symbolsData}
            candidates={candidates}
            config={config}
            onUpdateConfig={(next) => setConfig(sanitizeConfigUpdate(next, config))}
            onApplyPreset={handleApplyPreset}
            onCloseTrade={handleCloseTrade}
            onCloseAllTrades={handleCloseAllTrades}
            winStreak={winStreak}
            lossStreak={lossStreak}
            entriesToday={entriesToday}
            vpsRiskAvailable={vpsRiskAvailable}
            isLossCooldown={isLossCooldown}
            cooldownEndsAt={cooldownEndsAt}
            cooldownKind={cooldownKind}
            engineRisk={engineRisk}
            engineRunning={vpsStatus.engineRunning}
            vpsConnected={vpsStatus.connected}
            vpsBalance={vpsBalance}
            onPushConfigToVps={pushConfigToVps}
            vpsPushResult={vpsPushResult}
            controlPaused={vpsControl.paused}
            onToggleVpsPause={handleToggleVpsPause}
            serverStats={vpsStatus.stats || null}
            wsStreams={wsStreams}
            loopState={loopState}
            futuresState={futuresState}
            untrackedPnl={untrackedPnl}
            roadmap={roadmap}
            soak={soak}
            onSoakControl={handleSoakControl}
          />
        )}

        {activeTab === 'signals' && (
          <SignalInspector symbolsData={symbolsData} config={config} />
        )}

        {activeTab === 'debug' && (
          <DebugConsole
            logs={logs}
            onClearLogs={() => setLogs([])}
            signalInterval={config.signalInterval}
          />
        )}

        {activeTab === 'config' && (
          <ConfigTab
            config={config}
            onUpdateConfig={(next) => setConfig(sanitizeConfigUpdate(next, config))}
            onApplyPreset={handleApplyPreset}
            vpsConnected={vpsStatus.connected}
            onPushToVps={pushConfigToVps}
          />
        )}

        {activeTab === 'deploy' && <DeployGuide />}
      </main>
    </div>
  );
}
