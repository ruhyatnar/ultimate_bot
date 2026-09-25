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
  ./venv/bin/python3 capital_roadmap.py        # equity AND pairs from the live DB:
                                               # the pairs are the engine's watched set
  ./venv/bin/python3 capital_roadmap.py --equity 22     # force the equity
  ./venv/bin/python3 capital_roadmap.py --pairs NEARUSDT,LINKUSDT   # force the pairs
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
# CLI default / last-resort fallback ONLY. The served roadmap is given the
# ENGINE'S live watched set instead (see compute_roadmap's `pairs`): the engine
# runs DYNAMIC_SYMBOLS with a rotating screener selection, so a list baked in
# here would analyse pairs the bot is not trading while missing the ones it is.
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


DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "trading.db"
)


def _risk_state_value(key):
    """One raw risk_state value from the engine's DB, or None (read-only)."""
    try:
        db = sqlite3.connect(DB_PATH)
        try:
            row = db.execute(
                "SELECT value FROM risk_state WHERE key=?", (key,)
            ).fetchone()
        finally:
            db.close()
        return row[0] if row else None
    except Exception:
        return None


def equity_from_db():
    """The engine's own equity view: live DB risk_state.total_equity."""
    try:
        row = _risk_state_value("total_equity")
        return float(row) if row else None
    except Exception:
        return None


def watched_symbols_from_db():
    """The engine's LIVE watched pairs (trade_logic.update_symbols ->
    risk_state.monitored_symbols: screener picks + STATIC_SYMBOLS + any symbol
    with an open trade).

    The CLI defaults to these for the same reason status.py passes them in: the
    engine runs DYNAMIC_SYMBOLS, so DEFAULT_PAIRS is a different universe from
    the one being traded. [] when unavailable; callers fall back.
    """
    raw = _risk_state_value("monitored_symbols")
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(s).upper() for s in parsed if s]


def config_core():
    """RISK_PER_TRADE / SL_PERCENT from the deployed config (no engine boot)."""
    import config as cfg_mod
    c = cfg_mod.load_config()
    return float(c["RISK_PER_TRADE"]), float(c["SL_PERCENT"]), float(c.get("FUTURES_LEVERAGE", 1))


def compute_roadmap(equity, pairs=None, include_prices=True):
    """Roadmap as DATA (for status.py / the web monitor).

    `pairs` is the CALLER'S symbol list: status.py passes the engine's live
    watched set (trade_logic.update_symbols -> risk_state.monitored_symbols), so
    every figure describes the pairs the bot is actually trading. DEFAULT_PAIRS
    applies only when the caller has no list (engine predates the field, or its
    watchlist is empty), and `pairs_source` reports which of the two was used so
    the card never presents a fallback list as the live one.

    Network reads (exchangeInfo floors, prices, funding) are cached by the
    caller when called often. Returns a JSON-safe dict:
      {equity, risk_per_trade, sl_percent, proven_notional, implied_leverage,
       pairs: [{symbol, price, floor, ok, required_equity, funding_rate}],
       ok_pairs, blocked_pairs,
       stages: [{threshold, label, reached}], fee_edge_pct, pairs_source}
    """
    requested = [p.strip().upper() for p in (pairs or []) if p and p.strip()]
    pairs = requested or list(DEFAULT_PAIRS)
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
            # Equity at which proven sizing clears THIS pair's floor:
            # floor * SL% / RISK%. Per-pair, so the dashboard never has to
            # assume a threshold that only holds for the current watchlist.
            "required_equity": round(floor * sl_pct / risk, 2),
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
        "pairs_source": "engine-watchlist" if requested else "default",
        "generated_ms": int(time.time() * 1000),
    }


