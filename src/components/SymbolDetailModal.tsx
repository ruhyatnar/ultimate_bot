import React from 'react';
import {
  X,
  Activity,
  Crosshair,
  Gauge,
  TrendingUp,
  CheckCircle2,
  XCircle
} from 'lucide-react';
import { MarketSymbolData, BotConfig } from '../types';
import { effectiveAllocation, MIN_NOTIONAL_USDT } from '../utils/envGenerator';

interface SymbolDetailModalProps {
  symbolData: MarketSymbolData;
  config: BotConfig;
  equity: number;
  onClose: () => void;
}

export const SymbolDetailModal: React.FC<SymbolDetailModalProps> = ({
  symbolData,
  config,
  equity,
  onClose
}) => {
  const s = symbolData.signal;
  const price = symbolData.price;
  const regimeUp = s.regime === 'UP';
  const isBuy = s.trigger && s.signal === 'BUY';
  const rsiKnown = s.rsi !== null && s.rsi !== undefined;

  // Fixed-% bracket — the exact math trade_logic._compute_bracket applies.
  const stopLoss = price > 0 ? price * (1 - config.slPercent) : 0;
  let takeProfit = price > 0 ? price * (1 + config.tpPercent) : 0;
  const minTpDist = price * config.minTpPercent;
  if (takeProfit - price < minTpDist) takeProfit = price + minTpDist;

  const maxAlloc = Math.round(effectiveAllocation(config, equity));
  const estimatedQuantity = price > 0 ? maxAlloc / price : 0;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm animate-in fade-in">
      <div className="bg-slate-900 border border-slate-700/80 rounded-2xl w-full max-w-2xl overflow-hidden shadow-2xl">
        {/* Modal Header */}
        <div className="p-5 border-b border-slate-800 flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <div className="w-10 h-10 rounded-xl bg-indigo-500/10 border border-indigo-500/30 flex items-center justify-center text-indigo-400 font-bold text-base">
              {symbolData.symbol.substring(0, 3)}
            </div>
            <div>
              <div className="flex items-center space-x-2">
                <h3 className="font-bold text-lg text-white">{symbolData.symbol}</h3>
                <span className={`px-2 py-0.5 text-xs font-semibold rounded ${
                  symbolData.priceChange24h >= 0 ? 'bg-emerald-500/10 text-emerald-400' : 'bg-rose-500/10 text-rose-400'
                }`}>
                  {symbolData.priceChange24h >= 0 ? '+' : ''}{symbolData.priceChange24h.toFixed(2)}%
                </span>
              </div>
              <p className="text-xs text-slate-400">{symbolData.name} • Binance Spot Market</p>
            </div>
          </div>

          <div className="flex items-center space-x-4">
            <div className="text-right">
              <div className="text-xs text-slate-400">Current Market Price</div>
              <div className="text-lg font-mono font-bold text-slate-100">
                {price > 0 ? `$${price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: price < 10 ? 6 : 2 })}` : '—'}
              </div>
            </div>
            <button
              id="close-modal-btn"
              onClick={onClose}
              className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Modal Body */}
        <div className="p-5 space-y-5 max-h-[80vh] overflow-y-auto">
          {/* Engine signal summary */}
          <div className={`p-4 rounded-xl border ${
            isBuy
              ? 'bg-emerald-950/30 border-emerald-500/40 text-emerald-200'
              : 'bg-slate-800/60 border-slate-700/60 text-slate-300'
          }`}>
            <div className="flex items-center justify-between">
              <div>
                <div className="flex items-center space-x-2">
                  <span className="text-xs uppercase tracking-wider font-semibold text-slate-400">Engine Signal</span>
                  <span className={`px-2 py-0.5 rounded text-xs font-bold ${
                    isBuy
                      ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                      : regimeUp
                        ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30'
                        : 'bg-rose-500/20 text-rose-300 border border-rose-500/30'
                  }`}>
                    {isBuy ? 'READY TO BUY' : regimeUp ? 'REGIME UP — WAITING FOR DIP' : `REGIME ${s.regime}`}
                  </span>
                </div>
                <p className="text-xs text-slate-300 mt-1 max-w-md">{s.reason || 'No reason published yet.'}</p>
              </div>
              <div className="text-right shrink-0 ml-4">
                <div className="text-[10px] text-slate-400">RSI({s.rsi_period ?? config.rsiPeriod})</div>
                <div className="text-3xl font-bold font-mono text-slate-100">
                  {rsiKnown ? (s.rsi as number).toFixed(1) : '—'}
                </div>
                <div className="text-[10px] text-slate-400">&lt; {s.oversold ?? config.rsiOversold} = dip</div>
              </div>
            </div>
          </div>

          {/* Gate breakdown */}
          <div>
            <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-3 flex items-center space-x-1.5">
              <Activity className="w-3.5 h-3.5 text-indigo-400" />
              <span>Signal Gate Breakdown</span>
            </h4>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 text-xs">
              <div className="bg-slate-800/60 p-3 rounded-lg border border-slate-700/50 flex items-center justify-between">
                <div>
                  <div className="font-semibold text-slate-200">1. Daily Regime EMA-{s.regime_ema ?? config.regimeEma}</div>
                  <div className="text-[11px] text-slate-400 mt-0.5">
                    Close vs EMA{s.regime_price !== undefined && s.regime_ema_value !== undefined
                      ? ` • ${s.regime_price.toPrecision(6)} / ${s.regime_ema_value.toPrecision(6)}`
                      : ''}
                  </div>
                </div>
                {regimeUp ? (
                  <span className="text-emerald-400 flex items-center space-x-1 font-bold">
                    <CheckCircle2 className="w-4 h-4" /><span>UP</span>
                  </span>
                ) : (
                  <span className="text-slate-400 flex items-center space-x-1 font-bold">
                    <XCircle className="w-4 h-4 text-slate-500" /><span>{s.regime}</span>
                  </span>
                )}
              </div>

              <div className="bg-slate-800/60 p-3 rounded-lg border border-slate-700/50 flex items-center justify-between">
                <div>
                  <div className="font-semibold text-slate-200">2. RSI Dip &amp; Turn</div>
                  <div className="text-[11px] text-slate-400 mt-0.5">
                    {rsiKnown ? `now ${(s.rsi as number).toFixed(2)}` : 'not ready'}
                    {s.rsi_prev !== null && s.rsi_prev !== undefined ? ` • prev ${s.rsi_prev}` : ''}
                  </div>
                </div>
                {s.trigger ? (
                  <span className="text-emerald-400 flex items-center space-x-1 font-bold">
                    <CheckCircle2 className="w-4 h-4" /><span>Trigger</span>
                  </span>
                ) : (
                  <span className="text-slate-400 flex items-center space-x-1 font-bold">
                    <XCircle className="w-4 h-4 text-slate-500" /><span>No dip</span>
                  </span>
                )}
              </div>

              <div className="bg-slate-800/60 p-3 rounded-lg border border-slate-700/50 flex items-center justify-between sm:col-span-2">
                <div>
                  <div className="font-semibold text-slate-200 flex items-center gap-1.5">
                    <Gauge className="w-3.5 h-3.5 text-amber-400" />
                    RSI source: {config.rsiPeriod}-period Wilder RSI on {config.timeframe} closes, read at each closed {s.rsi_timeframe ?? config.rsiTimeframe} bucket
                  </div>
                  <div className="text-[11px] text-slate-400 mt-0.5">
                    Exec timeout: {Math.round(config.maxHoldTime / 3600)}h • EOD close {config.closeAtUtcDayEnd ? 'ON' : 'OFF'} • breakeven {config.breakevenEnabled ? 'ON' : 'OFF'}
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Bracket levels */}
          <div className="bg-slate-800/40 p-4 rounded-xl border border-slate-700/60 space-y-3">
            <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-400 flex items-center space-x-1.5">
              <Crosshair className="w-3.5 h-3.5 text-amber-400" />
              <span>Fixed % Bracket &amp; Sizing</span>
            </h4>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs font-mono">
              <div className="bg-slate-900/70 p-2.5 rounded-lg border border-slate-800">
                <span className="text-slate-400 block text-[10px]">Entry (market)</span>
                <span className="text-slate-100 font-bold">{price > 0 ? `$${price.toFixed(price < 10 ? 6 : 2)}` : '—'}</span>
              </div>
              <div className="bg-slate-900/70 p-2.5 rounded-lg border border-slate-800">
                <span className="text-rose-400 block text-[10px]">Stop -{(config.slPercent * 100).toFixed(2)}%</span>
                <span className="text-rose-400 font-bold">{stopLoss > 0 ? `$${stopLoss.toFixed(stopLoss < 10 ? 6 : 2)}` : '—'}</span>
              </div>
              <div className="bg-slate-900/70 p-2.5 rounded-lg border border-slate-800">
                <span className="text-emerald-400 block text-[10px]">Take Profit +{(config.tpPercent * 100).toFixed(2)}%</span>
                <span className="text-emerald-400 font-bold">{takeProfit > 0 ? `$${takeProfit.toFixed(takeProfit < 10 ? 6 : 2)}` : '—'}</span>
              </div>
              <div className="bg-slate-900/70 p-2.5 rounded-lg border border-slate-800">
                <span className="text-amber-400 block text-[10px]">Equity deploy cap</span>
                <span className="text-amber-400 font-bold">${maxAlloc}</span>
              </div>
            </div>

            <div className="text-[11px] text-slate-400">
              Estimated size ≈ <strong className="text-slate-200 font-mono">{estimatedQuantity.toFixed(estimatedQuantity < 1 ? 6 : 4)}</strong>{' '}
              {symbolData.symbol.replace('USDT', '')} at the current price. Sizing is owned by the engine (1% fixed-fractional risk, capped by
              notional limits).
              {maxAlloc < MIN_NOTIONAL_USDT && (
                <span className="text-amber-400"> Allocation is below Binance NOTIONAL.minNotional (${MIN_NOTIONAL_USDT}) — entries are skipped.</span>
              )}
            </div>
          </div>

          <div className="flex items-start space-x-1.5 text-[11px] text-slate-500">
            <TrendingUp className="w-3.5 h-3.5 mt-0.5 shrink-0" />
            <span>
              Entries and exits are executed by the engine, not this dashboard. Use the Trading Desk to close an open position or the engine
              pause control to block new entries.
            </span>
          </div>
        </div>
      </div>
    </div>
  );
};
