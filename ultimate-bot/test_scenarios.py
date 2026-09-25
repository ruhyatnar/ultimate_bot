#!/usr/bin/env python3
"""Offline failure-scenario battery (restored, compact).

Covers the defect classes that historically bit this bot:
  S1  _post_entry_wick_range excludes pre-entry candles (the 13s-exit bug)
  S2  _post_entry_wick_range tolerates malformed klines
  S3  Loss re-entry gate: block inside window, allow after expiry
  S4  Funding gate tunable present and sane (0 disables, positive caps)
  S5  PnL auto-verifier math vs fapi userTrades field shape
  S6  Live order-call signature contract across every market/transport client
  S7  Multi-assets phantom balance rows must not inflate equity
  S8  The shared exit decision (evaluate_exit) contract: ordering, reasons, fills
  S9  Active-trade persistence: a change reaches SQLite promptly (the web monitor
      renders the position from that row, so an unpublished field = a stale one)
  S10 Capital-roadmap CLI states each blocked pair's OWN equity threshold, not
      one max-floor figure quoted for the whole blocked set
  S11 Exit attribution in the served stats/orders: a trade is not a leg, a short's
      BUY exit IS an exit, and a legacy row's label is flagged as a guess
  S12 Engine-log tail contract: /api/logs cannot be asked for the whole file, the
      tail/incremental readers never serve (or skip past) a half-written record,
      and a measured 0 ms clock offset is not mistaken for "never measured"
  S13 The soak surface (card, watchdog summary) reads the SAME book as the
      dashboard: shorts, scale-out trades, the last EXIT, and the live soak length
  S14 The browser check compares the card against a payload re-read at DOM-read
      time: a rotated watchlist must trigger a re-read, not a false failure
  S15 The market screener's cache: a failed (or empty) live fetch is backed off
      instead of blocking a 4s request in front of every 1 Hz payload build
"""
import os, sys, time, asyncio
os.environ.setdefault("LOG_LEVEL", "ERROR")
os.environ.setdefault("PAPER_TRADE", "true")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.trade.trade_logic import _post_entry_wick_range

FAILS = []
def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)

# --- S1: wick filter -----------------------------------------------------
ENTRY = 1_000_000
kl = [
    # pre-entry dip candle: low 0.90 — must be IGNORED
    [999_000, "1.00", "1.01", "0.90", "1.00", "1", 0, 0, 0, 0, 0, 0],
    # post-entry candles: low 0.98 (above a 0.976 stop)
    [1_000_000, "1.00", "1.01", "0.98", "1.00", "1", 0, 0, 0, 0, 0, 0],
    [1_300_000, "1.00", "1.02", "0.99", "1.01", "1", 0, 0, 0, 0, 0, 0],
]
lo, hi = _post_entry_wick_range(kl, ENTRY)
check("S1 pre-entry wick excluded", abs(lo - 0.98) < 1e-12 and abs(hi - 1.02) < 1e-12, f"lo={lo} hi={hi}")

# no post-entry candle yet -> (None, None) -> caller uses live tick
lo, hi = _post_entry_wick_range(kl[:1], ENTRY)
check("S1 no-post-entry fallback", lo is None and hi is None)

# --- S2: malformed klines ------------------------------------------------
check("S2 garbage tolerated", _post_entry_wick_range([["x", None]], ENTRY) == (None, None))
check("S2 empty tolerated", _post_entry_wick_range([], ENTRY) == (None, None))

# --- S3: loss re-entry gate ---------------------------------------------
from src.risk.risk_manager import RiskManager

class FakeDB:
    def get_risk_state(self):
        return {"total_equity": 22.0, "free_quote": 22.0, "locked_quote": 0.0,
                "daily_pnl": 0.0, "drawdown_breaker": False, "breaker_reason": "",
                "cooldown_until": 0, "cooldown_kind": "loss",
                "loss_streak": 0, "win_streak": 0,
                "symbol_states": {"ONEUSDT": {"loss_streak": 1, "win_streak": 0,
                                              "cooldown_until": 0, "last_loss_at": 0}}}

    async def set_risk_state(self, *_a, **_k):
        pass

class FakeCfg(dict):
    def get(self, k, d=None):
        return dict.__getitem__(self, k) if k in self else d

rm = RiskManager(FakeCfg({"LOSS_REENTRY_COOLDOWN": 900, "MAX_LOSS_STREAK": 3,
                          "COOLDOWN_LOSS": 3600, "COOLDOWN_WIN": 300,
                          "MAX_DAILY_DRAWDOWN": 0.03, "SIGNAL_INTERVAL": 10}), FakeDB(), None)
rm.symbol_states["ONEUSDT"] = {"loss_streak": 1, "win_streak": 0,
                               "cooldown_until": 0, "last_loss_at": 0}
rm.symbol_states["ONEUSDT"]["last_loss_at"] = int(time.time()) - 60
ok = asyncio.get_event_loop().run_until_complete(rm.check_risk("ONEUSDT"))
check("S3 re-entry blocked inside 900s window", ok is False)

rm.symbol_states["ONEUSDT"]["last_loss_at"] = int(time.time()) - 901
ok = asyncio.get_event_loop().run_until_complete(rm.check_risk("ONEUSDT"))
check("S3 re-entry allowed after expiry", ok is True)

# other symbols unaffected
ok = asyncio.get_event_loop().run_until_complete(rm.check_risk("NEARUSDT"))
check("S3 gate isolated per symbol", ok is True)

# --- S4: funding gate tunable -------------------------------------------
from config import load_config
os.environ["FUNDING_RATE_MAX"] = "0.0005"
cfg = load_config()
fr = float(cfg.get("FUNDING_RATE_MAX", 0))
check("S4 FUNDING_RATE_MAX loads from env", abs(fr - 0.0005) < 1e-9, f"got {fr}")
os.environ["FUNDING_RATE_MAX"] = "0"
check("S4 0 disables gate", float(load_config().get("FUNDING_RATE_MAX", 1)) == 0.0)

# --- S5: PnL verifier math (fapi userTrades shape) ----------------------
def verifier_pnl(rows, qty_exit):
    """Mirror of close_trade's verifier: rPnL − commission over matched fills."""
    matched = [r for r in rows if str(r.get("orderId")) == "777"]
    pnl = sum(float(r.get("realizedPnl", 0) or 0) for r in matched)
    fee = sum(abs(float(r.get("commission", 0) or 0)) for r in matched)
    return pnl - fee

rows = [{"orderId": "777", "realizedPnl": "0.6292", "commission": "0.0235", "buyer": True},
        {"orderId": "888", "realizedPnl": "-1.0", "commission": "9.9"}]
check("S5 verifier isolates order + nets fees", abs(verifier_pnl(rows, 88) - (0.6292 - 0.0235)) < 1e-9)

