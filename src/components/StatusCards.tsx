import React, { useState } from 'react';
import { Radio, Layers, Rocket, FlaskConical, Play, Square, TrendingUp, TrendingDown, Clock } from 'lucide-react';
import { WsStreams, FuturesState, Roadmap, FuturesSoak } from '../types';

export const FuturesPanel: React.FC<{ state: FuturesState | null; market: string; paper: boolean }> = ({ state, market, paper }) => {
  if (market !== 'futures') return null;
  const positions = state?.positions ?? [];
  const stale = state?.age_s != null && state.age_s > 60;
  return (
    <div className="bg-slate-800/80 border border-slate-700/60 rounded-xl p-5 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-bold uppercase tracking-wider text-amber-400 flex items-center space-x-2">
          <Layers className="w-4 h-4" />
          <span>Futures (USDⓈ-M)</span>
        </h3>
        <span className="text-[10px] px-2 py-0.5 rounded-full border bg-slate-500/10 text-slate-300 border-slate-500/30">
          {paper ? 'PAPER' : 'LIVE'}
        </span>
        <span className={`text-[10px] px-2 py-0.5 rounded-full border ${
          stale ? 'bg-rose-500/10 text-rose-300 border-rose-500/30'
                : 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30'}`}>
          {state?.age_s != null ? `${state.age_s.toFixed(0)}s ago` : 'no data yet'}
        </span>
      </div>
      <div className="grid grid-cols-3 gap-3 text-xs mb-3">
        <div className="bg-slate-900/60 rounded-lg p-2">
          <div className="text-[10px] text-slate-500">Leverage</div>
          <div className="font-mono text-slate-200">{state?.leverage ?? 1}×</div>
        </div>
        <div className="bg-slate-900/60 rounded-lg p-2">
          <div className="text-[10px] text-slate-500">Margin</div>
          <div className="font-mono text-slate-200">{state?.margin_type ?? 'ISOLATED'}</div>
        </div>
        <div className="bg-slate-900/60 rounded-lg p-2">
          <div className="text-[10px] text-slate-500">Mode</div>
          <div className="font-mono text-slate-200">{state?.one_way === false ? 'HEDGE' : 'ONE-WAY'}</div>
        </div>
      </div>
      {positions.length === 0 ? (
        <p className="text-[11px] text-slate-500">No open futures positions.</p>
      ) : (
        <div className="space-y-2">
          {positions.map((p, idx) => (
            <div key={`${p.symbol}-${idx}`} className="bg-slate-900/60 rounded-lg p-2.5 text-xs">
              <div className="flex items-center justify-between mb-1">
                <span className="font-semibold text-slate-200">
                  {p.symbol}
                  {p.paper && <span className="ml-2 text-[9px] px-1.5 py-0.5 rounded bg-sky-500/10 text-sky-300 border border-sky-500/30">PAPER</span>}
                  <span className={`ml-2 text-[10px] font-mono ${(p.amount ?? 0) < 0 ? 'text-rose-400' : 'text-emerald-400'}`}>
                    {(p.amount ?? 0) < 0 ? 'BEARISH' : 'BULLISH'} {Math.abs(p.amount ?? 0)}
                  </span>
                </span>
                {p.unrealized_pnl != null && (
                  <span className={`font-mono ${(p.unrealized_pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                    {(p.unrealized_pnl ?? 0) >= 0 ? '+' : ''}{(p.unrealized_pnl ?? 0).toFixed(4)}
                  </span>
                )}
              </div>
              <div className="grid grid-cols-4 gap-2 text-[10px] text-slate-400 font-mono">
                <div>entry {p.entry_price ? p.entry_price.toPrecision(6) : '—'}</div>
                <div>mark {p.mark_price ? p.mark_price.toPrecision(6) : '—'}</div>
                <div>liq {p.liquidation_price ? p.liquidation_price.toPrecision(6) : '—'}</div>
                <div>{p.leverage ?? '—'}× {p.margin_type ?? ''}</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

/**
 * Capital roadmap card: the account's growth stages for futures viability,
 * computed from LIVE exchange floors (status.py → capital_roadmap.py).
 */
export const RoadmapCard: React.FC<{ roadmap: Roadmap | null }> = ({ roadmap }) => {
  if (!roadmap) return null;
  const nextStage = roadmap.stages.find(s => !s.reached);
  const progress = nextStage
    ? Math.min(100, (roadmap.equity / nextStage.threshold) * 100)
    : 100;
  // Every figure below is a SNAPSHOT taken when the monitor last recomputed it
  // (exchange floors + prices, cached 10 min), so the age is shown rather than
  // implied. The header's Equity is the live number — they are not the same read.
  const roadmapAgeS = roadmap.age_s ?? null;
  const roadmapAgeLabel = roadmapAgeS == null
    ? 'refreshed 10 min'
    : roadmapAgeS < 90
      ? `computed ${Math.round(roadmapAgeS)}s ago`
      : `computed ${Math.round(roadmapAgeS / 60)}m ago`;
  // Which universe is on screen: the engine's live watched symbols (the screener
  // rotates them), or the fallback list used only before the engine publishes.
  const roadmapUniverse = roadmap.pairs_source === 'default'
    ? `${roadmap.pairs.length} fallback pairs`
    : `${roadmap.pairs.length} engine-watched pairs`;
  return (
    <div className="bg-slate-800/80 border border-slate-700/60 rounded-xl p-5 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-bold uppercase tracking-wider text-amber-400 flex items-center space-x-2">
          <TrendingUp className="w-4 h-4" />
          <span>Capital Roadmap</span>
        </h3>
        <span className="text-[10px] text-slate-500">
          {roadmapUniverse} · live exchange floors · {roadmapAgeLabel}
        </span>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs mb-3">
        <div className="bg-slate-900/60 rounded-lg p-2">
          <div className="text-[10px] text-slate-500">Equity (at compute)</div>
          <div className="font-mono text-slate-200">${roadmap.equity.toFixed(2)}</div>
        </div>
        <div className="bg-slate-900/60 rounded-lg p-2">
          <div className="text-[10px] text-slate-500">Proven notional</div>
          <div className="font-mono text-slate-200">${roadmap.proven_notional.toFixed(2)}</div>
        </div>
        <div className="bg-slate-900/60 rounded-lg p-2">
          <div className="text-[10px] text-slate-500">Implied leverage</div>
          <div className="font-mono text-slate-200">{roadmap.implied_leverage.toFixed(2)}×</div>
        </div>
        <div className="bg-slate-900/60 rounded-lg p-2">
          <div className="text-[10px] text-slate-500">Futures fee edge</div>
          <div className="font-mono text-emerald-400">−{roadmap.fee_edge_pct.toFixed(2)}%/RT</div>
        </div>
      </div>
      {nextStage && (
        <div className="mb-3">
          <div className="flex justify-between text-[10px] text-slate-400 mb-1">
            <span>Next stage: {nextStage.label}</span>
            <span className="font-mono">${roadmap.equity.toFixed(0)} / ${nextStage.threshold.toFixed(0)}</span>
          </div>
          <div className="h-2 bg-slate-700 rounded-full overflow-hidden">
            <div className="h-full bg-gradient-to-r from-amber-500 to-emerald-500" style={{ width: `${progress}%` }} />
          </div>
        </div>
      )}
      <div className="space-y-1">
        {roadmap.stages.map(s => (
          <div key={s.threshold} className="flex items-center space-x-2 text-[11px]">
            <span className={s.reached ? 'text-emerald-400' : 'text-slate-600'}>{s.reached ? '✓' : '○'}</span>
            <span className="font-mono text-slate-400 w-14">${s.threshold.toFixed(0)}</span>
            <span className={s.reached ? 'text-slate-300' : 'text-slate-500'}>{s.label}</span>
          </div>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap gap-1">
        {roadmap.ok_pairs.map(p => (
          <span key={p} className="text-[9px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-300 border border-emerald-500/30 font-mono">{p}</span>
        ))}
        {roadmap.blocked_pairs.map(p => (
          <span key={p} className="text-[9px] px-1.5 py-0.5 rounded bg-slate-500/10 text-slate-400 border border-slate-500/30 font-mono">{p} @ $24</span>
        ))}
      </div>
    </div>
  );
};

/**
 * 24h futures paper-soak card: PM2 health of the futures-soak engine and its
 * isolated-DB progress (trades/PnL while the real spot engine keeps trading).
 */
export const FuturesSoakCard: React.FC<{
  soak: FuturesSoak | null;
  onSoakControl?: (action: 'start' | 'stop') => Promise<boolean>;
}> = ({ soak, onSoakControl }) => {
  const [soakBusy, setSoakBusy] = useState<'start' | 'stop' | null>(null);
  const [soakMsg, setSoakMsg] = useState<string | null>(null);
  const [showEvents, setShowEvents] = useState(false);
  if (!soak) return null;
  const target = 24; // hours
  const pct = Math.min(100, ((soak.uptime_h ?? 0) / target) * 100);
  const equity = soak.equity ?? soak.paper_balance ?? 0;
  const pnl = soak.pnl ?? 0;
  const closed = soak.closed ?? 0;
  const wins = soak.wins ?? 0;
  return (
    <div className="bg-slate-800/80 border border-slate-700/60 rounded-xl p-5 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-bold uppercase tracking-wider text-amber-400 flex items-center space-x-2">
          <Clock className="w-4 h-4" />
          <span>Futures Soak (24h paper)</span>
        </h3>
        <div className="flex items-center space-x-1.5">
          <span className={`text-[10px] px-2 py-0.5 rounded-full border ${
            soak.running ? 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30'
                         : 'bg-rose-500/10 text-rose-300 border-rose-500/30'}`}>
            {soak.status}
          </span>
          {(() => {
            const wd = soak.watchdog_status;
            const alerts = soak.watchdog_alerts ?? [];
            const wdColor = wd === 'online' && alerts.length === 0
              ? 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30'
              : alerts.length > 0
                ? 'bg-amber-500/10 text-amber-300 border-amber-500/30'
                : 'bg-rose-500/10 text-rose-300 border-rose-500/30';
            const wdLabel = wd === 'online'
              ? (alerts.length ? `watchdog: ${alerts.join(', ')}` : 'watchdog: armed')
              : wd === 'not running' ? 'watchdog: OFF' : `watchdog: ${wd ?? 'n/a'}`;
            return (
              <span
                className={`text-[10px] px-2 py-0.5 rounded-full border ${wdColor}`}
                title={soak.watchdog_last_check_ms
                  ? `Last supervision pass ${new Date(soak.watchdog_last_check_ms).toLocaleTimeString()}${soak.watchdog_restarts ? ` · ${soak.watchdog_restarts} restarts` : ''}`
                  : 'No supervision pass recorded yet'}
              >
                {wdLabel}
              </span>
            );
          })()}
        </div>
      </div>
      <div className="mb-3">
        <div className="flex justify-between text-[10px] text-slate-400 mb-1">
          <span>Soak progress</span>
          <span className="font-mono">
            {(soak.uptime_h ?? 0).toFixed(1)}h / {soak.hours_total ?? target}h
            {soak.deadline_ms
              ? ` · ends ${new Date(soak.deadline_ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
              : ''}
          </span>
        </div>
        <div className="h-2 bg-slate-700 rounded-full overflow-hidden">
          <div className="h-full bg-gradient-to-r from-sky-500 to-emerald-500" style={{ width: `${pct}%` }} />
        </div>
      </div>
      {onSoakControl && (
        <div className="flex items-center space-x-2 mb-3">
          <button
            onClick={async () => {
              setSoakBusy('start'); setSoakMsg(null);
              const ok = await onSoakControl('start');
              setSoakMsg(ok ? 'Soak started — 24h clock reset.' : 'Soak start failed (see logs).');
              setSoakBusy(null);
            }}
            disabled={soakBusy !== null || soak.running}
            title={soak.running ? 'Soak is already running' : 'Start a fresh 24h futures paper soak'}
            className="flex items-center space-x-1 px-3 py-1.5 rounded-lg text-[11px] font-semibold bg-emerald-600/80 hover:bg-emerald-600 disabled:opacity-40 disabled:cursor-not-allowed text-white transition-colors"
          >
            <Play className="w-3 h-3" />
            <span>{soakBusy === 'start' ? 'Starting…' : 'Start soak'}</span>
          </button>
          <button
            onClick={async () => {
              if (!window.confirm('Stop the 24h futures soak early? Progress is kept in the soak DB, but the run will not complete.')) return;
              setSoakBusy('stop'); setSoakMsg(null);
              const ok = await onSoakControl('stop');
              setSoakMsg(ok ? 'Soak stopped — DB preserved.' : 'Soak stop failed (see logs).');
              setSoakBusy(null);
            }}
            disabled={soakBusy !== null || !soak.running}
            title={soak.running ? 'Stop the soak early (DB preserved)' : 'Soak is not running'}
            className="flex items-center space-x-1 px-3 py-1.5 rounded-lg text-[11px] font-semibold bg-rose-600/80 hover:bg-rose-600 disabled:opacity-40 disabled:cursor-not-allowed text-white transition-colors"
          >
            <Square className="w-3 h-3" />
            <span>{soakBusy === 'stop' ? 'Stopping…' : 'Stop soak'}</span>
          </button>
          {soakMsg && <span className="text-[10px] text-slate-400">{soakMsg}</span>}
        </div>
      )}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
        <div className="bg-slate-900/60 rounded-lg p-2">
          <div className="text-[10px] text-slate-500">Paper equity</div>
          <div className="font-mono text-slate-200">${equity.toFixed(2)}</div>
        </div>
        <div className="bg-slate-900/60 rounded-lg p-2">
          <div className="text-[10px] text-slate-500">Soak PnL</div>
          <div className={`font-mono ${pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
            {pnl >= 0 ? '+' : ''}{pnl.toFixed(4)}
          </div>
        </div>
        <div className="bg-slate-900/60 rounded-lg p-2">
          <div className="text-[10px] text-slate-500">Closed trades</div>
          <div className="font-mono text-slate-200">{closed} ({wins}W/{closed - wins}L)</div>
        </div>
        <div className="bg-slate-900/60 rounded-lg p-2">
          <div className="text-[10px] text-slate-500">Last trade</div>
          <div className="font-mono text-slate-200">
            {soak.last_trade_ms ? new Date(soak.last_trade_ms).toLocaleTimeString() : '—'}
          </div>
        </div>
      </div>
      {(() => {
        // Watchdog footer: visible only when it has something to say — alert
        // flags (amber) or a supervision pass suspiciously older than its 60s
        // poll + hourly liveness stamp (rose = supervisor likely hung).
        const alerts = soak.watchdog_alerts ?? [];
        const ageH = soak.watchdog_last_check_ms
          ? (Date.now() - soak.watchdog_last_check_ms) / 3_600_000 : null;
        if (alerts.length === 0 && (ageH === null || ageH < 2.5)) return null;
        return (
          <div className="mt-3 flex items-center justify-between text-[10px]">
            <span className={alerts.length
              ? 'text-amber-300'
              : 'text-rose-300'}>
              {alerts.length
                ? `⚠ ${alerts.join(' · ')}`
                : `watchdog last pass ${ageH != null ? ageH.toFixed(1) : '?'}h ago — supervisor may be hung`}
            </span>
            {soak.watchdog_restarts ? (
              <span className="text-slate-500">{soak.watchdog_restarts} restarts</span>
            ) : null}
          </div>
        );
      })()}
      {(() => {
        // Supervision event feed — collapsible, newest first. Kind-colored
        // dots: green=completed/recovered, red=alerts, sky=watching,
        // slate=armed/started.
        const events = (soak.watchdog_events ?? []).slice().reverse();
        if (events.length === 0) return null;
        const dotColor = (k: string) =>
          k === 'completed' || k === 'recovered' ? 'bg-emerald-400'
          : k === 'death_alert' || k === 'stall_alert' ? 'bg-rose-400'
          : k === 'watching' ? 'bg-sky-400'
          : 'bg-slate-400';
        const fmtAge = (s: number) =>
          s < 90 ? `${s}s` : s < 5400 ? `${Math.round(s / 60)}m` : `${(s / 3600).toFixed(1)}h`;
        return (
          <div className="mt-3 pt-2 border-t border-slate-700/50">
            <button
              onClick={() => setShowEvents(v => !v)}
              className="flex items-center space-x-1.5 text-[10px] text-slate-400 hover:text-slate-200 transition-colors"
              title="Recent watchdog supervision events (newest first)"
            >
              <span className={`inline-block w-1.5 h-1.5 rounded-full ${dotColor(events[0]?.kind ?? '')}`} />
              <span>{showEvents ? 'Hide' : 'Show'} watchdog events ({events.length})</span>
              <span className="text-slate-600">{showEvents ? '▾' : '▸'}</span>
            </button>
            {showEvents && (
              <div className="mt-2 max-h-40 overflow-y-auto space-y-1 pr-1">
                {events.map((e, i) => (
                  <div
                    key={`${e.ts_ms}-${i}`}
                    className="flex items-start space-x-2 text-[10px] leading-snug"
                    title={`${e.utc} — ${e.kind}: ${e.detail}`}
                  >
                    <span className={`mt-1 inline-block w-1.5 h-1.5 flex-shrink-0 rounded-full ${dotColor(e.kind)}`} />
                    <span className="font-mono text-slate-300 w-16 flex-shrink-0">{fmtAge(e.age_s)} ago</span>
                    <span className="text-slate-400 font-semibold w-20 flex-shrink-0">{e.kind}</span>
                    <span className="text-slate-500 truncate">{e.detail}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })()}
    </div>
  );
};

const StreamLight: React.FC<{
  label: string;
  ok: boolean;
  detail: string;
  title: string;
  lagging?: boolean;
  warn?: boolean;
}> = ({ label, ok, detail, title, lagging = false, warn = false }) => {
  const tone = !ok || warn
    ? 'border-rose-500/40 bg-rose-950/40 text-rose-300'
    : lagging
      ? 'border-amber-500/40 bg-amber-950/40 text-amber-300'
      : 'border-emerald-500/40 bg-emerald-950/40 text-emerald-300';
  const dot = !ok || warn
    ? 'bg-rose-400'
    : lagging
      ? 'bg-amber-400'
      : 'bg-emerald-400 animate-pulse';
  return (
    <div title={title} className={`border rounded-lg px-2.5 py-1 flex items-center gap-1.5 ${tone}`}>
      <span className={`w-2 h-2 rounded-full ${dot}`} />
      <span className="text-slate-200 font-medium">{label}</span>
      <span className="text-[10px] opacity-80 font-mono">{detail}</span>
    </div>
  );
};

/**
 * Realtime Streams strip: every realtime engine path is a WebSocket. The engine
 * publishes each transport's health (risk_state["ws_streams"]), so a silently
 * dead feed is visible here instead of being inferred from stale numbers.
 */
export const StreamLights: React.FC<{ streams: WsStreams | null; connected?: boolean }> = ({ streams, connected = false }) => {
  if (!streams) {
    return (
      <div className="bg-slate-800/60 border border-slate-700/60 rounded-xl px-4 py-2 text-xs text-slate-400 flex items-center gap-2">
        <span className="w-2 h-2 rounded-full bg-slate-500" />
        Realtime streams: <span className="text-slate-300 font-medium">no health data</span>
        <span className="text-slate-500">
          {connected ? '(engine has not published stream health yet)' : '(engine offline)'}
        </span>
      </div>
    );
  }
  // Total ticker lag = the engine's own last-frame age PLUS how long ago the
  // engine published it (a stalled engine freezes its last numbers).
  const engineAge = typeof streams.engine_age_s === 'number' ? streams.engine_age_s : 0;
  const engineStale = engineAge > 30;
  const baseFrame = streams.all_tickers?.last_frame_age_s;
  const frameAge = typeof baseFrame === 'number' ? baseFrame + engineAge : baseFrame;
  const frameLagging = typeof frameAge === 'number' && frameAge > 5;
  const frameDown = typeof frameAge === 'number' && frameAge > 20;
  return (
    <div
      title={
        'Realtime transports — all engine data flows over WebSocket.\n' +
        (engineStale
          ? `Engine STALE: last health published ${engineAge.toFixed(0)}s ago (engine stopped?).`
          : `Engine health published ${engineAge.toFixed(0)}s ago.`)
      }
      className={`bg-slate-800/60 rounded-xl px-4 py-2 text-xs flex flex-wrap items-center gap-2 ${engineStale ? 'border border-rose-500/50' : 'border border-slate-700/60'}`}
    >
      <span className="text-slate-400 font-medium mr-1">Realtime Streams:</span>
      {engineStale && (
        <span className="text-rose-300 font-semibold" title="The engine has not published stream health recently — these values are its last known state.">
          engine stale
        </span>
      )}
      <StreamLight
        label="Ticks"
        ok={!!streams.market?.connected}
        warn={engineStale}
        lagging={false}
        detail={streams.market?.connected ? `${streams.market?.symbols ?? 0} sym` : 'down'}
        title="Per-symbol aggTrade stream — feeds the engine's trade decisions."
      />
      <StreamLight
        label="All-market"
        ok={!!streams.all_tickers?.connected}
        lagging={frameLagging && !frameDown}
        warn={frameDown}
        detail={typeof frameAge === 'number' ? `${frameAge}s` : streams.all_tickers?.connected ? 'live' : 'down'}
        title={"!miniTicker@arr stream — realtime screener/monitor prices for every pair." +
          (streams.all_tickers?.transport === 'rest'
            ? '\nFed via bulk-REST fallback (exchange frames dropped on this network path) — prices live and fresh.'
            : '')}
      />
      {streams.all_tickers?.transport === 'rest' && (
        <span
          className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-sky-500/15 text-sky-300 border border-sky-500/30"
          title="The exchange socket connects but this network path drops its data frames — the engine feeds prices via bulk-REST fallback instead. Fully functional, honestly labeled."
        >rest</span>
      )}
      <StreamLight
        label="Order API"
        ok={!!streams.order_api?.connected}
        warn={engineStale}
        detail={streams.order_api?.connected ? 'ws' : 'REST'}
        title="Authenticated WebSocket API session — order placement/cancellation."
      />
      <StreamLight
        label="User data"
        ok={!!streams.order_api?.user_stream}
        warn={engineStale}
        detail={streams.order_api?.user_stream ? 'fills • balances' : 'REST poll'}
        title="userDataStream.subscribe — event-driven fills (executionReport) and account balances (outboundAccountPosition)."
      />
    </div>
  );
};
