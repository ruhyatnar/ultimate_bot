import React, { useState } from 'react';
import {
  Zap,
  Clock,
  TrendingUp,
  TrendingDown,
  Sparkles,
  ArrowUpRight,
  ArrowDownRight,
  Crosshair,
  History,
  Inbox,
  Wallet,
  Layers,
  Radar,
  ShieldAlert,
  PauseCircle
} from 'lucide-react';
import { BotConfig, ActiveTrade, ClosedTrade, MarketSymbolData, CandidateSymbol, EngineRiskState, WsStreams, FuturesState, Roadmap, FuturesSoak, VpsBalanceData, PushResult } from '../types';
import { TuningControlBar } from './TuningControlBar';
import { DynamicScreener } from './DynamicScreener';
import { SymbolDetailModal } from './SymbolDetailModal';
import { VpsSyncModal } from './VpsSyncModal';
import { FuturesPanel, FuturesSoakCard, RoadmapCard } from './StatusCards';
import { EngineHealthCard } from './EngineHealthCard';
import { Section, Chip } from './Section';

interface LiveDashboardProps {
  equity: number;
  dailyRealizedPnl: number;
  unrealizedPnl: number;
  activeTrades: ActiveTrade[];
  closedTrades: ClosedTrade[];
  symbolsData: MarketSymbolData[];
  candidates: CandidateSymbol[];
  config: BotConfig;
  onUpdateConfig: (config: BotConfig) => void;
  onApplyPreset: (preset: BotConfig['preset']) => void;
  onCloseTrade: (symbol: string, reason: string) => void;
  onCloseAllTrades: () => void;
  winStreak: number;
  lossStreak: number;
  entriesToday: number;
  vpsRiskAvailable: boolean;
  isLossCooldown: boolean;
  cooldownEndsAt: number;
  cooldownKind: string;
  engineRisk: EngineRiskState | null;
  vpsConnected: boolean;
  vpsBalance: VpsBalanceData | null;
  onPushConfigToVps: () => Promise<PushResult>;
  vpsPushResult: PushResult | null;
  controlPaused: boolean;
  onToggleVpsPause: () => void;
  serverStats: {
    closed_trades?: number;
    winning_trades?: number;
    losing_trades?: number;
    breakeven_trades?: number;
    win_rate?: number;
    profit_factor?: number | null;
    total_realized_pnl?: number;
  } | null;
  wsStreams: WsStreams | null;
  futuresState: FuturesState | null;
  untrackedPnl: number | null;
  roadmap: Roadmap | null;
  soak: FuturesSoak | null;
  onSoakControl: (action: 'start' | 'stop') => Promise<boolean>;
}

const formatPrice = (price: number): string =>
  price >= 1000 ? price.toFixed(2) : price >= 1 ? price.toFixed(4) : price >= 0.01 ? price.toFixed(5) : price.toFixed(7);

const formatQty = (qty: number): string =>
  qty >= 1000 ? qty.toFixed(1) : qty >= 1 ? qty.toFixed(3) : qty.toFixed(5);

/** 95 -> "1m 35s ago"; 5400 -> "1h 30m ago" */
const formatHoldTime = (seconds: number): string => {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
};

/** 47m from now -> "in 47m"; overdue -> "now" — for cooldown end times. */
const formatInTime = (msFromNow: number): string =>
  msFromNow <= 0 ? 'now' : `in ${formatHoldTime(msFromNow / 1000)}`;

/** "held 12m ago · 38% of the 23.5h time stop" */
const holdCell = (entryTime: number, maxHoldSec: number): string => {
  const held = Math.max(0, (Date.now() - entryTime) / 1000);
  const pct = maxHoldSec > 0 ? Math.min(100, (held / maxHoldSec) * 100) : 0;
  return `${formatHoldTime(held)}${pct >= 1 ? ` · ${pct.toFixed(0)}% of time stop` : ''}`;
};

/**
 * Bracket Ladder — one horizontal price axis per position with the current
 * price marker between the stop (left, rose) and TP (right, emerald). Reads
 * exactly like the engine's decision problem: how much room to each exit.
 * Falls back to plain text when the bracket math is degenerate.
 */
const BracketLadder: React.FC<{ trade: ActiveTrade }> = ({ trade }) => {
  const lo = trade.stopPrice;
  const hi = trade.takeProfit;
  const px = trade.currentPrice;
  const valid = hi > lo && lo > 0 && px > 0;
  const pos = valid ? Math.min(100, Math.max(0, ((px - lo) / (hi - lo)) * 100)) : 50;
  return (
    <div className="min-w-[180px]">
      {valid && (
        <div className="relative mb-1 h-1.5 rounded-full bg-gradient-to-r from-rose-500/70 via-slate-600/60 to-emerald-500/70">
          <div
            className="absolute -top-1 h-3.5 w-0.5 rounded bg-white shadow-[0_0_4px_rgba(255,255,255,0.7)]"
            style={{ left: `calc(${pos}% - 1px)` }}
            title={`Current ${formatPrice(px)}`}
          />
        </div>
      )}
      <div className="flex items-center justify-between gap-2 text-[10px]">
        <span className="num text-rose-400" title="Stop loss">SL {formatPrice(lo)}</span>
        <span className="num text-slate-500">
          {valid ? `${((trade.currentPrice - lo) / lo * 100).toFixed(1)}% ▲ ${((hi - trade.currentPrice) / trade.currentPrice * 100).toFixed(1)}%` : '—'}
        </span>
        <span className="num text-emerald-400" title="Take profit">TP {formatPrice(hi)}</span>
      </div>
    </div>
  );
};

