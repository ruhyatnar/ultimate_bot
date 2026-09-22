import React, { useState, useEffect, useRef } from 'react';
import { 
  Terminal as TerminalIcon, 
  Trash2, 
  Copy, 
  Check 
} from 'lucide-react';
import { LogMessage } from '../types';

interface DebugConsoleProps {
  logs: LogMessage[];
  onClearLogs: () => void;
  signalInterval: number;
}

export const DebugConsole: React.FC<DebugConsoleProps> = ({
  logs,
  onClearLogs,
  signalInterval
}) => {
  const [filter, setFilter] = useState<'ALL' | 'SKIPPED' | 'ORDERS' | 'RISK' | 'DEBUG'>('ALL');
  const [autoScroll, setAutoScroll] = useState(true);
  const [copied, setCopied] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logs, autoScroll]);

  const filteredLogs = logs.filter(log => {
    if (filter === 'ALL') return true;
    if (filter === 'SKIPPED') {
      return log.message.toLowerCase().includes('skipped') || 
             log.message.toLowerCase().includes('skipping') || 
             log.message.toLowerCase().includes('neutral');
    }
    if (filter === 'ORDERS') return log.category === 'ORDER' || log.message.includes('BUY') || log.message.includes('SELL') || log.message.includes('CLOSE');
    if (filter === 'RISK') return log.category === 'RISK' || log.message.toLowerCase().includes('risk') || log.message.toLowerCase().includes('drawdown');
    if (filter === 'DEBUG') return log.level === 'DEBUG';
    return true;
  });

  const handleCopy = () => {
    const text = filteredLogs.map(l => `[${l.timestamp}] [${l.level}] ${l.message}`).join('\n');
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="space-y-4">
      {/* Console Controls Bar */}
      <div className="bg-slate-800/80 border border-slate-700/60 rounded-xl p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 shadow-sm">
        <div className="flex items-center space-x-3">
          <div className="w-8 h-8 rounded-lg bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-emerald-400">
            <TerminalIcon className="w-4 h-4" />
          </div>
          <div>
            <h2 className="text-sm font-bold text-white flex items-center space-x-2">
              <span>Execution Engine & Debug Terminal</span>
              <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
            </h2>
            <p className="text-xs text-slate-400">
              Live engine log stream (regime gate, RSI dip evaluation, entries/exits, risk checks)
            </p>
          </div>
        </div>

        {/* Filter Pills */}
        <div className="flex items-center flex-wrap gap-1.5 text-xs">
          <button
            onClick={() => setFilter('ALL')}
            className={`px-2.5 py-1 rounded-md font-medium transition-colors ${
              filter === 'ALL'
                ? 'bg-slate-700 text-white border border-slate-600'
                : 'bg-slate-900/60 text-slate-400 hover:text-slate-200'
            }`}
          >
            All Logs ({logs.length})
          </button>
          <button
            onClick={() => setFilter('SKIPPED')}
            className={`px-2.5 py-1 rounded-md font-medium transition-colors ${
              filter === 'SKIPPED'
                ? 'bg-amber-500/20 text-amber-300 border border-amber-500/40 font-bold'
                : 'bg-slate-900/60 text-slate-400 hover:text-slate-200'
            }`}
          >
            Skipped Symbols Only
          </button>
          <button
            onClick={() => setFilter('ORDERS')}
            className={`px-2.5 py-1 rounded-md font-medium transition-colors ${
              filter === 'ORDERS'
                ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
                : 'bg-slate-900/60 text-slate-400 hover:text-slate-200'
            }`}
          >
            Orders & Fills
          </button>
          <button
            onClick={() => setFilter('RISK')}
            className={`px-2.5 py-1 rounded-md font-medium transition-colors ${
              filter === 'RISK'
                ? 'bg-rose-500/20 text-rose-300 border border-rose-500/40'
                : 'bg-slate-900/60 text-slate-400 hover:text-slate-200'
            }`}
          >
            Risk & Cooldown
          </button>
          <button
            onClick={() => setFilter('DEBUG')}
            className={`px-2.5 py-1 rounded-md font-medium transition-colors ${
              filter === 'DEBUG'
                ? 'bg-sky-500/20 text-sky-300 border border-sky-500/40'
                : 'bg-slate-900/60 text-slate-400 hover:text-slate-200'
            }`}
          >
            DEBUG Lines Only
          </button>

          <div className="h-4 w-px bg-slate-700 mx-1 hidden sm:block" />

          <button
            onClick={handleCopy}
            className="p-1.5 rounded-md bg-slate-900/60 text-slate-400 hover:text-slate-200 border border-slate-700/50"
            title="Copy logs to clipboard"
          >
            {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
          </button>
          <button
            onClick={onClearLogs}
            className="p-1.5 rounded-md bg-slate-900/60 text-slate-400 hover:text-rose-300 hover:bg-rose-950/30 border border-slate-700/50"
            title="Clear terminal"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Terminal Screen */}
      <div className="bg-slate-950 border border-slate-800 rounded-xl overflow-hidden font-mono text-xs shadow-2xl">
        <div className="bg-slate-900 px-4 py-2 border-b border-slate-800 flex items-center justify-between text-slate-400 text-[11px]">
          <div className="flex items-center space-x-2">
            <span className="w-2.5 h-2.5 rounded-full bg-rose-500/80 inline-block" />
            <span className="w-2.5 h-2.5 rounded-full bg-amber-500/80 inline-block" />
            <span className="w-2.5 h-2.5 rounded-full bg-emerald-500/80 inline-block" />
            <span className="ml-2 text-slate-300 font-semibold">python3 main.py (stdout - WAL DB & WebSocket Loop)</span>
          </div>

          <div className="flex items-center space-x-3 text-slate-400">
            <span>Scan interval: {signalInterval}s</span>
            <label className="flex items-center space-x-1 cursor-pointer">
              <input
                type="checkbox"
                checked={autoScroll}
                onChange={e => setAutoScroll(e.target.checked)}
                className="rounded border-slate-700 bg-slate-800 text-emerald-500 focus:ring-0 w-3 h-3"
              />
              <span>Autoscroll</span>
            </label>
          </div>
        </div>

        {/* Scrollable Terminal Output */}
        <div 
          ref={scrollRef}
          className="p-4 h-[450px] overflow-y-auto space-y-1.5 scrollbar-thin scrollbar-thumb-slate-800"
        >
          {filteredLogs.length === 0 ? (
            <div className="text-slate-500 py-12 text-center">
              No matching log lines found. The bot loop writes new logs every {signalInterval} seconds.
            </div>
          ) : (
            filteredLogs.map((log, idx) => {
              let levelColor = 'text-slate-400';
              let bgHighlight = '';

              if (log.level === 'DEBUG') {
                if (log.message.includes('skipped') || log.message.includes('skipping')) {
                  levelColor = 'text-amber-400/90';
                  bgHighlight = 'bg-amber-950/15';
                } else {
                  levelColor = 'text-sky-400/80';
                }
              } else if (log.level === 'INFO') {
                levelColor = 'text-emerald-400';
                if (log.message.includes('BUY') || log.message.includes('ENTRY')) {
                  bgHighlight = 'bg-emerald-950/30 text-emerald-200';
                }
              } else if (log.level === 'WARN') {
                levelColor = 'text-amber-400 font-bold';
              } else if (log.level === 'ERROR') {
                levelColor = 'text-rose-400 font-bold';
                bgHighlight = 'bg-rose-950/20';
              } else if (log.level === 'SUCCESS') {
                levelColor = 'text-emerald-300 font-bold';
              }

              return (
                <div 
                  key={`log_${log.id}_${idx}`} 
                  className={`flex items-start space-x-2.5 py-0.5 px-2 rounded hover:bg-slate-900/60 leading-relaxed ${bgHighlight}`}
                >
                  <span className="text-slate-600 select-none shrink-0">{log.timestamp}</span>
                  <span className={`font-semibold shrink-0 w-14 ${levelColor}`}>
                    [{log.level}]
                  </span>
                  {log.symbol && (
                    <span className="text-indigo-400 font-bold shrink-0">
                      {log.symbol}:
                    </span>
                  )}
                  <span className={`text-slate-200 break-all ${levelColor}`}>
                    {log.message}
                  </span>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
};
