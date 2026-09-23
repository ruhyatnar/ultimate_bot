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
  B1. Loop heartbeat: the engine publishes a per-iteration heartbeat (so the
     dashboard's loop light cannot freeze while the engine is healthy) and it
     ACKNOWLEDGES the pause the monitor requested.
  E2. Position mark sync: inject the engine's futures_state snapshot -> the served
     trade row carries the ENGINE's mark / floating PnL / exchange size, and an
     untracked exchange position is counted without double-counting PnL.
  E3. Roadmap watchlist sync: the capital roadmap analyses the engine's LIVE
     watched pairs (risk_state.monitored_symbols), not a list baked into
     capital_roadmap.py, and a rotated watchlist invalidates its cache.
  F. Config live-refresh: the monitor re-reads .env when its mtime changes, so
     an out-of-band edit is served without restarting the monitor.
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
        check(
            "tracked / untracked / live counts reconcile",
            payload.get("positions_tracked") == 1
            and payload.get("positions_untracked") == 1
            and payload.get("positions_live") == 2,
            f"{payload.get('positions_tracked')}/{payload.get('positions_untracked')}"
            f"/{payload.get('positions_live')}",
        )
        check(
            "open_positions counts the untracked exposure too",
            payload.get("open_positions") == 2,
            f"open_positions={payload.get('open_positions')}",
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
