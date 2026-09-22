import React, { useState } from 'react';
import { 
  Copy, 
  Check, 
  ShieldCheck, 
  Server, 
  CheckCircle2
} from 'lucide-react';

export const DeployGuide: React.FC = () => {
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const copyCommand = (id: string, text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const steps = [
    {
      id: 'step-0',
      title: '1. Install Debian 13 CLI Prerequisites',
      desc: 'Ensure Python3 virtual environment tools, OpenSSL, build libraries, and Node.js/PM2 are installed on Debian 13.',
      cmd: `sudo apt update && sudo apt install -y python3 python3-venv python3-pip git curl openssl build-essential libsqlite3-dev nodejs npm\nsudo npm install -g pm2`
    },
    {
      id: 'step-1',
      title: '2. Clone & Navigate to Project Directory',
      desc: 'Clone the repository onto your Debian 13 VPS and enter the Python engine folder.',
      cmd: `git clone https://github.com/ruhyatnar/ultimate_bot.git\ncd ultimate_bot/ultimate-bot`
    },
    {
      id: 'step-2',
      title: '3. Create Virtual Environment & Install Dependencies',
      desc: 'Debian 13 adheres strictly to PEP 668. Create an isolated Python venv and install all trading packages.',
      cmd: `python3 -m venv venv\nsource venv/bin/activate\npip install -r requirements.txt`
    },
    {
      id: 'step-3',
      title: '4. Generate Binance Ed25519 Key Pair',
      desc: 'Generate a non-exportable Ed25519 key for sub-millisecond authenticated Binance order routing.',
      cmd: `mkdir -p keys\nopenssl genpkey -algorithm ed25519 -outform PEM -out keys/private_key.pem\nchmod 600 keys/private_key.pem\n# Generate public key to copy into Binance API Management:\nopenssl pkey -in keys/private_key.pem -pubout -out keys/public_key.pem\ncat keys/public_key.pem`
    },
    {
      id: 'step-4',
      title: '5. Configure .env File',
      desc: 'Configure API key, monitored symbols, and verify AUTO_LIQUIDATE_ORPHANS=false to protect existing spot assets.',
      cmd: `nano .env\n# Verify BINANCE_API_KEY, PAPER_TRADE=true (for testing) or false (live)\n# Ensure AUTO_LIQUIDATE_ORPHANS=false`
    },
    {
      id: 'step-5',
      title: '6. Start 24/7 Engine with PM2',
      desc: 'Start the bot with PM2 for automated restarts on crashes or server reboots.',
      cmd: `pm2 start ecosystem.config.cjs\npm2 save\npm2 startup\n# Stream logs in real-time:\npm2 logs ultimate-bot`
    },
    {
      id: 'step-6',
      title: '7. Monitor Mode A: CLI Live Terminal Dashboard',
      desc: 'View a continuously refreshing terminal dashboard of equity, PnL, active trades, stops, and fills.',
      cmd: `python3 status.py --watch\n# Refreshes every 2s directly in your SSH terminal`
    },
    {
      id: 'step-7',
      title: '8. Monitor Mode B: PM2 Interactive Dashboard',
      desc: 'View real-time CPU, RAM, event loop latency, and log outputs in an interactive terminal UI.',
      cmd: `pm2 monit`
    },
    {
      id: 'step-8',
      title: '9. Monitor Mode C: Web Dashboard & Remote Browser UI',
      desc: 'Access your live dashboard from any browser using the built-in Python web server, PM2, or static React server.',
      cmd: `# Single command serves the React dashboard, the WebSocket API and the HTTP API:\npython3 status.py --web 3000\n# (React + WS realtime push on /ws + polling fallback, SPA fallback, gzip, ETag/304 caching, keep-alive — no Node needed)\n\n# Automated 24/7 PM2 Supervision (Runs bot + web monitor together):\npm2 start ecosystem.config.cjs\npm2 save\n\n# Remote Access: Visit http://YOUR_VPS_IP:3000 in your browser\n# Or SSH Port Tunnel (run on your local PC): ssh -L 3000:localhost:3000 user@YOUR_VPS_IP`
    }
  ];

  return (
    <div className="space-y-6">
      {/* Overview Banner */}
      <div className="bg-slate-800/80 border border-slate-700/60 rounded-xl p-5 shadow-sm">
        <div className="flex items-center space-x-3">
          <div className="w-10 h-10 rounded-xl bg-orange-500/10 border border-orange-500/20 flex items-center justify-center text-orange-400">
            <Server className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-base font-bold text-white">Production VPS Deployment & Live Readiness</h2>
            <p className="text-xs text-slate-400 mt-0.5">
              Tested on Ubuntu 22.04 / 24.04 LTS, Debian 12, and macOS with Python 3.10+ and PM2.
            </p>
          </div>
        </div>
      </div>

      {/* Live Trading Readiness Audit Card */}
      <div className="bg-emerald-950/30 border border-emerald-500/30 rounded-xl p-5 space-y-3">
        <div className="flex items-center space-x-2 text-emerald-400">
          <ShieldCheck className="w-5 h-5" />
          <h3 className="text-sm font-bold">Live Trading Hardening Applied & Verified</h3>
        </div>
        <p className="text-xs text-slate-300 leading-relaxed">
          All critical audit risks have been resolved in the engine:
        </p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5 text-xs text-slate-300">
          <div className="flex items-start space-x-2 bg-slate-900/60 p-2.5 rounded-lg border border-slate-800">
            <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
            <div>
              <span className="font-semibold text-white">Wallet Protection:</span> Existing spot balances are never automatically liquidated (<code className="text-emerald-300">AUTO_LIQUIDATE_ORPHANS=false</code>).
            </div>
          </div>
          <div className="flex items-start space-x-2 bg-slate-900/60 p-2.5 rounded-lg border border-slate-800">
            <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
            <div>
              <span className="font-semibold text-white">LOT_SIZE Precision:</span> Exact stepSize rounding via <code className="text-emerald-300">Decimal.quantize()</code> prevents -1013 filter errors.
            </div>
          </div>
          <div className="flex items-start space-x-2 bg-slate-900/60 p-2.5 rounded-lg border border-slate-800">
            <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
            <div>
              <span className="font-semibold text-white">MIN_NOTIONAL Guard:</span> Orders automatically conform to Binance's 5–10 USDT minimum value filter.
            </div>
          </div>
          <div className="flex items-start space-x-2 bg-slate-900/60 p-2.5 rounded-lg border border-slate-800">
            <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
            <div>
              <span className="font-semibold text-white">Dynamic Equity Sync:</span> Risk manager refreshes live Binance equity on every closed position to avoid stale metrics.
            </div>
          </div>
          <div className="flex items-start space-x-2 bg-slate-900/60 p-2.5 rounded-lg border border-slate-800">
            <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
            <div>
              <span className="font-semibold text-white">3-Bar FVG Imbalance:</span> Corrected Fair Value Gap formula to evaluate true institutional 3-candle displacement.
            </div>
          </div>
          <div className="flex items-start space-x-2 bg-slate-900/60 p-2.5 rounded-lg border border-slate-800">
            <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
            <div>
              <span className="font-semibold text-white">Thread-Safe WS API:</span> Asyncio locks protect WebSocket order request/response streams from race conditions.
            </div>
          </div>
        </div>
      </div>

      {/* Steps List */}
      <div className="space-y-4">
        {steps.map(step => (
          <div key={step.id} className="bg-slate-800/80 border border-slate-700/60 rounded-xl p-5 shadow-sm space-y-3">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-sm font-bold text-slate-100">{step.title}</h3>
                <p className="text-xs text-slate-400 mt-0.5">{step.desc}</p>
              </div>

              <button
                onClick={() => copyCommand(step.id, step.cmd)}
                className="flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-700 text-slate-200 hover:bg-slate-600 transition-colors"
              >
                {copiedId === step.id ? (
                  <>
                    <Check className="w-3.5 h-3.5 text-emerald-400" />
                    <span>Copied</span>
                  </>
                ) : (
                  <>
                    <Copy className="w-3.5 h-3.5" />
                    <span>Copy Commands</span>
                  </>
                )}
              </button>
            </div>

            <div className="bg-slate-950 border border-slate-800 rounded-lg p-3.5 font-mono text-xs text-emerald-400 overflow-x-auto leading-relaxed">
              <pre>
                <code>{step.cmd}</code>
              </pre>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
