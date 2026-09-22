import React from 'react';
import {
  Play,
  Pause,
  Sliders,
  Terminal,
  Activity,
  FileCode2,
  BookOpen,
  Zap,
  LayoutDashboard,
  TrendingUp,
  TrendingDown,
  Wifi,
  WifiOff
} from 'lucide-react';
import { BotConfig } from '../types';

interface HeaderProps {
  isRunning: boolean;
  onToggleRunning: () => void;
  activeTab: 'dashboard' | 'signals' | 'debug' | 'code' | 'config' | 'deploy';
  setActiveTab: (tab: 'dashboard' | 'signals' | 'debug' | 'code' | 'config' | 'deploy') => void;
  config: BotConfig;
  activeTradesCount: number;
  unrealizedPnl: number;
  totalEquity: number;
  vpsConnected?: boolean;
  isLossCooldown?: boolean;
}

const TABS: Array<{
  id: HeaderProps['activeTab'];
  label: string;
  icon: React.ReactNode;
  iconClass: string;
}> = [
  { id: 'dashboard', label: 'Trading Desk', icon: <LayoutDashboard className="w-3.5 h-3.5" />, iconClass: 'text-amber-400' },
  { id: 'signals', label: 'Signal State', icon: <Activity className="w-3.5 h-3.5" />, iconClass: 'text-indigo-400' },
  { id: 'debug', label: 'Engine Log', icon: <Terminal className="w-3.5 h-3.5" />, iconClass: 'text-emerald-400' },
  { id: 'code', label: 'Project Code', icon: <FileCode2 className="w-3.5 h-3.5" />, iconClass: 'text-sky-400' },
  { id: 'config', label: 'Strategy & .env', icon: <Sliders className="w-3.5 h-3.5" />, iconClass: 'text-purple-400' },
  { id: 'deploy', label: 'VPS Guide', icon: <BookOpen className="w-3.5 h-3.5" />, iconClass: 'text-orange-400' }
];

