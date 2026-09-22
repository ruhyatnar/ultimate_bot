import React, { useState } from 'react';
import {
  Sliders,
  Clock,
  Percent,
  Gauge,
  ChevronDown,
  ChevronUp,
  AlertOctagon,
  Sparkles,
  Share2,
  PauseCircle,
  PlayCircle,
  Target
} from 'lucide-react';
import { BotConfig, StrategyPreset } from '../types';

interface TuningControlBarProps {
  config: BotConfig;
  onUpdateConfig: (newConfig: BotConfig) => void;
  onApplyPreset: (preset: StrategyPreset) => void;
  onCloseAllTrades: () => void;
  onOpenVpsSync: () => void;
  activeTradesCount: number;
  vpsConnected?: boolean;
  controlPaused?: boolean;
  onToggleVpsPause?: () => void;
}

export const TuningControlBar: React.FC<TuningControlBarProps> = ({
  config,
  onUpdateConfig,
  onApplyPreset,
  onCloseAllTrades,
  onOpenVpsSync,
  activeTradesCount,
  vpsConnected = false,
  controlPaused = false,
  onToggleVpsPause
}) => {
  const [isExpanded, setIsExpanded] = useState<boolean>(false);
  const [showPanicConfirm, setShowPanicConfirm] = useState<boolean>(false);

  return (
    <div className="bg-slate-900/90 border border-slate-700/70 rounded-xl shadow-lg p-4 transition-all">
      {/* Top Primary Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        {/* Left: Strategy Selector (the bot runs exactly one strategy) */}
        <div className="flex items-center space-x-2">
          <span className="text-xs font-semibold text-slate-400 flex items-center space-x-1.5">
            <Sliders className="w-3.5 h-3.5 text-amber-400" />
            <span>Active Strategy:</span>
          </span>
          <button
            id="preset-btn-intraday_rsi"
            onClick={() => onApplyPreset('intraday_rsi')}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold border transition-all ${
              config.preset === 'intraday_rsi'
                ? 'bg-amber-500 text-slate-950 border-amber-400 shadow-sm shadow-amber-500/30'
                : 'bg-slate-800 text-slate-300 border-slate-700 hover:text-white'
            }`}
          >
            🎯 intraday_rsi
          </button>
          <span className="text-[11px] text-slate-500 hidden md:inline">
            daily EMA-{config.regimeEma} regime + RSI({config.rsiPeriod}) dip • fixed {(-config.slPercent * 100).toFixed(2)}%/
            +{(config.tpPercent * 100).toFixed(2)}%
          </span>
        </div>

        {/* Center: Dynamic Mode Badge */}
        <div className="flex items-center space-x-2 text-xs">
          <button
            id="toggle-dynamic-symbols-btn"
            onClick={() => onUpdateConfig({ ...config, dynamicSymbols: !config.dynamicSymbols })}
            className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-lg border font-medium transition-all ${
              config.dynamicSymbols
                ? 'bg-indigo-500/20 text-indigo-300 border-indigo-500/40 shadow-sm shadow-indigo-500/10'
                : 'bg-slate-800 text-slate-400 border-slate-700 hover:text-slate-200'
            }`}
          >
            <Sparkles className="w-3.5 h-3.5 text-indigo-400" />
            <span>Dynamic Screener: <strong>{config.dynamicSymbols ? 'ON (Top Momentum)' : 'OFF (Static)'}</strong></span>
          </button>
        </div>

        {/* Right: Quick Actions & Expand */}
        <div className="flex items-center space-x-2 ml-auto">
          {vpsConnected && onToggleVpsPause && (
            <button
              id="vps-pause-toggle-btn"
              onClick={onToggleVpsPause}
              className={`flex items-center space-x-1 px-3 py-1.5 rounded-lg text-xs font-semibold border transition-all shadow-sm ${
                controlPaused
                  ? 'bg-amber-500/20 text-amber-300 border-amber-500/40 hover:bg-amber-500 hover:text-white'
                  : 'bg-slate-800 text-slate-300 border-slate-700 hover:bg-slate-700 hover:text-white'
              }`}
              title={controlPaused ? 'Resume the engine (entries re-enabled)' : 'Pause new entries on the engine (open positions stay managed)'}
            >
              {controlPaused ? <PlayCircle className="w-3.5 h-3.5" /> : <PauseCircle className="w-3.5 h-3.5" />}
              <span>{controlPaused ? 'Engine Paused — Resume' : 'Pause Engine'}</span>
            </button>
          )}

          <button
            id="open-vps-sync-btn"
            onClick={onOpenVpsSync}
            className="flex items-center space-x-1 px-3 py-1.5 rounded-lg text-xs font-semibold bg-purple-600/20 text-purple-300 border border-purple-500/40 hover:bg-purple-600 hover:text-white transition-all shadow-sm"
          >
            <Share2 className="w-3.5 h-3.5" />
            <span>Sync to engine .env</span>
          </button>

          {activeTradesCount > 0 && (
            <button
              id="panic-close-all-btn"
              onClick={() => setShowPanicConfirm(true)}
              className="flex items-center space-x-1 px-3 py-1.5 rounded-lg text-xs font-semibold bg-rose-600/20 text-rose-300 border border-rose-500/40 hover:bg-rose-600 hover:text-white transition-all shadow-sm"
            >
              <AlertOctagon className="w-3.5 h-3.5" />
              <span>Close All ({activeTradesCount})</span>
            </button>
          )}

          <button
            id="toggle-tuner-expand-btn"
            onClick={() => setIsExpanded(prev => !prev)}
            className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 border border-slate-700 transition-colors"
            title="Toggle Live Parameter Sliders"
          >
            {isExpanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
          </button>
        </div>
      </div>

      {/* Panic Confirmation Banner */}
      {showPanicConfirm && (
        <div className="mt-3 p-3 rounded-lg bg-rose-950/60 border border-rose-500/50 flex items-center justify-between text-xs animate-in fade-in">
          <div className="flex items-center space-x-2 text-rose-200">
            <AlertOctagon className="w-4 h-4 text-rose-400 shrink-0" />
            <span>
              <strong>EMERGENCY LIQUIDATION:</strong> Immediately execute market sell orders for all <strong>{activeTradesCount}</strong> active positions?
            </span>
          </div>
          <div className="flex items-center space-x-2">
            <button
              id="confirm-panic-close-btn"
              onClick={() => {
                onCloseAllTrades();
                setShowPanicConfirm(false);
              }}
              className="px-3 py-1 rounded font-bold bg-rose-600 text-white hover:bg-rose-500 transition-colors"
            >
              Confirm Close All
            </button>
            <button
              id="cancel-panic-close-btn"
              onClick={() => setShowPanicConfirm(false)}
              className="px-3 py-1 rounded font-medium bg-slate-800 text-slate-300 hover:bg-slate-700 transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Expanded Interactive Tuners */}
      {isExpanded && (
        <div className="mt-4 pt-4 border-t border-slate-800 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 text-xs">
          {/* Signal trigger tuners */}
          <div className="bg-slate-800/60 p-3 rounded-lg border border-slate-700/50 space-y-2">
            <div className="flex items-center justify-between">
              <span className="font-semibold text-slate-300 flex items-center space-x-1.5">
                <Gauge className="w-3.5 h-3.5 text-indigo-400" />
                <span>RSI Dip Trigger</span>
              </span>
              <span className="font-mono text-slate-200 font-bold">RSI({config.rsiPeriod}) &lt; {config.rsiOversold}</span>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-[10px] text-slate-400 block mb-1">RSI Period</label>
                <input
                  id="slider-rsi-period"
                  type="range"
                  min="2"
                  max="21"
                  step="1"
                  value={config.rsiPeriod}
                  onChange={(e) => onUpdateConfig({ ...config, rsiPeriod: parseInt(e.target.value) })}
                  className="w-full accent-indigo-500 cursor-pointer"
                />
              </div>
              <div>
                <label className="text-[10px] text-slate-400 block mb-1">Oversold Level</label>
                <input
                  id="slider-rsi-oversold"
                  type="range"
                  min="20"
                  max="50"
                  step="1"
                  value={config.rsiOversold}
                  onChange={(e) => onUpdateConfig({ ...config, rsiOversold: parseFloat(e.target.value) })}
                  className="w-full accent-indigo-500 cursor-pointer"
                />
              </div>
            </div>
          </div>

          {/* Bracket tuners */}
          <div className="bg-slate-800/60 p-3 rounded-lg border border-slate-700/50 space-y-2">
            <div className="flex items-center justify-between">
              <span className="font-semibold text-slate-300 flex items-center space-x-1.5">
                <Target className="w-3.5 h-3.5 text-rose-400" />
                <span>Fixed % Bracket</span>
              </span>
              <span className="font-mono text-slate-200 font-bold">
                -{(config.slPercent * 100).toFixed(2)}% / +{(config.tpPercent * 100).toFixed(2)}%
              </span>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-[10px] text-slate-400 block mb-1">Stop Loss %</label>
                <input
                  id="slider-sl-percent"
                  type="range"
                  min="0.004"
                  max="0.05"
                  step="0.001"
                  value={config.slPercent}
                  onChange={(e) => onUpdateConfig({ ...config, slPercent: parseFloat(e.target.value) })}
                  className="w-full accent-rose-500 cursor-pointer"
                />
              </div>
              <div>
                <label className="text-[10px] text-slate-400 block mb-1">Take Profit %</label>
                <input
                  id="slider-tp-percent"
                  type="range"
                  min="0.01"
                  max="0.1"
                  step="0.001"
                  value={config.tpPercent}
                  onChange={(e) => onUpdateConfig({ ...config, tpPercent: parseFloat(e.target.value) })}
                  className="w-full accent-emerald-500 cursor-pointer"
                />
              </div>
            </div>
          </div>

          {/* Scan interval & max symbols */}
          <div className="bg-slate-800/60 p-3 rounded-lg border border-slate-700/50 space-y-2">
            <div className="flex items-center justify-between">
              <span className="font-semibold text-slate-300 flex items-center space-x-1.5">
                <Clock className="w-3.5 h-3.5 text-amber-400" />
                <span>Scan Interval &amp; Max Pairs</span>
              </span>
              <span className="font-mono text-slate-200 font-bold">
                {config.signalInterval}s • Max {config.maxSymbols}
              </span>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-[10px] text-slate-400 block mb-1">Interval (sec)</label>
                <input
                  id="slider-signal-interval"
                  type="range"
                  min="2"
                  max="30"
                  step="1"
                  value={config.signalInterval}
                  onChange={(e) => onUpdateConfig({ ...config, signalInterval: parseInt(e.target.value) })}
                  className="w-full accent-amber-500 cursor-pointer"
                />
              </div>
              <div>
                <label className="text-[10px] text-slate-400 block mb-1">Max Positions</label>
                <input
                  id="slider-max-symbols"
                  type="range"
                  min="1"
                  max="6"
                  step="1"
                  value={config.maxSymbols}
                  onChange={(e) => onUpdateConfig({ ...config, maxSymbols: parseInt(e.target.value) })}
                  className="w-full accent-amber-500 cursor-pointer"
                />
              </div>
            </div>
          </div>

          {/* Capital allocation */}
          <div className="bg-slate-800/60 p-3 rounded-lg border border-slate-700/50 space-y-2">
            <div className="flex items-center justify-between">
              <span className="font-semibold text-slate-300 flex items-center space-x-1.5">
                <Percent className="w-3.5 h-3.5 text-emerald-400" />
                <span>Capital Allocation</span>
              </span>
              <span className="font-mono text-emerald-400 font-bold">
                {(config.balanceUsagePercent * 100).toFixed(0)}% / Max {(config.maxSymbolAllocationPercent * 100).toFixed(0)}%
              </span>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-[10px] text-slate-400 block mb-1">Total Usage %</label>
                <input
                  id="slider-balance-usage"
                  type="range"
                  min="0.1"
                  max="1.0"
                  step="0.05"
                  value={config.balanceUsagePercent}
                  onChange={(e) => onUpdateConfig({ ...config, balanceUsagePercent: parseFloat(e.target.value) })}
                  className="w-full accent-emerald-500 cursor-pointer"
                />
              </div>
              <div>
                <label className="text-[10px] text-slate-400 block mb-1">Max Per Coin %</label>
                <input
                  id="slider-symbol-allocation"
                  type="range"
                  min="0.05"
                  max="1.0"
                  step="0.05"
                  value={config.maxSymbolAllocationPercent}
                  onChange={(e) => onUpdateConfig({ ...config, maxSymbolAllocationPercent: parseFloat(e.target.value) })}
                  className="w-full accent-emerald-500 cursor-pointer"
                />
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