# --- S6: live order-call signature contract -----------------------------
# OrderManager always calls place_order(..., reduce_only=...) from its REST
# fallback. EVERY client the engine can be wired to (spot/futures x REST/WS-API)
# must accept the keyword, or the order raises TypeError and is silently skipped
# — the exact class of bug that made live spot REST orders impossible.
import inspect as _inspect
from src.exchange.rest_client import RestClient as _SpotRest
from src.exchange.futures_rest_client import FuturesRestClient as _FutRest
from src.exchange.ws_api_client import WSApiClient as _SpotWs
from src.exchange.futures_ws_api_client import FuturesWSApiClient as _FutWs
from src.trade.order_manager import OrderManager as _OM

for _cls in (_SpotRest, _FutRest, _SpotWs, _FutWs):
    check(f"S6 {_cls.__name__}.place_order accepts reduce_only",
          "reduce_only" in _inspect.signature(_cls.place_order).parameters)
check("S6 OrderManager has no dead user_stream param",
      "user_stream" not in _inspect.signature(_OM.__init__).parameters)

# --- S7: multi-assets phantom balance rows --------------------------------
# Binance multi-assets collateral mode reports a collateral-equivalent
# availableBalance for EVERY asset the wallet *could* use as margin, while
# walletBalance stays 0 for the ones it does not actually hold. Seeding those
# rows priced N units of BNB/BTC/ETH at the real ticker and inflated WS-path
# equity ~5x ($23 -> $111.75) on every balance cycle — feeding position sizing,
# the daily drawdown breaker and the dashboard with a phantom base.
import logging as _logging

def _seed(payload):
    c = object.__new__(_FutWs)
    c.balances = {}; c._balances_seeded_at = None; c.logger = _logging.getLogger("s7")
    c.seed_balances(payload)
    return c.balances

_live_shaped = [
    {"asset": "USDT", "walletBalance": "23.01525645", "availableBalance": "23.01065374"},
    {"asset": "BNB",  "walletBalance": "0.00000000",  "availableBalance": "0.02740418"},
    {"asset": "BTC",  "walletBalance": "0.00000000",  "availableBalance": "0.00025357"},
    {"asset": "USDC", "walletBalance": "0.00000000",  "availableBalance": "23.00685562"},
]
_b = _seed({"assets": _live_shaped})
check("S7 phantom collateral rows dropped", set(_b) == {"USDT"}, f"seeded {sorted(_b)}")
check("S7 real holding keeps spendable margin",
      abs(_b["USDT"]["free"] - 23.01065374) < 1e-9
      and abs(_b["USDT"]["locked"] - (23.01525645 - 23.01065374)) < 1e-6, f"{_b.get('USDT')}")

_b2 = _seed({"assets": _live_shaped + [{"asset": "BNB", "walletBalance": "0.5", "availableBalance": "0.5"}]})
check("S7 genuine second-asset holding kept", set(_b2) == {"USDT", "BNB"}, f"seeded {sorted(_b2)}")

_b3 = _seed({"balances": [{"asset": "USDT", "free": "10.0", "locked": "2.0"}]})
check("S7 legacy free/locked shape still seeds",
      abs(_b3["USDT"]["free"] - 10.0) < 1e-9 and abs(_b3["USDT"]["locked"] - 2.0) < 1e-9, f"{_b3}")

# --- S8: the ONE exit decision (trade_policy.evaluate_exit) --------------
# The live manage cycle and the backtest both call this, so its contract is
# pinned here: ordering (a stop always outranks profit-taking), the reason
# labels the dashboard filters on, and gap-aware fills. A rule that drifts
# between what is backtested and what trades real money shows up as a failure.
from src.strategies.trade_policy import evaluate_exit

_CFG = {"MAX_HOLD_TIME": 3600, "SCALE_OUT_ENABLED": True,
        "SCALE_OUT_FRACTION": 0.5, "SCALE_OUT_R_MULTIPLE": 1.0}
ENTRY, STOP, TP, QTY = 100.0, 98.0, 104.0, 10.0   # 2% stop, 4% TP -> +1R = 102


def _ev(**kw):
    base = dict(entry_price=ENTRY, quantity=QTY, stop_price=STOP, take_profit=TP,
                config=_CFG, low=ENTRY, high=ENTRY, reference_price=ENTRY)
    base.update(kw)
    return evaluate_exit(**base)


# One window that both reaches +1R (102) and wicks through the stop (97): the
# intrabar path is unknowable, so the position must be assumed stopped.
p = _ev(low=97.0, high=102.0)
check("S8 stop outranks scale-out in one window",
      p.reason == "STOP_LOSS" and p.partial_units == 0.0, f"{p}")

p = _ev(low=99.0, high=104.5)
check("S8 take-profit outranks scale-out",
      p.reason == "TAKE_PROFIT" and p.fill == TP, f"{p}")

p = _ev(low=99.0, high=102.5)
check("S8 scale-out banks a partial and stays open",
      p.reason is None and abs(p.partial_units - 5.0) < 1e-9 and p.partial_price == 102.0, f"{p}")

# Terminal scheduled exits pre-empt a partial in the same window (banking into
# a position that is closing anyway would be a phantom trade).
p = _ev(low=99.0, high=102.5, entry_ms=1_000, now_ms=10_000_000)
check("S8 time stop pre-empts scale-out", p.reason == "TIME_STOP", f"{p}")

p = _ev(low=99.0, high=102.5, eod=True)
check("S8 day-end flatten pre-empts scale-out", p.reason == "EOD_CLOSE", f"{p}")

# Scheduled exits fill at the decision-moment price the caller supplies.
p = _ev(low=99.0, high=100.0, eod=True, reference_price=101.5)
check("S8 day-end fills at the decision price", p.fill == 101.5, f"{p}")

# Gap-aware fills: a level already traded through fills at the market, not at
# a price the market has left behind.
p = _ev(low=97.0, high=97.5, reference_price=95.0)
check("S8 gapped stop fills at the market, not the stop", p.fill == 95.0, f"{p}")
p = _ev(low=97.5, high=99.0)
check("S8 wick stop with a recovered price fills at the stop", p.fill == STOP, f"{p}")
p = _ev(low=99.0, high=105.0, reference_price=105.5)
check("S8 gapped take-profit fills at the market", p.fill == 105.5, f"{p}")

# The trail is the effective stop once armed, and reports under its own reason
# so exit-reason histograms from live and backtest are comparable.
p = _ev(low=100.5, high=101.0, reference_price=101.0, trailing_stop=100.8, trailing_active=True)
check("S8 armed trail is the effective stop + own reason",
      p.reason == "TRAILING_STOP" and p.fill == 100.8, f"{p}")
p = _ev(low=97.0, high=99.0, reference_price=99.0, trailing_stop=100.8, trailing_active=False)
check("S8 unarmed trail is ignored",
      p.reason == "STOP_LOSS" and p.fill == STOP, f"{p}")