const fmtUsd = (v: number) =>
  v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const Header: React.FC<HeaderProps> = ({
  isRunning,
  onToggleRunning,
  activeTab,
  setActiveTab,
  config,
  activeTradesCount,
  unrealizedPnl,
  totalEquity,
  vpsConnected = false,
  isLossCooldown = false
}) => {
  const isFutures = config.market === 'futures';
  const isLive = !config.paperTrade;
  // Mode chip: LIVE FUTURES / LIVE SPOT / Futures Paper / Spot Paper —
  // live-danger always wins the palette so a live account is unmissable.
  const modeChip = isFutures
    ? (isLive
        ? 'bg-rose-500/15 text-rose-300 border-rose-500/40'
        : 'bg-amber-500/10 text-amber-300 border-amber-500/30')
    : (isLive
        ? 'bg-rose-500/15 text-rose-300 border-rose-500/40'
        : 'bg-sky-500/10 text-sky-300 border-sky-500/25');
  const modeLabel = `${isFutures ? 'Futures' : 'Spot'} ${isLive ? 'LIVE' : 'Paper'}`;

  return (
    <header className="sticky top-0 z-30 border-b border-slate-800 bg-slate-950/85 backdrop-blur supports-[backdrop-filter]:bg-slate-950/70">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div className="flex h-16 items-center justify-between gap-4">
          {/* Brand block */}
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-amber-500/30 bg-amber-500/10 text-amber-400 shadow-sm shadow-amber-500/10">
              <Zap className="h-5 w-5 fill-amber-400/20" />
            </div>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="truncate text-base font-bold tracking-tight text-white">
                  Market-Only Trading Bot
                </span>
                {/* Connection light: engine sync state from /api/status */}
                <span
                  title={vpsConnected ? 'Engine snapshot streaming (/api/status + /ws)' : 'No engine snapshot — check status.py / PM2'}
                  className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold ${
                    vpsConnected
                      ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400'
                      : 'border-rose-500/30 bg-rose-500/10 text-rose-400'
                  }`}
                >
                  {vpsConnected ? <Wifi className="h-3 w-3" /> : <WifiOff className="h-3 w-3" />}
                  {vpsConnected ? 'Synced' : 'Offline'}
                </span>
                {/* Trading mode: paper/live × spot/futures */}
                <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${modeChip}`}>
                  {modeLabel}
                </span>
                {isFutures && (
                  <span className="hidden rounded-full border border-amber-500/20 bg-amber-500/10 px-2 py-0.5 font-mono text-[11px] font-semibold text-amber-300 sm:inline">
                    {config.futuresLeverage}× {config.futuresMarginType}
                  </span>
                )}
                {isLossCooldown && (
                  <span className="animate-pulse rounded-full border border-rose-500/30 bg-rose-500/15 px-2 py-0.5 text-[11px] font-semibold text-rose-300">
                    ⏸ Cooldown
                  </span>
                )}
                <span className="hidden rounded-full border border-slate-700 bg-slate-800 px-2 py-0.5 text-[11px] font-medium capitalize text-slate-300 md:inline">
                  {config.preset}
                </span>
              </div>
              <p className="hidden text-[11px] text-slate-500 sm:block">
                {isFutures ? 'USDⓈ-M Futures Engine' : 'Spot Engine'} · intraday_rsi (daily EMA regime + RSI dip) · realtime SQLite sync
              </p>
            </div>
          </div>

          {/* KPI strip (hidden on small screens; the desk cards repeat it) */}
          <div className="hidden items-center gap-5 rounded-xl border border-slate-700/60 bg-slate-900/70 px-4 py-1.5 text-xs lg:flex">
            <div>
              <span className="text-slate-500">Equity </span>
              <span className="num font-bold text-slate-100">${fmtUsd(totalEquity)}</span>
            </div>
            <div className="h-3.5 w-px bg-slate-700" />
            <div>
              <span className="text-slate-500">Float </span>
              <span className={`num inline-flex items-center gap-0.5 font-bold ${unrealizedPnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                {unrealizedPnl >= 0 ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
                {unrealizedPnl >= 0 ? '+' : '-'}${fmtUsd(Math.abs(unrealizedPnl))}
              </span>
            </div>
            <div className="h-3.5 w-px bg-slate-700" />
            <div>
              <span className="text-slate-500">Positions </span>
              <span className="num font-bold text-amber-300">
                {activeTradesCount}
                <span className="text-slate-500">/{config.maxSymbols}</span>
              </span>
            </div>
            <div className="h-3.5 w-px bg-slate-700" />
            {/* Engine control: pause blocks NEW entries only — open positions stay managed */}
            <button
              id="bot-toggle-btn"
              onClick={onToggleRunning}
              title={isRunning ? 'Pause new entries (open positions stay managed)' : 'Resume entries'}
              className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold shadow-sm transition-all ${
                isRunning
                  ? 'border border-amber-500/30 bg-amber-500/15 text-amber-300 hover:bg-amber-500/25'
                  : 'bg-emerald-600 text-white shadow-emerald-600/20 hover:bg-emerald-500'
              }`}
            >
              {isRunning ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5 fill-white" />}
              {isRunning ? 'Pause Entries' : 'Resume'}
            </button>
          </div>

          {/* Control always visible on small screens */}
          <button
            id="bot-toggle-btn-sm"
            onClick={onToggleRunning}
            className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold shadow-sm transition-all lg:hidden ${
              isRunning
                ? 'border border-amber-500/30 bg-amber-500/15 text-amber-300'
                : 'bg-emerald-600 text-white'
            }`}
          >
            {isRunning ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5 fill-white" />}
            {isRunning ? 'Pause' : 'Resume'}
          </button>
        </div>

        {/* Tab navigation */}
        <nav className="flex items-center gap-1 overflow-x-auto border-t border-slate-800/80 py-2 scrollbar-none" aria-label="Sections">
          {TABS.map(tab => (
            <button
              key={tab.id}
              id={`tab-${tab.id}`}
              onClick={() => setActiveTab(tab.id)}
              aria-current={activeTab === tab.id ? 'page' : undefined}
              className={`inline-flex items-center gap-2 whitespace-nowrap rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                activeTab === tab.id
                  ? 'border border-slate-700 bg-slate-800 text-white shadow-sm'
                  : 'text-slate-400 hover:bg-slate-800/50 hover:text-slate-200'
              }`}
            >
              <span className={tab.iconClass}>{tab.icon}</span>
              {tab.label}
            </button>
          ))}
        </nav>
      </div>
    </header>
  );
};
