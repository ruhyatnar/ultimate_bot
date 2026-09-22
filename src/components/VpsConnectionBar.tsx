import React, { useState } from 'react';
import { 
  Server, 
  Wifi, 
  WifiOff, 
  RefreshCw, 
  Settings2, 
  CheckCircle2, 
  AlertTriangle, 
  Database,
  PauseCircle,
  PlayCircle
} from 'lucide-react';
import { VpsBotStatus } from '../types';
import { WsTransport } from '../utils/vpsSocket';
import { SyncClock } from './SyncClock';

interface VpsConnectionBarProps {
  vpsStatus: VpsBotStatus;
  vpsEndpoint: string;
  onUpdateVpsEndpoint: (url: string) => void;
  onRefreshVps: () => void;
  isPolling: boolean;
  controlPaused?: boolean;
  onToggleVpsPause?: () => void;
  wsTransport?: WsTransport;
}

export const VpsConnectionBar: React.FC<VpsConnectionBarProps> = ({
  vpsStatus,
  vpsEndpoint,
  onUpdateVpsEndpoint,
  onRefreshVps,
  isPolling,
  controlPaused = false,
  onToggleVpsPause,
  wsTransport = 'connecting'
}) => {
  const [showConfigModal, setShowConfigModal] = useState<boolean>(false);
  const [inputUrl, setInputUrl] = useState<string>(vpsEndpoint);
  const [testResult, setTestResult] = useState<{ success?: boolean; message?: string } | null>(null);
  const [isTesting, setIsTesting] = useState<boolean>(false);

  const handleTestConnection = async () => {
    setIsTesting(true);
    setTestResult(null);
    const targetUrl = (inputUrl || window.location.origin).replace(/\/+$/, '');
    const testEndpoint = targetUrl.endsWith('/api/status') ? targetUrl : `${targetUrl}/api/status`;

    try {
      const startTime = performance.now();
      const res = await fetch(testEndpoint, { mode: 'cors' });
      const elapsed = Math.round(performance.now() - startTime);

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}: ${res.statusText}`);
      }
      const data = await res.json();
      setTestResult({
        success: true,
        message: `Connected successfully! Latency: ${elapsed}ms | Engine: ${data.process || 'OK'} | Trades in DB: ${data.data?.trades?.length ?? 0}`
      });
    } catch (err: any) {
      setTestResult({
        success: false,
        message: `Connection failed: ${err.message}. Make sure status.py --web 3000 is running on your VPS and port 3000 is allowed in your Tencent Cloud firewall.`
      });
    } finally {
      setIsTesting(false);
    }
  };

  const handleSaveEndpoint = () => {
    onUpdateVpsEndpoint(inputUrl.trim());
    setShowConfigModal(false);
    onRefreshVps();
  };

  return (
    <div className="bg-slate-950/90 border-b border-slate-800 px-4 py-2 sm:px-6 lg:px-8 text-xs">
      <div className="max-w-7xl mx-auto flex flex-wrap items-center justify-between gap-3">
        {/* Engine link (the monitor reads the real engine — no local simulator) */}
        <div className="flex items-center space-x-2">
          <span className="text-slate-400 font-medium hidden sm:inline">Data Source:</span>
          <span className="inline-flex items-center space-x-1.5 px-3 py-1 rounded-lg bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 font-medium">
            <Server className="w-3.5 h-3.5" />
            <span>Live Engine Sync</span>
            {vpsStatus.connected && <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />}
          </span>
        </div>

        {/* Live Status Indicators */}
        <div className="flex items-center space-x-3 sm:space-x-4">
              <div className="flex items-center space-x-2">
                {vpsStatus.connected ? (
                  <span className="inline-flex items-center space-x-1 text-emerald-400 bg-emerald-950/60 border border-emerald-800/60 px-2 py-0.5 rounded-full font-medium">
                    <Wifi className="w-3 h-3" />
                    <span>VPS Online</span>
                    {vpsStatus.latencyMs !== undefined && (
                      <span className="text-slate-400 font-normal text-[10px]">({vpsStatus.latencyMs}ms)</span>
                    )}
                  </span>
                ) : (
                  <span className="inline-flex items-center space-x-1 text-rose-400 bg-rose-950/60 border border-rose-800/60 px-2 py-0.5 rounded-full font-medium">
                    <WifiOff className="w-3 h-3" />
                    <span>VPS Disconnected</span>
                  </span>
                )}
              </div>

              {/* Transport badge: realtime WebSocket push vs HTTP polling fallback */}
              <span
                  title={wsTransport === 'websocket'
                    ? 'Realtime WebSocket push (ws://…/ws) — updates stream instantly from status.py'
                    : wsTransport === 'polling'
                      ? 'HTTP polling fallback (WebSocket unavailable or blocked)'
                      : 'Negotiating WebSocket connection…'}
                  className={`hidden md:inline-flex items-center space-x-1 px-2 py-0.5 rounded-full border font-medium ${
                    wsTransport === 'websocket'
                      ? 'text-cyan-300 bg-cyan-950/60 border-cyan-800/60'
                      : wsTransport === 'polling'
                        ? 'text-amber-300 bg-amber-950/60 border-amber-800/60'
                        : 'text-slate-400 bg-slate-900 border-slate-800'
                  }`}
                >
                  <span className={`w-2 h-2 rounded-full ${wsTransport === 'websocket' ? 'bg-cyan-400 animate-pulse' : 'bg-amber-400'}`} />
                <span>{wsTransport === 'websocket' ? 'WS LIVE' : wsTransport === 'polling' ? 'HTTP POLL' : 'WS…'}</span>
              </span>

              {/* Engine process badge */}
              <div className="hidden md:flex items-center space-x-1.5 text-slate-300 bg-slate-900 border border-slate-800 px-2 py-0.5 rounded-md">
                <span className="text-slate-400">Process:</span>
                <span className={vpsStatus.engineRunning ? 'text-emerald-400 font-semibold' : 'text-amber-400 font-semibold'}>
                  {vpsStatus.engineStatus || 'Checking...'}
                </span>
              </div>

              {/* Remote engine pause state */}
              {controlPaused && (
                <span className="inline-flex items-center space-x-1 text-amber-300 bg-amber-950/60 border border-amber-700/60 px-2 py-0.5 rounded-full font-semibold animate-pulse">
                  <PauseCircle className="w-3 h-3" />
                  <span>Engine Paused</span>
                </span>
              )}

              {/* Sync clock: engine time vs browser, skew + data age */}
              <SyncClock
                serverEpochMs={vpsStatus.serverEpochMs}
                lastPayloadAt={vpsStatus.lastPayloadAt}
                connected={vpsStatus.connected}
                latencyMs={vpsStatus.latencyMs}
              />

              {/* Sync timestamp */}
              {vpsStatus.lastSyncTime && (
                <span className="text-slate-400 text-[11px] hidden 2xl:inline">
                  Last Sync: {vpsStatus.lastSyncTime}
                </span>
              )}

              {/* Remote pause / resume */}
              {onToggleVpsPause && (
                <button
                  id="vps-bar-pause-btn"
                  onClick={onToggleVpsPause}
                  title={controlPaused ? 'Resume trading on the live engine' : 'Pause new entries on the live engine (open positions stay managed)'}
                  className={`flex items-center space-x-1 px-2 py-1 rounded border text-[11px] font-semibold transition-colors ${
                    controlPaused
                      ? 'bg-amber-500/20 text-amber-300 border-amber-500/40 hover:bg-amber-500 hover:text-white'
                      : 'bg-slate-900 border-slate-800 text-slate-300 hover:text-white hover:bg-slate-800'
                  }`}
                >
                  {controlPaused ? <PlayCircle className="w-3.5 h-3.5" /> : <PauseCircle className="w-3.5 h-3.5" />}
                  <span className="hidden sm:inline">{controlPaused ? 'Resume' : 'Pause'}</span>
                </button>
              )}

              {/* Manual refresh button */}
              <button
                id="refresh-vps-btn"
                onClick={onRefreshVps}
                disabled={isPolling}
                title="Force refresh database status"
                className="p-1 rounded bg-slate-900 border border-slate-800 text-slate-300 hover:text-white hover:bg-slate-800 transition-colors"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${isPolling ? 'animate-spin text-amber-400' : ''}`} />
              </button>

              {/* Connection setup button */}
              <button
                id="config-endpoint-btn"
                onClick={() => {
                  setInputUrl(vpsEndpoint);
                  setTestResult(null);
                  setShowConfigModal(true);
                }}
                className="flex items-center space-x-1 px-2 py-1 rounded bg-slate-900 border border-slate-800 text-slate-300 hover:text-white hover:bg-slate-800 transition-colors"
              >
                <Settings2 className="w-3.5 h-3.5 text-slate-400" />
                <span className="hidden sm:inline">VPS Endpoint</span>
              </button>
        </div>
      </div>

      {/* VPS Endpoint Settings Modal */}
      {showConfigModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm">
          <div className="bg-slate-900 border border-slate-800 rounded-xl max-w-lg w-full p-6 shadow-2xl space-y-4">
            <div className="flex items-center justify-between pb-3 border-b border-slate-800">
              <div className="flex items-center space-x-2">
                <Server className="w-5 h-5 text-emerald-400" />
                <h3 className="text-base font-semibold text-white">Connect Real Bot (VPS)</h3>
              </div>
              <button
                onClick={() => setShowConfigModal(false)}
                className="text-slate-400 hover:text-white text-lg font-bold"
              >
                ✕
              </button>
            </div>

            <div className="space-y-3 text-slate-300">
              <p className="text-xs text-slate-400 leading-relaxed">
                Enter your VPS IP and port where <code className="text-amber-400">status.py --web 3000</code> or PM2 is running.
                If you are serving the web dashboard directly from the VPS, leave this empty or use <code className="text-amber-400">/api</code>.
              </p>

              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">
                  VPS Bot Server URL
                </label>
                <input
                  type="text"
                  value={inputUrl}
                  onChange={(e) => setInputUrl(e.target.value)}
                  placeholder="e.g. http://100.96.0.8:3000 or leave empty for current host"
                  className="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white font-mono placeholder-slate-600 focus:outline-none focus:border-emerald-500"
                />
              </div>

              {testResult && (
                <div
                  className={`p-3 rounded-lg text-xs border ${
                    testResult.success
                      ? 'bg-emerald-950/60 border-emerald-800 text-emerald-300'
                      : 'bg-rose-950/60 border-rose-800 text-rose-300'
                  }`}
                >
                  <div className="flex items-start space-x-2">
                    {testResult.success ? (
                      <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
                    ) : (
                      <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
                    )}
                    <span>{testResult.message}</span>
                  </div>
                </div>
              )}

              <div className="bg-slate-950 p-3 rounded-lg border border-slate-800/80 space-y-1 text-[11px] text-slate-400">
                <div className="font-semibold text-slate-300 flex items-center space-x-1">
                  <Database className="w-3.5 h-3.5 text-amber-400" />
                  <span>How Real VPS Data Works:</span>
                </div>
                <p>1. On your VPS, run: <code className="text-amber-300">./venv/bin/python3 status.py --web 3000</code></p>
                <p>2. Ensure port 3000 is open in Tencent Cloud Security Group (Inbound TCP: 3000).</p>
                <p>3. The web dashboard will automatically read SQLite <code className="text-emerald-300">trading.db</code> active trades, daily PnL, win streaks, and orders in real-time!</p>
              </div>
            </div>

            <div className="flex items-center justify-between pt-3 border-t border-slate-800">
              <button
                type="button"
                onClick={handleTestConnection}
                disabled={isTesting}
                className="px-3.5 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold flex items-center space-x-1.5 transition-colors"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${isTesting ? 'animate-spin' : ''}`} />
                <span>{isTesting ? 'Testing...' : 'Test Connection'}</span>
              </button>

              <div className="flex space-x-2">
                <button
                  type="button"
                  onClick={() => setShowConfigModal(false)}
                  className="px-3 py-1.5 rounded-lg text-slate-400 hover:text-white text-xs"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={handleSaveEndpoint}
                  className="px-4 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold transition-colors shadow-sm"
                >
                  Save & Connect
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
