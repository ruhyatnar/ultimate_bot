/**
 * UI smoke test runner — zero new dependencies.
 *
 * Bundles tests/smoke/dashboard.test.tsx with esbuild (already shipped inside
 * node_modules by vite) and executes it with Node's built-in `node:test`
 * runner. Renders the real LiveDashboard component tree with
 * react-dom/server and asserts the six dashboard sections appear.
 *
 * Usage: npm run test:ui
 */
import { build } from 'esbuild';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const outdir = mkdtempSync(path.join(tmpdir(), 'ui-smoke-'));
// CJS output: react-dom/server is CommonJS and dynamically requires node
// builtins (util, stream), which only resolves when the bundle itself is CJS.
const outfile = path.join(outdir, 'dashboard.test.cjs');

await build({
  entryPoints: [path.join(root, 'tests/smoke/dashboard.test.tsx')],
  bundle: true,
  format: 'cjs',
  platform: 'node',
  jsx: 'automatic',
  outfile,
  alias: { '@': root },
  define: { 'process.env.NODE_ENV': '"test"' },
  logLevel: 'info',
});

const res = spawnSync(process.execPath, ['--test', outfile], { stdio: 'inherit' });
process.exit(res.status ?? 1);
