#!/usr/bin/env python3
"""Capital roadmap for the Ultimate Bot — grounded in LIVE exchange data.

Answers, for the account's actual equity, at every growth stage:
  * can the proven config (RISK_PER_TRADE x SL_PERCENT sizing) place orders
    under the REAL USDⓈ-M futures NOTIONAL floor of each watched pair?
  * what effective leverage does the proven sizing imply (notional/equity)?
  * when does each futures constraint stop binding?

Fetches live /fapi/v1/exchangeInfo (per-symbol NOTIONAL floors), /fapi/v1/ticker/price
and the account's equity from the engine DB (or --equity override), then prints a
stage table. No orders are ever placed — read-only.

Usage:
  ./venv/bin/python3 capital_roadmap.py                 # equity from live DB
  ./venv/bin/python3 capital_roadmap.py --equity 22     # force a number
  ./venv/bin/python3 capital_roadmap.py --pairs NEARUSDT,LINKUSDT
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FUTURES_BASE = "https://fapi.binance.com"
DEFAULT_PAIRS = ["NEARUSDT", "LINKUSDT", "DOTUSDT", "ARBUSDT", "OPUSDT", "LSKUSDT"]
SPOT_TAKER = 0.001          # 0.1% per leg (matches backtest.TAKER_FEE)
FUTURES_TAKER = 0.0005      # 0.05% per leg (matches backtest.FUTURES_TAKER_FEE)


def http_get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "roadmap/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def futures_floors(symbols):
    """{symbol: NOTIONAL floor} from the live futures exchangeInfo."""
    info = http_get_json(f"{FUTURES_BASE}/fapi/v1/exchangeInfo")
    floors = {}
    for s in info.get("symbols", []):
        sym = s.get("symbol", "")
        if sym not in symbols:
            continue
        for f in s.get("filters", []):
            if f.get("filterType") in ("MIN_NOTIONAL", "NOTIONAL"):
                floors[sym] = float(f.get("notional") or f.get("minNotional") or 0)
                break
    return floors


def futures_prices(symbols):
    tickers = http_get_json(f"{FUTURES_BASE}/fapi/v1/ticker/price")
    return {t["symbol"]: float(t["price"]) for t in tickers if t["symbol"] in symbols}


def latest_funding(symbol):
    """Most recent funding rate (fraction per 8h) or 0.0 on failure."""
    try:
        rows = http_get_json(f"{FUTURES_BASE}/fapi/v1/fundingRate?symbol={symbol}&limit=1")
        return float(rows[-1]["fundingRate"]) if rows else 0.0
    except Exception:
        return 0.0


def equity_from_db():
    """The engine's own equity view: live DB risk_state.total_equity."""
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "trading.db")
    try:
        db = sqlite3.connect(db_path)
        row = db.execute("SELECT value FROM risk_state WHERE key='total_equity'").fetchone()
        return float(row[0]) if row else None
    except Exception:
        return None


def config_core():
    """RISK_PER_TRADE / SL_PERCENT from the deployed config (no engine boot)."""
    import config as cfg_mod
    c = cfg_mod.load_config()
    return float(c["RISK_PER_TRADE"]), float(c["SL_PERCENT"]), float(c.get("FUTURES_LEVERAGE", 1))


def compute_roadmap(equity, pairs=None, include_prices=True):
    """Roadmap as DATA (for status.py / the web monitor).

    Network reads (exchangeInfo floors, prices, funding) are cached by the
    caller when called often. Returns a JSON-safe dict:
      {equity, risk_per_trade, sl_percent, proven_notional, implied_leverage,
       pairs: [{symbol, price, floor, ok, funding_rate}], ok_pairs, blocked_pairs,
       stages: [{threshold, label, reached}], fee_edge_pct}
    """
    pairs = [p.strip().upper() for p in (pairs or DEFAULT_PAIRS) if p.strip()]
    risk, sl_pct, lev_cfg = config_core()
    floors = futures_floors(set(pairs))
    prices = futures_prices(set(pairs)) if include_prices else {}
    proven_notional = equity * risk / sl_pct

    pair_rows, ok_pairs, blocked_pairs = [], [], []
    for s in pairs:
        floor = floors.get(s)
        if floor is None:
            continue
        ok = proven_notional >= floor
        (ok_pairs if ok else blocked_pairs).append(s)
        pair_rows.append({
            "symbol": s,
            "price": prices.get(s),
            "floor": floor,
            "ok": ok,
            "funding_rate": latest_funding(s),
        })

    min_floor = min([floors.get(s, 0) for s in pairs if floors.get(s)] or [5.0])
    max_floor = max([floors.get(s, 0) for s in pairs] or [5.0])
    stages = [
        {"threshold": min_floor * sl_pct / risk,
         "label": f"lowest-floor pairs (${min_floor:.0f}) tradable at proven risk"},
        {"threshold": max_floor * sl_pct / risk,
         "label": f"highest-floor watched pair (${max_floor:.0f}) — ALL pairs OK"},
        {"threshold": 100.0, "label": "legacy '$100 floor' assumption satisfied"},
    ]
    for st in stages:
        st["reached"] = equity >= st["threshold"]
    return {
        "equity": round(equity, 2),
        "risk_per_trade": risk,
        "sl_percent": sl_pct,
        "proven_notional": round(proven_notional, 2),
        "implied_leverage": round(proven_notional / equity, 3) if equity > 0 else 0.0,
        "futures_leverage_cfg": lev_cfg,
        "pairs": pair_rows,
        "ok_pairs": ok_pairs,
        "blocked_pairs": blocked_pairs,
        "stages": stages,
        "fee_edge_pct": round((SPOT_TAKER - FUTURES_TAKER) * 2 * 100, 3),
        "generated_ms": int(time.time() * 1000),
    }


