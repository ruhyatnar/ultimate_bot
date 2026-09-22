#!/usr/bin/env python3
"""Soak watchdog — supervises the 24h futures paper soak (PM2: futures-soak).

Responsibilities (checked every POLL_S seconds):
  1. DEADLINE   — when the soak reaches SOAK_HOURS (default 24h since the
                  start marker), post the FINAL summary to Discord and stop
                  the soak (DB is preserved by futures_soak.sh stop).
  2. DEATH      — if the soak process disappears from PM2 or sits in
                  errored/stopped state before the deadline, alert immediately.
  3. STALL      — if the engine log has not been touched for STALL_MIN minutes
                  while PM2 claims the process is online, alert (hung engine).

Every alert is sent ONCE (state file prevents duplicate spam across restarts).
Run under PM2:
    pm2 start ./venv/bin/python3 --name soak-watchdog -- soak_watchdog.py
Flags: --once (single check, exit)  --dry (log instead of Discord/stop)
"""
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv

load_dotenv()

ROOT = os.path.dirname(os.path.abspath(__file__))
SOAK_DB = os.path.join(ROOT, "data", "futures_soak.db")
SOAK_LOG = os.path.join(ROOT, "logs", "futures_soak.log")
START_MARKER = os.path.join(ROOT, "data", "futures_soak_start_ms")
STATE_FILE = os.path.join(ROOT, "data", "soak_watchdog_state.json")

POLL_S = int(os.getenv("SOAK_WATCH_POLL_S", "60"))
SOAK_HOURS = float(os.getenv("SOAK_HOURS", "24"))
STALL_S = float(os.getenv("SOAK_STALL_S", "600"))   # heartbeat older than this = stalled
DRY = "--dry" in sys.argv
ONCE = "--once" in sys.argv


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


# ---- JSONL event feed (rendered on the dashboard's soak card) -------------
EVENTS_FILE = os.path.join(ROOT, "data", "soak_watchdog_events.jsonl")
_EVENTS_KEEP = 500  # trim bound: the dashboard reads only the last ~24


def log_event(kind, detail=""):
    """Append one supervision event to the JSONL feed (best-effort, trimmed)."""
    try:
        entry = {
            "ts_ms": int(time.time() * 1000),
            "utc": utcnow(),
            "kind": str(kind),
            "detail": str(detail)[:200],
        }
        lines = []
        try:
            with open(EVENTS_FILE) as f:
                lines = f.readlines()
        except FileNotFoundError:
            pass
        lines.append(json.dumps(entry) + "\n")
        if len(lines) > _EVENTS_KEEP + 100:  # occasional trim, not every write
            lines = lines[-_EVENTS_KEEP:]
        with open(EVENTS_FILE + ".tmp", "w") as f:
            f.writelines(lines)
        os.replace(EVENTS_FILE + ".tmp", EVENTS_FILE)
    except Exception as e:
        print(f"[watchdog] event log failed: {e}")


def soak_start_ms():
    """Soak start timestamp. Marker written by `futures_soak.sh start` is the
    source of truth (survives engine auto-restarts without resetting the 24h
    clock). Falls back to PM2's process start, then DB mtime (worst case)."""
    try:
        with open(START_MARKER) as f:
            return int(f.read().strip())
    except Exception:
        pass
    status, _ = pm2_soak_status()
    if status is not None:
        try:
            out = subprocess.run(["pm2", "jlist"], capture_output=True, text=True,
                                 timeout=20).stdout
            for p in json.loads(out or "[]"):
                if p.get("name") == "futures-soak":
                    up = p.get("pm2_env", {}).get("pm_uptime")
                    if up:
                        return int(up)
        except Exception:
            pass
    try:
        return int(os.path.getmtime(SOAK_DB) * 1000)
    except Exception:
        return None


def pm2_soak_status():
    """PM2 status string for futures-soak, or None when the process is gone."""
    try:
        out = subprocess.run(
            ["pm2", "jlist"], capture_output=True, text=True, timeout=20
        ).stdout
        procs = json.loads(out or "[]")
        for p in procs:
            if p.get("name") == "futures-soak":
                st = p.get("pm2_env", {}).get("status", "unknown")
                restarts = p.get("pm2_env", {}).get("restart_time", 0)
                return st, int(restarts or 0)
    except Exception as e:
        print(f"[watchdog] pm2 jlist failed: {e}")
    return None, 0


