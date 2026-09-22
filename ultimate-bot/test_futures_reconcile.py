#!/usr/bin/env python3
"""Offline test: futures reconciliation must read positionAmt, not wallet.

Regressions covered (the live B2USDT incident):
  1. Tracked futures trade + positionAmt=88  -> trade STAYS tracked (the old
     wallet-balance code deleted it 12s after every fill).
  2. Tracked futures trade + positionAmt=0   -> trade removed (external close).
  3. Orphan futures position matching our recent BUY fills -> adopted.
Run with the venv python; no network, no live engine interference.
"""
import asyncio, logging, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.DEBUG)
from src.trade.trade_logic import TradeLogic


class FakeDB:
    def __init__(self):
        self.saved, self.deleted = {}, []
    async def save_active_trade(self, t):
        self.saved[t["symbol"]] = t
    async def delete_active_trade(self, s):
        self.deleted.append(s)
    async def fetch_one(self, q, p=()):
        return ("999", 88.0, int(time.time() * 1000) - 60_000)
    async def fetch_all(self, q, p=()):
        # (executed_qty, avg_fill_price) — the real cost-basis contract.
        return [(44.0, 0.5307), (44.0, 0.531425)]


class FakeSelf:
    """Minimal attribute surface for sync_positions_from_exchange."""
    def __init__(self, positions):
        self.config = {"PAPER_TRADE": False, "QUOTE_ASSET": "USDT",
                       "MARKET": "futures", "ORPHAN_ADOPT_WINDOW_HOURS": 48,
                       "TIMEFRAME": "5m", "ATR_PERIOD": 14}
        self.is_futures = True
        self.quote_asset = "USDT"
        self._positions = positions
        self.current_symbols = {"B2USDT"}
        self.active_trades = {}
        self.db = FakeDB()
        self.logger = logging.getLogger("test")
        class W:  # webhook
            async def send(self, msg): print(f"   [webhook] {msg}")
        self.webhook = W()
        class WS:
            async def get_current_price(self, s): return 0.53
        self.ws_stream = WS()
        class REST:
            async def get_filters(self, s):
                return {"NOTIONAL": {"minNotional": "5"}, "LOT_SIZE": {"minQty": "0.001"}}
            symbol_info_cache = {"B2USDT": {}}
            async def get_user_trades(self, s, limit=50):
                return [{"qty": "44", "isBuyer": True, "time": int(time.time()*1000)-60_000},
                        {"qty": "44", "isBuyer": True, "time": int(time.time()*1000)-50_000}]
            async def _request(self, *a, **k): return []
        self.rest = REST()

    async def _read_positions_map(self):
        return dict(self._positions)

    async def _adopt_orphan_position(self, symbol, base_asset, qty, price):
        return await TradeLogic._adopt_orphan_position(self, symbol, base_asset, qty, price)

    async def _verify_recent_futures_qty(self, symbol, want_qty, window_ms):
        return await TradeLogic._verify_recent_futures_qty(self, symbol, want_qty, window_ms)

    def _compute_bracket(self, entry, atr=None):
        return entry * 0.988, entry * 1.03


TRADE = {"symbol": "B2USDT", "entry_price": 0.5310, "side": "BUY", "quantity": 88.0,
         "entry_time": time.time() - 600, "stop_price": 0.5246, "take_profit": 0.5469,
         "order_id": "999"}


async def main():
    ok = True

    # --- Case 1: live position -> trade must STAY ---
    fake = FakeSelf({"B2USDT": 88.0})
    fake.active_trades = {"B2USDT": dict(TRADE)}
    await TradeLogic.sync_positions_from_exchange(fake)
    kept = "B2USDT" in fake.active_trades and "B2USDT" not in fake.db.deleted
    print(f"case1 positionAmt=88 tracked -> {'KEPT' if kept else 'DELETED (BUG)'}")
    ok &= kept

    # --- Case 2: closed externally -> trade removed ---
    fake = FakeSelf({})
    fake.active_trades = {"B2USDT": dict(TRADE)}
    await TradeLogic.sync_positions_from_exchange(fake)
    removed = "B2USDT" not in fake.active_trades and "B2USDT" in fake.db.deleted
    print(f"case2 positionAmt=0 tracked  -> {'REMOVED' if removed else 'STUCK (BUG)'}")
    ok &= removed

    # --- Case 3: orphan position matching our fills -> adopted ---
    fake = FakeSelf({"B2USDT": 88.0})
    fake.active_trades = {}
    # provide the bits adoption needs
    fake._bucket_entry_latch = {}
    async def _get_cached_klines(*a, **k): return []
    fake.signal_gen = type("SG", (), {"_get_cached_klines": staticmethod(_get_cached_klines),
                                      "_calculate_atr": staticmethod(lambda df: None),
                                      "_to_df": staticmethod(lambda k: None)})()
    try:
        await TradeLogic.sync_positions_from_exchange(fake)
    except Exception as e:
        print(f"case3 raised: {e}")
    adopted = "B2USDT" in fake.active_trades and "B2USDT" in fake.db.saved
    print(f"case3 orphan (88u = 44+44 fills) -> {'ADOPTED' if adopted else 'NOT ADOPTED'}")
    ok &= adopted

    print("ALL_OK" if ok else "FAILURES_PRESENT")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