/** Shared KPI card shell for the 5-card top row (module-level: stable identity, no remount churn). */
const Kpi: React.FC<{ label: string; icon: React.ReactNode; iconClass: string; children: React.ReactNode; danger?: boolean }> =
  ({ label, icon, iconClass, children, danger }) => (
    <div className={`card p-4 ${danger ? 'border-red-500/60' : ''}`}>
      <div className="flex items-center justify-between text-xs font-medium text-slate-400">
        <span>{label}</span>
        <span className={iconClass}>{icon}</span>
      </div>
      {children}
    </div>
  );

export const LiveDashboard: React.FC<LiveDashboardProps> = ({
  equity,
  dailyRealizedPnl,
  unrealizedPnl,
  activeTrades,
  closedTrades,
  symbolsData,
  candidates,
  config,
  onUpdateConfig,
  onApplyPreset,
  onCloseTrade,
  onCloseAllTrades,
  winStreak,
  lossStreak,
  entriesToday,
  vpsRiskAvailable,
  isLossCooldown,
  cooldownEndsAt,
  cooldownKind,
  engineRisk,
  vpsConnected,
  vpsBalance,
  onPushConfigToVps,
  vpsPushResult,
  controlPaused,
  onToggleVpsPause,
  serverStats,
  wsStreams,
  futuresState,
  untrackedPnl,
  roadmap,
  soak,
  onSoakControl
}) => {
  const [inspectedSymbol, setInspectedSymbol] = useState<string | null>(null);
  const [showVpsSync, setShowVpsSync] = useState(false);

  const totalPnl = dailyRealizedPnl + unrealizedPnl;
  // Engine-reported drawdown usage is authoritative when available; the client
  // calculation is only a fallback for older engines without engine_risk.
  const drawdownUsed = engineRisk && engineRisk.drawdown_used > 0
    ? engineRisk.drawdown_used
    : Math.max(0, -totalPnl) / (equity > 0 ? equity : 1000);
  const drawdownLimit = config.maxDailyDrawdown;
  const drawdownPct = Math.min(100, (drawdownUsed / drawdownLimit) * 100);
  const breakerTripped = !!engineRisk?.drawdown_breaker;
  const activeCooldownMap = (engineRisk?.active_cooldowns ?? {}) as Record<string, { until: number; kind: string }>;
  const activeCooldownSymbols = Object.entries(activeCooldownMap)
    .filter(([, v]) => (v?.until ?? 0) * 1000 > Date.now());

  // In VPS mode prefer the engine's authoritative stats (aggregated over the full
  // SQLite history) over the locally-mapped window of recent orders, so win rate,
  // profit factor and win/loss counts always match the engine exactly.
  const useServerStats = !!serverStats && (serverStats.closed_trades ?? 0) > 0;

  const winningTrades = closedTrades.filter(t => t.pnl > 0);
  const losingTrades = closedTrades.filter(t => t.pnl < 0);
  const winRate = useServerStats
    ? (serverStats!.win_rate ?? 0)
    : closedTrades.length > 0
      ? (winningTrades.length / closedTrades.length) * 100
      : 0;

  // Prefer the engine-computed profit factor (handles breakevens and infinite
  // PF correctly); local window sums are the fallback for older engines.
  const grossProfit = winningTrades.reduce((acc, t) => acc + t.pnl, 0);
  const grossLoss = Math.abs(losingTrades.reduce((acc, t) => acc + t.pnl, 0));
  const profitFactor = useServerStats && serverStats!.profit_factor != null
    ? String(serverStats!.profit_factor)
    : grossLoss > 0
      ? (grossProfit / grossLoss).toFixed(2)
      : grossProfit > 0 ? 'MAX' : '0.00';

  const inspectedData = inspectedSymbol
    ? symbolsData.find(s => s.symbol === inspectedSymbol) || null
    : null;

  const showServerEquity = !vpsRiskAvailable;
  const equityLabel = showServerEquity
    ? 'Server Equity'
    : config.paperTrade
      ? 'Simulated Paper Equity'
      : 'Live Equity';
  const accountLabel = (vpsBalance && typeof vpsBalance.account === 'string' && vpsBalance.account)
    ? vpsBalance.account
    : (config.market === 'futures' ? 'FUTURES' : 'SPOT') + (config.paperTrade ? ' Paper' : ' Live');
  const accountChipClass = accountLabel.includes('FUTURES')
    ? 'bg-amber-500/20 text-amber-300 border-amber-500/30'
    : 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30';
  const modeChipClass = config.paperTrade
    ? 'bg-sky-500/20 text-sky-300 border-sky-500/30'
    : 'bg-rose-500/20 text-rose-300 border-rose-500/30';

  // Age of the newest per-symbol signal snapshot (engine decision-loop freshness).
  const lastSignalAgeS = symbolsData.length
    ? Math.max(0, (Date.now() - Math.max(...symbolsData.map(s => s.signal?.time ?? 0))) / 1000)
    : null;

  return (
    <div className="space-y-6">
      {/* ── Alert banners (engine-published states that block entries) ─────── */}
      {breakerTripped && (
        <div className="flex items-center justify-between rounded-lg border border-red-500/50 bg-red-950/60 p-3">
          <div className="flex items-center space-x-2 text-xs text-red-200">
            <span className="shrink-0 text-red-400">
              <ShieldAlert className="h-4 w-4" />
            </span>
            <span>
              <strong>Circuit Breaker Tripped — Daily Drawdown Limit Hit.</strong>{' '}
              {engineRisk?.breaker_reason || `Drawdown ${(drawdownUsed * 100).toFixed(2)}% breached the ${(drawdownLimit * 100).toFixed(1)}% equity limit`}.{' '}
              Entries blocked until the UTC daily reset.
            </span>
          </div>
        </div>
      )}
      {isLossCooldown && (
        <div className="flex items-center justify-between rounded-lg border border-rose-500/40 bg-rose-950/50 p-3">
          <div className="flex items-center space-x-2 text-xs text-rose-200">
            <span className="shrink-0 text-rose-400">
              <PauseCircle className="h-4 w-4" />
            </span>
            <span>
              <strong>
                {cooldownKind === 'win'
                  ? 'Trading Paused — Win-Streak Cooldown.'
                  : 'Trading Paused — Loss-Streak Cooldown.'}
              </strong>{' '}
              {cooldownKind === 'win'
                ? `MAX_WIN_STREAK reached — entries blocked until ${new Date(cooldownEndsAt).toLocaleTimeString()} (${formatInTime(cooldownEndsAt - Date.now())}, COOLDOWN_WIN).`
                : `MAX_LOSS_STREAK reached — entries blocked until ${new Date(cooldownEndsAt).toLocaleTimeString()} (${formatInTime(cooldownEndsAt - Date.now())}, COOLDOWN_LOSS).`}
              {activeCooldownSymbols.length > 0 && (
                <> Symbols: <b>{activeCooldownSymbols.map(([s]) => s).join(', ')}</b>.</>
              )}
            </span>
          </div>
        </div>
      )}

      {/* ── §1 Controls — quick tuner + engine pause + panic close ────────── */}
      <TuningControlBar
        config={config}
        onUpdateConfig={onUpdateConfig}
        onApplyPreset={onApplyPreset}
        onCloseAllTrades={onCloseAllTrades}
        onOpenVpsSync={() => setShowVpsSync(true)}
        activeTradesCount={activeTrades.length}
        vpsConnected={vpsConnected}
        controlPaused={controlPaused}
        onToggleVpsPause={onToggleVpsPause}
      />

      {/* ── §2 At-a-glance KPI row (5 unified cards) ──────────────────────── */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <Kpi label={equityLabel} icon={<Wallet className="h-4 w-4 text-emerald-400" />} iconClass="">
          {showServerEquity ? (
            <>
              <div className="mt-2">
                <span className="num text-2xl font-bold tracking-tight text-slate-500">—</span>
              </div>
              <p className="mt-2 text-[11px] leading-snug text-slate-500">
                No risk state in trading.db yet. Equity appears after the engine persists its first risk snapshot.
              </p>
            </>
          ) : (
            <>
              <div className="mt-2 flex flex-wrap items-baseline gap-x-2">
                <span className="num text-2xl font-bold tracking-tight text-white">
                  ${equity.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </span>
                <span className="text-xs text-slate-400">USDT</span>
                <span className={`rounded border px-1.5 py-0.5 text-[10px] font-bold ${accountChipClass}`}>{accountLabel}</span>
                <span className={`rounded border px-1.5 py-0.5 text-[10px] font-bold ${modeChipClass}`}>{config.paperTrade ? 'PAPER' : 'LIVE'}</span>
              </div>
              <div className="mt-2 flex items-center justify-between text-xs">
                <span className="text-slate-400">{!config.paperTrade && vpsBalance ? 'Free Quote:' : 'Capital Active:'}</span>
                <span className="num font-semibold text-slate-300">
                  {!config.paperTrade && vpsBalance
                    ? `$${vpsBalance.freeQuote.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                    : `${(config.balanceUsagePercent * 100).toFixed(0)}% ($${(equity * config.balanceUsagePercent).toFixed(1)})`}
                </span>
              </div>
            </>
          )}
        </Kpi>

        <Kpi label="Daily Net PnL" icon={totalPnl >= 0 ? <TrendingUp className="h-4 w-4 text-emerald-400" /> : <TrendingDown className="h-4 w-4 text-rose-400" />} iconClass="">
          {/* Floating PnL is derived from the engine-persisted active trades
              (entry/qty from SQLite, current price = the engine's own mark);
              the browser never re-quotes the market on its own. */}
          <div className="mt-2 flex items-baseline space-x-2">
            <span className={`num text-2xl font-bold tracking-tight ${totalPnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
              {totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)}
            </span>
            <span className={`num text-xs font-semibold ${totalPnl >= 0 ? 'text-emerald-500' : 'text-rose-500'}`}>
              ({totalPnl >= 0 ? '+' : ''}{((totalPnl / (equity - totalPnl || 1)) * 100).toFixed(2)}%)
            </span>
          </div>
          <div className="mt-2 flex items-center justify-between text-xs text-slate-400">
            <span>Realized: <b className="num text-slate-200">${dailyRealizedPnl.toFixed(2)}</b></span>
            <span>Floating: <b className={`num ${unrealizedPnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>${unrealizedPnl.toFixed(2)}</b></span>
          </div>
          {useServerStats && (serverStats!.total_realized_pnl ?? 0) !== 0 && (
            <div className="mt-1.5 flex items-center justify-between border-t border-slate-700/50 pt-1.5 text-[11px] text-slate-400">
              <span>All-time realized:</span>
              <b className={`num ${(serverStats!.total_realized_pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                ${(serverStats!.total_realized_pnl ?? 0).toFixed(2)}
              </b>
            </div>
          )}
          {untrackedPnl != null && Math.abs(untrackedPnl) >= 0.01 && (
            <div className="mt-1.5 flex items-center justify-between text-[11px] text-amber-300/90">
              <span>⚠ Untracked positions:</span>
              <b className="num">${untrackedPnl.toFixed(2)} floating — not SL/TP-managed</b>
            </div>
          )}
        </Kpi>

        <Kpi label="Drawdown Breaker" icon={<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={`h-4 w-4 ${breakerTripped ? 'text-red-400' : 'text-amber-400'}`}><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>} iconClass="" danger={breakerTripped}>
          <div className="mt-2 flex items-baseline justify-between">
            <span className="num text-2xl font-bold tracking-tight text-white">{(drawdownUsed * 100).toFixed(2)}%</span>
            <span className="text-xs text-slate-400">Limit: <strong className="text-amber-300">{(drawdownLimit * 100).toFixed(1)}%</strong></span>
          </div>
          <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-slate-700/60">
            <div
              className={`h-full transition-all duration-300 ${breakerTripped || drawdownPct > 75 ? 'bg-rose-500' : drawdownPct > 40 ? 'bg-amber-500' : 'bg-emerald-500'}`}
              style={{ width: `${Math.max(4, drawdownPct)}%` }}
            />
          </div>
          <div className="mt-2 text-[11px]">
            {breakerTripped ? (
              <span className="font-semibold text-red-300">⛔ TRIPPED — entries blocked until UTC daily reset.</span>
            ) : engineRisk ? (
              <span className="text-slate-500">{engineRisk.drawdown_used > 0 ? 'Engine-reported usage (engine_risk snapshot)' : 'Live from the engine'}</span>
            ) : (
              <span className="text-slate-500">Estimated client-side (engine snapshot unavailable)</span>
            )}
          </div>
        </Kpi>

        <Kpi label="Win Rate & Streaks" icon={<Sparkles className="h-4 w-4 text-purple-400" />} iconClass="">
          <div className="mt-2 flex items-baseline space-x-2">
            <span className="num text-2xl font-bold tracking-tight text-white">{winRate.toFixed(1)}%</span>
            <span className="text-xs text-slate-400">PF: <strong className="num text-slate-200">{profitFactor}</strong></span>
          </div>
          <div className="mt-2 flex items-center justify-between text-xs text-slate-400">
            <span>Win <b className="num text-emerald-400">{winStreak}</b>/{config.maxWinStreak}</span>
            <span>Loss <b className="num text-rose-400">{lossStreak}</b>/{config.maxLossStreak}</span>
          </div>
          <div className="mt-2 flex items-center justify-between border-t border-slate-700/50 pt-2 text-[11px] text-slate-400">
            {useServerStats ? (
              <span>
                <b className="text-emerald-400">{serverStats!.winning_trades ?? 0}W</b> / <b className="text-rose-400">{serverStats!.losing_trades ?? 0}L</b> / <b className="text-slate-300">{serverStats!.breakeven_trades ?? 0}B</b> • <b className="text-slate-200">{serverStats!.closed_trades ?? 0}</b> closed
              </span>
            ) : (
              <span>
                <b className="text-emerald-400">{winningTrades.length}W</b> / <b className="text-rose-400">{losingTrades.length}L</b> • <b className="text-slate-200">{closedTrades.length}</b> closed
              </span>
            )}
            <span>Today: <b className="num text-slate-200">{entriesToday}</b>/{config.maxTradesPerDay > 0 ? config.maxTradesPerDay : '∞'}</span>
          </div>
        </Kpi>

        {/* Open risk replaces the old per-symbol cooldown list — cooldowns moved
            to the position rows + Signal State tab; this row is actionable. */}
        <Kpi label="Open Risk" icon={<Layers className="h-4 w-4 text-amber-400" />} iconClass="">
          {activeTrades.length === 0 ? (
            <>
              <div className="mt-2">
                <span className="num text-2xl font-bold tracking-tight text-slate-400">flat</span>
              </div>
              <p className="mt-2 text-[11px] leading-snug text-slate-500">
                No open positions — every USDT is spendable. Next decision cycle in ≤ {config.signalInterval}s.
              </p>
            </>
          ) : (
            <>
              <div className="mt-2 flex items-baseline space-x-2">
                <span className="num text-2xl font-bold tracking-tight text-white">{activeTrades.length}</span>
                <span className="text-xs text-slate-400">of {config.maxSymbols} slots</span>
              </div>
              <div className="mt-2 space-y-1 text-[11px]">
                {activeTrades.map(t => {
                  const riskPct = t.entryPrice > 0 ? ((t.entryPrice - t.stopPrice) / t.entryPrice) * 100 : 0;
                  return (
                    <div key={t.symbol} className="flex items-center justify-between">
                      <span className="font-semibold text-slate-300">{t.symbol}</span>
                      <span className="num text-slate-400">
                        ${t.notional.toFixed(0)} · −{riskPct.toFixed(1)}% risk · {formatHoldTime((Date.now() - t.entryTime) / 1000)}
                      </span>
                    </div>
                  );
                })}
              </div>
              {activeCooldownSymbols.length > 0 && (
                <div className="mt-2 border-t border-slate-700/50 pt-2 text-[11px] text-slate-400">
                  Cooldowns:{' '}
                  {activeCooldownSymbols.map(([sym, cd]) => (
                    <span key={sym} className="mr-2 inline-block">
                      <b className={cd.kind === 'win' ? 'text-emerald-400' : 'text-rose-400'}>{sym}</b> until {new Date(cd.until * 1000).toLocaleTimeString()}
                    </span>
                  ))}
                </div>
              )}
            </>
          )}
        </Kpi>
      </div>

      {/* ── §3 Engine health — process, monitor link, transports, loop ────── */}
      <EngineHealthCard
        streams={wsStreams}
        connected={vpsConnected}
        engineRunning={vpsConnected && (wsStreams?.engine_age_s == null || wsStreams.engine_age_s <= 30)}
        controlPaused={controlPaused}
        engineRisk={engineRisk}
        breakerTripped={breakerTripped}
        scanInterval={config.signalInterval}
        lastSignalAgeS={lastSignalAgeS}
      />

      {/* ── §4 Market: watchlist + screener side-by-side (stacks on mobile) ── */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {/* Monitored symbols — the engine's live decision per pair */}
        <Section
          icon={<Crosshair className="h-4 w-4" />}
          title={`Monitored Symbols (${symbolsData.length})`}
          subtitle={config.dynamicSymbols ? 'top momentum screener' : 'static watchlist'}
          meta={
            <>
              <Chip label="Scan" value={`${config.signalInterval}s`} />
              <Chip label="Regime" value={`EMA-${config.regimeEma} ${config.mtfTimeframe}`} />
            </>
          }
          bodyClass="p-4"
        >
          {symbolsData.length === 0 ? (
            <div className="flex flex-col items-center justify-center space-y-2 rounded-lg border border-dashed border-slate-700/60 bg-slate-900/40 px-4 py-8 text-center">
              <div className="h-6 w-6 animate-spin rounded-full border-2 border-amber-500/30 border-t-amber-400" />
              <p className="text-sm font-medium text-slate-300">Synchronizing monitored pairs with the engine…</p>
              <p className="text-xs text-slate-400">
                Waiting for the engine's first signal snapshot ({config.staticSymbols?.join(', ') || 'monitored pairs'}).
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {symbolsData.map(sym => {
                const sig = sym.signal;
                const isBullishTrigger = sig.trigger && sig.signal === 'BUY';
                const hasActiveTrade = activeTrades.some(t => t.symbol === sym.symbol);
                const regimeTone =
                  sig.regime === 'UP'
                    ? 'border border-emerald-500/30 bg-emerald-500/20 text-emerald-300'
                    : sig.regime === 'DOWN'
                      ? 'border border-rose-500/30 bg-rose-500/20 text-rose-300'
                      : 'bg-slate-800 text-slate-300';
                return (
                  <div
                    key={sym.symbol}
                    className={`rounded-lg border p-3.5 transition-all ${
                      isBullishTrigger && !hasActiveTrade
                        ? 'border-emerald-500/40 bg-emerald-950/20 shadow-sm shadow-emerald-900/20'
                        : 'border-slate-700/50 bg-slate-900/50'
                    }`}
                  >
                    <div className="flex items-start justify-between">
                      <div className="min-w-0">
                        <div className="flex items-center space-x-2">
                          <span className="text-sm font-bold text-white">{sym.symbol}</span>
                          {hasActiveTrade && (
                            <span className="rounded border border-amber-500/30 bg-amber-500/20 px-1.5 py-0.5 text-[10px] font-semibold text-amber-300">Active</span>
                          )}
                          {sym.inCooldown && (
                            <span className="rounded border border-rose-500/30 bg-rose-500/20 px-1.5 py-0.5 text-[10px] font-semibold text-rose-300">Cooldown</span>
                          )}
                        </div>
                        <div className="mt-0.5 truncate text-xs text-slate-400">{sym.name}</div>
                      </div>
                      <div className="shrink-0 text-right">
                        <div className="num text-sm font-bold text-slate-100">
                          ${sym.price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: sym.price < 10 ? 4 : 2 })}
                        </div>
                        <div className={`flex items-center justify-end space-x-0.5 text-xs font-semibold ${sym.priceChange24h >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                          {sym.priceChange24h >= 0 ? <ArrowUpRight className="w-3 h-3" /> : <ArrowDownRight className="w-3 h-3" />}
                          <span className="num">{sym.priceChange24h >= 0 ? '+' : ''}{sym.priceChange24h.toFixed(2)}%</span>
                        </div>
                      </div>
                    </div>
                    <div className="mt-3 flex items-center justify-between border-t border-slate-700/50 pt-2.5 text-xs">
                      <div className="flex items-center space-x-1.5">
                        <span className="text-slate-400">Regime:</span>
                        <span className={`rounded px-1.5 py-0.5 text-[11px] font-bold ${regimeTone}`}>{sig.regime}</span>
                        <span className="text-slate-400">RSI:</span>
                        <span className="num font-bold text-slate-200">{sig.rsi !== null && sig.rsi !== undefined ? sig.rsi.toFixed(1) : '—'}</span>
                      </div>
                      <button
                        id={`inspect-card-${sym.symbol}`}
                        onClick={() => setInspectedSymbol(sym.symbol)}
                        className="rounded bg-slate-800 px-2 py-0.5 text-[11px] font-medium text-slate-300 transition-colors hover:bg-slate-700 hover:text-white"
                        title="Inspect the engine signal state"
                      >
                        Inspect
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </Section>

        {/* Right column: screener (when active) + auxiliary engine cards */}
        <div className="space-y-4">
          {candidates.length > 0 && (
            <DynamicScreener
              candidates={candidates}
              config={config}
              onInspectSymbol={(sym) => setInspectedSymbol(sym)}
            />
          )}
          {config.dynamicSymbols && candidates.length === 0 && (
            <div className="card flex flex-col items-center justify-center gap-1.5 p-6 text-center">
              <Radar className="h-6 w-6 text-slate-600" />
              <p className="text-sm font-medium text-slate-300">Dynamic screener active — no candidates yet</p>
              <p className="max-w-md text-xs text-slate-500">
                Scanning the market for pairs that clear the momentum gates; the panels below stay live meanwhile.
              </p>
            </div>
          )}
          <FuturesPanel state={futuresState} market={config.market} paper={config.paperTrade} />
          <FuturesSoakCard soak={soak} onSoakControl={onSoakControl} />
          <RoadmapCard roadmap={roadmap} />
        </div>
      </div>

      {/* Non-Zero Spot Balances Strip (Live Mode) */}
      {!config.paperTrade && vpsBalance?.balances && vpsBalance.balances.length > 0 && (
        <div className="card flex flex-wrap items-center gap-2 px-4 py-3 text-xs">
          <span className="mr-1 flex items-center gap-1.5 font-medium text-slate-400">
            <span
              title={
                vpsBalance.source === 'ws'
                  ? 'Balances streamed from the engine\'s user-data WS cache (outboundAccountPosition).'
                  : vpsBalance.source === 'rest'
                    ? 'WS balance cache stale — the engine/monitor is using a REST /api/v3/account snapshot.'
                    : vpsBalance.source === 'db'
                      ? 'Exchange unreachable — showing the engine\'s last published equity.'
                      : 'Balance source: engine.'
              }
              className={`h-2 w-2 rounded-full ${vpsBalance.source === 'ws' ? 'bg-emerald-400 animate-pulse' : vpsBalance.source === 'db' ? 'bg-rose-400' : 'bg-sky-400'}`}
            ></span>
            Spot Balances:
            <span className="num text-[10px] text-slate-500">
              {vpsBalance.source === 'ws'
                ? `via WS${typeof vpsBalance.ageS === 'number' ? ` · verified ${Math.round(vpsBalance.ageS)}s ago` : ''}`
                : vpsBalance.source === 'rest'
                  ? 'via REST'
                  : vpsBalance.source === 'db'
                    ? 'last known'
                    : ''}
            </span>
          </span>
          {vpsBalance.balances.slice(0, 8).map(b => (
            <div key={b.asset} className="flex items-center gap-1.5 rounded-lg border border-slate-700/60 bg-slate-900/80 px-2.5 py-1 text-slate-300">
              <span className="font-bold text-white">{b.asset}:</span>
              <span className="num">{(b.free + (b.locked || 0)).toFixed(4)}</span>
              {b.usd_value ? <span className="text-slate-400">(${b.usd_value.toFixed(1)})</span> : null}
            </div>
          ))}
        </div>
      )}

      {/* ── §5 Open positions — with SL/TP bracket ladders ─────────────────── */}
      <Section
        icon={<Zap className="h-4 w-4" />}
        title="Active Positions (Market-Only)"
        subtitle="every position carries a software-managed stop and target"
        meta={
          <>
            <Chip label="Slots" value={`${activeTrades.length}/${config.maxSymbols}`} />
            <Chip label="Time stop" value={formatHoldTime(config.maxHoldTime)} />
            <Chip label="Trail" value={config.trailingAtrMultiplier > 0 ? `${config.trailingAtrMultiplier}× ATR` : `${(config.trailingStopCallback * 100).toFixed(1)}%`} />
          </>
        }
        bodyClass=""
      >
        {activeTrades.length === 0 ? (
          <div className="flex flex-col items-center justify-center space-y-2 p-8 text-center">
            <Inbox className="mb-1 h-8 w-8 text-slate-600" />
            <p className="text-sm font-medium text-slate-300">No open positions</p>
            <p className="max-w-md text-xs text-slate-500">
              The engine scans the monitored pairs each {config.signalInterval}s cycle: a BUY fires when the daily EMA-{config.regimeEma} regime is up and RSI({config.rsiPeriod}) dips below {config.rsiOversold} on the {config.rsiTimeframe} bucket and turns up.
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="border-b border-slate-700/50 bg-slate-900/60 font-semibold uppercase tracking-wider text-slate-400">
                <tr>
                  <th className="py-3 px-4">Symbol / Held</th>
                  <th className="py-3 px-4">Entry → Now</th>
                  <th className="py-3 px-4">Size</th>
                  <th className="py-3 px-4">Bracket Ladder</th>
                  <th className="py-3 px-4">Locks</th>
                  <th className="py-3 px-4">Unrealized</th>
                  <th className="py-3 px-4 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700/50">
                {activeTrades.map((trade, idx) => {
                  const pnlColor = trade.unrealizedPnl >= 0 ? 'text-emerald-400' : 'text-rose-400';
                  const changePct = trade.entryPrice > 0 ? ((trade.currentPrice - trade.entryPrice) / trade.entryPrice) * 100 : 0;
                  return (
                    <tr key={`active_trade_${trade.id || trade.symbol}_${trade.entryTime || idx}`} className="transition-colors hover:bg-slate-700/20">
                      <td className="py-3.5 px-4">
                        <div className="flex items-center space-x-1.5 font-bold text-slate-100">
                          <span>{trade.symbol}</span>
                          <span className="rounded border border-emerald-500/30 bg-emerald-500/20 px-1.5 py-0.5 text-[10px] font-bold text-emerald-400">{trade.side}</span>
                        </div>
                        <div className="num mt-0.5 text-[11px] text-slate-400" title="Held vs the MAX_HOLD_TIME time stop">
                          {holdCell(trade.entryTime, config.maxHoldTime)}
                        </div>
                      </td>

                      <td className="num py-3.5 px-4">
                        <div className="text-slate-400">{formatPrice(trade.entryPrice)}</div>
                        <div className="font-bold text-slate-100">
                          {formatPrice(trade.currentPrice)}{' '}
                          <span className={`text-[11px] font-semibold ${changePct >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                            ({changePct >= 0 ? '+' : ''}{changePct.toFixed(2)}%)
                          </span>
                        </div>
                      </td>

                      <td className="num py-3.5 px-4">
                        <div className="font-semibold text-slate-200">{formatQty(trade.quantity)}</div>
                        <div className="text-[11px] text-slate-400">${trade.notional.toFixed(2)}</div>
                      </td>

                      <td className="py-3.5 px-4">
                        <BracketLadder trade={trade} />
                      </td>

                      <td className="py-3.5 px-4">
                        <div className="flex flex-col space-y-1">
                          <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-semibold ${trade.trailingActive ? 'border border-amber-500/30 bg-amber-500/20 text-amber-300' : 'bg-slate-700/40 text-slate-400'}`}>
                            Trailing {trade.trailingActive ? 'ACTIVE' : 'OFF'}
                          </span>
                          <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-semibold ${trade.breakevenActivated ? 'border border-blue-500/30 bg-blue-500/20 text-blue-300' : 'bg-slate-700/40 text-slate-400'}`}>
                            Breakeven {trade.breakevenActivated ? 'LOCKED' : 'OFF'}
                          </span>
                        </div>
                      </td>

                      <td className="num py-3.5 px-4">
                        <div className={`text-sm font-bold ${pnlColor}`}>
                          {trade.unrealizedPnl >= 0 ? '+' : ''}${trade.unrealizedPnl.toFixed(2)}
                        </div>
                        <div className={`text-[11px] font-semibold ${pnlColor}`}>
                          {trade.unrealizedPnlPct >= 0 ? '+' : ''}{trade.unrealizedPnlPct.toFixed(2)}%
                        </div>
                      </td>

                      <td className="py-3.5 px-4 text-right">
                        <button
                          id={`close-trade-${trade.symbol}`}
                          onClick={() => onCloseTrade(trade.symbol, 'MANUAL')}
                          className="rounded border border-rose-500/30 bg-rose-600/20 px-2.5 py-1 text-xs font-semibold text-rose-300 transition-colors hover:bg-rose-600 hover:text-white"
                        >
                          Market Close
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {/* ── §6 Trade history ──────────────────────────────────────────────── */}
      <Section
        icon={<History className="h-4 w-4 text-slate-400" />}
        title="Recent Completed Trades"
        subtitle="exits with realized PnL, newest first"
        meta={<Chip label="Total closed" value={closedTrades.length} />}
        bodyClass=""
      >
        {closedTrades.length === 0 ? (
          <div className="flex flex-col items-center justify-center space-y-1 p-6 text-center">
            <Clock className="h-6 w-6 text-slate-600" />
            <p className="text-xs text-slate-500">No closed trades yet — exits land here the moment the engine records a realized PnL.</p>
          </div>
        ) : (
          <div className="max-h-60 overflow-x-auto overflow-y-auto">
            <table className="w-full text-left text-xs">
              <thead className="sticky top-0 bg-slate-900/95 text-[11px] font-semibold uppercase text-slate-400 backdrop-blur">
                <tr>
                  <th className="py-2.5 px-4">Symbol</th>
                  <th className="py-2.5 px-4">Side</th>
                  <th className="py-2.5 px-4">Entry → Exit</th>
                  <th className="py-2.5 px-4">Exited</th>
                  <th className="py-2.5 px-4">Exit Reason</th>
                  <th className="py-2.5 px-4 text-right">PnL (USDT)</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700/50">
                {closedTrades.slice(-8).reverse().map((trade, idx) => (
                  <tr key={`closed_trade_${trade.id || trade.symbol}_${trade.exitTime || idx}`} className="hover:bg-slate-700/20">
                    <td className="py-2.5 px-4 font-bold text-slate-200">{trade.symbol}</td>
                    <td className="py-2.5 px-4">
                      <span className="rounded bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-bold text-emerald-400">{trade.side}</span>
                    </td>
                    <td className="num py-2.5 px-4 text-slate-300">
                      {formatPrice(trade.entryPrice)} → {formatPrice(trade.exitPrice)}
                    </td>
                    <td className="py-2.5 px-4 text-slate-300">
                      <ExitedCell trade={trade} />
                    </td>
                    <td className="py-2.5 px-4">
                      <span className={`rounded px-2 py-0.5 text-[10px] font-semibold ${
                        trade.exitReason === 'TAKE_PROFIT'
                          ? 'border border-emerald-500/30 bg-emerald-500/20 text-emerald-300'
                          : trade.exitReason === 'TRAILING_STOP'
                          ? 'border border-amber-500/30 bg-amber-500/20 text-amber-300'
                          : 'border border-rose-500/30 bg-rose-500/20 text-rose-300'
                      }`}>
                        {trade.exitReason.replace(/_/g, ' ')}
                      </span>
                    </td>
                    <td className="num py-2.5 px-4 text-right font-bold">
                      <span className={trade.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}>
                        {trade.pnl >= 0 ? '+' : ''}${trade.pnl.toFixed(2)} ({trade.pnlPct >= 0 ? '+' : ''}{trade.pnlPct.toFixed(2)}%)
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {/* Modals */}
      {inspectedSymbol && inspectedData && (
        <SymbolDetailModal
          symbolData={inspectedData}
          config={config}
          equity={equity}
          onClose={() => setInspectedSymbol(null)}
        />
      )}
      {showVpsSync && (
        <VpsSyncModal
          config={config}
          onClose={() => setShowVpsSync(false)}
          vpsConnected={vpsConnected}
          onApplyToVps={onPushConfigToVps}
          lastPushResult={vpsPushResult}
        />
      )}
    </div>
  );
};

/** Exit timestamp cell: absolute date + relative age, "~" marks engine-estimated entries. */
const ExitedCell: React.FC<{ trade: ClosedTrade }> = ({ trade }) => {
  const d = new Date(trade.exitTime);
  const ageS = Math.max(0, (Date.now() - trade.exitTime) / 1000);
  return (
    <div>
      <div className="num">{d.toLocaleDateString()} {d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</div>
      <div className="text-[11px] text-slate-500">
        {formatHoldTime(ageS)} ago · entry {trade.entryTimeEstimated ? '~' : ''}{formatHoldTime(Math.max(0, (trade.exitTime - trade.entryTime) / 1000))} hold
      </div>
    </div>
  );
};
