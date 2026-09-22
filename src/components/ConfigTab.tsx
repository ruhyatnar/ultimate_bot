import React, { useState } from 'react';
import {
  Copy,
  Check,
  Zap,
  ShieldAlert,
  Layers,
  FileText,
  Send,
  Loader2,
  AlertTriangle,
  CheckCircle2,
  WifiOff
} from 'lucide-react';
import { BotConfig, PushResult } from '../types';
import { generateEnvString } from '../utils/envGenerator';

interface ConfigTabProps {
  config: BotConfig;
  onUpdateConfig: (newConfig: BotConfig) => void;
  onApplyPreset: (preset: BotConfig['preset']) => void;
  vpsConnected?: boolean;
  onPushToVps?: () => Promise<PushResult>;
}

export const ConfigTab: React.FC<ConfigTabProps> = ({
  config,
  onUpdateConfig,
  onApplyPreset,
  vpsConnected = false,
  onPushToVps
}) => {
  const [copied, setCopied] = useState(false);
  const [savedNotice, setSavedNotice] = useState(false);
  const [isPushing, setIsPushing] = useState(false);
  const [pushResult, setPushResult] = useState<PushResult | null>(null);

  const handlePushToVps = async () => {
    if (!onPushToVps) return;
    setIsPushing(true);
    setPushResult(null);
    try {
      setPushResult(await onPushToVps());
    } finally {
      setIsPushing(false);
    }
  };

  const handleCopyEnv = () => {
    navigator.clipboard.writeText(generateEnvString(config));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleChange = (key: keyof BotConfig, value: any) => {
    onUpdateConfig({ ...config, [key]: value });
    setSavedNotice(true);
    setTimeout(() => setSavedNotice(false), 1500);
  };

  return (
    <div className="space-y-6">
      {/* Preset Selector Banner */}
      <div className="bg-slate-800/80 border border-slate-700/60 rounded-xl p-5 shadow-sm">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <div className="flex items-center space-x-2">
              <Zap className="w-5 h-5 text-amber-400" />
              <h2 className="text-base font-bold text-white">Strategy Preset</h2>
              {savedNotice && (
                <span className="px-2 py-0.5 text-xs font-semibold rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                  Settings Updated
                </span>
              )}
            </div>
            <p className="text-xs text-slate-400 mt-1">
              The bot ships one proven strategy. It uses a daily EMA-{config.regimeEma} regime gate plus an RSI dip trigger, with a fixed %
              bracket and market exits.
            </p>
          </div>

          <button
            onClick={() => onApplyPreset('intraday_rsi')}
            className={`px-3.5 py-2 rounded-lg text-xs font-bold transition-all ${
              config.preset === 'intraday_rsi'
                ? 'bg-emerald-600 text-white shadow-lg shadow-emerald-600/30 border border-emerald-400'
                : 'bg-slate-900/80 text-slate-300 hover:bg-slate-700 border border-slate-700'
            }`}
          >
            🎯 intraday_rsi (5m exec / 1d regime) ★
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left: Parameter Form */}
        <div className="lg:col-span-7 space-y-5">
          {/* Signal engine tuning */}
          <div className="bg-slate-800/80 border border-slate-700/60 rounded-xl p-5 shadow-sm space-y-4">
            <h3 className="text-xs font-bold uppercase tracking-wider text-amber-400 flex items-center space-x-2">
              <Layers className="w-4 h-4" />
              <span>Signal Engine — Regime + RSI Dip</span>
            </h3>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs">
              <div>
                <label className="font-semibold text-slate-200 block mb-1">
                  Market ({config.market === 'futures' ? 'USDⓈ-M Futures' : 'Spot — proven'})
                </label>
                <select
                  value={config.market}
                  onChange={e => handleChange('market', e.target.value)}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 text-xs"
                >
                  <option value="spot">spot (proven)</option>
                  <option value="futures">futures (USDⓈ-M)</option>
                </select>
                {config.market === 'futures' && (
                  <p className="text-[10px] text-amber-400 mt-1">
                    Futures module is new: not yet backtested under leverage + funding. Leverage stays at
                    FUTURES_LEVERAGE (now {config.futuresLeverage}×, {config.futuresMarginType} margin).
                  </p>
                )}
              </div>

              {config.market === 'futures' && (
                <div>
                  <label className="font-semibold text-slate-200 block mb-1">
                    Funding Rate Cap ({(config.fundingRateMax * 100).toFixed(3)}% / 8h, 0 = off)
                  </label>
                  <input
                    type="number"
                    step="0.0001"
                    min="0"
                    max="0.01"
                    value={config.fundingRateMax}
                    onChange={e => handleChange('fundingRateMax', parseFloat(e.target.value) || 0)}
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs"
                  />
                  <p className="text-[10px] text-slate-500 mt-1">
                    Skips long entries on pairs whose live funding exceeds the cap — a long pays positive funding.
                  </p>
                </div>
              )}

              <div>
                <label className="font-semibold text-slate-200 block mb-1">
                  Soak Stall Alert ({config.soakStallSeconds}s)
                </label>
                <input
                  type="number"
                  step={60}
                  min={120}
                  max={3600}
                  value={config.soakStallSeconds}
                  onChange={e => handleChange('soakStallSeconds', parseInt(e.target.value, 10))}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs"
                />
                <p className="text-[10px] text-slate-500 mt-1">
                  Watchdog alerts when the futures soak's DB heartbeat is silent this long while PM2
                  says online. Applied on the next watchdog restart.
                </p>
                <label className="text-xs font-medium text-slate-300">
                  Ticker REST Fallback ({config.tickersRestFallbackSeconds}s)
                </label>
                <input
                  type="number"
                  step={1}
                  min={1}
                  max={60}
                  value={config.tickersRestFallbackSeconds}
                  onChange={e => handleChange('tickersRestFallbackSeconds', parseFloat(e.target.value))}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs"
                />
                <p className="text-[10px] text-slate-500 mt-1">
                  If the exchange delivers no all-market data frames, the engine refreshes screener
                  prices via bulk REST at this cadence instead. Lower = fresher prices, higher API weight.
                </p>
              </div>

              <div>
                <label className="font-semibold text-slate-200 block mb-1">Execution Timeframe</label>
                <select
                  value={config.timeframe}
                  onChange={e => handleChange('timeframe', e.target.value)}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 text-xs"
                >
                  {['1m', '3m', '5m', '15m', '30m', '1h'].map(tf => (
                    <option key={tf} value={tf}>{tf}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="font-semibold text-slate-200 block mb-1">Regime Timeframe</label>
                <select
                  value={config.mtfTimeframe}
                  onChange={e => handleChange('mtfTimeframe', e.target.value)}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 text-xs"
                >
                  {['1h', '4h', '1d'].map(tf => (
                    <option key={tf} value={tf}>{tf}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="font-semibold text-slate-200 block mb-1">
                  Regime EMA Span ({config.regimeEma})
                </label>
                <input
                  type="number"
                  step={5}
                  min={10}
                  max={200}
                  value={config.regimeEma}
                  onChange={e => handleChange('regimeEma', parseInt(e.target.value, 10))}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs"
                />
              </div>

              <div>
                <label className="font-semibold text-slate-200 block mb-1">
                  EMA Rising-Over Days ({config.regimeSlopeDays})
                </label>
                <input
                  type="number"
                  step={1}
                  min={1}
                  max={10}
                  value={config.regimeSlopeDays}
                  onChange={e => handleChange('regimeSlopeDays', parseInt(e.target.value, 10))}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs"
                />
              </div>

              <div>
                <label className="font-semibold text-slate-200 block mb-1">RSI Period ({config.rsiPeriod})</label>
                <input
                  type="number"
                  step={1}
                  min={2}
                  max={21}
                  value={config.rsiPeriod}
                  onChange={e => handleChange('rsiPeriod', parseInt(e.target.value, 10))}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs"
                />
              </div>

              <div>
                <label className="font-semibold text-slate-200 block mb-1">RSI Oversold Level ({config.rsiOversold})</label>
                <input
                  type="number"
                  step={1}
                  min={20}
                  max={50}
                  value={config.rsiOversold}
                  onChange={e => handleChange('rsiOversold', parseFloat(e.target.value))}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs"
                />
              </div>

              <div>
                <label className="font-semibold text-slate-200 block mb-1">RSI Floor Gate ({config.entryRsiMin === 0 ? 'off' : config.entryRsiMin})</label>
                <input
                  type="number"
                  step={1}
                  min={0}
                  max={60}
                  value={config.entryRsiMin}
                  onChange={e => handleChange('entryRsiMin', parseFloat(e.target.value))}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs"
                />
              </div>

              <div>
                <label className="font-semibold text-slate-200 block mb-1">
                  Extension Gate ({config.entryMaxExtAtr === 0 ? 'off' : `${config.entryMaxExtAtr}× ATR`})
                </label>
                <input
                  type="number"
                  step="0.25"
                  min="0"
                  max="5"
                  value={config.entryMaxExtAtr}
                  onChange={e => handleChange('entryMaxExtAtr', parseFloat(e.target.value))}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs"
                />
                <p className="text-[10px] text-slate-500 mt-1">Skip entries when price is stretched &gt; N×ATR above the EMA. 0 = off.</p>
              </div>

              <div>
                <label className="font-semibold text-slate-200 block mb-1">Extension EMA Span ({config.entryExtEma})</label>
                <input
                  type="number"
                  step={1}
                  min={5}
                  max={100}
                  value={config.entryExtEma}
                  onChange={e => handleChange('entryExtEma', parseInt(e.target.value, 10))}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs"
                />
              </div>

              <div>
                <label className="font-semibold text-slate-200 block mb-1">
                  Volume Gate ({config.entryVolMult === 0 ? 'off' : `${config.entryVolMult}× avg`})
                </label>
                <input
                  type="number"
                  step="0.1"
                  min="0"
                  max="5"
                  value={config.entryVolMult}
                  onChange={e => handleChange('entryVolMult', parseFloat(e.target.value))}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs"
                />
                <p className="text-[10px] text-slate-500 mt-1">Require confirmation volume ≥ N× the rolling average. 0 = off.</p>
              </div>

              <div>
                <label className="font-semibold text-slate-200 block mb-1">Volume Lookback ({config.entryVolLookback} bars)</label>
                <input
                  type="number"
                  step={1}
                  min={5}
                  max={100}
                  value={config.entryVolLookback}
                  onChange={e => handleChange('entryVolLookback', parseInt(e.target.value, 10))}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs"
                />
              </div>

              <div>
                <label className="font-semibold text-slate-200 block mb-1">RSI Sampling Bucket</label>
                <select
                  value={config.rsiTimeframe}
                  onChange={e => {
                    const tf = e.target.value;
                    const ms = { '15m': 900000, '30m': 1800000, '1h': 3600000, '4h': 14400000, '1d': 86400000 }[tf] || 3600000;
                    onUpdateConfig({ ...config, rsiTimeframe: tf, rsiTimeframeMs: ms });
                  }}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 text-xs"
                >
                  {['15m', '30m', '1h', '4h', '1d'].map(tf => (
                    <option key={tf} value={tf}>{tf}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="font-semibold text-slate-200 block mb-1">
                  Max Entries / UTC Day ({config.maxTradesPerDay > 0 ? config.maxTradesPerDay : '∞'})
                </label>
                <input
                  type="number"
                  step={1}
                  min={0}
                  max={20}
                  value={config.maxTradesPerDay}
                  onChange={e => handleChange('maxTradesPerDay', parseInt(e.target.value, 10))}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs"
                />
              </div>

              <div className="sm:col-span-2 flex items-center space-x-2">
                <input
                  type="checkbox"
                  checked={config.entryRequireRsiRise2}
                  onChange={e => handleChange('entryRequireRsiRise2', e.target.checked)}
                  className="accent-amber-500"
                />
                <span className="text-[11px] text-slate-300">Require two consecutive rising RSI prints before entry</span>
              </div>

              <div className="sm:col-span-2">
                <label className="font-semibold text-slate-200 block mb-1">Signal Check Interval ({config.signalInterval}s)</label>
                <input
                  type="range"
                  min={3}
                  max={60}
                  step={1}
                  value={config.signalInterval}
                  onChange={e => handleChange('signalInterval', parseInt(e.target.value))}
                  className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-amber-500"
                />
              </div>

              <div className="sm:col-span-2">
                <label className="font-semibold text-slate-200 block mb-1">Static Symbols (comma separated)</label>
                <input
                  type="text"
                  value={config.staticSymbols.join(', ')}
                  onChange={e => handleChange('staticSymbols', e.target.value.split(',').map(s => s.trim().toUpperCase()).filter(Boolean))}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 focus:outline-none focus:border-amber-500 font-mono text-xs"
                />
              </div>
            </div>
          </div>

          {/* Risk & exit parameters */}
          <div className="bg-slate-800/80 border border-slate-700/60 rounded-xl p-5 shadow-sm space-y-4">
            <h3 className="text-xs font-bold uppercase tracking-wider text-rose-400 flex items-center space-x-2">
              <ShieldAlert className="w-4 h-4" />
              <span>Risk Management &amp; Exits</span>
            </h3>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs">
              <div>
                <label className="font-semibold text-slate-200 block mb-1">
                  Stop Loss ({(config.slPercent * 100).toFixed(2)}% below entry)
                </label>
                <input
                  type="number"
                  step="0.001"
                  min="0.004"
                  max="0.1"
                  value={config.slPercent}
                  onChange={e => handleChange('slPercent', parseFloat(e.target.value))}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs"
                />
              </div>

              <div>
                <label className="font-semibold text-slate-200 block mb-1">
                  Take Profit ({(config.tpPercent * 100).toFixed(2)}% above entry)
                </label>
                <input
                  type="number"
                  step="0.001"
                  min="0.01"
                  max="0.2"
                  value={config.tpPercent}
                  onChange={e => handleChange('tpPercent', parseFloat(e.target.value))}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs"
                />
              </div>

              <div>
                <label className="font-semibold text-slate-200 block mb-1">
                  Trailing Activate ({(config.trailingStopActivate * 100).toFixed(1)}%)
                </label>
                <input
                  type="number"
                  step="0.005"
                  min="0.005"
                  max="0.2"
                  value={config.trailingStopActivate}
                  onChange={e => handleChange('trailingStopActivate', parseFloat(e.target.value))}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs"
                />
              </div>

              <div>
                <label className="font-semibold text-slate-200 block mb-1">
                  Trailing ATR Multiplier ({config.trailingAtrMultiplier === 0 ? '% callback mode' : `${config.trailingAtrMultiplier}x ATR`})
                </label>
                <input
                  type="number"
                  step="0.5"
                  min="0"
                  max="6"
                  value={config.trailingAtrMultiplier}
                  onChange={e => handleChange('trailingAtrMultiplier', parseFloat(e.target.value))}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs"
                />
              </div>

              <div>
                <label className="font-semibold text-slate-200 block mb-1">
                  ATR Stop ({config.slAtrMultiplier === 0 ? 'off — fixed %' : `${config.slAtrMultiplier}× ATR`})
                </label>
                <input
                  type="number"
                  step="0.25"
                  min="0"
                  max="6"
                  value={config.slAtrMultiplier}
                  onChange={e => handleChange('slAtrMultiplier', parseFloat(e.target.value))}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs"
                />
                <p className="text-[10px] text-slate-500 mt-1">Volatility-adaptive stop: N×ATR replaces the fixed % when &gt; 0. Proven OFF.</p>
              </div>

              <div>
                <label className="font-semibold text-slate-200 block mb-1">
                  ATR Stop Cap ({(config.slAtrMaxPercent * 100).toFixed(1)}%)
                </label>
                <input
                  type="number"
                  step="0.002"
                  min="0.005"
                  max="0.1"
                  value={config.slAtrMaxPercent}
                  onChange={e => handleChange('slAtrMaxPercent', parseFloat(e.target.value))}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs"
                />
              </div>

              <div>
                <label className="font-semibold text-slate-200 block mb-1">
                  Trailing Callback ({(config.trailingStopCallback * 100).toFixed(1)}%)
                </label>
                <input
                  type="number"
                  step="0.001"
                  min="0.001"
                  max="0.1"
                  value={config.trailingStopCallback}
                  onChange={e => handleChange('trailingStopCallback', parseFloat(e.target.value))}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs"
                />
              </div>

              <div>
                <label className="font-semibold text-slate-200 block mb-1">
                  Max Daily Drawdown ({(config.maxDailyDrawdown * 100).toFixed(1)}%)
                </label>
                <input
                  type="number"
                  step="0.01"
                  min="0.01"
                  max="0.25"
                  value={config.maxDailyDrawdown}
                  onChange={e => handleChange('maxDailyDrawdown', parseFloat(e.target.value))}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs"
                />
              </div>

              <div>
                <label className="font-semibold text-slate-200 block mb-1">
                  Max Position Allocation ({(config.maxSymbolAllocationPercent * 100).toFixed(0)}%)
                </label>
                <input
                  type="number"
                  step="0.05"
                  min="0.05"
                  max="1.0"
                  value={config.maxSymbolAllocationPercent}
                  onChange={e => handleChange('maxSymbolAllocationPercent', parseFloat(e.target.value))}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs"
                />
                <p className="text-[10px] text-slate-500 mt-1">
                  Keep at 1.0 on small accounts — 0.2 × a $22 balance is below the exchange's $5 minimum notional.
                </p>
              </div>

              <div className="flex items-center space-x-2">
                <input
                  type="checkbox"
                  checked={config.closeAtUtcDayEnd}
                  onChange={e => handleChange('closeAtUtcDayEnd', e.target.checked)}
                  className="accent-amber-500"
                />
                <span className="text-[11px] text-slate-300">Force-close positions at the UTC day end</span>
              </div>

              <div className="flex items-center space-x-2">
                <input
                  type="checkbox"
                  checked={config.breakevenEnabled}
                  onChange={e => handleChange('breakevenEnabled', e.target.checked)}
                  className="accent-amber-500"
                />
                <span className="text-[11px] text-slate-300">Breakeven lock — proven OFF for this strategy</span>
              </div>

              <div>
                <label className="font-semibold text-slate-200 block mb-1">
                  Breakeven Trigger ({(config.breakevenTrigger * 100).toFixed(1)}% profit)
                </label>
                <input
                  type="number"
                  step="0.001"
                  min="0.002"
                  max="0.1"
                  value={config.breakevenTrigger}
                  onChange={e => handleChange('breakevenTrigger', parseFloat(e.target.value))}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs"
                />
              </div>

              <div>
                <label className="font-semibold text-slate-200 block mb-1">
                  Breakeven Offset ({(config.breakevenOffset * 100).toFixed(2)}% above entry)
                </label>
                <input
                  type="number"
                  step="0.0005"
                  min="0"
                  max="0.02"
                  value={config.breakevenOffset}
                  onChange={e => handleChange('breakevenOffset', parseFloat(e.target.value))}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs"
                />
                <p className="text-[10px] text-slate-500 mt-1">Once armed, the stop locks entry + offset — 0.25% covers the round-trip fee.</p>
              </div>
            </div>
          </div>
        </div>

        {/* Right: Live .env Output */}
        <div className="lg:col-span-5 bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-xl flex flex-col h-[620px]">
          <div className="p-4 bg-slate-800/60 border-b border-slate-800 flex items-center justify-between">
            <div className="flex items-center space-x-2">
              <FileText className="w-4 h-4 text-amber-400" />
              <span className="text-xs font-bold text-white font-mono">.env (Live Generator)</span>
            </div>

            <div className="flex items-center space-x-1.5">
              {onPushToVps && (
                <button
                  id="push-config-to-vps-btn"
                  onClick={handlePushToVps}
                  disabled={isPushing || !vpsConnected}
                  title={vpsConnected ? 'Apply this tuned configuration to the engine' : 'Connect to the engine web monitor first (Connection Bar)'}
                  className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-md text-xs font-semibold transition-colors ${
                    vpsConnected
                      ? 'bg-emerald-600 text-white hover:bg-emerald-500'
                      : 'bg-slate-800 text-slate-500 cursor-not-allowed'
                  }`}
                >
                  {isPushing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : vpsConnected ? <Send className="w-3.5 h-3.5" /> : <WifiOff className="w-3.5 h-3.5" />}
                  <span>{isPushing ? 'Applying...' : vpsConnected ? 'Push to Engine' : 'Engine Offline'}</span>
                </button>
              )}
              <button
                onClick={handleCopyEnv}
                className="flex items-center space-x-1.5 px-3 py-1.5 rounded-md text-xs font-semibold bg-slate-700 text-slate-200 hover:bg-slate-600 transition-colors"
              >
                {copied ? (
                  <>
                    <Check className="w-3.5 h-3.5 text-emerald-400" />
                    <span>Copied</span>
                  </>
                ) : (
                  <>
                    <Copy className="w-3.5 h-3.5" />
                    <span>Copy .env</span>
                  </>
                )}
              </button>
            </div>
          </div>

          {pushResult && (
            <div className={`p-3 border-b text-[11px] flex items-start space-x-2 ${
              pushResult.ok
                ? 'bg-emerald-950/60 border-emerald-800 text-emerald-300'
                : 'bg-rose-950/60 border-rose-800 text-rose-300'
            }`}>
              {pushResult.ok
                ? <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
                : <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />}
              <span>{pushResult.message}</span>
            </div>
          )}

          <div className="flex-1 p-4 overflow-auto font-mono text-xs text-slate-300 bg-slate-950/70 leading-relaxed scrollbar-thin">
            <pre>
              <code>{generateEnvString(config)}</code>
            </pre>
          </div>
        </div>
      </div>
    </div>
  );
};