def main():
    ap = argparse.ArgumentParser(description="Capital roadmap (read-only, live exchange data)")
    ap.add_argument("--equity", type=float, default=None, help="override account equity (USDT)")
    ap.add_argument("--pairs", default=",".join(DEFAULT_PAIRS), help="comma-separated futures pairs")
    args = ap.parse_args()

    pairs = [p.strip().upper() for p in args.pairs.split(",") if p.strip()]
    risk, sl_pct, lev_cfg = config_core()
    equity = args.equity if args.equity is not None else equity_from_db()
    if equity is None:
        print("No equity found (no DB row, no --equity). Pass --equity 22.")
        return 1

    floors = futures_floors(set(pairs))
    prices = futures_prices(set(pairs))

    # Proven sizing: qty = equity*risk / (entry*SL%) -> notional = equity*risk/SL%
    proven_notional = equity * risk / sl_pct

    print("=" * 78)
    print(f"CAPITAL ROADMAP — equity ${equity:.2f} | RISK_PER_TRADE={risk:.3f} SL={sl_pct*100:.1f}%")
    print(f"proven sizing implies notional ${proven_notional:.2f}/trade "
          f"(implied leverage {proven_notional/equity:.2f}x on equity)")
    print("=" * 78)
    print(f"{'pair':10} {'price':>10} {'floor$':>7} {'ok?':>4} {'wallet%':>8} {'liq@lev':>9} {'funding/8h':>11}")
    ok_pairs, blocked_pairs = [], []
    for s in pairs:
        floor = floors.get(s)
        px = prices.get(s)
        if floor is None or px is None:
            print(f"{s:10} {'?':>10} {'?':>7}  SKIP (no exchange data)")
            continue
        ok = proven_notional >= floor
        (ok_pairs if ok else blocked_pairs).append(s)
        # Wallet fit at exchange leverage 1: notional cannot exceed the wallet.
        wallet_pct = proven_notional / equity * 100
        # Liquidation distance at the CONFIGURED leverage (maintenance-margin
        # ignored, conservative): 1/lev of the entry. At lev=1 that is ~100% —
        # unreachable for a 1.2%-SL strategy, i.e. liquidation is a non-factor
        # unless someone raises FUTURES_LEVERAGE.
        liq_pct = (1.0 / max(lev_cfg, 1)) * 100
        fr = latest_funding(s)
        print(f"{s:10} {px:>10.4f} {floor:>7.0f} {'YES' if ok else 'NO':>4} "
              f"{wallet_pct:>7.0f}% {liq_pct:>8.0f}% {fr*100:>10.4f}%")
    print()
    print(f"(wallet% = margin used at FUTURES_LEVERAGE=1; liq@lev = liquidation "
          f"distance at the configured {lev_cfg}x — both far beyond the "
          f"{sl_pct*100:.1f}% stop)")
    print()

    # ---- staged plan -------------------------------------------------------
    min_floor = min([floors.get(s, 0) for s in pairs if floors.get(s)] or [5.0])
    max_floor = max([floors.get(s, 0) for s in pairs] or [5.0])
    stages = []
    # equity needed so proven sizing clears a floor: floor * SL% / RISK%
    stages.append((min_floor * sl_pct / risk,
                   f"lowest-floor pairs (${min_floor:.0f}) tradable at proven 1% risk"))
    stages.append((max_floor * sl_pct / risk,
                   f"highest-floor watched pair (${max_floor:.0f}) tradable — ALL pairs OK"))
    stages.append((100.0, "legacy '$100 floor' assumption satisfied; every perp tradable"))
    stages.sort()

    print("STAGES (equity thresholds where futures constraints stop binding):")
    for th, why in stages:
        mark = "REACHED" if equity >= th else f"at ${th:.0f}"
        print(f"  ${th:>7.2f}  {mark:<18} {why}")

    print("\nRECOMMENDATION:")
    fee_edge = (SPOT_TAKER - FUTURES_TAKER) * 2 * 100
    if ok_pairs:
        print(f"  * Futures viable NOW on: {', '.join(ok_pairs)}")
        print(f"    (proven ${proven_notional:.2f} notional clears their floors, "
              f"implied {proven_notional/equity:.2f}x leverage — no extra leverage needed)")
    if blocked_pairs:
        th = max_floor * sl_pct / risk
        print(f"  * Stay spot on: {', '.join(blocked_pairs)} until ~${th:.0f} equity "
              f"(or drop them from the futures watchlist).")
    print(f"  * Futures fee edge: {fee_edge:.2f}% per round trip (taker), identical signals.")
    print("  * Keep PAPER first: MARKET=futures PAPER_TRADE=true on an isolated DB,")
    print("    then flip MARKET in .env only after a 24h futures soak mirrors this math.")
    print("  * FUTURES_LEVERAGE stays 1: the proven sizing never needs exchange leverage")
    print("    to hit its floors — raising it only adds liquidation risk, not expectancy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
