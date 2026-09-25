#!/usr/bin/env python3
"""
Smoke test for the Ultimate Binance Bot engine.

Runs the real engine (`main.py`) in PAPER_TRADE mode against an isolated
temporary database and verifies, end-to-end:

  1. Engine boots and initializes the SQLite schema + risk state
  2. The single-instance lock is acquired (monitor reports RUNNING)
  3. A second engine instance is rejected ("Another instance is already running")
  4. The web monitor (`status.py --web`) serves /api/status with consistent
     win/loss statistics (closed = wins + losses + breakevens; BUY entries excluded)
  5. SIGTERM triggers a clean shutdown ("Shutdown complete.") within a timeout
     and releases the lock

The test never touches the real `./data/trading.db`, `.env` or logs — every path
is redirected into a temporary directory via environment variables (python-dotenv
does not override already-set env vars).

Usage:
    ./venv/bin/python3 smoke_test.py        # uses the project venv
    python3 smoke_test.py                   # falls back to current interpreter

Exit code 0 = all checks passed.
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
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import trade_stats  # noqa: E402  (one definition of "an exit", see module)
VENV_PY = os.path.join(ROOT, "venv", "bin", "python3")
PYTHON = VENV_PY if os.path.exists(VENV_PY) else sys.executable

LOCK_FILE = "/tmp/ultimate_bot.smoke-test.lock"  # test engine runs with LOCK_SCOPE=smoke-test
BOOT_TIMEOUT = 90  # generous: exchangeInfo + WS connect on first boot
SHUTDOWN_TIMEOUT = 15
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(
        f"  [{'PASS' if ok else 'FAIL'}] {name}"
        + (f" — {detail}" if detail else "")
    )
    return bool(ok)


def lock_is_held():
    """True when another process currently holds the single-instance lock."""
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


def wait_for(predicate, timeout, interval=0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    print("=" * 64)
    print("ULTIMATE BINANCE BOT — STARTUP SMOKE TEST")
    print(f"  interpreter: {PYTHON}")
    print(f"  project dir: {ROOT}")
    print("=" * 64)

    if not os.path.exists(os.path.join(ROOT, "main.py")):
        print("FATAL: main.py not found next to smoke_test.py")
        return 2

    # 0. Precondition: no other engine instance should be running.
    if lock_is_held():
        print("FATAL: another engine instance is already running (lock held).")
        print(
            "       Stop it first (pm2 delete ultimate-bot / kill) and re-run."
        )
        return 2

    tmp = tempfile.mkdtemp(prefix="bot_smoke_")
    env = dict(os.environ)
    env.update(
        {
            "PAPER_TRADE": "true",
            "LOCK_SCOPE": "smoke-test",
            "DB_PATH": os.path.join(tmp, "trading.db"),
            "CONTROL_FILE": os.path.join(tmp, "engine_control.json"),
            "LOG_FILE": os.path.join(tmp, "trading.log"),
            "STATIC_SYMBOLS": "BTCUSDT,ETHUSDT",
            "DYNAMIC_SYMBOLS": "false",
            "MAX_SYMBOLS": "2",
            "SIGNAL_INTERVAL": "10",
            # Pin the market: the tests exercise the proven spot path even when the
            # live engine on this box is running MARKET=futures.
            "MARKET": "spot",
        }
    )
    engine_log = os.path.join(tmp, "engine.stdout.log")
    monitor_log = os.path.join(tmp, "monitor.stdout.log")

    engine = None
    monitor = None
    try:
        # ---- 1. Engine boots + initializes schema / risk state ----
        print("\n[1/5] Booting engine (isolated temp DB)...")
        with open(engine_log, "w") as f:
            engine = subprocess.Popen(
                [PYTHON, "main.py"],
                cwd=ROOT,
                env=env,
                stdout=f,
                stderr=subprocess.STDOUT,
            )

        def db_ready():
            db = os.path.join(tmp, "trading.db")
            if not os.path.exists(db):
                return False
            try:
                conn = sqlite3.connect(
                    f"file:{db}?mode=ro", uri=True, timeout=3
                )
                try:
                    tables = {
                        r[0]
                        for r in conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                    }
                    if not {"orders", "active_trades", "risk_state"} <= tables:
                        return False
                    row = conn.execute(
                        "SELECT value FROM risk_state WHERE key='paper_balance'"
                    ).fetchone()
                    return row is not None and float(row[0]) > 0
                finally:
                    conn.close()
            except sqlite3.Error:
                return False

        booted = wait_for(db_ready, BOOT_TIMEOUT)
        if not booted:
            print(
                "FAIL: engine did not create a usable database in time. Log tail:"
            )
            with open(engine_log) as f:
                print("".join(f.readlines()[-15:]))
        check("engine booted (tables + paper_balance risk state)", booted)
        if not booted:
            return 1

        if not wait_for(lambda: lock_is_held(), 15):
            check("single-instance lock acquired", False)
        else:
            check("single-instance lock acquired", True)

        # ---- 2. Second instance must be rejected ----
        print("\n[2/5] Rejecting a second engine instance...")
        try:
            second = subprocess.run(
                [PYTHON, "main.py"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=45,
            )
            rejected = (
                second.returncode == 1 and "already running" in second.stdout
            )
            check(
                "second instance rejected (exit 1 + message)",
                rejected,
                f"rc={second.returncode}",
            )
        except subprocess.TimeoutExpired:
            check(
                "second instance rejected (exit 1 + message)",
                False,
                "timed out",
            )

        # ---- 3. Web monitor serves /api/status with consistent stats ----
        print("\n[3/5] Web monitor /api/status...")
        port = free_port()
        with open(monitor_log, "w") as f:
            monitor = subprocess.Popen(
                [PYTHON, "status.py", "--web", str(port)],
                cwd=ROOT,
                env=env,
                stdout=f,
                stderr=subprocess.STDOUT,
            )

        def api_ok():
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/status", timeout=3
                ) as resp:
                    return resp.status == 200
            except Exception:
                return False

        if not wait_for(api_ok, 30):
            check("monitor serves /api/status", False)
        else:
            check("monitor serves /api/status", True)
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/status", timeout=5
            ) as resp:
                data = json.load(resp)
            stats = (data.get("data") or {}).get("stats") or {}
            closed = int(stats.get("closed_trades") or 0)
            wins = int(stats.get("winning_trades") or 0)
            losses = int(stats.get("losing_trades") or 0)
            breakevens = int(stats.get("breakeven_trades") or 0)
            consistent = closed == wins + losses + breakevens
            check(
                "stats reconcile (closed == W + L + B)",
                consistent,
                f"{wins}W/{losses}L/{breakevens}B closed={closed}",
            )
            running = "RUNNING" in str(data.get("process", ""))
            check(
                "monitor reports engine RUNNING", running, data.get("process")
            )

            # Prove the monitor read the ISOLATED temp DB (not the real one) by
            # comparing its stats to a direct SQL query of the temp database.
            db = os.path.join(tmp, "trading.db")
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3)
            conn.row_factory = sqlite3.Row  # named columns, as the monitor reads it
            try:
                # Counted with the SAME definition the monitor serves (the shared
                # trade_stats attribution), so this check isolates the wiring it
                # is about — DID the monitor read this temp DB — instead of
                # re-asserting the definition with a `side='SELL'` query that
                # would disagree the first time a futures short closes.
                row = conn.execute(
                    trade_stats.trades_totals_sql(
                        trade_stats.exit_predicate(
                            trade_stats.has_exit_reason(conn)
                        )
                    )
                ).fetchone()
                db_closed = int(row["closed"] or 0)
                db_wins = int(row["wins"] or 0)
                db_losses = int(row["losses"] or 0)
            finally:
                conn.close()
            api_closed = int(stats.get("closed_trades") or 0)
            api_wins = int(stats.get("winning_trades") or 0)
            api_losses = int(stats.get("losing_trades") or 0)
            matches_db = (api_closed, api_wins, api_losses) == (
                db_closed,
                db_wins,
                db_losses,
            )
            check(
                "monitor reads the isolated temp DB",
                matches_db,
                f"api=({api_closed},{api_wins},{api_losses}) db=({db_closed},{db_wins},{db_losses})",
            )

            # ---- 3b. WebSocket realtime stream (React + WS API + Python stack) ----
            print("\n[3b/5] WebSocket /ws realtime push...")
            # Minimal RFC 6455 client: handshake, then read pushed messages.
            # Asserts the server accepts the upgrade and streams at least one
            # realtime status snapshot without any client request.
            ws_probe = r"""
