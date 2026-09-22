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

print()
print("ALL_OK" if not FAILS else f"FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