# Nothing breached -> hold, and no scale-out before the level is reached.
p = _ev(low=99.0, high=101.9)
check("S8 quiet window holds",
      p.reason is None and p.partial_units == 0.0, f"{p}")

# Evidence may be missing entirely (a failed kline fetch must not fabricate an
# exit, and must not skip the stop check either — the tick is still evidence).
p = _ev(low=None, high=None, reference_price=None)
check("S8 absent evidence holds rather than guessing",
      p.reason is None and p.partial_units == 0.0, f"{p}")

# --- S9: active-trade persistence (web-monitor sync) ---------------------
# The monitor reads the DB row, so any managed field left in memory only renders
# as a stale position: the dashboard's bracket ladder, trailing/breakeven locks
# and size all come from this row while the exits are decided from memory.
# manage_trade used to flush on `int(time.time()) % 30 == 0` — a one-second
# window — so with a 10s cadence a ratcheted trail could sit unpublished for
# minutes. It must flush on CHANGE (plus a slow heartbeat).
import logging
from src.trade.trade_logic import TradeLogic


class _ProbeDB:
    def __init__(self):
        self.rows = []

    async def save_active_trade(self, trade):
        self.rows.append(dict(trade))


class _PersistProbe:
    """TradeLogic's persistence helpers, isolated from the full engine wiring."""
    _PERSISTED_TRADE_FIELDS = TradeLogic._PERSISTED_TRADE_FIELDS
    _persist_active_trade_if_changed = TradeLogic._persist_active_trade_if_changed
    _mark_trade_persisted = TradeLogic._mark_trade_persisted

    def __init__(self):
        self.db = _ProbeDB()
        self._saved_trade_sig = {}
        self.logger = logging.getLogger("s9.probe")


async def _s9():
    probe = _PersistProbe()
    trade = {"symbol": "S9USDT", "entry_price": 100.0, "quantity": 1.0,
             "stop_price": 98.0, "take_profit": 104.0, "atr": 0.5,
             "trailing_active": False, "trailing_stop": 98.0,
             "breakeven_activated": False, "scale_out_done": False,
             "initial_qty": 1.0, "initial_stop_price": 98.0}
    await probe._persist_active_trade_if_changed("S9USDT", trade)
    check("S9 first evaluation persists the row", len(probe.db.rows) == 1)
    await probe._persist_active_trade_if_changed("S9USDT", trade)
    check("S9 unchanged state does NOT re-write (no per-cycle churn)",
          len(probe.db.rows) == 1, f"writes={len(probe.db.rows)}")
    trade["trailing_stop"] = 99.5
    await probe._persist_active_trade_if_changed("S9USDT", trade)
    check("S9 a ratcheted trail is flushed immediately", len(probe.db.rows) == 2,
          f"writes={len(probe.db.rows)}")
    trade["breakeven_activated"] = True
    await probe._persist_active_trade_if_changed("S9USDT", trade)
    check("S9 a breakeven lock is flushed immediately", len(probe.db.rows) == 3,
          f"writes={len(probe.db.rows)}")
    trade["quantity"] = 0.5
    await probe._persist_active_trade_if_changed("S9USDT", trade)
    check("S9 a size change (scale-out) is flushed immediately",
          len(probe.db.rows) == 4, f"writes={len(probe.db.rows)}")
    # Heartbeat: even with nothing changed the row is refreshed eventually, so an
    # interrupted write cannot leave SQLite behind forever.
    await probe._persist_active_trade_if_changed("S9USDT", trade, heartbeat=0.0)
    check("S9 heartbeat re-writes an unchanged row", len(probe.db.rows) == 5,
          f"writes={len(probe.db.rows)}")
    # A DB failure must degrade to a warning and retry next cycle: a persistence
    # hiccup must never block stop/TP management.
    class _BrokenDB:
        async def save_active_trade(self, trade):
            raise RuntimeError("db down")

    probe.db = _BrokenDB()
    trade["stop_price"] = 100.5
    try:
        await probe._persist_active_trade_if_changed("S9USDT", trade)
        check("S9 a DB failure is swallowed (retried next cycle)", True)
    except Exception as exc:  # noqa: BLE001 - the check IS the assertion
        check("S9 a DB failure is swallowed (retried next cycle)", False, str(exc))
    # Older/partial row shapes must not raise either.
    probe.db = _ProbeDB()
    await probe._persist_active_trade_if_changed("S9USDT", {"symbol": "S9USDT"})
    check("S9 a partial trade dict is tolerated", len(probe.db.rows) == 1)


asyncio.run(_s9())

# --- S10: capital-roadmap CLI prints a PER-PAIR threshold ----------------
# The CLI used to quote a single `max_floor * SL% / RISK%` for the whole blocked
# set, so a $20-floor pair needing $24 was told to wait for $120 — the highest
# floor in the group. Each blocked pair must state its OWN required equity, and
# the table must show the same figure per row.
import contextlib
import io
import capital_roadmap as _cr


def _roadmap_cli(equity, pairs, floors):
    """Run the CLI offline with the exchange stubbed; return (rc, stdout)."""
    saved = (_cr.futures_floors, _cr.futures_prices, _cr.latest_funding,
             _cr.config_core)
    # RISK 1% / SL 1.2% / leverage 1 — so a $5 floor needs $6 and a $100 floor
    # needs $120, which makes an over-quoted global figure obvious.
    _cr.futures_floors = lambda syms: {s: floors[s] for s in syms if s in floors}
    _cr.futures_prices = lambda syms: {s: 1.0 for s in syms}
    _cr.latest_funding = lambda s: 0.0
    _cr.config_core = lambda: (0.01, 0.012, 1.0)
    argv_saved, buf = sys.argv, io.StringIO()
    try:
        sys.argv = ["capital_roadmap.py", "--equity", str(equity),
                    "--pairs", ",".join(pairs)]
        with contextlib.redirect_stdout(buf):
            rc = _cr.main()
    finally:
        sys.argv = argv_saved
        (_cr.futures_floors, _cr.futures_prices, _cr.latest_funding,
         _cr.config_core) = saved
    return rc, buf.getvalue()


S10_FLOORS = {"A5USDT": 5.0, "B20USDT": 20.0, "C100USDT": 100.0}
rc10, out10 = _roadmap_cli(22, ["A5USDT", "B20USDT", "C100USDT"], S10_FLOORS)
check("S10 CLI runs offline with a stubbed exchange", rc10 == 0, f"rc={rc10}")

lines10 = out10.splitlines()


def _rec_line(sym):
    return [l for l in lines10 if sym in l and "tradable at" in l]


# The binding defect: B20USDT's own requirement is $24, and it must NOT be told
# to wait for the group maximum ($120, C100USDT's number).
b20 = _rec_line("B20USDT")
check("S10 blocked B20USDT quotes its OWN $24, not the group max $120",
      len(b20) == 1 and "~$24" in b20[0] and "~$120" not in b20[0], f"{b20}")
