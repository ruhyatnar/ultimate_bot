#!/usr/bin/env python3
"""Engine <-> Web Monitor sync integration test (paper mode, isolated DB).

Verifies the full data chain end to end:
  A. Boot engine (PAPER_TRADE=true, temp DB) -> /api/status shows RUNNING,
     paper equity from risk_state, sanitized config (secrets stripped).
  B. Remote-control sync: write pause into CONTROL_FILE -> /api/status.control
     shows paused=true; resume -> false.
  B2. Realtime transport sync: engine-published ws_streams health (the dashboard's
     stream lights) + balance provenance, and the SAME fields over the /ws push
     so the HTTP and WebSocket transports can never disagree.
  C. Streak sync: inject a per-symbol risk_<SYM> JSON blob into risk_state ->
     /api/status aggregates top-level win_streak/loss_streak/cooldown_until.
  D. Stats sync: inject a closed SELL exit order with PnL -> winning/losing
     counts and total_realized_pnl reflect it (closed = W+L+B reconciles).
  D2. Exit attribution served live: the engine's OWN reason reaches /api/status
     verbatim (not re-derived from the PnL sign), a scale-out's legs count as one
     trade, a short's BUY closing order is a closed trade, and rows the monitor
     must still infer are flagged.
  E. Active-trade sync: inject an active_trades row -> /api/status data.trades
     shows it with entry/stop/TP.
  B1. Loop heartbeat: the engine publishes a per-iteration heartbeat (so the
     dashboard's loop light cannot freeze while the engine is healthy) and it
     ACKNOWLEDGES the pause the monitor requested.
  E2. Position mark sync: inject the engine's futures_state snapshot -> the served
     trade row carries the ENGINE's mark / floating PnL / exchange size, and an
     untracked exchange position is counted without double-counting PnL.
  E3. Roadmap watchlist sync: the capital roadmap analyses the engine's LIVE
     watched pairs (risk_state.monitored_symbols), not a list baked into
     capital_roadmap.py, a rotated watchlist invalidates its cache, and a FAILED
     refresh is backed off instead of being retried (with 15s network timeouts)
     on every one of the /ws pusher's 1 Hz payload builds.
  E4. Soak payload contract: /api/status always carries a `soak` section (null
     hides the card) and a live soak exposes the fields the card reads.
  F. Config live-refresh: the monitor re-reads .env when its mtime changes, so
     an out-of-band edit is served without restarting the monitor.
  H. Engine-log contract: /api/logs cannot be asked for the whole file (0 and
     negative `lines=` used to return every byte), serves only complete records,
     and the /ws incremental push delivers appended lines as whole records.
  G. Clean shutdown: SIGTERM after the sync writes exits 0 and releases the lock.

Engine is left RUNNING during B–E so the monitor reads live state; SIGTERM at
the end must still exit cleanly (exit 0, lock released).

Usage:
    ./venv/bin/python3 sync_test.py     # from ultimate-bot/ (or repo root: npm run test:sync)

Exit code 0 = all checks passed, 1 = failure.
"""

import fcntl
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.join(ROOT, "venv", "bin", "python3")
PYTHON = VENV_PY if os.path.exists(VENV_PY) else sys.executable
LOCK_FILE = "/tmp/ultimate_bot.sync-test.lock"  # test engine runs with LOCK_SCOPE=sync-test

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(
        f"  [{'PASS' if ok else 'FAIL'}] {name}"
        + (f" — {detail}" if detail else "")
    )
    return bool(ok)


