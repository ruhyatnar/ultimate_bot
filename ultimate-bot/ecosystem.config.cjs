// PM2 process definitions for the trading engine + web monitor.
//
// Log management:
//   * `log_file` was removed — it is not an app-level PM2 option, and it made
//     the same stream land in pm2-combined.log AND pm2-out.log (both files
//     reached an identical 355 MB before being truncated).
//   * PM2 core does NOT rotate app logs; `max_size`/`retain` in this file
//     would be silently ignored. PM2's real rotation lives in the separate
//     pm2-logrotate module (`pm2 install pm2-logrotate`), which only covers
//     ~/.pm2/logs — not this folder. Run `pm2 logrotate -u $USER` to install a
//     system logrotate config if these files ever need capping.
//   * In practice the cap lives in the app: main.py/status.py log to
//     ./logs/trading.log through a bounded RotatingFileHandler (10 MB x 5), and
//     stdout growth is small as long as LOG_LEVEL stays at INFO. At DEBUG the
//     stdout capture grew 355 MB in ~6 hours — keep LOG_LEVEL=INFO in .env.
//   * `merge_logs` keeps clustered workers in one stream; with fork mode it is
//     a no-op but harmless.
module.exports = {
  apps: [
    {
      name: 'ultimate-bot',
      script: 'main.py',
      interpreter: './venv/bin/python3',
      cwd: __dirname,
      max_memory_restart: '2G',
      // Crash-loop breaker: a config/boot error (e.g. a bad .env value raising
      // in load_config) used to spin the engine hundreds of times per day.
      // After 5 fast failures PM2 stops retrying (errored state) so the bad
      // value is visible in `pm2 ls` instead of burning the API weight budget.
      max_restarts: 5,
      min_uptime: '30s',
      restart_delay: 3000,
      watch: false,
      env: {
        NODE_ENV: 'production'
      },
      error_file: './logs/pm2-error.log',
      out_file: './logs/pm2-out.log',
      merge_logs: true,
      time: true
    },
    {
      name: 'bot-web-monitor',
      script: 'status.py',
      args: '--web 3000',
      interpreter: './venv/bin/python3',
      cwd: __dirname,
      watch: false,
      max_restarts: 5,
      min_uptime: '30s',
      restart_delay: 3000,
      error_file: './logs/web-error.log',
      out_file: './logs/web-out.log',
      merge_logs: true,
      time: true
    }
  ]
};
