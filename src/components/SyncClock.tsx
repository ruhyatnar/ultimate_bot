import React, { useEffect, useState } from 'react';
import { Clock, CheckCircle2, AlertTriangle, WifiOff } from 'lucide-react';

interface SyncClockProps {
  /** Epoch ms from the engine's payload (`server_epoch_ms`) — engine-authoritative clock. */
  serverEpochMs?: number;
  /** Client Date.now() when the last engine payload arrived. */
  lastPayloadAt?: number;
  connected?: boolean;
  latencyMs?: number;
}

const timeFmt = (ms: number): string =>
  new Date(ms).toLocaleTimeString('en-GB', { hour12: false });

/**
 * Live clock proving engine <-> monitor sync:
 *  - Engine clock (extrapolated between 1s WS pushes) vs browser clock.
 *  - Clock-skew delta: |Δ| ≤ 2s = synced; bigger = VPS clock drift warning.
 *  - Data age: time since the last payload landed (LIVE / STALE).
 */
export const SyncClock: React.FC<SyncClockProps> = ({
  serverEpochMs,
  lastPayloadAt,
  connected = false,
  latencyMs
}) => {
  const [now, setNow] = useState<number>(() => Date.now());

  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  const haveEngineClock = typeof serverEpochMs === 'number' && typeof lastPayloadAt === 'number';
  const ageSec = lastPayloadAt ? Math.max(0, (now - lastPayloadAt) / 1000) : null;
  // Engine clock now ≈ engine epoch at payload time + elapsed client time since.
  const engineNow = haveEngineClock ? serverEpochMs! + (now - lastPayloadAt!) : null;
  const skewSec = engineNow !== null ? (engineNow - now) / 1000 : null;
  const skewOk = skewSec !== null && Math.abs(skewSec) <= 2;

  if (!connected) {
    return (
      <span
        title="No connection to the engine — the monitor is showing stale data."
        className="inline-flex items-center space-x-1 text-rose-300 bg-rose-950/60 border border-rose-800/60 px-2 py-0.5 rounded-full font-medium"
      >
        <WifiOff className="w-3 h-3" />
        <span className="font-mono">DISCONNECTED</span>
      </span>
    );
  }
  const ageColor = ageSec === null
    ? 'text-slate-400'
    : ageSec <= 3
      ? 'text-emerald-300'
      : ageSec <= 10
        ? 'text-amber-300'
        : 'text-rose-300';
  const ageLabel = ageSec === null
    ? '—'
    : ageSec <= 3
      ? 'LIVE'
      : `STALE ${Math.floor(ageSec)}s`;

  return (
    <span
      title={
        'Sync clock — engine time vs this browser.\n' +
        'Δ = clock skew between the VPS and your browser (±2s is normal).\n' +
        'Data age = time since the last engine snapshot arrived (WS pushes every 1s).'
      }
      className="inline-flex items-center space-x-1.5 sm:space-x-2 px-2 py-0.5 rounded-full border bg-slate-900/80 border-slate-700/70 font-mono text-[11px] whitespace-nowrap"
    >
      <Clock className="w-3 h-3 text-slate-400" />
      {engineNow !== null && (
        <span className="text-slate-200" title="Engine clock (VPS), shown in your timezone">
          {timeFmt(engineNow)}
        </span>
      )}
      {skewSec !== null && (
        <span
          className={`inline-flex items-center space-x-0.5 font-semibold ${
            skewOk ? 'text-emerald-400' : 'text-amber-400'
          }`}
          title={skewOk ? 'Engine and browser clocks agree' : 'VPS clock differs from your browser by more than 2s'}
        >
          {skewOk ? <CheckCircle2 className="w-3 h-3" /> : <AlertTriangle className="w-3 h-3" />}
          <span>Δ {skewSec >= 0 ? '+' : ''}{skewSec.toFixed(1)}s</span>
        </span>
      )}
      <span className={`font-semibold ${ageColor}`} title="Freshness of engine data">
        {ageLabel}
      </span>
      {latencyMs !== undefined && (
        <span className="text-slate-500" title="Last measured round-trip latency to the engine">
          {latencyMs}ms
        </span>
      )}
    </span>
  );
};
