import React, { useState } from 'react';
import { 
  X, 
  Copy, 
  Check, 
  Terminal, 
  Server, 
  FileText, 
  Sparkles,
  Send,
  Loader2,
  AlertTriangle,
  CheckCircle2
} from 'lucide-react';
import { BotConfig, PushResult } from '../types';
import { generateEnvString } from '../utils/envGenerator';

interface VpsSyncModalProps {
  config: BotConfig;
  onClose: () => void;
  vpsConnected?: boolean;
  onApplyToVps?: () => Promise<PushResult>;
  lastPushResult?: PushResult | null;
}

export const VpsSyncModal: React.FC<VpsSyncModalProps> = ({ config, onClose, vpsConnected = false, onApplyToVps, lastPushResult = null }) => {
  const [copiedScript, setCopiedScript] = useState<boolean>(false);
  const [copiedEnv, setCopiedEnv] = useState<boolean>(false);
  const [isApplying, setIsApplying] = useState<boolean>(false);
  const [applyResult, setApplyResult] = useState<PushResult | null>(lastPushResult);

  const handleApplyToVps = async () => {
    if (!onApplyToVps) return;
    setIsApplying(true);
    setApplyResult(null);
    try {
      const result = await onApplyToVps();
      setApplyResult(result);
    } finally {
      setIsApplying(false);
    }
  };

  const generateEnvContent = () => generateEnvString(config);

  // The web monitor (status.py --web) and engine are supervised by PM2 via
  // ecosystem.config.cjs; only the engine needs a reload to pick up .env changes.
  // The full-file overwrite is preceded by a timestamped backup so a mistake can
  // never destroy an existing configuration (credentials included) irrecoverably.
  const bashCommand = `cp .env .env.bak.$(date +%s) 2>/dev/null || true
cat << 'EOF' > .env
${generateEnvContent()}EOF
# ⚠️ If you had a real BINANCE_API_KEY / BINANCE_API_SECRET on this VPS, verify the
# new .env still contains them (the generated block only carries a placeholder).
pm2 reload ultimate-bot`;

  const handleCopyBash = () => {
    navigator.clipboard.writeText(bashCommand);
    setCopiedScript(true);
    setTimeout(() => setCopiedScript(false), 2000);
  };

  const handleCopyEnv = () => {
    if (navigator.clipboard) {
      navigator.clipboard.writeText(generateEnvContent());
    }
    setCopiedEnv(true);
    setTimeout(() => setCopiedEnv(false), 2000);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm animate-in fade-in">
      <div className="bg-slate-900 border border-slate-700 rounded-2xl w-full max-w-3xl overflow-hidden shadow-2xl flex flex-col max-h-[85vh]">
        {/* Header */}
        <div className="p-5 border-b border-slate-800 flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <div className="w-10 h-10 rounded-xl bg-purple-500/10 border border-purple-500/30 flex items-center justify-center text-purple-400">
              <Server className="w-5 h-5" />
            </div>
            <div>
              <h3 className="font-bold text-base text-white">VPS Sync & Export Center</h3>
              <p className="text-xs text-slate-400">
                Apply web-tuned parameters directly to your Debian 13 VPS with zero downtime.
              </p>
            </div>
          </div>
          <button
            id="close-vps-sync-btn"
            onClick={onClose}
            className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-5 space-y-4 overflow-y-auto flex-1 text-xs">
          {/* One-Click Live Apply (when connected to the VPS web monitor) */}
          {vpsConnected && onApplyToVps && (
            <div className="bg-emerald-950/40 border border-emerald-700/50 rounded-xl p-4">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                <div className="flex items-start space-x-2.5">
                  <Send className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
                  <div>
                    <div className="font-bold text-emerald-200">Push to Running Bot</div>
                    <p className="text-emerald-300/80 mt-0.5">
                      Applies these tuned parameters directly to the VPS <code className="text-emerald-300">.env</code> and reloads the engine via PM2 — no SSH needed.
                    </p>
                  </div>
                </div>
                <button
                  id="apply-config-to-vps-btn"
                  onClick={handleApplyToVps}
                  disabled={isApplying}
                  className="px-4 py-2 rounded-lg font-bold text-xs bg-emerald-600 hover:bg-emerald-500 text-white transition-colors disabled:opacity-60 flex items-center justify-center space-x-1.5 shadow-sm shrink-0"
                >
                  {isApplying ? (
                    <><Loader2 className="w-3.5 h-3.5 animate-spin" /><span>Applying...</span></>
                  ) : (
                    <><Send className="w-3.5 h-3.5" /><span>Apply to Live Bot</span></>
                  )}
                </button>
              </div>
              {applyResult && (
                <div className={`mt-3 p-3 rounded-lg border text-[11px] flex items-start space-x-2 ${
                  applyResult.ok
                    ? 'bg-emerald-900/40 border-emerald-700 text-emerald-200'
                    : 'bg-rose-950/60 border-rose-700 text-rose-200'
                }`}>
                  {applyResult.ok
                    ? <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
                    : <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />}
                  <span>{applyResult.message}</span>
                </div>
              )}
            </div>
          )}

          {/* Workflow Step Explanation */}
          <div className="bg-slate-800/60 p-4 rounded-xl border border-slate-700/60">
            <div className="font-semibold text-slate-200 mb-2 flex items-center space-x-1.5">
              <Sparkles className="w-4 h-4 text-amber-400" />
              <span>How Web-Tuned Parameters Sync to Your Debian 13 VPS</span>
            </div>
            <p className="text-[11px] text-amber-300/90 bg-amber-950/40 border border-amber-700/40 rounded-lg p-2.5 mb-3">
              ⚠️ <strong>Credentials safety:</strong> the 1-click command below replaces the whole <code className="text-amber-200">.env</code> and
              writes a timestamped backup (<code className="text-amber-200">.env.bak.&lt;timestamp&gt;</code>) first. The generated block only contains a
              placeholder <code className="text-amber-200">BINANCE_API_KEY</code> — on an existing deployment, restore your real key/secret from
              the backup (or use the <em>Push to VPS</em> button, which applies only whitelisted tuning keys and never touches credentials).
            </p>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-slate-300">
              <div className="bg-slate-900/60 p-2.5 rounded-lg border border-slate-800">
                <span className="font-bold text-amber-400 block mb-1">1. Tune Interactively</span>
                Adjust the signal and risk parameters, or Dynamic Symbols, in this web monitor.
              </div>
              <div className="bg-slate-900/60 p-2.5 rounded-lg border border-slate-800">
                <span className="font-bold text-purple-400 block mb-1">2. Run Sync Command</span>
                Paste the 1-click command into your SSH terminal inside <code className="text-purple-300">ultimate-bot/</code>.
              </div>
              <div className="bg-slate-900/60 p-2.5 rounded-lg border border-slate-800">
                <span className="font-bold text-emerald-400 block mb-1">3. Zero Downtime</span>
                PM2 reloads the process with the new configuration while preserving open positions.
              </div>
            </div>
          </div>

          {/* 1-Click SSH Command */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="font-semibold text-slate-300 flex items-center space-x-1.5">
                <Terminal className="w-3.5 h-3.5 text-emerald-400" />
                <span>1-Click SSH Command (Overwrite .env & Graceful Reload)</span>
              </span>
              <button
                id="copy-ssh-command-btn"
                onClick={handleCopyBash}
                className="flex items-center space-x-1 px-2.5 py-1 rounded bg-indigo-600/30 text-indigo-200 border border-indigo-500/40 hover:bg-indigo-600 hover:text-white transition-colors"
              >
                {copiedScript ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                <span>{copiedScript ? 'Copied to Clipboard!' : 'Copy Full Command'}</span>
              </button>
            </div>
            <pre className="bg-slate-950 p-3 rounded-lg border border-slate-800 text-emerald-400 font-mono text-[11px] overflow-x-auto select-all">
              {bashCommand}
            </pre>
          </div>

          {/* Raw .env preview */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="font-semibold text-slate-300 flex items-center space-x-1.5">
                <FileText className="w-3.5 h-3.5 text-slate-400" />
                <span>Generated .env File Content</span>
              </span>
              <button
                id="copy-env-btn"
                onClick={handleCopyEnv}
                className="text-slate-400 hover:text-white flex items-center space-x-1"
              >
                {copiedEnv ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                <span>{copiedEnv ? 'Copied' : 'Copy .env only'}</span>
              </button>
            </div>
            <pre className="bg-slate-950 p-3 rounded-lg border border-slate-800 text-slate-300 font-mono text-[11px] max-h-48 overflow-y-auto">
              {generateEnvContent()}
            </pre>
          </div>
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-slate-800 bg-slate-900/60 flex items-center justify-between text-xs text-slate-400">
          <span>Target Directory: <code>/path/to/ultimate-bot</code></span>
          <button
            id="done-vps-sync-btn"
            onClick={onClose}
            className="px-4 py-1.5 rounded-lg font-semibold bg-slate-800 text-slate-200 hover:bg-slate-700 transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
};
