import React from 'react';
import { Activity, Signal, Radio, Wifi, WifiOff, PauseCircle, ShieldAlert, Gauge, Clock } from 'lucide-react';
import { WsStreams, EngineRiskState } from '../types';
import { StreamLights } from './StatusCards';
import { ENGINE_FRESHNESS_S, engineIsStale } from '../utils/freshness';

interface EngineHealthCardProps {
  streams: WsStreams | null;
  connected: boolean;
  engineRunning: boolean;
  controlPaused: boolean;
  engineRisk: EngineRiskState | null;
  breakerTripped: boolean;
  /**
   * Decision-loop cadence to display. The engine's OWN published interval when
   * its heartbeat carries one, else the configured SIGNAL_INTERVAL — see
   * `intervalFromEngine`, which says which of the two this is.
   */
  scanInterval: number;
  /** True when `scanInterval` came from the engine heartbeat rather than config. */
  intervalFromEngine: boolean;
  /** Age of the engine's decision-loop heartbeat (falls back to the newest signal snapshot). */
  loopAgeS: number | null;
  /** True when the age above came from the engine's heartbeat rather than the snapshot heuristic. */
  loopFromHeartbeat: boolean;
  /** Engine's acknowledgement of the pause request; null = engine published no heartbeat. */
  pauseApplied: boolean | null;
}

/**
 * Engine Health — the operator's "is everything actually working?" panel.
 * One card answers four questions at a glance:
 *   1. Is the engine process up and accepting entries?   (Process row)
 *   2. Is the monitor's data feed live?                  (Monitor link row)
 *   3. Are all four realtime transports flowing?         (StreamLights strip)
 *   4. Is the engine's decision loop still iterating?     (Decision loop row —
 *      age of the engine's per-iteration heartbeat; a frozen age = wedged loop)
 * Any red item names exactly what to fix.
 */