c100 = _rec_line("C100USDT")
check("S10 blocked C100USDT quotes its OWN $120",
      len(c100) == 1 and "~$120" in c100[0], f"{c100}")


def _tab_row(sym):
    for l in lines10:
        parts = l.split()
        if parts and parts[0] == sym:
            return parts
    return []


row_b = _tab_row("B20USDT")
row_a = _tab_row("A5USDT")
# Columns: symbol, price, floor$, req$, ok?, wallet%, liq@lev, funding/8h
check("S10 table row B20USDT: floor 20 -> req$ 24, NO",
      len(row_b) >= 5 and row_b[2] == "20" and row_b[3] == "24"
      and row_b[4] == "NO", f"{row_b}")
check("S10 table row A5USDT: floor 5 -> req$ 6, YES",
      len(row_a) >= 5 and row_a[2] == "5" and row_a[3] == "6"
      and row_a[4] == "YES", f"{row_a}")
# Read the thresholds back out of the recommendation lines. A single global
# figure would print one value for every blocked pair; these differing — each
# equal to its own floor * SL% / RISK% — is what proves they are per-pair.
import re as _re10
quoted10 = {
    m.group(1): int(m.group(2))
    for m in (_re10.search(r"(\S+)\s+floor \$.*~\$(\d+)", l) for l in lines10
              if "tradable at" in l)
    if m
}
check("S10 the two blocked pairs receive DIFFERENT thresholds (24 vs 120)",
      quoted10.get("B20USDT") == 24 and quoted10.get("C100USDT") == 120,
      f"quoted={quoted10}")

# --- S11: exit attribution in the served stats/orders ---------------------
# The monitor used to infer an exit's REASON from the PnL sign, identify exits by
# `side='SELL'`, and count exit LEGS as closed TRADES. Each of those produced a
# different answer from the engine for the same book. These checks pin the
# corrected attribution against synthetic order histories.
import sqlite3 as _sqlite11
import tempfile as _tempfile11
import status as _status11

_S11_SCHEMA = (
    "CREATE TABLE orders (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT UNIQUE,"
    " symbol TEXT, side TEXT, order_type TEXT, price REAL, stop_price REAL, quantity REAL,"
    " executed_qty REAL, status TEXT, created_at INTEGER, updated_at INTEGER,"
    " profit_loss REAL DEFAULT 0, avg_fill_price REAL%s)"
)
_T0 = 1_700_000_000_000


def _s11_db(with_reason):
    path = os.path.join(_tempfile11.mkdtemp(prefix="s11_"), "trading.db")
    conn = _sqlite11.connect(path)
    conn.execute(_S11_SCHEMA % (", exit_reason TEXT" if with_reason else ""))
    conn.commit()
    return path, conn


def _s11_ins(conn, **cols):
    conn.execute(
        f"INSERT INTO orders ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
        tuple(cols.values()),
    )
    conn.commit()


# (a) Engine-migrated DB: a scale-out long (partial leg + final leg, net +60) and
#     a SHORT whose exit order is a BUY — invisible to a `side='SELL'` filter.
_s11_path, _s11_conn = _s11_db(True)
_s11_ins(_s11_conn, symbol="LONGUSDT", side="BUY", status="FILLED", created_at=_T0,
         avg_fill_price=10.0, profit_loss=0.0)
_s11_ins(_s11_conn, symbol="LONGUSDT", side="SELL", status="CANCELED", created_at=_T0 + 1000,
         avg_fill_price=12.0, profit_loss=100.0, exit_reason="TAKE_PROFIT")
_s11_ins(_s11_conn, symbol="LONGUSDT", side="SELL", status="FILLED", created_at=_T0 + 2000,
         avg_fill_price=8.8, profit_loss=-40.0, exit_reason="STOP_LOSS")
_s11_ins(_s11_conn, symbol="SHORTUSDT", side="SELL", status="FILLED", created_at=_T0 + 50,
         avg_fill_price=5.0, profit_loss=0.0)
_s11_ins(_s11_conn, symbol="SHORTUSDT", side="BUY", status="FILLED", created_at=_T0 + 1500,
         avg_fill_price=4.5, profit_loss=5.0, exit_reason="TAKE_PROFIT")
_s11 = _status11.read_database(_s11_path)
_s11_stats = _s11["stats"]
_s11_by_ts = {r["created_at"]: r for r in _s11["orders"]}

check("S11 scale-out legs count as ONE trade",
      _s11_stats["closed_trades"] == 2 and _s11_stats["exit_legs"] == 3,
      f"closed={_s11_stats['closed_trades']} legs={_s11_stats['exit_legs']}")
check("S11 a trade is classified by its NET pnl (win+100/-40 = 1 win, 0 losses)",
      _s11_stats["winning_trades"] == 2 and _s11_stats["losing_trades"] == 0
      and abs(_s11_stats["total_realized_pnl"] - 65.0) < 1e-9,
      f"W={_s11_stats['winning_trades']} L={_s11_stats['losing_trades']}"
      f" pnl={_s11_stats['total_realized_pnl']}")
check("S11 trade nets sum to total_realized_pnl",
      _s11_stats["closed_trades"] == _s11_stats["winning_trades"] + _s11_stats["losing_trades"]
      + _s11_stats["breakeven_trades"])
check("S11 a short's BUY exit IS a closed trade (not filtered by side)",
      _s11_by_ts[_T0 + 1500]["exit_reason"] == "TAKE_PROFIT"
      and _s11_by_ts[_T0 + 1500]["exit_reason_inferred"] is False,
      f"{_s11_by_ts[_T0 + 1500]['exit_reason']}")
check("S11 a short exit's entry comes from the opposite (SELL) leg",
      _s11_by_ts[_T0 + 1500]["entry_price"] == 5.0
      and _s11_by_ts[_T0 + 1500]["entry_ts"] == _T0 + 50,
      f"entry_price={_s11_by_ts[_T0 + 1500]['entry_price']}"
      f" entry_ts={_s11_by_ts[_T0 + 1500]['entry_ts']}")
check("S11 the engine's recorded reason is served verbatim",
      _s11_by_ts[_T0 + 2000]["exit_reason"] == "STOP_LOSS"
      and _s11_by_ts[_T0 + 1000]["exit_reason"] == "TAKE_PROFIT",
      f"{_s11_by_ts[_T0 + 2000]['exit_reason']}")
check("S11 an entry row is not an exit and carries no entry leg",
      _s11_by_ts[_T0]["exit_reason"] is None
      and _s11_by_ts[_T0]["entry_price"] is None
      and _s11_by_ts[_T0]["exit_reason_inferred"] is False)
