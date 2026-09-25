#!/usr/bin/env python3
"""Soak report: summarize the 24h paper soak of the live engine.

Reads the REAL production database (data/trading.db) and the PM2 engine log
(logs/trading.log) and prints:
  - Engine uptime, restart count and process state (via `pm2 jlist`)
  - Paper equity, realized PnL (daily + total), open positions
  - Signal activity: BUY signals fired per symbol (from the log)
  - Entries/exits with reasons and per-trade PnL (from the orders table)
  - Win/loss stats reconciliation (closed == W + L + B) and streaks
  - Error/warning counts (log health)
  - DB size + order count (sanity)

Usage:
  ./venv/bin/python3 soak_report.py            # from ultimate-bot/
  npm run soak:report                          # from the repo root
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import trade_stats  # noqa: E402  (one definition of "an exit", see module)

DB_PATH = os.path.join(ROOT, "data", "trading.db")
LOG_PATH = os.path.join(ROOT, "logs", "trading.log")

GREEN, RED, YELLOW, BOLD, DIM, RESET = ("\033[92m", "\033[91m", "\033[93m", "\033[1m", "\033[2m", "\033[0m")


def hr():
    print("=" * 72)


def main():
    hr()
    print(f"{BOLD} SOAK REPORT — 24h paper soak of the Ultimate Binance Bot{RESET}")
    print(f" Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    hr()

    # ---- PM2 process health ----
    try:
        jlist = json.loads(subprocess.run(["pm2", "jlist"], capture_output=True, text=True, timeout=15).stdout or "[]")
        bot = next((a for a in jlist if a.get("name") == "ultimate-bot"), None)
        mon = next((a for a in jlist if a.get("name") == "bot-web-monitor"), None)
        if bot:
            st = bot.get("pm2_env", {})
            up_s = int(time.time()) - int(st.get("pm_uptime", 0)) / 1000 if st.get("pm_uptime") else 0
            print(f" Engine (PM2) : {bot.get('pm2_env', {}).get('status', '?')}"
                  f"  uptime {int(up_s // 3600)}h{int(up_s % 3600 // 60):02d}m"
                  f"  restarts {st.get('restart_time', 0)}"
                  f"  memory {bot.get('monit', {}).get('memory', 0) / 1e6:.0f}MB")
        else:
            print(f"{YELLOW} Engine (PM2) : not found in pm2 jlist{RESET}")
        if mon:
            print(f" Monitor (PM2): {mon.get('pm2_env', {}).get('status', '?')}"
                  f"  restarts {mon.get('pm2_env', {}).get('restart_time', 0)}")
    except Exception as e:
        print(f"{YELLOW} PM2 query failed: {e}{RESET}")

    if not os.path.exists(DB_PATH):
        print(f"{RED} No database at {DB_PATH} — engine may not have started.{RESET}")
        return 1

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # ---- Risk state / equity ----
    risk = {r["key"]: r["value"] for r in cur.execute("SELECT key, value FROM risk_state").fetchall()}
    equity = float(risk.get("paper_balance") or risk.get("total_equity") or 0)
    daily_pnl = float(risk.get("daily_pnl") or 0)
    monitored = json.loads(risk["monitored_symbols"]) if "monitored_symbols" in risk else []
    scanned = json.loads(risk["scanned_pairs"]) if "scanned_pairs" in risk else []

    print(f"\n{BOLD} ACCOUNT{RESET}")
    print(f"  Paper equity        : ${equity:,.2f}")
    print(f"  Daily realized PnL  : ${daily_pnl:+,.2f}")
    print(f"  Monitored symbols   : {len(monitored)} {monitored[:6]}{'...' if len(monitored) > 6 else ''}")
    print(f"  Screener candidates : {len(scanned)} pairs in last scan")

    # ---- Active trades ----
    trades = cur.execute("SELECT * FROM active_trades").fetchall()
    print(f"\n{BOLD} OPEN POSITIONS ({len(trades)}){RESET}")
    for t in trades:
        notional = float(t["entry_price"]) * float(t["quantity"])
        print(f"  {t['symbol']:<12} entry {float(t['entry_price']):.4f}  qty {float(t['quantity']):.4f}"
              f"  notional ${notional:.2f}  SL {float(t['stop_price']):.4f}  TP {float(t['take_profit']):.4f}")
    if not trades:
        print(f"  {DIM}none{RESET}")

    # ---- Orders / trade stats ----
    # ONE definition of a closed trade, shared with the web monitor's stats, the
    # soak card and the watchdog (trade_stats): the engine's own `exit_reason`
    # when the DB has been migrated (else a non-zero realized PnL), with exit
    # LEGS attributed to the trade they belong to. The `side='SELL'` form used
    # here silently dropped every futures SHORT exit — a short is closed with a
    # BUY — and counted a scale-out's two legs as two trades, so this report
    # disagreed with the dashboard about the same run.
    total_orders = int(cur.execute("SELECT COUNT(*) FROM orders").fetchone()[0] or 0)
    exit_pred = trade_stats.exit_predicate(trade_stats.has_exit_reason(cur))
    row = cur.execute(trade_stats.trades_totals_sql(exit_pred)).fetchone()
    closed, wins, losses = int(row["closed"] or 0), int(row["wins"] or 0), int(row["losses"] or 0)
    realized = float(row["pnl"] or 0)
    be = max(0, closed - wins - losses)
    wr = wins / closed * 100 if closed else 0.0

    print(f"\n{BOLD} TRADING ACTIVITY{RESET}")
    print(f"  Orders placed       : {total_orders}")
    print(f"  Closed trades       : {closed}  ({wins}W / {losses}L / {be}B)  win rate {wr:.1f}%")
    print(f"  Total realized PnL  : {GREEN if realized >= 0 else RED}${realized:+,.2f}{RESET} (net of fees)")

    exits = cur.execute(trade_stats.recent_exits_sql(exit_pred, 10)).fetchall()
    if exits:
        print(f"\n{BOLD} RECENT EXITS (last {len(exits)}){RESET}")
        for e in exits:
            pnl = float(e["profit_loss"] or 0)
            ts = datetime.fromtimestamp(int(e["created_at"]) / 1000).strftime("%m-%d %H:%M") if e["created_at"] else "?"
            color = GREEN if pnl > 0 else (RED if pnl < 0 else DIM)
            print(f"  {ts}  {e['symbol']:<12} {e['status']:<8} {color}{pnl:+.2f} USDT{RESET}")

    # ---- Signal activity from the log (CURRENT session only) ----
    # Uses the same reset-at-banner convention as LOG HEALTH below. Counting the
    # whole file made a long-dead session's signals show up as THIS soak's
    # activity, which reads like a bug next to "Orders placed: 0" even though it
    # is just history (the same trap the error counts already avoid).
    print(f"\n{BOLD} SIGNAL ACTIVITY (log, current session){RESET}")
    sig = {}
    n_signals = 0
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if "Starting MARKET-ONLY BOT" in line:
                    sig = {}
                    n_signals = 0
                    continue
                if "BUY signal for" in line:
                    m = re.search(r"BUY signal for (\w+)", line)
                    if m:
                        sig[m.group(1)] = sig.get(m.group(1), 0) + 1
                        n_signals += 1
    except FileNotFoundError:
        pass
    if sig:
        top = sorted(sig.items(), key=lambda x: -x[1])[:8]
        print(f"  BUY signals fired   : {n_signals}")
        for s, c in top:
            print(f"    {s:<12} x{c}")
    else:
        print(f"  {DIM}No BUY signals yet (rsi_dip fires ~1-3 trades/week per pair — patience).{RESET}")

    # ---- Log health (only the CURRENT session: lines after the newest
    # "Starting MARKET-ONLY BOT" banner — older sessions' errors are noise) ----
    errors, warnings = 0, 0
    crash_markers = 0
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            session_start = 0
            for i, line in enumerate(f):
                if "Starting MARKET-ONLY BOT" in line:
                    session_start = i
                    errors = warnings = crash_markers = 0
                    continue
                if i <= session_start:
                    continue
                if " - ERROR - " in line:
                    errors += 1
                elif " - WARNING - " in line:
                    warnings += 1
                if "Traceback" in line or "Task was destroyed" in line:
                    crash_markers += 1
    except FileNotFoundError:
        pass
    print(f"\n{BOLD} LOG HEALTH{RESET}")
    warn_col = GREEN if warnings < 20 else YELLOW
    err_col = GREEN if errors == 0 else RED
    print(f"  Warnings: {warn_col}{warnings}{RESET}   Errors: {err_col}{errors}{RESET}   Crash markers: {crash_markers}")

    # ---- Streaks ----
    print(f"\n{BOLD} STREAKS / COOLDOWNS{RESET}")
    streak_rows = [(k, v) for k, v in risk.items() if k.startswith("risk_")]
    active_streaks = []
    for k, v in streak_rows:
        try:
            st = json.loads(v)
            if isinstance(st, dict) and (st.get("loss_streak") or st.get("win_streak") or st.get("cooldown_until", 0) > time.time()):
                active_streaks.append((k[5:], st))
        except Exception:
            pass
    if active_streaks:
        for s, st in active_streaks[:8]:
            cd = st.get("cooldown_until", 0)
            cd_str = f" cooldown until {datetime.fromtimestamp(cd).strftime('%m-%d %H:%M')}" if cd > time.time() else ""
            print(f"  {s:<12} W{st.get('win_streak', 0)} / L{st.get('loss_streak', 0)}{cd_str}")
    else:
        print(f"  {DIM}all symbols at 0W/0L, no cooldowns armed{RESET}")

    conn.close()
    hr()
    print(f"{DIM} Re-run any time: npm run soak:report. Soak verdict: judge after a full 24h{RESET}")
    print(f"{DIM} of runtime (and ideally a full week — rsi_dip trades are low-frequency).{RESET}")
    hr()
    return 0


if __name__ == "__main__":
    sys.exit(main())