export const EngineHealthCard: React.FC<EngineHealthCardProps> = ({
  streams,
  connected,
  engineRunning,
  controlPaused,
  engineRisk,
  breakerTripped,
  scanInterval,
  intervalFromEngine,
  loopAgeS,
  loopFromHeartbeat,
  pauseApplied
}) => {
  // One freshness budget for the whole app (utils/freshness) — the stream lights
  // and this card must never disagree about the same payload.
  const engineStale = engineIsStale(streams?.engine_age_s);
  // Streak cooldowns block entries but leave open positions managed, so the row
  // says so instead of leaving the chip to imply the breaker is the only guard.
  const activeCooldownMap = (engineRisk?.active_cooldowns ?? {}) as Record<
    string,
    { until: number; kind: string }
  >;
  const activeSymbolCooldowns = Object.entries(activeCooldownMap)
    .filter(([, v]) => (v?.until ?? 0) * 1000 > Date.now());
  const cooldownActive =
    (engineRisk?.cooldown_until ?? 0) * 1000 > Date.now() || activeSymbolCooldowns.length > 0;
  const loopAge = typeof loopAgeS === 'number' ? loopAgeS : null;
  const loopOk = loopAge !== null && loopAge <= Math.max(60, scanInterval * 6);
  const loopTone = loopAge === null ? 'text-slate-400' : loopOk ? 'text-emerald-300' : 'text-amber-300';
  // Engine's measured clock offset vs the exchange (null = never measured:
  // paper mode, no Ed25519 key, or an older engine build).
  const clockOffsetMs = typeof streams?.order_api?.time_offset_ms === 'number' ? streams.order_api.time_offset_ms : null;
  const clockFresh = streams?.order_api?.clock_synced === true;
  const clockOk = clockFresh && Math.abs(clockOffsetMs ?? 0) < 2500; // recvWindow is 5s
  const clockTone = clockOffsetMs === null ? 'text-slate-400' : clockOk ? 'text-emerald-300' : 'text-amber-300';

  const Row: React.FC<{ icon: React.ReactNode; label: string; ok: boolean | null; children: React.ReactNode; title?: string }> =
    ({ icon, label, ok, children, title }) => (
      <div title={title} className="flex items-center justify-between gap-3 rounded-lg border border-slate-700/50 bg-slate-900/50 px-3 py-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="shrink-0 text-slate-400">{icon}</span>
          <span className="text-xs font-medium text-slate-300">{label}</span>
        </div>
        <div className="flex items-center gap-2 text-right">
          {children}
          <span
            className={`inline-block h-2 w-2 shrink-0 rounded-full ${
              ok === null ? 'bg-slate-600' : ok ? 'bg-emerald-400' : 'bg-rose-400'
            }${ok === true ? ' animate-pulse' : ''}`}
          />
        </div>
      </div>
    );

  return (
    <section className="card p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-sm font-bold text-white">
          <Activity className="h-4 w-4 text-emerald-400" />
          Engine Health
        </h2>
        <span className="text-[10px] text-slate-500">engine-published · live</span>
      </div>

      <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
        <Row
          icon={<Gauge className="h-3.5 w-3.5" />}
          label="Process"
          ok={engineRunning && !engineStale}
          title={
            !engineRunning
              ? 'The engine\'s own process status (/api/status `process`) does not report RUNNING — it is stopped or failed to start.'
              : engineStale
                ? `The process reports RUNNING but its health snapshot is older than ${ENGINE_FRESHNESS_S}s — it may be hung mid-iteration (a wedged loop keeps the process alive).`
                : 'The engine reports RUNNING and its health snapshot is fresh.'
          }
        >
          <span className={`num text-xs font-bold ${engineRunning ? 'text-emerald-300' : 'text-rose-300'}`}>
            {engineRunning ? 'RUNNING' : 'DOWN'}
          </span>
          {controlPaused && (
            <span
              className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-semibold ${
                pauseApplied === false
                  ? 'border-slate-500/40 bg-slate-500/10 text-slate-300'
                  : 'border-amber-500/40 bg-amber-500/10 text-amber-300'
              }`}
              title={
                pauseApplied === false
                  ? 'The pause is written to the control file but the engine has not acknowledged it yet — it applies at the top of the next loop iteration.'
                  : 'The engine has applied the pause: new entries are blocked, open positions stay managed.'
              }
            >
              <PauseCircle className="h-3 w-3" />{' '}
              {pauseApplied === false ? 'pause requested…' : 'entries paused'}
            </span>
          )}
          {breakerTripped && (
            <span className="inline-flex items-center gap-1 rounded border border-rose-500/40 bg-rose-500/10 px-1.5 py-0.5 text-[10px] font-semibold text-rose-300">
              <ShieldAlert className="h-3 w-3" /> breaker
            </span>
          )}
        </Row>

        <Row
          icon={connected ? <Wifi className="h-3.5 w-3.5" /> : <WifiOff className="h-3.5 w-3.5" />}
          label="Monitor link"
          ok={connected}
          title="The monitor's connection to status.py (/api/status + /ws push)"
        >
          <span className={`num text-xs font-bold ${connected ? 'text-emerald-300' : 'text-rose-300'}`}>
            {connected ? 'SYNCED' : 'OFFLINE'}
          </span>
          {typeof streams?.engine_age_s === 'number' && !engineStale && (
            <span className="num text-[10px] text-slate-500">{streams.engine_age_s.toFixed(0)}s ago</span>
          )}
        </Row>

        <Row
          icon={<Clock className="h-3.5 w-3.5" />}
          label="Clock sync (Binance)"
          ok={clockOffsetMs === null ? null : clockOk}
          title="The engine's measured clock offset vs the exchange — every signed request timestamp is compensated by this amount (live WS API measurement; falls back to the REST client's server-time sync in paper mode). Amber means the measurement is stale or drift grew beyond ~2.5s: signed orders risk -1021 timestamp rejections."
        >
          <span className={`num text-xs font-bold ${clockTone}`}>
            {clockOffsetMs === null ? 'no data' : `${clockOffsetMs > 0 ? '+' : ''}${clockOffsetMs}ms`}
          </span>
          {clockOffsetMs !== null && !clockFresh && (
            <span className="text-[10px] font-semibold text-amber-300">stale</span>
          )}
        </Row>

        <div className="lg:col-span-2">
          <div className="mb-1.5 flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
            <Radio className="h-3 w-3" /> Realtime transports
          </div>
          <StreamLights streams={streams} connected={connected} />
        </div>

        <Row
          icon={<Signal className="h-3.5 w-3.5" />}
          label="Decision loop"
          ok={loopAge === null ? null : loopOk}
          title={
            loopFromHeartbeat
              ? `Age of the engine's decision-loop heartbeat, written every iteration on every path (trading, paused, health-pause). The loop runs every ${scanInterval}s${
                  intervalFromEngine
                    ? ' (the engine\'s own published cadence)'
                    : ' (configured SIGNAL_INTERVAL — this engine publishes no cadence)'
                } — a frozen age means the loop itself is wedged.`
              : `Age of the engine's last per-symbol signal snapshot (no heartbeat published by this build). It only advances when a symbol passes every gate, so it can read old while the engine is healthy — pausing or a full book freezes it.`
          }
        >
          <span className={`num text-xs font-bold ${loopTone}`}>
            {loopAge === null ? 'no data' : `${loopAge < 90 ? `${Math.round(loopAge)}s` : `${Math.round(loopAge / 60)}m`} ago`}
          </span>
          <span
            className="num text-[10px] text-slate-500"
            title={intervalFromEngine
              ? "The engine's own published loop interval"
              : 'Configured SIGNAL_INTERVAL — the engine publishes no interval'}
          >
            every {scanInterval}s
          </span>
        </Row>

        <Row
          icon={<Activity className="h-3.5 w-3.5" />}
          label="Risk guardrails"
          ok={engineRisk ? !breakerTripped : null}
          title={
            engineRisk
              ? 'The engine\'s own guardrails: the daily-drawdown circuit breaker (armed / tripped) and any active streak cooldown. A cooldown blocks NEW entries but keeps managing open positions.'
              : 'The engine has not published a risk snapshot yet (engine_risk): breaker and cooldown state are UNKNOWN, not clear.'
          }
        >
          <span className={`text-xs font-bold ${breakerTripped ? 'text-rose-300' : 'text-emerald-300'}`}>
            {engineRisk
              ? breakerTripped ? 'BREAKER TRIPPED' : 'armed'
              : 'no data'}
          </span>
          {engineRisk && cooldownActive && (
            <span
              className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-semibold ${
                engineRisk.cooldown_kind === 'win'
                  ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
                  : 'border-amber-500/40 bg-amber-500/10 text-amber-300'
              }`}
              title={
                `Entries blocked by the ${engineRisk.cooldown_kind === 'win' ? 'win' : 'loss'}-streak cooldown${
                  activeSymbolCooldowns.length > 0
                    ? ` — symbols: ${activeSymbolCooldowns.map(([s]) => s).join(', ')}`
                    : ''
                }. Open positions stay managed.`
              }
            >
              <PauseCircle className="h-3 w-3" />{' '}
              {engineRisk.cooldown_kind === 'win' ? 'win cooldown' : 'loss cooldown'}
            </span>
          )}
        </Row>
      </div>
    </section>
  );
};