def soak_metrics():
    """Paper equity / trade tally from the isolated soak DB."""
    m = {}
    try:
        conn = sqlite3.connect(f"file:{SOAK_DB}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        rs = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM risk_state")}
        m["balance"] = float(rs.get("paper_balance", 0) or 0)
        m["start_balance"] = float(rs.get("paper_start_balance", 0) or 0)
        m["max_dd_pct"] = float(rs.get("max_drawdown_pct", 0) or 0)
        m["win_streak"] = int(float(rs.get("win_streak", 0) or 0))
        m["lose_streak"] = int(float(rs.get("lose_streak", 0) or 0))
        try:
            row = conn.execute(
                "SELECT COUNT(*) n, COALESCE(SUM(profit_loss),0) pnl FROM orders WHERE status='CLOSED'"
            ).fetchone()
            m["closed"] = row["n"]
            m["pnl"] = row["pnl"]
            m["wins"] = conn.execute(
                "SELECT COUNT(*) FROM orders WHERE status='CLOSED' AND profit_loss>0"
            ).fetchone()[0]
        except sqlite3.Error:
            m["closed"] = m["wins"] = 0
            m["pnl"] = 0.0
        m["open"] = conn.execute("SELECT COUNT(*) FROM active_trades").fetchone()[0]
        conn.close()
    except Exception as e:
        m["error"] = str(e)
    return m


def heartbeat_age_s():
    """Seconds since the engine last published its `ws_streams` risk_state key.

    Written UNCONDITIONALLY every screener cycle (trade_logic.py) — unlike the
    log file, which is silent for hours when a session has no signals (all
    per-cycle logging is debug-level). This is the true liveness signal.
    Returns None when the key is missing entirely (engine never wrote).
    """
    try:
        conn = sqlite3.connect(f"file:{SOAK_DB}?mode=ro", uri=True, timeout=5)
        try:
            row = conn.execute(
                "SELECT updated_at FROM risk_state WHERE key='ws_streams'").fetchone()
        finally:
            conn.close()
        if not row or not row[0]:
            return None
        return (time.time() * 1000 - int(row[0])) / 1000.0
    except Exception:
        return None


def log_age_min():
    try:
        return (time.time() - os.path.getmtime(SOAK_LOG)) / 60.0
    except Exception:
        return 0.0


def fmt_summary(m, hours, prefix):
    pnl_pct = (m.get("pnl", 0) / m["start_balance"] * 100) if m.get("start_balance") else 0.0
    wr = (m.get("wins", 0) / m["closed"] * 100) if m.get("closed") else 0.0
    return (
        f"**{prefix}** ({utcnow()})\n"
        f"uptime: **{hours:.1f}h** | paper equity: **${m.get('balance', 0):.2f}** "
        f"(start ${m.get('start_balance', 0):.2f})\n"
        f"PnL: **${m.get('pnl', 0):+.4f} ({pnl_pct:+.2f}%)** | closed: **{m.get('closed', 0)}** "
        f"(W {m.get('wins', 0)} / L {m.get('closed', 0) - m.get('wins', 0)}, winrate {wr:.0f}%)\n"
        f"max DD: {m.get('max_dd_pct', 0):.2f}% | streaks W{m.get('win_streak', 0)}/L{m.get('lose_streak', 0)} "
        f"| open: {m.get('open', 0)}"
    )


def alert(msg):
    """Discord webhook POST (stdlib, sync — the engine's DiscordWebhook class is
    async + loop-bound, unusable from a plain supervisor script)."""
    print(f"[watchdog] ALERT: {msg.replace(chr(10), ' | ')}")
    url = os.getenv("DISCORD_WEBHOOK_URL", "")
    if DRY or not url:
        if not url:
            print("[watchdog] DISCORD_WEBHOOK_URL not set — alert logged only")
        return
    try:
        body = json.dumps({"content": msg[:1900]}).encode()
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status not in (200, 204):
                print(f"[watchdog] Discord responded {resp.status}")
    except Exception as e:
        print(f"[watchdog] Discord send failed: {e}")