def wait_for(pred, timeout, interval=0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return False


def lock_held():
    if not os.path.exists(LOCK_FILE):
        return False
    try:
        with open(LOCK_FILE, "r") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True
    except Exception:
        return False


def api(port, path):
    with urllib.request.urlopen(
        f"http://127.0.0.1:{port}{path}", timeout=5
    ) as r:
        return json.loads(r.read().decode())


def db_write(db_path, sql, params=()):
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def main():
    if lock_held():
        print("FATAL: another engine instance holds the lock; stop it first.")
        return 2

    tmp = tempfile.mkdtemp(prefix="bot_sync_")
    db_path = os.path.join(tmp, "trading.db")
    control_path = os.path.join(tmp, "engine_control.json")
    env = dict(os.environ)
    env.update(
        {
            # Pin the market: tests exercise the proven spot path even when the
            # live engine on this box runs MARKET=futures.
            "MARKET": "spot",
            "PAPER_TRADE": "true",
            "LOCK_SCOPE": "sync-test",
            "DB_PATH": db_path,
            "CONTROL_FILE": control_path,
            "LOG_FILE": os.path.join(tmp, "trading.log"),
            "STATIC_SYMBOLS": "BTCUSDT,ETHUSDT",
            "DYNAMIC_SYMBOLS": "false",
            "MAX_SYMBOLS": "2",
            "SIGNAL_INTERVAL": "10",
        }
    )
    engine_log = os.path.join(tmp, "engine.stdout.log")
    monitor_log = os.path.join(tmp, "monitor.stdout.log")
    engine = monitor = None
    try:
        print("=== A. Boot engine + monitor ===")
        with open(engine_log, "w") as f:
            engine = subprocess.Popen(
                [PYTHON, "main.py"],
                cwd=ROOT,
                env=env,
                stdout=f,
                stderr=subprocess.STDOUT,
            )

        def db_ready():
            if not os.path.exists(db_path):
                return False
            try:
                conn = sqlite3.connect(
                    f"file:{db_path}?mode=ro", uri=True, timeout=3
                )
                try:
                    row = conn.execute(
                        "SELECT value FROM risk_state WHERE key='paper_balance'"
                    ).fetchone()
                    return row is not None and float(row[0]) > 0
                finally:
                    conn.close()
            except sqlite3.Error:
                return False

        booted = wait_for(db_ready, 90)
        check("engine booted with paper_balance risk state", booted)
        if not booted:
            with open(engine_log) as f:
                print("".join(f.readlines()[-15:]))
            return 1

        port = socket.socket()
        port.bind(("127.0.0.1", 0))
        port_num = port.getsockname()[1]
        port.close()
        with open(monitor_log, "w") as f:
            monitor = subprocess.Popen(
                [PYTHON, "status.py", "--web", str(port_num)],
                cwd=ROOT,
                env=env,
                stdout=f,
                stderr=subprocess.STDOUT,
            )
        if not wait_for(lambda: _api_ok(port_num), 30):
            check("monitor serving /api/status", False)
            return 1
        check("monitor serving /api/status", True)

        snap = api(port_num, "/api/status")
        check(
            "status reports engine RUNNING",
            "RUNNING" in str(snap.get("process", "")),
        )
        eq = float(snap["data"]["risk"].get("paper_balance", 0) or 0)
        check(
            "paper equity synced from risk_state", eq > 0, f"paper_balance={eq}"
        )
        cfg = snap.get("config", {})
        check(
            "config sanitized (no secrets)",
            "BINANCE_API_KEY" not in cfg
            and "DISCORD_WEBHOOK_URL" not in cfg
            and "PRESET" in cfg,
        )

        print("=== B. Remote-control pause/resume sync ===")
        _write_json(control_path, {"paused": True, "pause_reason": "sync-test"})
        ok = wait_for(
            lambda: (
                api(port_num, "/api/status").get("control", {}).get("paused")
                is True
            ),
            15,
        )
        check("pause reflected in /api/status control", ok)

        # B1. The pause ACKNOWLEDGEMENT. The control file only records what the
        # monitor ASKED for; the engine applies it at the top of its next loop
        # iteration and reports that in its heartbeat, which is what lets the UI
        # distinguish "paused" from "pause requested, not applied yet".
        def loop_state():
            return api(port_num, "/api/status").get("loop_state") or {}

        ok = wait_for(lambda: isinstance(loop_state().get("cycle"), int), 45)
        beat = loop_state()
        check(
            "engine publishes a decision-loop heartbeat",
            ok and beat.get("cycle", 0) > 0,
            f"cycle={beat.get('cycle')} phase={beat.get('phase')}",
        )
        check(
            "heartbeat carries a fresh monitor-computed age",
            isinstance(beat.get("age_s"), (int, float))
            and beat["age_s"] < 60,
            f"age_s={beat.get('age_s')}",
        )
        ok = wait_for(
            lambda: loop_state().get("paused_applied") is True, 45
        )
        check(
            "heartbeat ACKNOWLEDGES the pause (requested != applied)",
            ok,
            f"paused_applied={loop_state().get('paused_applied')} "
            f"phase={loop_state().get('phase')}",
        )
        _write_json(control_path, {"paused": False, "pause_reason": ""})
        ok = wait_for(
            lambda: (
                api(port_num, "/api/status").get("control", {}).get("paused")
                is False
            ),
            15,
        )
        check("resume reflected in /api/status control", ok)
        ok = wait_for(lambda: loop_state().get("paused_applied") is False, 45)
        check(
            "heartbeat clears the acknowledgement after resume",
            ok,
            f"paused_applied={loop_state().get('paused_applied')} "
            f"phase={loop_state().get('phase')}",
        )

        print("=== B2. Realtime transport lights + balance provenance ===")
        ok = wait_for(
            lambda: isinstance(
                api(port_num, "/api/status").get("ws_streams"), dict
            ),
            45,
        )
        snap = api(port_num, "/api/status")
        ws_health = snap.get("ws_streams") or {}
        check(
            "engine publishes ws_streams transport health",
            ok and {"market", "all_tickers", "order_api"} <= set(ws_health),
            f"keys={sorted(ws_health)}",
        )
        check(
            "all-market stream light carries freshness",
            "connected" in (ws_health.get("all_tickers") or {}),
            f"all_tickers={ws_health.get('all_tickers')}",
        )
        bal = snap.get("balance") or {}
        check(
            "balance carries transport provenance",
            bal.get("source") in ("paper", "ws", "rest", "db"),
            f"source={bal.get('source')}",
        )
        wframe = ws_snapshot(port_num)
        check("/ws push delivers a frame", wframe is not None)
        if wframe:
            check(
                "/ws frame carries ws_streams",
                isinstance(wframe.get("ws_streams"), dict),
            )
            wbal = wframe.get("balance") or {}
            check(
                "HTTP and /ws agree on balance provenance",
                wbal.get("source") == bal.get("source"),
                f"http={bal.get('source')} ws={wbal.get('source')}",
            )
        # The poke the dashboard relies on: the periodic pusher must actually
        # reach a connected socket (not just answer the connect handshake).
        pushes = ws_status_pushes(port_num, 4)
        check(
            "the monitor PUSHES status over /ws (not only the connect snapshot)",
            pushes >= 2,
            f"pusher frames in 4s={pushes}",
        )

        print("=== C. Streak/cooldown aggregation sync ===")
        # NEW CONTRACT: streaks are ACCOUNT-LEVEL and displayed verbatim (the
        # engine persists them under these exact keys). A per-symbol blob must
        # never override them — that once hid a MAX_LOSS_STREAK breaker trip
        # behind a stale per-symbol blob. Per-symbol COOLDOWNS still surface.
        db_write(
            db_path,
            "INSERT OR REPLACE INTO risk_state (key, value, updated_at) VALUES (?,?,?)",
            ("loss_streak", "2", int(time.time() * 1000)),
        )
        db_write(
            db_path,
            "INSERT OR REPLACE INTO risk_state (key, value, updated_at) VALUES (?,?,?)",
            ("win_streak", "1", int(time.time() * 1000)),
        )
        sym_state = {
            "loss_streak": 3,
            "win_streak": 2,
            "cooldown_until": int(time.time()) + 3600,
        }
        db_write(
            db_path,
            "INSERT OR REPLACE INTO risk_state (key, value, updated_at) VALUES (?,?,?)",
            ("risk_TESTUSDT", json.dumps(sym_state), int(time.time() * 1000)),
        )
        # monitor re-reads DB per request (no cache on risk section)
        ok = wait_for(lambda: _streaks_ready(port_num), 10)
        snap = api(port_num, "/api/status")
        risk = snap["data"]["risk"]
        check(
            "account loss_streak displayed verbatim (not clobbered by per-symbol)",
            risk.get("loss_streak") == "2",
            f"loss_streak={risk.get('loss_streak')}",
        )
        check(
            "account win_streak displayed verbatim (not clobbered by per-symbol)",
            risk.get("win_streak") == "1",
            f"win_streak={risk.get('win_streak')}",
        )
        check(
            "per-symbol cooldown_until aggregated to top level",
            abs(
                int(risk.get("cooldown_until", "0"))
                - sym_state["cooldown_until"]
            )
            < 5,
            f"cooldown_until={risk.get('cooldown_until')}",
        )
        stats = snap["data"]["stats"]
        check(
            "stats surface account streaks",
            str(stats.get("loss_streak")) == "2",
            f"stats.loss_streak={stats.get('loss_streak')}",
        )

        print("=== D. Trade stats sync (closed SELL exit) ===")
        now_ms = int(time.time() * 1000)
        db_write(
            db_path,
            "INSERT INTO orders (order_id, symbol, side, order_type, price, quantity, executed_qty,"
            " status, created_at, updated_at, profit_loss, avg_fill_price)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "sync_test_exit_1",
                "TESTUSDT",
                "SELL",
                "MARKET",
                100.0,
                1.0,
                1.0,
                "FILLED",
                now_ms,
                now_ms,
                2.50,
                100.0,
            ),
        )
        ok = wait_for(
            lambda: (
                int(
                    (
                        api(port_num, "/api/status")["data"]["stats"].get(
                            "closed_trades"
                        )
                        or 0
                    )
                )
                >= 1
            ),
            10,
        )
        check("closed SELL exit appears in stats", ok)
        stats = api(port_num, "/api/status")["data"]["stats"]
        closed = int(stats["closed_trades"])
        wins = int(stats["winning_trades"])
        losses = int(stats["losing_trades"])
        be = int(stats.get("breakeven_trades") or 0)
        check(
            "stats reconcile closed == W+L+B",
            closed == wins + losses + be,
            f"{wins}W/{losses}L/{be}B closed={closed}",
        )
        check(
            "winning PnL counted",
            wins == 1 and float(stats["total_realized_pnl"]) == 2.50,
            f"wins={wins} pnl={stats['total_realized_pnl']}",
        )

        print("=== D2. Exit attribution served by the live monitor ===")
        # Two defects met here. The reason: the monitor DERIVED each exit's label
        # from the PnL sign, so the engine's real reason (STOP_LOSS / TRAILING_STOP
        # / TIME_STOP / EOD_CLOSE) never reached the dashboard — a profitable
        # protective stop was coloured as a take-profit win. The identity: exits
        # were found by `side='SELL'`, which misses any closing order that is not a
        # spot-style SELL, and a scale-out's legs were counted as separate trades.
        # The engine now persists its own reason (orders.exit_reason, added by the
        # idempotent migration on boot) and the monitor must serve the corrected
        # attribution.
        def db_query(sql, params=()):
            conn = sqlite3.connect(db_path, timeout=5)
            try:
                return conn.execute(sql, params).fetchall()
            finally:
                conn.close()

        order_cols = {r[1] for r in db_query("PRAGMA table_info(orders)")}
        check(
            "engine migrated orders.exit_reason on boot",
            "exit_reason" in order_cols,
            f"cols={sorted(order_cols)}",
        )

        before = api(port_num, "/api/status")["data"]["stats"]
        before_closed = int(before.get("closed_trades") or 0)
        before_legs = int(before.get("exit_legs") or 0)
        ex_ms = int(time.time() * 1000)
        # (a) A scale-out on one symbol: a banked partial leg then the final exit,
        #     net +20 -> ONE winning trade across TWO exit legs.
        db_write(db_path,
                 "INSERT INTO orders (order_id, symbol, side, order_type, price, quantity,"
                 " executed_qty, status, created_at, updated_at, profit_loss, avg_fill_price,"
                 " exit_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("sync_test_scale_entry", "SCALEUSDT", "BUY", "MARKET", 10.0, 2.0, 2.0,
                  "FILLED", ex_ms, ex_ms, 0.0, 10.0, None))
        db_write(db_path,
                 "INSERT INTO orders (order_id, symbol, side, order_type, price, quantity,"
                 " executed_qty, status, created_at, updated_at, profit_loss, avg_fill_price,"
                 " exit_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("sync_test_scale_partial", "SCALEUSDT", "SELL", "MARKET", 0.0, 1.0, 1.0,
                  "CANCELED", ex_ms + 10, ex_ms + 10, 30.0, 12.0, "TAKE_PROFIT"))
        db_write(db_path,
                 "INSERT INTO orders (order_id, symbol, side, order_type, price, quantity,"
                 " executed_qty, status, created_at, updated_at, profit_loss, avg_fill_price,"
                 " exit_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("sync_test_scale_final", "SCALEUSDT", "SELL", "MARKET", 0.0, 1.0, 1.0,
                  "FILLED", ex_ms + 20, ex_ms + 20, -10.0, 8.0, "TRAILING_STOP"))
        # (b) A SHORT-style exit: the entry is a SELL and the closing order a BUY,
        #     which a `side='SELL'` filter would never see.
        db_write(db_path,
                 "INSERT INTO orders (order_id, symbol, side, order_type, price, quantity,"
                 " executed_qty, status, created_at, updated_at, profit_loss, avg_fill_price,"
                 " exit_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("sync_test_short_entry", "SHORTUSDT", "SELL", "MARKET", 5.0, 1.0, 1.0,
                  "FILLED", ex_ms + 30, ex_ms + 30, 0.0, 5.0, None))
        db_write(db_path,
                 "INSERT INTO orders (order_id, symbol, side, order_type, price, quantity,"
                 " executed_qty, status, created_at, updated_at, profit_loss, avg_fill_price,"
                 " exit_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("sync_test_short_exit", "SHORTUSDT", "BUY", "MARKET", 0.0, 1.0, 1.0,
                  "FILLED", ex_ms + 40, ex_ms + 40, 5.0, 4.5, "TAKE_PROFIT"))

        def _exit_attribution_ready():
            try:
                st = api(port_num, "/api/status")["data"]["stats"]
                return int(st.get("closed_trades") or 0) >= before_closed + 2
            except Exception:
                return False

        ok = wait_for(_exit_attribution_ready, 10)
        payload = api(port_num, "/api/status")
        stats = payload["data"]["stats"]
        rows = {
            o.get("order_id"): o
            for o in payload["data"].get("orders", [])
        }
        check("the monitor observed the injected exits (stats moved)", ok,
              f"closed={stats.get('closed_trades')} (before {before_closed})")
        closed = int(stats.get("closed_trades") or 0)
        legs = int(stats.get("exit_legs") or 0)
        # Deltas, not absolutes: the engine under test is LIVE against real market
        # data and may legitimately open and close its own paper trade during the
        # run, so an exact count would assert something this test does not control.
        check(
            "both injected exits reach the served stats",
            closed - before_closed >= 2 and legs - before_legs >= 3,
            f"legs {before_legs}->{legs}, trades {before_closed}->{closed}",
        )
        # Every trade is formed from at least one leg, so new_legs > new_trades is
        # exactly the statement "the scale-out's two legs were not counted as two
        # trades" — and it survives any engine activity during the run.
        check(
            "a scale-out's legs are not counted as separate trades",
            (legs - before_legs) > (closed - before_closed),
            f"new legs={legs - before_legs} new trades={closed - before_closed}",
        )
        check(
            "the engine's own reason is served verbatim (not inferred from the sign)",
            (rows.get("sync_test_scale_final") or {}).get("exit_reason") == "TRAILING_STOP"
            and (rows.get("sync_test_scale_final") or {}).get("exit_reason_inferred") is False,
            f"got={(rows.get('sync_test_scale_final') or {}).get('exit_reason')}",
        )
        check(
            "a profitable exit the engine called a TRAIL is not badged TAKE_PROFIT",
            (rows.get("sync_test_scale_partial") or {}).get("exit_reason") == "TAKE_PROFIT"
            and (rows.get("sync_test_scale_final") or {}).get("profit_loss") == -10.0,
        )
        check(
            "a short's BUY exit is served as a closed trade",
            (rows.get("sync_test_short_exit") or {}).get("exit_reason") == "TAKE_PROFIT"
            and (rows.get("sync_test_short_exit") or {}).get("exit_reason_inferred") is False,
            f"got={(rows.get('sync_test_short_exit') or {}).get('exit_reason')}",
        )
        check(
            "a short exit's entry price comes from the opposite (SELL) leg",
            float((rows.get("sync_test_short_exit") or {}).get("entry_price") or 0) == 5.0,
            f"entry_price={(rows.get('sync_test_short_exit') or {}).get('entry_price')}",
        )
        check(
            "an entry order is not served as an exit",
            (rows.get("sync_test_short_entry") or {}).get("exit_reason") is None,
            f"got={(rows.get('sync_test_short_entry') or {}).get('exit_reason')}",
        )
        check(
            "a legacy exit with no stored reason is flagged as inferred",
            (rows.get("sync_test_exit_1") or {}).get("exit_reason_inferred") is True,
            f"got={(rows.get('sync_test_exit_1') or {}).get('exit_reason_inferred')}",
        )
        total = float(stats.get("total_realized_pnl") or 0.0)
        wins = int(stats.get("winning_trades") or 0)
        losses = int(stats.get("losing_trades") or 0)
        be = int(stats.get("breakeven_trades") or 0)
        check(
            "stats reconcile closed == W+L+B after the scale-out",
            closed == wins + losses + be,
            f"{wins}W/{losses}L/{be}B closed={closed}",
        )
        check(
            "streaks are served as numbers (int-coercible), not raw risk_state strings",
            isinstance(stats.get("win_streak"), int)
            and isinstance(stats.get("loss_streak"), int),
            f"{type(stats.get('win_streak')).__name__}/{type(stats.get('loss_streak')).__name__}",
        )
        # Independent recomputation: the realized total is verified against the
        # exit predicate written out here, in the test, rather than re-read from
        # the serving code. If the monitor's predicate ever drifts (e.g. starts
        # counting zero-PnL rows again) this diverges.
        db_total = float(
            db_query(
                "SELECT COALESCE(SUM(profit_loss), 0) FROM orders WHERE"
                " exit_reason IS NOT NULL"
                " OR (profit_loss IS NOT NULL AND profit_loss != 0)"
            )[0][0]
            or 0.0
        )
        check(
            "served realized total matches an independent exit-predicate sum",
            abs(total - db_total) < 1e-6,
            f"served={total} sql={db_total}",
        )

        print("=== E. Active-trade sync ===")
        db_write(
            db_path,
            "INSERT OR REPLACE INTO active_trades (symbol, entry_price, side, quantity, entry_time,"
            " stop_price, take_profit, atr, trailing_active, trailing_stop, breakeven_activated, order_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "TESTUSDT",
                100.0,
                "BUY",
                1.0,
                now_ms,
                98.0,
                104.0,
                0.5,
                0,
                98.0,
                0,
                "sync_test_entry_1",
            ),
        )
        ok = wait_for(
            lambda: any(
                t.get("symbol") == "TESTUSDT"
                for t in api(port_num, "/api/status")["data"].get("trades", [])
            ),
            10,
        )
        trades = [
            t
            for t in api(port_num, "/api/status")["data"].get("trades", [])
            if t.get("symbol") == "TESTUSDT"
        ]
        check("active trade visible in /api/status", ok and len(trades) == 1)
        if trades:
            t = trades[0]
            check(
                "active trade fields intact",
                float(t["entry_price"]) == 100.0
                and float(t["stop_price"]) == 98.0
                and float(t["take_profit"]) == 104.0,
            )

        print("=== E2. Position mark / floating PnL sync with the engine ===")
        # The dashboard's active-position row renders "Entry → Now", the change %,
        # the Unrealized column and the bracket-ladder marker from `current_price`,
        # which the engine has never written into active_trades. The monitor must
        # therefore stamp the ENGINE's own published position state onto the row —
        # here a live futures_state snapshot (uPnL 2.5 on 1.0 @ 100 -> mark 102.5)
        # plus a second, UNTRACKED exchange position on another symbol.
        db_write(
            db_path,
            "INSERT OR REPLACE INTO risk_state (key, value, updated_at)"
            " VALUES ('futures_state', ?, ?)",
            (
                json.dumps(
                    {
                        "positions": [
                            {
                                "symbol": "TESTUSDT",
                                "amount": 1.0,
                                "entry_price": 100.0,
                                "unrealized_pnl": 2.5,
                            },
                            {
                                "symbol": "NAKEDUSDT",
                                "amount": 2.0,
                                "entry_price": 10.0,
                                "unrealized_pnl": -0.3,
                            },
                        ],
                        "leverage": 2,
                        "margin_type": "CROSSED",
                        "updated_ms": int(time.time() * 1000),
                    }
                ),
                int(time.time() * 1000),
            ),
        )
        ok = wait_for(
            lambda: any(
                t.get("current_price") is not None
                for t in api(port_num, "/api/status")["data"].get("trades", [])
                if t.get("symbol") == "TESTUSDT"
            ),
            10,
        )
        payload = api(port_num, "/api/status")
        marked = [
            t
            for t in payload["data"].get("trades", [])
            if t.get("symbol") == "TESTUSDT"
        ]
        check("engine mark reaches the served trade row", bool(ok and marked))
        if marked:
            t = marked[0]
            check(
                "mark derived from the engine's uPnL (100 + 2.5/1.0 = 102.5)",
                abs(float(t.get("current_price", 0)) - 102.5) < 1e-9,
                f"current_price={t.get('current_price')}",
            )
            check(
                "engine floating PnL served verbatim",
                abs(float(t.get("unrealized_pnl", 0)) - 2.5) < 1e-9,
                f"unrealized_pnl={t.get('unrealized_pnl')}",
            )
            check(
                "exchange-side size served",
                float(t.get("live_qty", 0)) == 1.0,
                f"live_qty={t.get('live_qty')}",
            )
            check(
                "provenance names the engine snapshot",
                t.get("position_source") == "engine-futures",
                f"position_source={t.get('position_source')}",
            )
        # Derive the expectations from what is ACTUALLY being managed. The engine
        # under test is live against real market data with its own signal loop, so
        # it may legitimately open a paper trade mid-run. A hardcoded `1` silently
        # asserted "the engine stayed flat for the whole run" — not this test's
        # subject, and not something the engine guarantees — which made these two
        # checks flaky (reproduced by injecting a second active_trades row). Assert
        # the PARTITION instead: tracked = the symbols the served rows cover,
        # untracked = published positions none of them covers, open = their union.
        served_syms = {
            str(t.get("symbol") or "").upper()
            for t in payload["data"].get("trades", [])
        }
        injected_live = {"TESTUSDT", "NAKEDUSDT"}      # the snapshot injected above
        exp_tracked = len(served_syms)
        exp_untracked = len(injected_live - served_syms)
        check(
            "tracked / untracked / live counts reconcile",
            payload.get("positions_tracked") == exp_tracked
            and payload.get("positions_untracked") == exp_untracked
            and payload.get("positions_live") == len(injected_live),
            f"tracked={payload.get('positions_tracked')} (rows {sorted(served_syms)})"
            f" untracked={payload.get('positions_untracked')} (exp {exp_untracked})"
            f" live={payload.get('positions_live')}",
        )
        check(
            "open_positions is tracked ∪ live (untracked exposure counted once)",
            payload.get("open_positions") == len(served_syms | injected_live),
            f"open_positions={payload.get('open_positions')}"
            f" (rows {sorted(served_syms)}, live {sorted(injected_live)})",
        )
        check(
            "untracked_pnl counts ONLY the untracked position (no double count)",
            abs(float(payload.get("untracked_pnl", 0)) - (-0.3)) < 1e-9,
            f"untracked_pnl={payload.get('untracked_pnl')}",
        )
        # The snapshot injected above is WS-cache shaped: per-position leverage /
        # margin_type are absent and there is no markPrice (only the positionRisk
        # fallback carries those). The monitor must normalise them from the
        # snapshot's own top level and from the published uPnL, or the Futures
        # card renders `1× CROSSED` above a `—×` column for the same position.
        served = (payload.get("futures") or {}).get("positions") or []
        pos = next((p for p in served if p.get("symbol") == "TESTUSDT"), {})
        check(
            "per-position leverage/margin normalised from the snapshot",
            pos.get("leverage") == 2 and pos.get("margin_type") == "CROSSED",
            f"leverage={pos.get('leverage')} margin={pos.get('margin_type')}",
        )
        check(
            "per-position mark derived from the engine's uPnL",
            abs(float(pos.get("mark_price") or 0) - 102.5) < 1e-9,
            f"mark_price={pos.get('mark_price')}",
        )
        # Clean up the synthetic snapshot so later sections see the real shape.
        db_write(
            db_path, "DELETE FROM risk_state WHERE key='futures_state'", ()
        )

        print(
            "=== E3. Capital roadmap follows the engine's live watchlist ==="
        )
        # The roadmap's pair list used to be a literal inside capital_roadmap.py,
        # so the card analysed a DIFFERENT universe from the one the engine trades
        # (DYNAMIC_SYMBOLS rotates the watchlist with the screener). What it
        # reports must be the pairs the engine actually has subscribed.
        try:
            import importlib

            st = importlib.import_module("status")
            import capital_roadmap as cr

            # The real module fetches fapi over the network; stub it so this
            # check is deterministic and offline.
            saved = (cr.futures_floors, cr.futures_prices, cr.latest_funding)
            # Floors picked to exercise BOTH outcomes whatever the deployed config:
            # one far below any plausible proven notional, one far above it.
            cr.futures_floors = lambda syms: {
                s: (1e9 if s == "BIGUSDT" else 0.01) for s in syms
            }
            cr.futures_prices = lambda syms: {s: 1.0 for s in syms}
            cr.latest_funding = lambda s: 0.0001
            try:
                # Read the payload from a PRIVATE COPY of the test DB: the serving
                # monitor holds the original, and seeding total_equity there would
                # make IT hit the exchange on every render. Copied via SQLite's
                # backup API, not shutil — the engine runs WAL, so the schema can
                # live entirely in the -wal sidecar and a bare file copy would
                # arrive with no tables at all.
                watch_db = os.path.join(tmp, "watchlist.db")
                if os.path.exists(watch_db):
                    os.remove(watch_db)
                src_db = sqlite3.connect(db_path)
                dst_db = sqlite3.connect(watch_db)
                try:
                    src_db.backup(dst_db)
                finally:
                    src_db.close()
                    dst_db.close()
                st._ROADMAP_CACHE.update(
                    {"data": None, "ts": 0.0, "pairs": None}
                )
                db_write(
                    watch_db,
                    "INSERT OR REPLACE INTO risk_state (key, value, updated_at)"
                    " VALUES ('total_equity', ?, ?)",
                    ("25.0", int(time.time() * 1000)),
                )
                watch = ["AAAUSDT", "BIGUSDT"]
                db_write(
                    watch_db,
                    "INSERT OR REPLACE INTO risk_state (key, value, updated_at)"
                    " VALUES ('monitored_symbols', ?, ?)",
                    (json.dumps(watch), int(time.time() * 1000)),
                )
                payload = st.build_status_payload({}, watch_db)
                rm = payload.get("roadmap") or {}
                pairs = [p.get("symbol") for p in rm.get("pairs", [])]
                check(
                    "roadmap analyses the engine's watched pairs",
                    pairs == watch,
                    f"pairs={pairs} watch={watch}",
                )
                check(
                    "roadmap pair list == served monitored_symbols",
                    pairs == payload.get("monitored_symbols"),
                )
                check(
                    "roadmap reports which list it used",
                    rm.get("pairs_source") == "engine-watchlist",
                    f"pairs_source={rm.get('pairs_source')}",
                )
                # Derive the expectation from the served proven notional rather
                # than hardcoding it, so the check holds under any RISK_PER_TRADE
                # / SL_PERCENT and still catches a mis-partitioned list.
                proven = float(rm.get("proven_notional") or 0.0)
                exp_ok = [
                    s
                    for s, floor in (("AAAUSDT", 0.01), ("BIGUSDT", 1e9))
                    if proven >= floor
                ]
                check(
                    "floors decide ok/blocked over the watched set"
                    f" (proven notional ${proven:.2f})",
                    rm.get("ok_pairs") == exp_ok
                    and rm.get("blocked_pairs") == [
                        s for s in watch if s not in exp_ok
                    ],
                    f"ok={rm.get('ok_pairs')} blocked={rm.get('blocked_pairs')}",
                )
                # Every pair must publish the equity at which ITS OWN floor is
                # cleared. The blocked chip used to render a literal '$24', which
                # was only ever right for the watchlist of the day; serving the
                # number per pair is what lets the card state the real one.
                sl_pct = float(rm.get("sl_percent") or 0.0)
                risk = float(rm.get("risk_per_trade") or 0.0)
                rows = {p.get("symbol"): p for p in rm.get("pairs", [])}
                mismatched = [
                    s
                    for s, r in rows.items()
                    if not (
                        risk > 0
                        and sl_pct > 0
                        and abs(
                            float(r.get("required_equity") or 0.0)
                            - float(r.get("floor") or 0.0) * sl_pct / risk
                        ) < 0.01
                    )
                ]
                check(
                    "each pair publishes its own floor-clearing threshold"
                    " (floor * SL% / RISK%)",
                    not mismatched,
                    f"mismatched={mismatched}",
                )
                blocked_rows = [
                    r
                    for r in rm.get("pairs", [])
                    if r.get("symbol") in (rm.get("blocked_pairs") or [])
                ]
                check(
                    "a blocked pair's threshold exceeds the proven notional"
                    f" (${proven:.2f})",
                    bool(blocked_rows)
                    and all(
                        float(r.get("required_equity") or 0.0) > proven
                        for r in blocked_rows
                    ),
                    f"blocked={[(r.get('symbol'), r.get('required_equity')) for r in blocked_rows]}",
                )
                # A rotated watchlist must invalidate the 10-minute cache, or the
                # card keeps reporting floors for pairs the engine has dropped and
                # omits the ones it just picked up.
                rotated = ["AAAUSDT"]
                db_write(
                    watch_db,
                    "INSERT OR REPLACE INTO risk_state (key, value, updated_at)"
                    " VALUES ('monitored_symbols', ?, ?)",
                    (json.dumps(rotated), int(time.time() * 1000)),
                )
                rm2 = (
                    st.build_status_payload({}, watch_db).get("roadmap") or {}
                )
                got2 = [p.get("symbol") for p in rm2.get("pairs", [])]
                check(
                    "a rotated watchlist invalidates the roadmap cache",
                    got2 == rotated,
                    f"pairs={got2} expected={rotated}",
                )
                # The soak section is always present (the dashboard's card decides
                # on null whether to render), and a real soak exposes the fields
                # the card reads. The card had no payload-contract test at all.
                soak = payload.get("soak")
                check(
                    "payload carries a soak section (null hides the card)",
                    "soak" in payload
                    and (soak is None or isinstance(soak, dict)),
                    f"soak={type(soak).__name__}",
                )
                if isinstance(soak, dict):
                    check(
                        "the soak section exposes the fields the card reads",
                        {"running", "status", "closed", "pnl"} <= set(soak),
                        f"keys={sorted(soak)}",
                    )
                # A roadmap refresh that FAILS must not be retried on every payload
                # build: the floors/prices/funding reads are the only blocking
                # network I/O in the payload path and the /ws pusher builds the
                # payload once a SECOND, so an unreachable fapi used to put a
                # 15s-timeout fetch in front of every push. The last good snapshot
                # keeps being served and the retry is rate-limited.
                good_pairs = [
                    p.get("symbol") for p in (rm2.get("pairs") or [])
                ]
                calls = {"n": 0}

                def _boom(*_a, **_kw):
                    calls["n"] += 1
                    raise OSError("fapi unreachable")

                real_compute = cr.compute_roadmap
                cr.compute_roadmap = _boom
                try:
                    # Stale beyond the success cache lifetime.
                    st._ROADMAP_CACHE["ts"] = time.time() - st._ROADMAP_RETRY_S - 5
                    st._ROADMAP_CACHE["fail_ts"] = 0.0
                    failed_1 = st.build_status_payload({}, watch_db).get("roadmap")
                    attempts = calls["n"]
                    failed_2 = st.build_status_payload({}, watch_db).get("roadmap")
                    check(
                        "a failed roadmap refresh is attempted once, then backed off",
                        attempts == 1 and calls["n"] == 1,
                        f"attempts={calls['n']}",
                    )
                    check(
                        "the last good roadmap survives a failed refresh",
                        isinstance(failed_1, dict)
                        and isinstance(failed_2, dict)
                        and [p.get("symbol") for p in failed_2.get("pairs") or []]
                        == good_pairs
                        and failed_2.get("age_s") is not None,
                        f"first={type(failed_1).__name__}"
                        f" second={type(failed_2).__name__}",
                    )
                    # Once the backoff expires the retry resumes (no permanent
                    # "never refresh again" state).
                    st._ROADMAP_CACHE["fail_ts"] = (
                        time.time() - st._ROADMAP_FAIL_BACKOFF_S - 1
                    )
                    st.build_status_payload({}, watch_db)
                    check(
                        "the roadmap retry resumes after the failure backoff",
                        calls["n"] == 2,
                        f"attempts={calls['n']}",
                    )
                finally:
                    cr.compute_roadmap = real_compute
            finally:
                cr.futures_floors, cr.futures_prices, cr.latest_funding = saved
        except Exception as e:
            check(
                "capital roadmap wiring available",
                False,
                f"{type(e).__name__}: {e}",
            )

        print(
            "=== F. Config live-refresh (monitor stays in sync with .env) ==="
        )
        # The monitor snapshots .env at startup; refresh_env_config must pick up
        # an out-of-band edit (manual change + engine reload) without a monitor
        # restart, or /api/status would advertise a stale configuration.
        try:
            import importlib

            st = importlib.import_module("status")
            tmp_env = os.path.join(tmp, "refresh.env")
            live = {}
            with open(tmp_env, "w") as f:
                f.write("SYNC_TEST_PROBE=11\n")
            st.refresh_env_config(live, tmp_env)
            check(
                "refresh_env_config loads a .env into the live dict",
                live.get("SYNC_TEST_PROBE") == "11",
                f"got={live.get('SYNC_TEST_PROBE')}",
            )
            time.sleep(1.1)  # guarantee a newer mtime
            with open(tmp_env, "w") as f:
                f.write("SYNC_TEST_PROBE=22\n")
            st.refresh_env_config(live, tmp_env)
            check(
                "refresh_env_config picks up an out-of-band edit (no restart)",
                live.get("SYNC_TEST_PROBE") == "22",
                f"got={live.get('SYNC_TEST_PROBE')}",
            )
        except Exception as e:
            check("refresh_env_config available", False, e)

        print("=== H. Engine-log endpoint + /ws log push ===")
        # The Engine Log tab tails these two transports. `lines=0` (and any
        # negative) used to reach readlines()[-0:] / [N:] — the WHOLE log, a
        # 10 MB JSON response per poll on an unauthenticated URL — and a record
        # caught mid-write was served truncated.
        log_file = env["LOG_FILE"]
        written = wait_for(
            lambda: os.path.exists(log_file) and os.path.getsize(log_file) > 0, 60
        )
        check("engine writes the log the monitor tails", written)
        if written:

            def read_log():
                with open(log_file, "rb") as f:
                    return f.read().decode("utf-8", "replace")

            def complete_records(text):
                return [r for r in text.splitlines(True) if r.endswith("\n")]

            pre = read_log()
            total = len(complete_records(pre))
            got5 = api(port_num, "/api/logs?lines=5").get("lines") or []
            post = read_log()
            check(
                "tail returns the requested number of records",
                0 < len(got5) <= 5 and total >= 5 and len(got5) == 5,
                f"got={len(got5)} file={total}",
            )
            check(
                "every served record is newline-terminated",
                bool(got5) and all(r.endswith("\n") for r in got5),
            )
            check("the served tail is a suffix of the real log", "".join(got5) in post)
            if pre == post:  # quiet file → the tail is exactly the newest records
                check(
                    "tail is exactly the newest records, in order",
                    got5 == complete_records(post)[-5:],
                    f"got={[r[:40] for r in got5]}",
                )

            zero = api(port_num, "/api/logs?lines=0").get("lines") or []
            check("lines=0 yields ONE record, not the whole log", len(zero) == 1,
                  f"got={len(zero)} of {total}")
            neg = api(port_num, "/api/logs?lines=-2").get("lines") or []
            check("a negative lines= yields ONE record, not the whole log", len(neg) == 1,
                  f"got={len(neg)} of {total}")
            huge = api(port_num, "/api/logs?lines=99999").get("lines") or []
            check("an over-large lines= is capped", 0 < len(huge) <= 500, f"got={len(huge)}")
            junk = api(port_num, "/api/logs?lines=abc").get("lines") or []
            check(
                "a non-numeric lines= falls back to the default, not to 1 record",
                len(junk) > len(zero) and len(junk) <= 120,
                f"got={len(junk)} zero={len(zero)} file={total}",
            )

            # A record caught mid-write must never be presented as a log line.
            with open(log_file, "a") as f:
                f.write("SYNC-LOG-UNTERMINATED")  # deliberately no newline
            last1 = api(port_num, "/api/logs?lines=1").get("lines") or []
            check(
                "a half-written record is not served truncated",
                bool(last1)
                and all(r.endswith("\n") for r in last1)
                and last1[-1] != "SYNC-LOG-UNTERMINATED",
                f"got={[r[:50] for r in last1]}",
            )

            marker = f"SYNC-LOG-PUSH-{int(time.time())}\n"
            with open(log_file, "a") as f:
                f.write(marker)
            pushed = ws_log_lines(port_num, 8)
            # `in`, not equality: the unterminated probe above was still dangling,
            # so the engine's next record (this marker) completes IT — which is
            # exactly the carry behaviour being verified.
            check(
                "/ws pushes log lines appended after connect",
                marker.strip() in "".join(pushed or []),
                f"frames={len(pushed or [])} got={[r[:45] for r in (pushed or [])[:2]]}",
            )
            check(
                "/ws log frames carry whole records",
                bool(pushed) and all(r.endswith("\n") for r in pushed),
                f"got={[r[:40] for r in (pushed or [])[:3]]}",
            )

        print("=== G. Clean shutdown after sync writes ===")
        engine.send_signal(signal.SIGTERM)
        exited = wait_for(lambda: engine.poll() is not None, 15, 0.25)
        check(
            "engine exits cleanly after DB writes",
            exited and engine.returncode == 0,
            f"rc={engine.returncode if exited else 'alive'}",
        )
        with open(engine_log) as f:
            etext = f.read()
        check("clean shutdown logged", "Shutdown complete." in etext)
        check("lock released", not lock_held())

        failed = [r for r in RESULTS if not r[1]]
        print(
            f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} sync checks passed"
        )
        return 1 if failed else 0
    finally:
        if engine and engine.poll() is None:
            engine.kill()
            engine.wait()
        if monitor and monitor.poll() is None:
            monitor.kill()
            monitor.wait()
        shutil.rmtree(tmp, ignore_errors=True)


