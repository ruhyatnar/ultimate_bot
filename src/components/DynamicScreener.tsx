import React from 'react';
import { 
  Sparkles, 
  TrendingUp, 
  TrendingDown, 
  CheckCircle2
} from 'lucide-react';
import { CandidateSymbol, BotConfig } from '../types';

interface DynamicScreenerProps {
  candidates: CandidateSymbol[];
  config: BotConfig;
  onInspectSymbol: (symbol: string) => void;
}

export const DynamicScreener: React.FC<DynamicScreenerProps> = ({
  candidates,
  config,
  onInspectSymbol
}) => {
  return (
    <div className="bg-slate-900/80 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
      {/* Header */}
      <div className="p-4 border-b border-slate-800 flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="flex items-center space-x-2">
            <Sparkles className="w-4 h-4 text-indigo-400" />
            <h2 className="font-bold text-sm text-white">Dynamic Symbol Screener & Momentum Leaderboard</h2>
            <span className="px-2 py-0.5 text-xs font-semibold rounded-full bg-indigo-500/10 text-indigo-300 border border-indigo-500/20">
              Ranked Pool ({candidates.length} Pairs)
            </span>
          </div>
          <p className="text-xs text-slate-400 mt-1">
            Ranks candidates via multi-factor Z-Score: <strong>20% Volume + 20% 24h Change + 20% Volatility + 40% ADX</strong>. Top {config.maxSymbols} monitored automatically.
          </p>
        </div>

        <div className="flex items-center space-x-2 text-xs">
          <span className="text-slate-400">Min Vol: <strong className="text-slate-200">&gt;$1M</strong></span>
          <span className="text-slate-600">•</span>
          <span className="text-slate-400">Min ADX: <strong className="text-indigo-300">&gt;{config.adxThreshold}</strong></span>
        </div>
      </div>

      {/* Table of Candidates */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs text-left">
          <thead className="bg-slate-950/60 text-slate-400 uppercase font-semibold text-[11px] border-b border-slate-800">
            <tr>
              <th className="py-2.5 px-4">Rank / Symbol</th>
              <th className="py-2.5 px-4">Live Price</th>
              <th className="py-2.5 px-4">24h Change</th>
              <th className="py-2.5 px-4">24h Volume (USDT)</th>
              <th className="py-2.5 px-4">Volatility</th>
              <th className="py-2.5 px-4">ADX Trend</th>
              <th className="py-2.5 px-4">Composite Z-Score</th>
              <th className="py-2.5 px-4">Bot Status</th>
              <th className="py-2.5 px-4 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/60">
            {candidates.map((c) => {
              const isSelected = c.isSelected;
              const isHighAdx = c.adx >= config.adxThreshold;

              return (
                <tr 
                  key={c.symbol} 
                  className={`hover:bg-slate-800/40 transition-colors ${
                    isSelected ? 'bg-indigo-950/15' : ''
                  }`}
                >
                  <td className="py-3 px-4">
                    <div className="flex items-center space-x-2">
                      <span className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold ${
                        c.momentumRank <= 3 
                          ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30' 
                          : 'bg-slate-800 text-slate-400'
                      }`}>
                        #{c.momentumRank}
                      </span>
                      <div>
                        <div className="font-bold text-slate-100 flex items-center space-x-1">
                          <span>{c.symbol}</span>
                          {c.momentumRank <= config.maxSymbols && (
                            <span className="px-1.5 py-0.2 rounded text-[9px] font-semibold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
                              Top {config.maxSymbols}
                            </span>
                          )}
                        </div>
                        <div className="text-[10px] text-slate-400">{c.name}</div>
                      </div>
                    </div>
                  </td>

                  <td className="py-3 px-4 font-mono font-semibold text-slate-200">
                    ${c.price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: c.price < 10 ? 4 : 2 })}
                  </td>

                  <td className="py-3 px-4 font-semibold">
                    <div className={`flex items-center space-x-0.5 ${c.priceChange24h >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                      {c.priceChange24h >= 0 ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
                      <span>{c.priceChange24h >= 0 ? '+' : ''}{c.priceChange24h.toFixed(2)}%</span>
                    </div>
                  </td>

                  <td className="py-3 px-4 font-mono text-slate-300">
                    ${(c.volume24h / 1_000_000).toFixed(1)}M
                  </td>

                  <td className="py-3 px-4 text-slate-300">
                    <span className="font-mono">{(c.volatility * 100).toFixed(2)}%</span>
                  </td>

                  <td className="py-3 px-4">
                    <div className="flex items-center space-x-1.5">
                      <span className={`font-mono font-bold ${isHighAdx ? 'text-emerald-400' : 'text-slate-400'}`}>
                        {c.adx.toFixed(1)}
                      </span>
                      {isHighAdx && (
                        <span className="px-1 py-0.2 rounded text-[9px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                          Trending
                        </span>
                      )}
                    </div>
                  </td>

                  <td className="py-3 px-4">
                    <div className="flex items-center space-x-2">
                      <div className="w-16 bg-slate-800 rounded-full h-2 overflow-hidden">
                        <div 
                          className="bg-indigo-500 h-full rounded-full"
                          style={{ width: `${Math.min(100, Math.max(10, (c.zScore + 2) * 25))}%` }}
                        />
                      </div>
                      <span className="font-mono font-bold text-indigo-300">
                        {c.zScore.toFixed(2)}
                      </span>
                    </div>
                  </td>

                  <td className="py-3 px-4">
                    {isSelected ? (
                      <span className="inline-flex items-center space-x-1 text-[11px] font-semibold px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
                        <CheckCircle2 className="w-3 h-3 text-emerald-400" />
                        <span>Active Watch</span>
                      </span>
                    ) : (
                      <span className="inline-flex items-center space-x-1 text-[11px] font-medium px-2 py-0.5 rounded bg-slate-800 text-slate-400">
                        <span>Candidate</span>
                      </span>
                    )}
                  </td>

                  <td className="py-3 px-4 text-right">
                    <div className="flex items-center justify-end space-x-1.5">
                      <button
                        id={`inspect-symbol-${c.symbol}`}
                        onClick={() => onInspectSymbol(c.symbol)}
                        className="px-2 py-1 rounded text-[11px] font-medium bg-slate-800 text-slate-300 hover:text-white hover:bg-slate-700 border border-slate-700 transition-colors"
                      >
                        Inspect
                      </button>

                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};
