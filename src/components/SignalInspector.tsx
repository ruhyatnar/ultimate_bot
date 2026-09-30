import React from 'react';
import {
  Activity,
  TrendingUp,
  Gauge,
  ShieldCheck,
  Target,
  CheckCircle,
  XCircle,
  Info,
  TrendingDown
} from 'lucide-react';
import { MarketSymbolData, BotConfig } from '../types';

interface SignalInspectorProps {
  symbolsData: MarketSymbolData[];
  config: BotConfig;
}

/**
 * Renders the ENGINE's real per-symbol decision state (published to
 * risk_state -> /api/status `signal_state`). Nothing here is computed in the
 * browser: the regime gate, RSI dip read and trigger reason all come from the
 * exact code the engine trades with.
 */
export const SignalInspector: React.FC<SignalInspectorProps> = ({ symbolsData, config }) => {
  const allowShorts = config.allowShorts ?? false;

  return (
    <div className="space-y-6">
      {/* Intro Header */}
      <div className="bg-slate-800/80 border border-slate-700/60 rounded-xl p-5 shadow-sm">
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
          <div>
            <div className="flex items-center space-x-2">
              <Activity className="w-5 h-5 text-indigo-400" />
              <h2 className="text-base font-bold text-white">Engine Signal State — intraday_rsi</h2>
              <span className="px-2 py-0.5 text-xs font-semibold rounded-full bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
                {config.preset}
              </span>
            </div>
            <p className="text-xs text-slate-400 mt-1 max-w-3xl">
              A <strong>BUY</strong> fires when the <strong>daily EMA-{config.regimeEma} regime</strong> is up (close above the EMA and the EMA rising
              over {config.regimeSlopeDays} days) <strong>and</strong> RSI({config.rsiPeriod}) on {config.timeframe} closes, sampled at each
              closed {config.rsiTimeframe} bucket, dips below <strong>{config.rsiOversold}</strong> and turns up.
              {allowShorts && (
                <>
                  A <strong>SELL (short)</strong> fires in a confirmed downtrend (price below a falling EMA) when RSI rises above
                  <strong>{config.shortRsiOverbought ?? 60}</strong> and turns down. Exits are the fixed{' '}
                  <strong>-{(config.slPercent * 100).toFixed(2)}% / +{(config.tpPercent * 100).toFixed(2)}%</strong> bracket (long) and
                  <strong>+{(config.shortSlPercent * 100).toFixed(2)}% / -{(config.shortTpPercent * 100).toFixed(2)}%</strong> bracket (short).
                </>
              )}
              {!allowShorts && (
                <>
                  Exits are the fixed <strong>-{(config.slPercent * 100).toFixed(2)}% / +{(config.tpPercent * 100).toFixed(2)}%</strong> bracket.
                </>
              )}
            </p>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
            <div className="bg-slate-900/60 p-2.5 rounded-lg border border-slate-700/50">
              <span className="text-slate-400">Exec TF:</span>
              <div className="font-bold text-slate-100">{config.timeframe}</div>
            </div>
            <div className="bg-slate-900/60 p-2.5 rounded-lg border border-slate-700/50">
              <span className="text-slate-400">Regime TF:</span>
              <div className="font-bold text-slate-100">{config.mtfTimeframe}</div>
            </div>
            <div className="bg-slate-900/60 p-2.5 rounded-lg border border-slate-700/50">
              <span className="text-slate-400">RSI Bucket:</span>
              <div className="font-bold text-slate-100">{config.rsiTimeframe}</div>
            </div>
            <div className="bg-slate-900/60 p-2.5 rounded-lg border border-slate-700/50">
              <span className="text-slate-400">Entries/Day:</span>
              <div className="font-bold text-slate-100">{config.maxTradesPerDay > 0 ? config.maxTradesPerDay : '∞'}</div>
            </div>
          </div>
        </div>
      </div>

      {symbolsData.length === 0 && (
        <div className="py-10 px-4 text-center bg-slate-800/40 rounded-xl border border-dashed border-slate-700/60">
          <p className="text-sm font-medium text-slate-300">Waiting for the engine's first signal snapshot…</p>
          <p className="text-xs text-slate-500 mt-1">
            The engine publishes state after each scan pass ({config.signalInterval}s) once it has enough daily/execution candles.
          </p>
        </div>
      )}

      {/* Per-symbol signal cards */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {symbolsData.map(sym => {
          const s = sym.signal;
          const isBuy = s.trigger && s.signal === 'BUY';
          const isShort = s.trigger && s.signal === 'SELL';
          const isTrigger = isBuy || isShort;
          const regimeUp = s.regime === 'UP';
          const regimeDown = s.regime === 'DOWN';
          const rsiKnown = s.rsi !== null && s.rsi !== undefined;
          const oversold = s.oversold ?? config.rsiOversold;
          const overbought = s.overbought ?? config.shortRsiOverbought ?? 60;
          const rsiColor = !rsiKnown
            ? 'text-slate-300'
            : ((s.rsi as number) < oversold || (isShort && (s.rsi as number) > overbought))
              ? 'text-amber-300'
              : 'text-slate-200';

          return (
            <div
              key={sym.symbol}
              className={`rounded-xl border p-5 transition-all shadow-sm ${
                isTrigger
                  ? 'bg-gradient-to-b from-slate-800 to-emerald-950/20 border-emerald-500/40'
                  : 'bg-slate-800/90 border-slate-700/60'
              }`}
            >
              {/* Card Header */}
              <div className="flex items-center justify-between pb-4 border-b border-slate-700/60">
                <div>
                  <div className="flex items-center space-x-2.5">
                    <span className="text-lg font-bold text-white">{sym.symbol}</span>
                    <span className="font-mono text-sm text-slate-300 font-semibold">
                      {sym.price > 0 ? `$${sym.price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: sym.price < 10 ? 6 : 2 })}` : '—'}
                    </span>
                  </div>
                  <div className="text-xs text-slate-400 mt-0.5">{sym.name}</div>
                </div>

                <div className={`px-3 py-1 rounded-lg text-xs font-bold flex items-center space-x-1.5 ${
                  isTrigger
                    ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                    : 'bg-slate-700/50 text-slate-300 border border-slate-600/50'
                }`}>
                  {isBuy ? (
                    <>
                      <CheckCircle className="w-3.5 h-3.5 text-emerald-400" />
                      <span>BUY TRIGGER</span>
                    </>
                  ) : isShort ? (
                    <>
                      <TrendingDown className="w-3.5 h-3.5 text-rose-400" />
                      <span>SHORT TRIGGER</span>
                    </>
                  ) : (
                    <>
                      <XCircle className="w-3.5 h-3.5 text-slate-400" />
                      <span>NO ENTRY</span>
                    </>
                  )}
                </div>
              </div>

              {/* Gate breakdown — mirrors the engine's decide() order */}
              <div className="py-4 space-y-2.5">
                {/* 1. Daily regime gate */}
                <div className="flex items-center justify-between p-2.5 rounded-lg bg-slate-900/60 border border-slate-700/40 text-xs">
                  <div className="flex items-center space-x-2">
                    <TrendingUp className="w-4 h-4 text-indigo-400" />
                    <div>
                      <span className="font-semibold text-slate-200">1. Daily Regime (EMA-{s.regime_ema ?? config.regimeEma})</span>
                      <p className="text-[11px] text-slate-400">
                        {regimeDown
                          ? `Price below EMA and EMA falling over {config.regimeSlopeDays}d (short regime)`
                          : `Close above EMA and EMA rising over {config.regimeSlopeDays}d (long regime)`}
                        {s.regime_price !== undefined && s.regime_ema_value !== undefined
                          ? ` • ${s.regime_price.toFixed(6)} vs ${s.regime_ema_value.toFixed(6)}`
                          : ''}
                      </p>
                    </div>
                  </div>
                  <div className="text-right">
                    <span className={`px-2 py-0.5 rounded font-bold text-[11px] ${
                      regimeUp
                        ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                        : regimeDown
                          ? 'bg-rose-500/20 text-rose-300 border border-rose-500/30'
                          : 'bg-slate-700 text-slate-300'
                    }`}>
                      {s.regime}
                    </span>
                  </div>
                </div>

                {/* 2. RSI trigger */}
                <div className="flex items-center justify-between p-2.5 rounded-lg bg-slate-900/60 border border-slate-700/40 text-xs">
                  <div className="flex items-center space-x-2">
                    <Gauge className="w-4 h-4 text-amber-400" />
                    <div>
                      <span className="font-semibold text-slate-200">
                        {isShort || regimeDown
                          ? `2. RSI({s.rsi_period ?? config.rsiPeriod}) Rip on {s.rsi_timeframe ?? config.rsiTimeframe} (SHORT)`
                          : `2. RSI({s.rsi_period ?? config.rsiPeriod}) Dip on {s.rsi_timeframe ?? config.rsiTimeframe} (LONG)`}
                      </span>
                      <p className="text-[11px] text-slate-400">
                        {isShort || regimeDown
                          ? `Trigger when RSI > ${overbought} and falling`
                          : `Trigger when RSI < ${oversold} and rising`}
                        {s.rsi_prev !== null && s.rsi_prev !== undefined ? ` • prev ${s.rsi_prev}` : ''}
                      </p>
                    </div>
                  </div>
                  <div className="text-right">
                    <span className={`px-2 py-0.5 rounded font-bold font-mono text-[11px] ${rsiColor}`}>
                      {rsiKnown ? (s.rsi as number).toFixed(2) : '—'}
                    </span>
                  </div>
                </div>

                {/* 3. Bracket */}
                <div className="flex items-center justify-between p-2.5 rounded-lg bg-slate-900/60 border border-slate-700/40 text-xs">
                  <div className="flex items-center space-x-2">
                    <Target className="w-4 h-4 text-rose-400" />
                    <div>
                      <span className="font-semibold text-slate-200">3. Fixed % Bracket (market exits)</span>
                      <p className="text-[11px] text-slate-400">Both legs execute as MARKET (taker) orders</p>
                    </div>
                  </div>
                  <div className="text-right font-mono text-[11px]">
                    {isShort ? (
                      <>
                        <span className="text-rose-300">+{(config.shortSlPercent * 100).toFixed(2)}% SL</span>
                        <span className="text-slate-500"> / </span>
                        <span className="text-emerald-300">-{(config.shortTpPercent * 100).toFixed(2)}% TP</span>
                      </>
                    ) : (
                      <>
                        <span className="text-rose-300">-{(config.slPercent * 100).toFixed(2)}%</span>
                        <span className="text-slate-500"> / </span>
                        <span className="text-emerald-300">+{(config.tpPercent * 100).toFixed(2)}%</span>
                      </>
                    )}
                  </div>
                </div>

                {/* Disciplines */}
                <div className="flex items-center justify-between p-2.5 rounded-lg bg-slate-900/60 border border-slate-700/40 text-xs">
                  <div className="flex items-center space-x-2">
                    <ShieldCheck className="w-4 h-4 text-emerald-400" />
                    <div>
                      <span className="font-semibold text-slate-200">Discipline guards</span>
                      <p className="text-[11px] text-slate-400">
                        Max {config.maxTradesPerDay > 0 ? config.maxTradesPerDay : '∞'} entry/day •{' '}
                        {config.closeAtUtcDayEnd ? 'EOD force-close ON' : 'EOD force-close OFF'} •{' '}
                        breakeven {config.breakevenEnabled ? 'ON' : 'OFF'}
                      </p>
                    </div>
                  </div>
                </div>
              </div>

              {/* Engine reason */}
              <div className="pt-3 border-t border-slate-700/60 flex items-start space-x-1.5 text-xs">
                <Info className="w-4 h-4 text-slate-400 mt-0.5 shrink-0" />
                <div>
                  <span className="font-semibold text-slate-300">Engine reason: </span>
                  <span className={isTrigger ? 'text-emerald-300 font-medium' : 'text-slate-400'}>
                    {s.reason || '—'}
                  </span>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