def ws_snapshot(port, timeout=15):
    """First STATUS snapshot from the monitor's /ws push, or None.

    The socket first delivers control frames ({"type": "hello"}, log tails) and
    then the full status payload — the same object /api/status serves, so the two
    transports can be compared key by key.
    """
    try:
        from websockets.sync.client import connect
    except Exception:
        return None
    try:
        with connect(
            f"ws://127.0.0.1:{port}/ws", open_timeout=timeout, close_timeout=2
        ) as ws:
            deadline = time.time() + timeout
            while time.time() < deadline:
                raw = ws.recv(timeout=max(1.0, deadline - time.time()))
                try:
                    frame = json.loads(
                        raw if isinstance(raw, str) else raw.decode()
                    )
                except Exception:
                    continue
                if isinstance(frame, dict) and frame.get("process") is not None:
                    return frame
        return None
    except Exception:
        return None


def ws_status_pushes(port, seconds=4):
    """Count the status snapshots the PUSHER delivers within `seconds`.

    The per-connection thread sends one snapshot on connect, so only frames
    beyond that first one prove the periodic pusher reaches connected sockets.
    It silently pushed nothing while /ws still looked alive: client sockets were
    never registered, and the broadcast frame was built from a `str` (TypeError)
    on every tick, swallowed by the pusher's blanket `except`.
    """
    try:
        from websockets.sync.client import connect
    except Exception:
        return 0
    count = 0
    try:
        with connect(
            f"ws://127.0.0.1:{port}/ws", open_timeout=10, close_timeout=2
        ) as ws:
            deadline = time.time() + seconds
            while time.time() < deadline:
                try:
                    raw = ws.recv(timeout=max(0.5, deadline - time.time()))
                except Exception:
                    break
                try:
                    frame = json.loads(
                        raw if isinstance(raw, str) else raw.decode()
                    )
                except Exception:
                    continue
                if (
                    isinstance(frame, dict)
                    and frame.get("type") != "log"
                    and frame.get("process")
                ):
                    count += 1
    except Exception:
        return 0
    return max(0, count - 1)  # minus the connection thread's initial snapshot


