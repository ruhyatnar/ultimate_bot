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

print()
print("ALL_OK" if not FAILS else f"FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