def main():
    ap = argparse.ArgumentParser(description="Capital roadmap (read-only, live exchange data)")
    ap.add_argument("--equity", type=float, default=None, help="override account equity (USDT)")
    ap.add_argument(
        "--pairs",
        default=None,
        help="comma-separated futures pairs (default: the engine's live watched "
        "set from the DB, else capital_roadmap.DEFAULT_PAIRS)",
    )
    args = ap.parse_args()

    if args.pairs:
        pairs = [p.strip().upper() for p in args.pairs.split(",") if p.strip()]
        pairs_source = "--pairs"
    else:
        pairs = watched_symbols_from_db()
        pairs_source = "engine watchlist (risk_state.monitored_symbols)"
        if not pairs:
            pairs = list(DEFAULT_PAIRS)
            pairs_source = "DEFAULT_PAIRS (engine watchlist empty)"
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
    print(f"pairs ({len(pairs)}) from {pairs_source}: {', '.join(pairs)}")
    print("=" * 78)
    print(f"{'pair':10} {'price':>10} {'floor$':>7} {'req$':>6} {'ok?':>4} {'wallet%':>8} {'liq@lev':>9} {'funding/8h':>11}")
    ok_pairs, blocked_pairs = [], []
    for s in pairs:
        floor = floors.get(s)
        px = prices.get(s)
        if floor is None or px is None:
            print(f"{s:10} {'?':>10} {'?':>7} {'?':>6}  SKIP (no exchange data)")
            continue
        ok = proven_notional >= floor
        (ok_pairs if ok else blocked_pairs).append(s)
        # Equity at which THIS pair's floor is cleared. Shown per row because a
        # watchlist can span several floors — quoting one figure for all of them
        # over-states what the cheaper pairs need.
        required = floor * sl_pct / risk
        # Wallet fit at exchange leverage 1: notional cannot exceed the wallet.
        wallet_pct = proven_notional / equity * 100
        # Liquidation distance at the CONFIGURED leverage (maintenance-margin
        # ignored, conservative): 1/lev of the entry. At lev=1 that is ~100% —
        # unreachable for a 1.2%-SL strategy, i.e. liquidation is a non-factor
        # unless someone raises FUTURES_LEVERAGE.
        liq_pct = (1.0 / max(lev_cfg, 1)) * 100
        fr = latest_funding(s)
        print(f"{s:10} {px:>10.4f} {floor:>7.0f} {required:>6.0f} {'YES' if ok else 'NO':>4} "
              f"{wallet_pct:>7.0f}% {liq_pct:>8.0f}% {fr*100:>10.4f}%")
    print()
    print("(req$ = equity at which THIS pair's floor is cleared, i.e. "
          f"floor * SL% / RISK% = floor * {sl_pct:.4f} / {risk:.3f}; "
          "wallet% = margin used at FUTURES_LEVERAGE=1; liq@lev = liquidation "
          f"distance at the configured {lev_cfg}x — both far beyond the "
          f"{sl_pct*100:.1f}% stop)")
    print()

    # ---- staged plan -------------------------------------------------------
    min_floor = min([floors.get(s, 0) for s in pairs if floors.get(s)] or [5.0])
    max_floor = max([floors.get(s, 0) for s in pairs] or [5.0])
    stages = []
    # equity needed so proven sizing clears a floor: floor * SL% / RISK%
    stages.append((min_floor * sl_pct / risk,
                   f"lowest-floor pairs (${min_floor:.0f}) tradable at proven {risk:.1%} risk"))
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
        # One threshold PER pair. This used to quote `max_floor * SL% / RISK%` for
        # the whole blocked set, which over-states what a lower-floor pair needs —
        # a $5-floor pair is blocked at $22 equity but tradable at $6, not $24.
        print("  * Stay spot on these until proven sizing clears THEIR floor:")
        for s in sorted(blocked_pairs, key=lambda x: floors.get(x) or 0.0):
            floor = floors.get(s)
            if floor is None:
                continue
            required = floor * sl_pct / risk
            print(f"      {s:12} floor ${floor:>6.0f}  ->  tradable at "
                  f"~${required:.0f} equity (+{required - equity:.0f})")
        print("    (or drop them from the futures watchlist.)")
    print(f"  * Futures fee edge: {fee_edge:.2f}% per round trip (taker), identical signals.")
    print("  * Keep PAPER first: MARKET=futures PAPER_TRADE=true on an isolated DB,")
    print("    then flip MARKET in .env only after a 24h futures soak mirrors this math.")
    print("  * FUTURES_LEVERAGE stays 1: the proven sizing never needs exchange leverage")
    print("    to hit its floors — raising it only adds liquidation risk, not expectancy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