def stop_soak():
    print("[watchdog] stopping soak (deadline reached)")
    if not DRY:
        subprocess.run(["./futures_soak.sh", "stop"], cwd=ROOT, timeout=60,
                       capture_output=True)


def main():
    state = load_state()
    if state.get("finished"):
        print("[watchdog] previous soak already finished — start a new soak to re-arm")
        return 0
    start = soak_start_ms()
    if start is None:
        print("[watchdog] no soak DB/start marker yet — nothing to supervise")
        return 1
    deadline_ms = start + SOAK_HOURS * 3600 * 1000

    # One-time backfill: soaks started before the event feed existed get a
    # synthetic soak_started entry so the dashboard feed has full context.
    if not os.path.exists(EVENTS_FILE):
        began = datetime.fromtimestamp(start / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        log_event("soak_started", f"backfilled — soak began {began}")
    print(f"[watchdog] armed: deadline in {(deadline_ms - int(time.time() * 1000)) / 3600_000:.1f}h")
    log_event("armed", f"soak supervised; deadline in {(deadline_ms - int(time.time() * 1000)) / 3600_000:.1f}h")

    while True:
        state = load_state()
        status, restarts = pm2_soak_status()
        now_ms = int(time.time() * 1000)
        hours = (now_ms - start) / 3600_000

        # 1. Deadline reached -> final summary + stop
        if now_ms >= deadline_ms:
            m = soak_metrics()
            alert(fmt_summary(m, min(hours, SOAK_HOURS), "🏁 FUTURES SOAK COMPLETE (24h)"))
            log_event("completed", f"{hours:.1f}h — {m.get('closed', 0)} closed trades, "
                                   f"PnL ${m.get('pnl', 0):+.4f}, equity ${m.get('balance', 0):.2f}")
            stop_soak()
            state["finished"] = True
            save_state(state)
            return 0

        # 2. Process death / errored before deadline
        if status in (None, "errored", "stopped"):
            if not state.get("alerted_death"):
                m = soak_metrics()
                alert(f"⚠️ **FUTURES SOAK DOWN** (pm2 status: {status}, "
                      f"restarts: {restarts}) at {hours:.1f}h — DB preserved.\n"
                      + fmt_summary(m, hours, "state at failure"))
                state["alerted_death"] = True
                save_state(state)
                log_event("death_alert", f"pm2 status={status}, restarts={restarts}, {hours:.1f}h in")
        else:
            if state.pop("alerted_death", None) is not None:
                log_event("recovered", f"soak online again at {hours:.1f}h (death alert cleared)")
            save_state(state)

        # 3. Heartbeat stall while PM2 says online
        hb = heartbeat_age_s()
        if status == "online" and hb is not None and hb > STALL_S:
            if not state.get("alerted_stall"):
                alert(f"⚠️ **FUTURES SOAK STALLED**: DB heartbeat (ws_streams) "
                      f"{hb:.0f}s old but PM2 reports online ({hours:.1f}h in).")
                state["alerted_stall"] = True
                save_state(state)
                log_event("stall_alert", f"heartbeat {hb:.0f}s old (threshold {STALL_S:.0f}s), {hours:.1f}h in")
        else:
            if state.pop("alerted_stall", None) is not None:
                log_event("recovered", f"heartbeat fresh again ({hb if hb is not None else 'n/a'}s) at {hours:.1f}h")
            save_state(state)

        if ONCE:
            print(f"[watchdog] once-mode check done: status={status} "
                  f"hours={hours:.2f} heartbeat={hb if hb is not None else 'n/a'}s "
                  f"log_age={log_age_min():.1f}m")
            return 0
        # Hourly liveness line so PM2 logs prove the supervisor is checking
        # (a silent supervisor and a crashed one look identical otherwise).
        if now_ms - state.get("last_liveness_ms", 0) > 3600_000:
            bal = soak_metrics().get("balance", "?")
            print(f"[watchdog] watching: status={status} {hours:.2f}h/24h "
                  f"heartbeat={hb if hb is not None else 'n/a'}s balance={bal}")
            log_event("watching", f"status={status} {hours:.2f}h/24h balance={bal}")
            state["last_liveness_ms"] = now_ms
            save_state(state)
        time.sleep(POLL_S)


if __name__ == "__main__":
    sys.exit(main())
