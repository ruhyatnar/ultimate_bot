import React, { useState } from 'react';
import { 
  FileCode2, 
  Folder, 
  Download, 
  Copy, 
  Check, 
  CheckCircle2, 
  Sparkles
} from 'lucide-react';
import JSZip from 'jszip';
import { BOT_FILES, INIT_PY } from '../data/botFiles';

export const CodeExplorer: React.FC = () => {
  const [selectedPath, setSelectedPath] = useState<string>('main.py');
  const [copied, setCopied] = useState(false);
  const [isZipping, setIsZipping] = useState(false);

  const activeFile = BOT_FILES.find(f => f.path === selectedPath) || BOT_FILES[0];

  const handleCopy = () => {
    navigator.clipboard.writeText(activeFile.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleDownloadZip = async () => {
    setIsZipping(true);
    try {
      const zip = new JSZip();
      const folder = zip.folder('ultimate-bot');

      // Add all defined files
      BOT_FILES.forEach(file => {
        folder?.file(file.path, file.content);
      });

      // Add placeholder directories and package init files
      folder?.file('keys/private_key.pem', '# Place your Ed25519 private key here for live Binance trading');
      folder?.file('keys/public_key.pem', '# Generate with: openssl pkey -in private_key.pem -pubout');
      folder?.file('data/.gitkeep', '');
      folder?.file('logs/.gitkeep', '');
      folder?.file(INIT_PY, '');

      const blob = await zip.generateAsync({ type: 'blob' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'ultimate-bot.zip';
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Failed to create zip', err);
    } finally {
      setIsZipping(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Top Banner with 1-Click ZIP Download */}
      <div className="bg-slate-800/80 border border-slate-700/60 rounded-xl p-5 shadow-sm">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <div className="flex items-center space-x-2">
              <Sparkles className="w-5 h-5 text-amber-400" />
              <h2 className="text-base font-bold text-white">
                Final Clean Market-Only Bot Codebase
              </h2>
              <span className="px-2 py-0.5 text-xs font-semibold rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                20+ Fixes Implemented
              </span>
            </div>
            <p className="text-xs text-slate-400 mt-1 max-w-2xl">
              Zero dead code, asynchronous SQLite with WAL & batch queue, Ed25519 WebSocket & REST API signing, one backtest-proven signal engine (daily EMA regime + RSI dip), and a real-engine web monitor over WebSocket + HTTP.
            </p>
          </div>

          <div>
            <button
              id="download-bot-zip-btn"
              onClick={handleDownloadZip}
              disabled={isZipping}
              className="flex items-center space-x-2 px-4 py-2 rounded-lg text-xs font-bold bg-amber-500 text-slate-950 hover:bg-amber-400 transition-colors shadow-lg shadow-amber-500/20"
            >
              <Download className="w-4 h-4" />
              <span>{isZipping ? 'Packaging ZIP...' : 'Download Bot (ZIP)'}</span>
            </button>
          </div>
        </div>
      </div>

      {/* Code Browser Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left Sidebar: File Tree */}
        <div className="lg:col-span-4 bg-slate-800/80 border border-slate-700/60 rounded-xl p-4 shadow-sm h-[600px] overflow-y-auto">
          <div className="flex items-center space-x-2 mb-3 pb-2 border-b border-slate-700/60 text-xs font-bold text-slate-300 uppercase tracking-wider">
            <Folder className="w-4 h-4 text-amber-400" />
            <span>Project Explorer (/ultimate-bot)</span>
          </div>

          <div className="space-y-1 text-xs">
            {BOT_FILES.map(file => {
              const isSelected = file.path === selectedPath;

              return (
                <button
                  key={file.path}
                  onClick={() => setSelectedPath(file.path)}
                  className={`w-full text-left px-3 py-2 rounded-lg transition-colors flex items-center justify-between ${
                    isSelected
                      ? 'bg-amber-500/15 text-amber-300 border border-amber-500/30 font-bold'
                      : 'text-slate-300 hover:bg-slate-700/40 hover:text-white'
                  }`}
                >
                  <div className="flex items-center space-x-2 truncate">
                    <FileCode2 className={`w-4 h-4 shrink-0 ${isSelected ? 'text-amber-400' : 'text-slate-400'}`} />
                    <span className="truncate">{file.path}</span>
                  </div>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-900/60 text-slate-400 uppercase font-semibold shrink-0 ml-2">
                    {file.category}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Right Pane: Code Viewer & Enhancements */}
        <div className="lg:col-span-8 bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-xl flex flex-col h-[600px]">
          {/* File Header */}
          <div className="p-4 bg-slate-850 bg-slate-800/60 border-b border-slate-800 flex items-center justify-between">
            <div>
              <div className="flex items-center space-x-2">
                <span className="text-sm font-bold text-white font-mono">{activeFile.path}</span>
                <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
                  {activeFile.category}
                </span>
              </div>
              <p className="text-xs text-slate-400 mt-0.5">{activeFile.description}</p>
            </div>

            <button
              onClick={handleCopy}
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
                  <span>Copy Code</span>
                </>
              )}
            </button>
          </div>

          {/* Enhancement Bullets */}
          {activeFile.enhancements.length > 0 && (
            <div className="px-4 py-2.5 bg-slate-950/80 border-b border-slate-800 text-xs">
              <span className="font-semibold text-amber-400 uppercase text-[10px] tracking-wider block mb-1">
                Implemented Fixes & Architecture:
              </span>
              <div className="flex flex-wrap gap-2">
                {activeFile.enhancements.map((e, idx) => (
                  <span key={idx} className="inline-flex items-center space-x-1 text-slate-300 bg-slate-800/80 px-2 py-0.5 rounded border border-slate-700/50">
                    <CheckCircle2 className="w-3 h-3 text-emerald-400 shrink-0" />
                    <span>{e}</span>
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Code Body */}
          <div className="flex-1 p-4 overflow-auto font-mono text-xs text-slate-300 bg-slate-950/60 leading-relaxed scrollbar-thin">
            <pre>
              <code>{activeFile.content}</code>
            </pre>
          </div>
        </div>
      </div>
    </div>
  );
};
