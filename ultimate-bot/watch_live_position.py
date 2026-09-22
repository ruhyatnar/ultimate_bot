#!/usr/bin/env python3
"""Watchdog for the LIVE position left on-exchange after the paper-mode switch.

The real NEAR position (entry 2.353, 7.39 NEAR) is intentionally NOT managed by
the engine anymore (PAPER_TRADE=true). Its exit is a MANUAL on-exchange SELL
LIMIT. This script is strictly READ-ONLY: it reports

  - live position (symbol, qty, entry) from the archived live DB
    (data/trading.live.db), if present
  - current mark price (public ticker)
  - unrealized PnL and distance to the manual exit order
  - status of open SELL orders on the exchange (signed read-only call)
  - reference levels: the engine's old proven bracket (SL -1.2% / TP +3%)

Usage:  ./venv/bin/python3 watch_live_position.py
        npm run watch:live        (from repo root)
"""
import asyncio
import json
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

LIVE_DB = os.path.join(ROOT, "data", "trading.live.db")
QUOTE = "USDT"

GREEN, YELLOW, RED, DIM, BOLD, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


def load_live_positions():
    """Best-effort read of the archived live DB's active trades."""
    if not os.path.exists(LIVE_DB):
        return []
    try:
        conn = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True, timeout=5)
        cur = conn.cursor()
        cols = [r[1] for r in cur.execute("PRAGMA table_info(active_trades)").fetchall()]
        if not cols:
            return []
        sel = ", ".join(c for c in ("symbol", "entry_price", "quantity", "side") if c in cols)
        rows = cur.execute(f"SELECT {sel} FROM active_trades").fetchall()
        conn.close()
        out = []
        for r in rows:
            d = dict(zip(sel.split(", "), r))
            out.append(d)
        return out
    except Exception as e:
        print(f"{YELLOW}  (could not read live DB: {e}){RESET}")
        return []


async def fetch_market_data(cfg, symbols):
    """Public price + signed open-orders (read-only)."""
    import urllib.request
    prices = {}
    for s in symbols:
        try:
            with urllib.request.urlopen(f"https://api.binance.com/api/v3/ticker/price?symbol={s}", timeout=5) as r:
                prices[s] = float(json.loads(r.read().decode())["price"])
        except Exception as e:
            print(f"{YELLOW}  (price fetch failed for {s}: {e}){RESET}")
    open_orders = []
    try:
        from src.exchange.rest_client import RestClient
        # Read-only watchdog: load signing credentials even in paper mode so
        # the signed openOrders read works. Paper mode skips key loading in
        # the constructor, so flip the flag for this instance only.
        cfg = dict(cfg)
        cfg["PAPER_TRADE"] = False
        rc = RestClient(cfg)
        await rc._ensure_session()
        for s in symbols:
            try:
                oo = await rc._request("GET", "/api/v3/openOrders", {"symbol": s}, signed=True)
                open_orders.extend(oo or [])
            except Exception as e:
                print(f"{YELLOW}  (openOrders fetch failed for {s}: {e}){RESET}")
        await rc.close()
    except Exception as e:
        print(f"{YELLOW}  (signed REST unavailable: {e}){RESET}")
    return prices, open_orders


async def main():
    from config import load_config
    cfg = load_config()

    print("=" * 72)
    print(f" LIVE POSITION WATCHDOG — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(" (engine is in PAPER mode; the real position is NOT engine-managed)")
    print("=" * 72)

    positions = load_live_positions()
    if not positions:
        print("  No live positions found in the archived DB — nothing to watch.")
        print("  (If you re-open live trading, restore data/trading.live.db -> trading.db)")
        return

    symbols = sorted({p.get("symbol") for p in positions if p.get("symbol")})
    prices, open_orders = await fetch_market_data(cfg, symbols)
    sells = [o for o in open_orders if o.get("side") == "SELL" and o.get("status") == "NEW"]

    for p in positions:
        sym = p.get("symbol", "?")
        qty = float(p.get("quantity") or 0)
        entry = float(p.get("entry_price") or 0)
        px = prices.get(sym)
        print(f"\n  {BOLD}{sym}{RESET}  qty={qty}  entry={entry}")
        if px is None:
            print("    price unavailable — cannot compute PnL")
            continue
        pnl = (px - entry) * qty
        pnl_pct = (px / entry - 1) * 100 if entry else 0.0
        col = GREEN if pnl >= 0 else RED
        print(f"    mark={px}  unrealized={col}{pnl:+.2f} {QUOTE} ({pnl_pct:+.2f}%){RESET}")
        # reference bracket (proven intraday_rsi levels)
        ref_sl, ref_tp = entry * 0.988, entry * 1.03
        print(f"    {DIM}reference levels: SL {ref_sl:.4f} (-1.2%) · TP {ref_tp:.4f} (+3%){RESET}")

    if sells:
        print(f"\n  {BOLD}OPEN EXIT ORDERS (exchange-side){RESET}")
        for o in sells:
            print(f"    {o['symbol']} SELL LIMIT {o.get('origQty')} @ {o.get('price')} "
                  f"(orderId {o['orderId']}, fills at +{(float(o.get('price', 0)) / positions[0]['entry_price'] - 1) * 100:.1f}% from entry)"
                  if positions else f"    {o['symbol']} SELL LIMIT {o.get('origQty')} @ {o.get('price')}")
    else:
        print(f"\n{RED}  WARNING: no open SELL order — the live position has NO exit resting on the book.{RESET}")

    print(f"\n  {DIM}Re-run: npm run watch:live{RESET}")


if __name__ == "__main__":
    asyncio.run(main())