def ws_log_lines(port, timeout=8):
    """Every log record the monitor's /ws channel pushes within `timeout`.

    Unlike `ws_snapshot` this keeps the `{"type": "log"}` frames, so the
    incremental tail (the Engine Log tab's primary transport) is verified end to
    end rather than assumed.
    """
    try:
        from websockets.sync.client import connect
    except Exception:
        return None
    out = []
    try:
        with connect(
            f"ws://127.0.0.1:{port}/ws", open_timeout=timeout, close_timeout=2
        ) as ws:
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    raw = ws.recv(timeout=max(0.5, deadline - time.time()))
                except Exception:
                    break
                try:
                    frame = json.loads(
                        raw if isinstance(raw, str) else raw.decode()
                    )
                except Exception:
                    continue
                if isinstance(frame, dict) and frame.get("type") == "log":
                    out.extend(frame.get("lines") or [])
    except Exception:
        return out
    return out


def _api_ok(port):
    try:
        return api(port, "/api/status").get("process") is not None
    except Exception:
        return False


def _streaks_ready(port):
    try:
        risk = api(port, "/api/status")["data"]["risk"]
        return risk.get("loss_streak") == "2" and risk.get("win_streak") == "1"
    except Exception:
        return False


def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


if __name__ == "__main__":
    sys.exit(main())