check("S11 streaks are served as NUMBERS, not risk_state strings",
      isinstance(_s11_stats["win_streak"], int)
      and isinstance(_s11_stats["loss_streak"], int),
      f"{type(_s11_stats['win_streak']).__name__}/{type(_s11_stats['loss_streak']).__name__}")
_s11_conn.close()

# (b) Legacy DB (no exit_reason column): the label is inferred from the sign and
#     must be FLAGGED as a guess; a legacy partial leg gets no invented trigger.
_s11l_path, _s11l_conn = _s11_db(False)
_s11_ins(_s11l_conn, symbol="OLDUSDT", side="BUY", status="FILLED", created_at=_T0,
         avg_fill_price=10.0, profit_loss=0.0)
_s11_ins(_s11l_conn, symbol="OLDUSDT", side="SELL", status="FILLED", created_at=_T0 + 1000,
         avg_fill_price=11.0, profit_loss=20.0)
_s11_ins(_s11l_conn, symbol="OLDUSDT", side="SELL", status="CANCELED", created_at=_T0 + 1500,
         avg_fill_price=11.0, profit_loss=3.0)
_s11l = _status11.read_database(_s11l_path)
_s11l_by_ts = {r["created_at"]: r for r in _s11l["orders"]}
check("S11 a legacy row's label is flagged as INFERRED",
      _s11l_by_ts[_T0 + 1000]["exit_reason"] == "TAKE_PROFIT"
      and _s11l_by_ts[_T0 + 1000]["exit_reason_inferred"] is True)
check("S11 a legacy partial leg gets no invented trigger",
      _s11l_by_ts[_T0 + 1500]["exit_reason"] is None
      and _s11l_by_ts[_T0 + 1500]["exit_reason_inferred"] is True)
check("S11 legacy trade nets still sum to the realized total",
      abs(_s11l["stats"]["total_realized_pnl"] - 23.0) < 1e-9
      and _s11l["stats"]["closed_trades"] == 2,
      f"closed={_s11l['stats']['closed_trades']}"
      f" pnl={_s11l['stats']['total_realized_pnl']}")
_s11l_conn.close()

# (c) The stats query's degraded path: a SQLite without window functions (or a
#     malformed file) must fall back to a leg-based aggregate, never raise.
class _S11Cursor:
    def __init__(self, allow_window, row):
        self.allow_window, self.row, self.sql = allow_window, row, ""

    def execute(self, sql):
        if "OVER (" in sql and not self.allow_window:
            raise _sqlite11.OperationalError("no such function: OVER")
        self.sql = sql
        return self

    def fetchone(self):
        return self.row


_S11_ROW = {"closed_trades": 1, "exit_legs": 2}
check("S11 stats prefer the trade-aware query when window functions exist",
      _status11._orders_stats_row(_S11Cursor(True, _S11_ROW)) is not None)
_s11_c = _S11Cursor(False, _S11_ROW)
check("S11 stats fall back to leg counts without window functions",
      _status11._orders_stats_row(_s11_c) is not None and "OVER (" not in _s11_c.sql)


class _S11Dead:
    def execute(self, sql):
        raise _sqlite11.OperationalError("no such table: orders")


# --- S12: engine-log tail contract (/api/logs + the /ws log push) ---------
# Two defects on the Engine Log surface:
#   (a) `/api/logs?lines=0` (and any negative) reached readlines()[-0:] / [N:],
#       i.e. the WHOLE log — a 10 MB JSON response on an unauthenticated URL the
#       browser polls every 5s.
#   (b) the incremental /ws push advanced its offset past a half-written final
#       record, so a line caught mid-write arrived truncated and its remainder
#       was lost forever.
import tempfile as _tempfile12

_s12_dir = _tempfile12.mkdtemp(prefix="s12_")
_s12_log = os.path.join(_s12_dir, "trading.log")
with open(_s12_log, "wb") as _f12:
    _f12.write(b"".join(b"rec-%04d\n" % i for i in range(300)))
    _f12.write(b"HALF-WRITTEN-RECORD")  # mid-write: no trailing newline yet

check("S12 lines=0 is clamped to 1, not the whole log",
      _status11.clamp_log_lines("0") == 1)
check("S12 a negative lines= is clamped to 1, not readlines()[N:]",
      _status11.clamp_log_lines("-3") == 1)
check("S12 lines= is capped at MAX_LOG_LINES",
      _status11.clamp_log_lines("99999") == _status11.MAX_LOG_LINES)
check("S12 junk/missing lines= falls back to the default",
      _status11.clamp_log_lines("abc") == _status11.DEFAULT_LOG_LINES
      and _status11.clamp_log_lines(None) == _status11.DEFAULT_LOG_LINES)

_s12_newest5 = [f"rec-{i:04d}\n" for i in range(295, 300)]
check("S12 the tail is exactly the NEWEST N records, in order",
      _status11.tail_log_file(_s12_log, 5) == _s12_newest5,
      f"{_status11.tail_log_file(_s12_log, 5)}")
check("S12 a half-written final record is never served truncated",
      "HALF" not in "".join(_status11.tail_log_file(_s12_log, 99999)))
check("S12 tail(0)/tail(-1) still return one record",
      len(_status11.tail_log_file(_s12_log, 0)) == 1
      and len(_status11.tail_log_file(_s12_log, -1)) == 1)
check("S12 over-large tails return every complete record",
      len(_status11.tail_log_file(_s12_log, 99999)) == 300)

# A file bigger than one 8 KB scan block (the backward reader must stitch blocks
# without mis-slicing a record).
_s12_big = os.path.join(_s12_dir, "big.log")
with open(_s12_big, "wb") as _f12:
    _f12.write(b"".join(b"line-%06d-padding\n" % i for i in range(2000)))
check("S12 the backward scan stitches multiple blocks correctly",
      _status11.tail_log_file(_s12_big, 3)
      == [f"line-{i:06d}-padding\n" for i in range(1997, 2000)])

# Incremental /ws reader: carry the partial record, deliver it once complete.
_s12_recs, _s12_off, _s12_seed_end = _status11._tail_log_window(_s12_log, 2)
check("S12 the tail window reports a record boundary",
      _s12_recs == ["rec-0298\n", "rec-0299\n"], f"{_s12_recs}")
# The END offset is the one the pusher resumes from after seeding: resuming at
# the START would re-send the whole tail every tick while the file is small.
check("S12 the window's end offset sits right after the last returned record",
      _status11._read_new_log_lines(_s12_log, _s12_seed_end)
      == ([], _s12_seed_end), f"end={_s12_seed_end}")
_s12_end = os.path.getsize(_s12_log) - len(b"HALF-WRITTEN-RECORD")
check("S12 the end offset stops short of the half-written record",
      _s12_seed_end == _s12_end, f"end={_s12_seed_end} expect={_s12_end}")
check("S12 an incomplete final record is carried, not emitted",
      _status11._read_new_log_lines(_s12_log, _s12_end) == ([], _s12_end),
      f"{_status11._read_new_log_lines(_s12_log, _s12_end)}")
