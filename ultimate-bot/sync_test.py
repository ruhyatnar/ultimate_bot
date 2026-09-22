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
  E. Active-trade sync: inject an active_trades row -> /api/status data.trades
     shows it with entry/stop/TP.
  F. Config sync: /api/status config exposes non-credential keys only.
  G. Config live-refresh: the monitor re-reads .env when its mtime changes, so
     an out-of-band edit is served without restarting the monitor.

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
        _write_json(control_path, {"paused": False, "pause_reason": ""})
        ok = wait_for(
            lambda: (
                api(port_num, "/api/status").get("control", {}).get("paused")
                is False
            ),
            15,
        )
        check("resume reflected in /api/status control", ok)

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