import base64, json, os, socket, struct, sys

host, port = sys.argv[1], int(sys.argv[2])
sock = socket.create_connection((host, port), timeout=5)
key = base64.b64encode(os.urandom(16)).decode()
req = (f"GET /ws HTTP/1.1\r\nHost: {host}:{port}\r\n"
       "Upgrade: websocket\r\nConnection: Upgrade\r\n"
       f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
sock.sendall(req.encode())
resp = b""
while b"\r\n\r\n" not in resp:
    chunk = sock.recv(4096)
    if not chunk:
        raise SystemExit(2)
    resp += chunk
head, buf = resp.split(b"\r\n\r\n", 1)
if b" 101 " not in head.split(b"\r\n")[0]:
    print(head.decode(errors="replace")[:120])
    raise SystemExit(2)

def readexact(n):
    global buf
    while len(buf) < n:
        chunk = sock.recv(4096)
        if not chunk:
            raise SystemExit(2)
        buf += chunk
    out, buf = buf[:n], buf[n:]
    return out

def read_message():
    b1, b2 = readexact(2)
    ln = b2 & 0x7F
    if ln == 126:
        ln = struct.unpack(">H", readexact(2))[0]
    elif ln == 127:
        ln = struct.unpack(">Q", readexact(8))[0]
    return b1 & 0x0F, readexact(ln)

import time
deadline = time.time() + 15
types = []
while time.time() < deadline and "status" not in types:
    sock.settimeout(max(0.5, deadline - time.time()))
    try:
        op, payload = read_message()
    except (socket.timeout, OSError):
        break
    if op in (0x1, 0x2):
        try:
            types.append(json.loads(payload.decode("utf-8")).get("type", "status"))
        except Exception:
            pass
sock.close()
print(",".join(types) or "none")
"""
            probe = subprocess.run(
                [PYTHON, "-c", ws_probe, "127.0.0.1", str(port)],
                capture_output=True,
                text=True,
                timeout=45,
            )
            ws_ok = probe.returncode == 0 and "status" in probe.stdout
            check(
                "ws /ws upgrade + realtime status push",
                ws_ok,
                (probe.stdout.strip() or probe.stderr.strip()[-120:]),
            )

        # ---- 4. Clean SIGTERM shutdown ----
        print("\n[4/5] SIGTERM graceful shutdown...")
        engine.send_signal(signal.SIGTERM)
        exited = wait_for(
            lambda: engine.poll() is not None, SHUTDOWN_TIMEOUT, 0.25
        )
        check(
            "engine exited after SIGTERM",
            exited,
            f"rc={engine.poll()}" if exited else "still alive",
        )
        if exited:
            check(
                "clean exit code 0",
                engine.returncode == 0,
                f"rc={engine.returncode}",
            )
        else:
            check("clean exit code 0", False)
            engine.kill()
            engine.wait()

        with open(engine_log) as f:
            engine_text = f.read()
        check(
            "log contains 'Shutdown complete.'",
            "Shutdown complete." in engine_text,
        )
        check(
            "no 'Task was destroyed' warnings",
            "Task was destroyed" not in engine_text,
        )
        check("lock released after shutdown", not lock_is_held())

        # ---- 5. Summary ----
        print("\n[5/5] Summary")
        failed = [r for r in RESULTS if not r[1]]
        print(f"  {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
        for name, ok, _ in RESULTS:
            print(f"    {'✓' if ok else '✗'} {name}")
        shutil.rmtree(tmp, ignore_errors=True)
        return 1 if failed else 0
    finally:
        if engine and engine.poll() is None:
            engine.kill()
            engine.wait()
        if monitor and monitor.poll() is None:
            monitor.kill()
            monitor.wait()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