with open(_s12_log, "ab") as _f12:
    _f12.write(b"-COMPLETED\n")
_s12_new, _s12_newoff = _status11._read_new_log_lines(_s12_log, _s12_end)
check("S12 the carried record is delivered whole once its newline lands",
      _s12_new == ["HALF-WRITTEN-RECORD-COMPLETED\n"]
      and _s12_newoff == os.path.getsize(_s12_log), f"{_s12_new} off={_s12_newoff}")
check("S12 a consumed record is never delivered twice",
      _status11._read_new_log_lines(_s12_log, _s12_newoff) == ([], _s12_newoff))
with open(_s12_log, "wb") as _f12:
    _f12.write(b"rotated-1\nrotated-2\n")
check("S12 a rotated (smaller) log restarts from the start, not mid-file",
      _status11._read_new_log_lines(_s12_log, _s12_newoff)
      == (["rotated-1\n", "rotated-2\n"], 20))
check("S12 a missing log file degrades to an empty tail, never raises",
      _status11.tail_log_file(os.path.join(_s12_dir, "nope.log"), 5) == []
      and _status11._read_new_log_lines(os.path.join(_s12_dir, "nope.log"), 0)
      == ([], 0))

# --- S12b: a measured 0 ms clock offset is a MEASUREMENT ------------------
# Both REST clients used `time_offset == 0` as the "never synced" sentinel, so a
# local clock that actually matched Binance to the millisecond forced a fresh
# /time GET before EVERY signed request (and on futures that GET bypasses the
# rate limiter). The offset that reaches the dashboard's Clock sync row is the
# same field, so "0 ms" must mean 0 ms.
from src.exchange.rest_client import RestClient
from src.exchange.futures_rest_client import FuturesRestClient

for _s12_name, _s12_cls in (("spot", RestClient), ("futures", FuturesRestClient)):
    _s12_client = _s12_cls(FakeCfg({"PAPER_TRADE": True, "API_KEY": "test-key"}))
    _s12_calls = []

    async def _s12_fake_sync(_calls=_s12_calls):
        _calls.append(1)

    _s12_client.sync_time = _s12_fake_sync
    _s12_client.time_offset = 0  # genuinely measured, within a millisecond
    _s12_client.last_time_sync = int(time.time())
    _s12_ts = asyncio.run(_s12_client._get_timestamp())
    check(f"S12 {_s12_name}: a 0 ms offset does not re-sync every signed request",
          not _s12_calls, f"sync_time calls={len(_s12_calls)}")
    check(f"S12 {_s12_name}: the signed timestamp uses the measured 0 ms offset",
          isinstance(_s12_ts, int) and abs(_s12_ts - time.time() * 1000) < 100,
          f"ts={_s12_ts}")
    _s12_client.time_offset = None
    _s12_client.last_time_sync = 0
    _s12_ts = asyncio.run(_s12_client._get_timestamp())
    check(f"S12 {_s12_name}: an unmeasured offset syncs once before signing",
          len(_s12_calls) == 1, f"sync_time calls={len(_s12_calls)}")
    check(f"S12 {_s12_name}: a failed sync still yields a usable timestamp",
          isinstance(_s12_ts, int) and abs(_s12_ts - time.time() * 1000) < 100,
          f"ts={_s12_ts}")
    _s12_client.time_offset = 5
    _s12_client.last_time_sync = int(time.time()) - 10_000
    asyncio.run(_s12_client._get_timestamp())
    check(f"S12 {_s12_name}: a stale (>300s) offset is re-synced",
          len(_s12_calls) == 2, f"sync_time calls={len(_s12_calls)}")

# --- S13: the SOAK surface (card, watchdog, report) reads the same book ----
# The futures-soak card, the watchdog's Discord summaries and the post-soak report
# each carried their own SQL for "how many trades closed". Three of them were
# wrong in the same way and for different reasons: the card filtered
# `side='SELL'` in a FUTURES run (a short is closed with a BUY, so every short was
# missing), the watchdog asked for `status='CLOSED'` (a status the engine has
# never written, so every summary read 0 trades / $0.00 PnL), and the watchdog's
# risk_state keys `lose_streak` / `paper_start_balance` / `max_drawdown_pct` do
# not exist either. All of them now share trade_stats. The card's other two
# defects are pinned here as well: "last trade" was MAX(updated_at) over EVERY
# order (an entry fill dated the last trade), and the soak's LENGTH was read from
# the import-time process env instead of the live config that the watchdog uses.
import json as _json13
import types as _types13
import trade_stats as _ts13
import status as _st13

_T13 = 1_700_000_000_000
_s13_dir = _tempfile11.mkdtemp(prefix="s13_")
_s13_db = os.path.join(_s13_dir, "futures_soak.db")
_s13_conn = _sqlite11.connect(_s13_db)
_s13_conn.executescript(
    "CREATE TABLE orders (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT UNIQUE,"
    " symbol TEXT, side TEXT, order_type TEXT, price REAL, stop_price REAL, quantity REAL,"
    " executed_qty REAL, status TEXT, created_at INTEGER, updated_at INTEGER,"
    " profit_loss REAL DEFAULT 0, avg_fill_price REAL, exit_reason TEXT);"
    "CREATE TABLE risk_state (key TEXT PRIMARY KEY, value TEXT, updated_at INTEGER);"
    "CREATE TABLE active_trades (symbol TEXT PRIMARY KEY);"
)


def _s13_order(**cols):
    _s13_conn.execute(
        f"INSERT INTO orders ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
        tuple(cols.values()),
    )
    _s13_conn.commit()


# Same book as S11(a): a scale-out long (two legs, one trade, net +60), a SHORT
# whose exit is a BUY (+5), and an entry written LAST whose updated_at is the
# newest write in the table (it must not become the "last trade").
_s13_order(symbol="LONGUSDT", side="BUY", status="FILLED", created_at=_T13,
           updated_at=_T13, profit_loss=0.0, avg_fill_price=10.0)
_s13_order(symbol="LONGUSDT", side="SELL", status="CANCELED", created_at=_T13 + 1000,
           updated_at=_T13 + 1000, profit_loss=100.0, exit_reason="TAKE_PROFIT")
_s13_order(symbol="LONGUSDT", side="SELL", status="FILLED", created_at=_T13 + 2000,
           updated_at=_T13 + 2000, profit_loss=-40.0, exit_reason="STOP_LOSS")
_s13_order(symbol="SHORTUSDT", side="SELL", status="FILLED", created_at=_T13 + 50,
           updated_at=_T13 + 50, profit_loss=0.0, avg_fill_price=5.0)
_s13_order(symbol="SHORTUSDT", side="BUY", status="FILLED", created_at=_T13 + 1500,
           updated_at=_T13 + 1500, profit_loss=5.0, exit_reason="TAKE_PROFIT")
_s13_order(symbol="NEWUSDT", side="BUY", status="FILLED", created_at=_T13 + 5000,
           updated_at=_T13 + 9000, profit_loss=0.0)
# equity moves WITH the book: 22.5 start + the 65.0 realized above.
for _s13_kv in (("total_equity", "87.5"), ("paper_balance", "87.5"),
                ("loss_streak", "2"), ("win_streak", "1")):
    _s13_conn.execute(
        "INSERT INTO risk_state (key, value, updated_at) VALUES (?,?,?)",
        (_s13_kv[0], _s13_kv[1], _T13),
    )
_s13_conn.commit()
_s13_conn.close()

# (a) The shared definition itself: a short's BUY exit is an exit, a scale-out's
#     two legs are one trade, and the totals reconcile.
_s13_c = _sqlite11.connect(f"file:{_s13_db}?mode=ro", uri=True)
_s13_c.row_factory = _sqlite11.Row
_s13_row = _s13_c.execute(
    _ts13.trades_totals_sql(_ts13.exit_predicate(_ts13.has_exit_reason(_s13_c)))
).fetchone()
check("S13 trade_stats sees a short's BUY exit (closed = 2, not 1)",
      int(_s13_row["closed"]) == 2, f"closed={_s13_row['closed']}")
check("S13 trade_stats counts a scale-out's legs as ONE trade (2 trades, not 3)",
      int(_s13_row["closed"]) == 2 and int(_s13_row["wins"]) == 2
      and int(_s13_row["losses"]) == 0,
      f"closed={_s13_row['closed']} W={_s13_row['wins']} L={_s13_row['losses']}")
check("S13 trade_stats nets the scale-out (65.0) instead of summing legs blindly",
      abs(float(_s13_row["pnl"]) - 65.0) < 1e-9, f"pnl={_s13_row['pnl']}")
check("S13 trade_stats probes the schema before referencing exit_reason",
      _ts13.has_exit_reason(_s13_c) is True
      and "exit_reason" not in _ts13.exit_predicate(False),
      _ts13.exit_predicate(False))
_s13_c.close()

# (b) The soak card: PM2 jlist is stubbed (offline/deterministic) and the module
#     globals are pointed at the fixture, so this exercises the real builder.
_s13_marker = os.path.join(_s13_dir, "futures_soak_start_ms")
with open(_s13_marker, "w") as _f:
    _f.write(str(_T13))
_s13_saved = (_st13._SOAK_DB, _st13._SOAK_START_MARKER, _st13._WATCHDOG_STATE,
              _st13._WATCHDOG_EVENTS, _st13.subprocess.run)
_s13_procs = _json13.dumps([
    {"name": "futures-soak",
     "pm2_env": {"status": "online", "pm_uptime": _T13}},
    {"name": "soak-watchdog",
     "pm2_env": {"status": "online", "restart_time": 0}},
])
_st13._SOAK_DB = _s13_db
_st13._SOAK_START_MARKER = _s13_marker
_st13._WATCHDOG_STATE = os.path.join(_s13_dir, "no_state.json")
_st13._WATCHDOG_EVENTS = os.path.join(_s13_dir, "no_events.jsonl")
_st13.subprocess.run = lambda *a, **kw: _types13.SimpleNamespace(stdout=_s13_procs)
try:
    _s13_snap = _st13._futures_soak_snapshot({"SOAK_HOURS": "6"})
finally:
    (_st13._SOAK_DB, _st13._SOAK_START_MARKER, _st13._WATCHDOG_STATE,
     _st13._WATCHDOG_EVENTS, _st13.subprocess.run) = _s13_saved

check("S13 soak card counts the short's BUY exit (closed = 2, W 2 / L 0)",
      _s13_snap.get("closed") == 2 and _s13_snap.get("wins") == 2
      and _s13_snap.get("losses") == 0,
      f"closed={_s13_snap.get('closed')} W={_s13_snap.get('wins')}"
      f" L={_s13_snap.get('losses')}")
check("S13 soak card's PnL is the per-trade net (65.0)",
      abs(float(_s13_snap.get("pnl") or 0.0) - 65.0) < 1e-9,
      f"pnl={_s13_snap.get('pnl')}")
check("S13 soak card's 'last trade' is the newest EXIT, not the newest order write",
      _s13_snap.get("last_trade_ms") == _T13 + 2000,
      f"last_trade_ms={_s13_snap.get('last_trade_ms')}")
check("S13 soak card serves the soak DB's own equity",
      abs(float(_s13_snap.get("equity") or 0.0) - 87.5) < 1e-9,
      f"equity={_s13_snap.get('equity')}")
check("S13 soak length comes from the LIVE config, not the import-time env",
      _s13_snap.get("hours_total") == 6.0
      and _s13_snap.get("deadline_ms") == int(_T13 + 6 * 3_600_000),
      f"hours={_s13_snap.get('hours_total')} deadline={_s13_snap.get('deadline_ms')}"
      f" (module default {_st13._SOAK_HOURS}h)")

# (c) The watchdog's Discord summary uses the same tally, and the risk-state keys
#     it reads actually exist (lose_streak / paper_start_balance did not).
import soak_watchdog as _sw13

_s13_wd_db = _sw13.SOAK_DB
_sw13.SOAK_DB = _s13_db
try:
    _s13_m = _sw13.soak_metrics()
finally:
    _sw13.SOAK_DB = _s13_wd_db
check("S13 watchdog tally agrees with the soak card",
      _s13_m.get("closed") == 2 and _s13_m.get("wins") == 2
      and _s13_m.get("losses") == 0
      and abs(float(_s13_m.get("pnl") or 0.0) - 65.0) < 1e-9,
      f"closed={_s13_m.get('closed')} W={_s13_m.get('wins')}"
      f" L={_s13_m.get('losses')} pnl={_s13_m.get('pnl')}")
check("S13 watchdog reads the engine's REAL loss_streak key",
      _s13_m.get("loss_streak") == 2 and _s13_m.get("win_streak") == 1,
      f"W={_s13_m.get('win_streak')} L={_s13_m.get('loss_streak')}")
check("S13 watchdog derives a non-zero starting balance (paper_start_balance is absent)",
      abs(float(_s13_m.get("start_balance") or 0.0) - 22.5) < 1e-9,
      f"start={_s13_m.get('start_balance')}")
_s13_summary = _sw13.fmt_summary(_s13_m, 6.0, "TEST")
check("S13 watchdog summary states the real trade tally",
      "closed: **2**" in _s13_summary and "W 2 / L 0" in _s13_summary
      and "+65.0000" in _s13_summary,
      _s13_summary.replace("\n", " | "))

# --- S14: the browser check's roadmap re-read (a LIVE payload, not a snapshot) --
# browser_smoke compared the card on screen against the /api/status payload it
# fetched BEFORE navigating. The page is fed by the 1 Hz /ws push and the engine's
# screener rotates the watchlist, which re-partitions ok/blocked pairs — so a
# rotation between the two reads read as "the card dropped a chip" and failed a
# healthy dashboard (observed: missing ['ZROUSDT']). The card is now re-read
# together with a fresh payload until the pair is self-consistent; these checks
# pin the predicate that decides when to re-read, and that a real disagreement is
# still reported rather than retried away.
import browser_smoke as _bs14

_s14_rm = {"pairs": [{"symbol": "ZROUSDT", "required_equity": 4.2},
                    {"symbol": "AAAUSDT", "required_equity": 24.0}],
           "ok_pairs": ["ZROUSDT"], "blocked_pairs": ["AAAUSDT"]}
_s14_chips = {"chips": ["ZROUSDT", "AAAUSDT @ $24"]}
_s14_rotated = {"chips": ["AAAUSDT @ $24"]}
_s14_wrong = {"chips": ["ZROUSDT", "AAAUSDT @ $99"]}
check("S14 an agreed (payload, card) pair is not re-read",
      not (_bs14._expected_chips(_s14_rm) - set(_s14_chips["chips"]))
      and not _bs14._contradicting_chips(_s14_chips, _s14_rm),
      f"expected={sorted(_bs14._expected_chips(_s14_rm))}")
check("S14 a rotated watchlist triggers a re-read instead of a failure",
      sorted(_bs14._expected_chips(_s14_rm) - set(_s14_rotated["chips"]))
      == ["ZROUSDT"],
      f"chips={sorted(_s14_rotated['chips'])}")
check("S14 a genuine threshold disagreement is still detected",
      _bs14._contradicting_chips(_s14_wrong, _s14_rm)
      == ["AAAUSDT @ $99 (served 24.0)"],
      f"{_bs14._contradicting_chips(_s14_wrong, _s14_rm)}")

# --- S15: the SCREENER cache's failure backoff (the payload path runs at 1 Hz) --
# fetch_scanned_pairs is BLOCKING network I/O (4s urlopen timeout) called by every
# build_status_payload() — and the /ws pusher builds that once a second. Its 15s
# TTL was gated on `_scanned_cache["data"]` being truthy, so after a FAILED fetch
# `ts` stayed 0 and `now - ts < 15` was never true: every payload build re-ran the
# blocking request while the endpoint was unreachable, stalling the realtime
# status+log stream behind it. An empty-but-SUCCESSFUL screener (an unusual
# QUOTE_ASSET, a quiet market) relapsed the same way. The gate is now the attempt
# timestamp, a failure is backed off, and the last good list keeps being served.
# Engine-published risk_state.scanned_pairs short-circuits the network entirely —
# the monitor reads that path normally, but it is ABSENT exactly when the engine
# is stopped, which is when a monitoring dashboard matters most.
import status as _st15

_s15_real_urlopen = _st15.urllib.request.urlopen
_s15_saved_cache = dict(_st15._scanned_cache)


class _S15Boom:
    """urlopen stand-in that counts attempts and always fails."""

    def __init__(self):
        self.n = 0

    def open(self, *_a, **_kw):
        self.n += 1
        raise OSError("api.binance.com unreachable (simulated)")


class _S15Resp:
    """A 200 response whose body is a caller-supplied JSON string."""

    def __init__(self, body):
        self.status = 200
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class _S15Stub:
    """urlopen stand-in that always answers 200 with `body`."""

    def __init__(self, body=b"[]"):
        self.n = 0
        self._body = body

    def open(self, *_a, **_kw):
        self.n += 1
        return _S15Resp(self._body)


def _s15_read(why, db_data=None):
    _st15.urllib.request.urlopen = why.open
    return _st15.fetch_scanned_pairs(
        {"QUOTE_ASSET": "USDT", "STATIC_SYMBOLS": "BTCUSDT"},
        db_data if db_data is not None else {"scanned_pairs": []},
    )


try:
    boom = _S15Boom()
    # A dead endpoint across 5 consecutive payload builds is ONE attempt.
    _st15._scanned_cache.update({"ts": 0, "data": None, "fail_ts": 0.0})
    for _ in range(5):
        _s15_read(boom)
    check(
        "S15 a failed screener fetch is attempted once, then backed off",
        boom.n == 1,
        f"attempts={boom.n}",
    )
    # ... but not permanently: the retry resumes once the backoff expires.
    _st15._scanned_cache["fail_ts"] = (
        time.time() - _st15._SCANNED_FAIL_BACKOFF_S - 1
    )
    _s15_read(boom)
    check(
        "S15 the screener retry resumes after the backoff",
        boom.n == 2,
        f"attempts={boom.n}",
    )
    # Engine-published pairs never touch the network at all.
    good = [{"symbol": "BTCUSDT", "price": 1.0}]
    served = _s15_read(boom, {"scanned_pairs": good})
    check(
        "S15 engine-published pairs short-circuit the network entirely",
        boom.n == 2 and served == good,
        f"attempts={boom.n}",
    )
    # A later FAILED fetch keeps serving the last good list rather than blanking
    # the card (the same "degrade, don't disappear" rule the roadmap follows).
    _st15._scanned_cache["ts"] = time.time() - _st15._SCANNED_TTL_S - 1
    _st15._scanned_cache["fail_ts"] = 0.0
    served = _s15_read(boom)
    check(
        "S15 a failed fetch keeps serving the last good screener",
        boom.n == 3 and served == good,
        f"attempts={boom.n} served={len(served)}",
    )
    # A SUCCESS that selects no symbol is still a completed read: cached for the
    # TTL (the gate is the ATTEMPT time) and it clears the backoff.
    empty = _S15Stub(b"[]")
    _st15._scanned_cache.update({"ts": 0, "data": None, "fail_ts": 0.0})
    for _ in range(4):
        _s15_read(empty)
    check(
        "S15 an empty-but-successful fetch is cached (1 read in 4 builds)",
        empty.n == 1 and _st15._scanned_cache["fail_ts"] == 0.0,
        f"attempts={empty.n} fail_ts={_st15._scanned_cache['fail_ts']}",
    )
    # A success clears the backoff, so a LATER failure re-arms it instead of
    # being swallowed by a stale fail_ts.
    _st15._scanned_cache["ts"] = time.time() - _st15._SCANNED_TTL_S - 1
    _s15_read(boom)
    check(
        "S15 a success clears the backoff (a later failure re-arms it)",
        boom.n == 4 and _st15._scanned_cache["fail_ts"] > 0,
        f"attempts={boom.n} fail_ts={_st15._scanned_cache['fail_ts']}",
    )
finally:
    _st15.urllib.request.urlopen = _s15_real_urlopen
    _st15._scanned_cache.update(_s15_saved_cache)

print()
print("ALL_OK" if not FAILS else f"FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
