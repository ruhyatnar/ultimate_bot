#!/usr/bin/env python3
"""Backtest engine for the bot's single strategy: `intraday_rsi` (rsi_dip).

Replays real historical Binance klines through the LIVE engine's own decision
core (SignalGenerator.decide) — the exact code the engine trades with — so there
is zero drift between what is backtested and what trades real money.

Trade management mirrors trade_logic.py:
  - Bracket: fixed % of entry (SL_PERCENT / TP_PERCENT), TP floored at
    MIN_TP_PERCENT and widened to MIN_RISK_REWARD when needed
  - Trigger: daily-EMA50 regime gate + RSI dip (inside SignalGenerator.decide)
  - Taker fees both legs (0.1% x 2) netted from every trade's PnL — the engine
    exits every position with a MARKET order, so there is no maker TP relief
  - 1R fixed-fractional sizing (RISK_PER_TRADE of current equity), with the
    notional caps (BALANCE_USAGE_PERCENT / MAX_SYMBOL_ALLOCATION_PERCENT)
  - Exchange NOTIONAL.minNotional floor (5 USDT, per exchangeInfo) — trades the
    live engine could not place are skipped, not counted as wins/losses
  - MAX_TRADES_PER_DAY entries per UTC day + post-exit cooldown bars
  - Same-UTC-day close (CLOSE_AT_UTC_DAY_END) and a hard time stop (MAX_HOLD_TIME)
  - Daily drawdown circuit breaker (MAX_DAILY_DRAWDOWN) — same rule as live

The active configuration has NO trailing stop and NO breakeven lock
(BREAKEVEN_ENABLED=false; TRAILING_STOP_ACTIVATE=5% sits beyond the +3% TP), so
the bracket + EOD + time stop are the complete exit model — matching the engine.

Usage:
  ./venv/bin/python3 backtest.py                                  # .env defaults
  ./venv/bin/python3 backtest.py --symbol ETHUSDT --pages 10
  ./venv/bin/python3 backtest.py --equity 22 --sl 0.012 --tp 0.03

Exit code 0 = backtest ran (regardless of profitability), 2 = could not run.
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

import pandas as pd

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from config import load_config, PRESETS  # noqa: E402
from src.strategies.signal_generator import SignalGenerator  # noqa: E402
from src.strategies.trade_policy import (  # noqa: E402
    bar_exit,
    effective_bracket,
    ratchet_stops,
)

BASE_URL = "https://api.binance.com"
FUTURES_BASE_URL = "https://fapi.binance.com"
TAKER_FEE = 0.001            # 0.1% per leg, matching the engine's fee model (spot)
FUTURES_TAKER_FEE = 0.0005   # USDⓈ-M futures taker: 0.05% per leg (VIP0)
# NOTIONAL floor fallback ONLY (used if the live exchangeInfo fetch fails).
# Ground truth 2026-09: 5 USDT dominates (719 of 725 USDT perps); a few sit at 20-50.
# The backtest always prefers the REAL per-symbol floor via fetch_futures_min_notional.
FUTURES_DEFAULT_MIN_NOTIONAL = 5.0

_FUTURES_FLOOR_CACHE = {}


def fetch_futures_min_notional(symbol):
    """Real NOTIONAL floor for a futures symbol from fapi exchangeInfo (cached).

    The futures folklore says "~100 USDT", but the live exchange says otherwise
    (2026-09: 5 USDT on 719 of 725 USDT perps). The real value decides whether
    small accounts can trade futures at all — so fetch it per symbol.
    """
    if symbol in _FUTURES_FLOOR_CACHE:
        return _FUTURES_FLOOR_CACHE[symbol]
    try:
        req = urllib.request.Request(
            f"{FUTURES_BASE_URL}/fapi/v1/exchangeInfo?symbol={symbol}",
            headers={"User-Agent": "BacktestEngine/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            info = json.loads(resp.read().decode("utf-8"))
        floor = FUTURES_DEFAULT_MIN_NOTIONAL
        for s in info.get("symbols", []):
            for f in s.get("filters", []):
                if f.get("filterType") in ("MIN_NOTIONAL", "NOTIONAL"):
                    floor = float(f.get("notional") or f.get("minNotional") or floor)
                    break
        _FUTURES_FLOOR_CACHE[symbol] = floor
        return floor
    except Exception:
        _FUTURES_FLOOR_CACHE[symbol] = FUTURES_DEFAULT_MIN_NOTIONAL
        return FUTURES_DEFAULT_MIN_NOTIONAL


def fetch_klines(symbol, interval, limit, end_time=None, market="spot"):
    base = FUTURES_BASE_URL if market == "futures" else BASE_URL
    path = "/fapi/v1/klines" if market == "futures" else "/api/v3/klines"
    query = f"symbol={symbol}&interval={interval}&limit={min(limit, 1000)}"
    if end_time:
        query += f"&endTime={int(end_time)}"
    url = f"{base}{path}?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": "BacktestEngine/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_funding_rates(symbol, start_ms, end_ms):
    """Historical funding rates (fapi /fapi/v1/fundingRate) covering [start_ms, end_ms].

    Returns [(fundingTime_ms, rate_as_fraction)] oldest-first, or [] if the
    endpoint is unreachable (then the backtest falls back to a flat estimate).
    Funding settles every 8h (00/08/16 UTC); a long PAYS when rate > 0.
    """
    rates = []
    cursor = int(start_ms)
    try:
        while cursor < int(end_ms):
            query = f"symbol={symbol}&startTime={int(cursor)}&endTime={int(end_ms)}&limit=1000"
            url = f"{FUTURES_BASE_URL}/fapi/v1/fundingRate?{query}"
            req = urllib.request.Request(url, headers={"User-Agent": "BacktestEngine/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                rows = json.loads(resp.read().decode("utf-8"))
            if not rows:
                break
            for r in rows:
                try:
                    rates.append((int(r["fundingTime"]), float(r["fundingRate"])))
                except (KeyError, TypeError, ValueError):
                    continue
            last_t = int(rows[-1]["fundingTime"])
            if len(rows) < 1000:
                break
            cursor = last_t + 1
    except Exception:
        return rates  # partial data is still better than a flat guess
    return rates


_KLINE_CACHE = {}   # (symbol, interval, pages, end_time, market) -> rows; repeated sweeps skip refetching


def fetch_history(symbol, interval, pages, end_time=None, market="spot"):
    """Walk backwards `pages` requests of up to 1000 bars each (cached per process)."""
    key = (symbol, interval, pages, end_time, market)
    if key in _KLINE_CACHE:
        return _KLINE_CACHE[key]
    all_rows = []
    cursor = end_time
    for _ in range(pages):
        rows = fetch_klines(symbol, interval, 1000, end_time=cursor, market=market)
        if not rows:
            break
        all_rows = rows + all_rows
        cursor = rows[0][0] - 1
        if len(rows) < 1000:
            break
    _KLINE_CACHE[key] = all_rows
    return all_rows


def to_df(rows):
    df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume",
                                     "close_time", "quote_volume", "trades", "taker_buy_base",
                                     "taker_buy_quote", "ignore"])
    for col in ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_quote"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    df["open_time"] = pd.to_numeric(df["open_time"], errors="coerce")
    return df


def run_backtest(symbol, preset_name, pages, end_time=None, quiet=False,
                 sl_percent=None, tp_percent=None, cooldown_bars=None, min_tp=None,
                 max_hold=None, min_notional=None, equity=None,
                 balance_usage_percent=None, max_symbol_allocation_percent=None,
                 overrides=None, market="spot"):
    """Replay the strategy on real klines.

    market="futures" switches the whole simulation to USDⓈ-M perp assumptions:
      * klines fetched from fapi (futures prices/order books differ slightly)
      * taker fee 0.05% per leg (VIP0 futures) instead of 0.1% spot
      * historical per-8h funding charged on the open position's notional
        (long pays positive rate) — fetched from /fapi/v1/fundingRate
      * NOTIONAL.minNotional floor defaults to 100 USDT (futures reality)

    `overrides` (dict) is applied AFTER the preset so sweeps can vary
    preset-pinned keys (e.g. MAX_TRADES_PER_DAY, RSI_TIMEFRAME_MS) — the same
    precedence the engine's .env would have had if the preset did not pin them.
    """
    preset = PRESETS.get(preset_name) or PRESETS["intraday_rsi"]
    cfg = load_config()
    cfg.update({k: v for k, v in preset.items()})
    if overrides:
        cfg.update(overrides)
    if sl_percent is not None: cfg["SL_PERCENT"] = float(sl_percent)
    if tp_percent is not None: cfg["TP_PERCENT"] = float(tp_percent)
    if min_tp is not None: cfg["MIN_TP_PERCENT"] = float(min_tp)
    if max_hold is not None: cfg["MAX_HOLD_TIME"] = float(max_hold)   # research: time-stop override (seconds)
    if min_notional is not None: cfg["MIN_NOTIONAL"] = float(min_notional)  # research: Binance spot minNotional floor
    if balance_usage_percent is not None: cfg["BALANCE_USAGE_PERCENT"] = float(balance_usage_percent)
    if max_symbol_allocation_percent is not None: cfg["MAX_SYMBOL_ALLOCATION_PERCENT"] = float(max_symbol_allocation_percent)

    import logging
    logging.disable(logging.CRITICAL)
    sg = SignalGenerator(cfg, rest=None)

    tf_ms = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
             "1h": 3_600_000, "4h": 14_400_000}.get(cfg["TIMEFRAME"], 300_000)
    # Mirror the live engine: default post-exit cooldown derives from COOLDOWN_LOSS,
    # but MAX_TRADES_PER_DAY (the live engine's binding entry cap) takes precedence:
    # when set, the backtest enforces entries-per-UTC-day instead of a blanket lockout.
    max_per_day = int(cfg.get("MAX_TRADES_PER_DAY", 0) or 0)
    if cooldown_bars is None:
        cooldown_bars = 0 if max_per_day > 0 else max(0, int(round(cfg["COOLDOWN_LOSS"] * 1000 / tf_ms)))
    if max_per_day > 0:
        cfg["_MAX_TRADES_PER_DAY"] = max_per_day   # consumed by the entry loop

    # Real regime candles for the filter — whatever MTF_TIMEFRAME the engine is
    # configured with (1d by default; 4h/1h are supported and modelled exactly).
    mtf = cfg["MTF_TIMEFRAME"]
    mtf_ms = {"5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000,
              "4h": 14_400_000, "1d": 86_400_000}.get(mtf)
    if mtf_ms is None:
        print(f"UNSUPPORTED MTF_TIMEFRAME {mtf!r} (use 5m/15m/30m/1h/4h/1d)")
        return None
    hist = fetch_history(symbol, mtf, 1, end_time=end_time)
    if len(hist) < 70:
        print(f"NOT ENOUGH {mtf} DATA for the regime filter: got {len(hist)} candles, need >= 70")
        return None
    daily_df = to_df(hist)
    warmup_bars = 288          # 1 day of 5m bars; regime warmup lives in daily_df
    ltf_bars = 100
    lookback_total = warmup_bars + ltf_bars

    rows = fetch_history(symbol, cfg["TIMEFRAME"], pages, end_time=end_time)
    if len(rows) < lookback_total + 10:
        print(f"NOT ENOUGH DATA: got {len(rows)} bars, need > {lookback_total + 10} "
              f"(warmup {warmup_bars} + {ltf_bars}-bar LTF window + trades)")
        return None

    df = to_df(rows)
    closes = df["close"].tolist()
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    opens = df["open"].tolist()
    times = df["open_time"].tolist()
    n = len(df)

    equity = float(equity) if equity is not None else 1000.0
    start_equity = equity
    risk_per_trade = float(cfg.get("RISK_PER_TRADE", 0.01))
    max_daily_dd = float(cfg.get("MAX_DAILY_DRAWDOWN", 0.05))
    close_eod = bool(cfg.get("CLOSE_AT_UTC_DAY_END", True))
    # Notional allocation caps — mirrors RiskManager.calculate_position_size, which
    # takes min(risk-based size, allocation cap).
    alloc_cap_frac = min(float(cfg.get("BALANCE_USAGE_PERCENT", 1.0)),
                         float(cfg.get("MAX_SYMBOL_ALLOCATION_PERCENT", 1.0)))
    # Binance spot NOTIONAL.minNotional floor (5.0 is the real exchange value for
    # NEARUSDT via /api/v3/exchangeInfo AND the fallback every live code path uses).
    # Futures: fee leg switches to 0.05%; the NOTIONAL floor is the REAL per-symbol
    # value from fapi exchangeInfo (5 USDT on most perps, 20-50 on a few) unless
    # the caller overrode it for research.
    is_futures = (market == "futures")
    fee_leg = FUTURES_TAKER_FEE if is_futures else TAKER_FEE
    if min_notional is not None:
        min_notional = float(min_notional)   # explicit research override wins
    elif is_futures:
        min_notional = float(cfg.get("MIN_NOTIONAL") or fetch_futures_min_notional(symbol))
    else:
        min_notional = float(cfg.get("MIN_NOTIONAL", 5.0))

    position = None
    trades = []
    equity_curve = []
    day_key = None
    day_start_equity = equity
    day_blocked = False
    last_exit_i = -10 ** 9
    funding_events = []       # (ms, rate, notional, fee) — futures only, charged at settlement bars
    funding_total = 0.0

    if is_futures:
        # Historical funding over the exact replay window (falls back to [] on
        # network failure — the loop then skips funding, never crashes).
        _f0 = times[0] if times else 0
        _f1 = times[-1] + tf_ms if times else 0
        funding_events = fetch_funding_rates(symbol, _f0, _f1)

    def record(i, reason, fill_price, qty, entry_price, initial_stop, entry_fee):
        gross = (fill_price - entry_price) * qty
        fees = entry_fee + fill_price * qty * fee_leg
        return {"reason": reason, "entry_price": entry_price, "exit_price": fill_price,
                "qty": qty, "gross": gross, "fees": fees, "pnl": gross - fees,
                "exit_index": i, "entry_index": position["entry_index"],
                "entry_ms": position["entry_ms"],
                "r": (fill_price - entry_price) / (entry_price - initial_stop)
                     if (entry_price - initial_stop) > 0 else 0.0}

    for i in range(lookback_total, n):
        # ---- daily drawdown circuit breaker (resets each UTC day) ----
        d = datetime.fromtimestamp(times[i] / 1000, tz=timezone.utc).date()
        if d != day_key:
            day_key = d
            day_start_equity = equity
            day_blocked = False
        if day_blocked:
            equity_curve.append(equity)
            continue
        if day_start_equity > 0 and equity - day_start_equity <= -max_daily_dd * day_start_equity:
            day_blocked = True
            equity_curve.append(equity)
            continue

        # ---- manage the open position on this bar ----
        # Exits are decided by the SHARED policy (trade_policy.bar_exit), i.e. the
        # exact function the live engine's numbers come from: stop (gap-aware,
        # trailing-aware) → take-profit → +R scale-out → time stop. Ratchets are
        # applied AFTER the exit check, so a stop raised by this bar's high can
        # only trigger from the next bar — the pessimistic intrabar convention
        # (the live engine polls ticks every SIGNAL_INTERVAL and never assumes the
        # favourable path through a bar).
        if position is not None:
            p = position
            bar = {"open": opens[i], "high": highs[i], "low": lows[i],
                   "close": closes[i], "ms": times[i]}
            # ---- funding settlement (futures only) ----
            # A long PAYS when the rate is positive. Charged on the position's
            # notional at each 8h settlement timestamp inside this bar.
            if is_futures and funding_events:
                bar_end = times[i] + tf_ms
                while funding_events and funding_events[0][0] < bar_end:
                    f_ms, f_rate = funding_events.pop(0)
                    if f_ms >= times[i]:
                        f_fee = f_rate * p["entry"] * p["qty"]
                        equity -= f_fee
                        funding_total += f_fee
            plan = bar_exit(
                bar, p["entry"], p["qty"], p["stop"], p["tp"], cfg,
                now_ms=times[i], entry_ms=p.get("entry_ms"),
                trailing_stop=p.get("trailing_stop"), trailing_active=p.get("trailing_active", False),
                initial_stop_price=p.get("initial_stop"),
                scale_out_done=p.get("scale_out_done", False),
            )
            if plan.reason:
                leg = record(i, plan.reason, plan.fill, p["qty"], p["entry"],
                             p.get("initial_stop") or p["stop"], p["entry_fee"])
                equity += leg["pnl"]
                trades.append(leg)
                position = None
                equity_curve.append(equity)
                if position is None:
                    last_exit_i = i
                continue
                equity += leg["pnl"]
                trades.append(leg)
                position = None
                equity_curve.append(equity)
                if position is None:
                    last_exit_i = i
                continue
            if plan.partial_units > 0:
                # +R scale-out: bank part of the position, keep the runner open.
                qty = min(plan.partial_units, p["qty"])
                fee = (p["entry"] * qty + plan.partial_price * qty) * fee_leg
                leg = {"reason": "PARTIAL_EXIT", "partial": True, "entry_price": p["entry"],
                       "exit_price": plan.partial_price, "qty": qty,
                       "gross": (plan.partial_price - p["entry"]) * qty, "fees": fee,
                       "pnl": (plan.partial_price - p["entry"]) * qty - fee,
                       "exit_index": i, "entry_index": p["entry_index"],
                       "entry_ms": p["entry_ms"],
                       "r": ((plan.partial_price - p["entry"]) /
                             (p["entry"] - (p.get("initial_stop") or p["stop"])))
                            if (p["entry"] - (p.get("initial_stop") or p["stop"])) > 0 else 0.0}
                equity += leg["pnl"]
                trades.append(leg)
                p["entry_fee"] -= p["entry"] * qty * fee_leg   # entry fee already paid on the sold part
                p["qty"] -= qty
                p["scale_out_done"] = True
                # Live engine locks breakeven on the runner right after the partial.
                _ratchet = ratchet_stops(p["entry"], plan.partial_price, p["stop"], cfg,
                                         trailing_stop=p.get("trailing_stop"),
                                         trailing_active=p.get("trailing_active", False),
                                         breakeven_activated=p.get("breakeven_activated", False),
                                         atr=p.get("atr"))
                p["stop"] = _ratchet["stop_price"]
                p["trailing_stop"] = _ratchet["trailing_stop"]
                p["trailing_active"] = _ratchet["trailing_active"]
                p["breakeven_activated"] = _ratchet["breakeven_activated"]
            else:
                bar_day = int(times[i]) // 86_400_000
                entry_day = int(p.get("entry_ms") or times[p["entry_index"]]) // 86_400_000
                if close_eod and bar_day != entry_day:
                    # Live closes in the last 5 minutes of the UTC day; a bar replay
                    # fills at the next day's OPEN (the day-end price).
                    leg = record(i, "TIME_STOP", bar["open"], p["qty"], p["entry"],
                                 p.get("initial_stop") or p["stop"], p["entry_fee"])
                    equity += leg["pnl"]
                    trades.append(leg)
                    position = None
                    last_exit_i = i
                else:
                    # Profit-protection ladder on this bar's favourable extreme.
                    _ratchet = ratchet_stops(p["entry"], bar["high"], p["stop"], cfg,
                                             trailing_stop=p.get("trailing_stop"),
                                             trailing_active=p.get("trailing_active", False),
                                             breakeven_activated=p.get("breakeven_activated", False),
                                             atr=p.get("atr"))
                    p["stop"] = _ratchet["stop_price"]
                    p["trailing_stop"] = _ratchet["trailing_stop"]
                    p["trailing_active"] = _ratchet["trailing_active"]
                    p["breakeven_activated"] = _ratchet["breakeven_activated"]
            equity_curve.append(equity)
            if position is None:
                continue
            continue

        # ---- look for a new entry (uses the LIVE engine's decide()) ----
        if i - last_exit_i < cooldown_bars:
            equity_curve.append(equity)
            continue
        _max_day = int(cfg.get("_MAX_TRADES_PER_DAY", 0) or 0)
        if _max_day > 0:
            _today = times[i] // 86_400_000
            _taken = sum(1 for t in trades
                         if (t.get("entry_ms") or 0) // 86_400_000 == _today)
            if _taken >= _max_day:
                equity_curve.append(equity)
                continue
        # decide() can only fire on a closed RSI bucket (it returns NEUTRAL
        # otherwise), so skip the call entirely on non-bucket-close bars —
        # identical results, ~12x faster at 1h buckets over 5m bars.
        bucket_ms = int(cfg.get("RSI_TIMEFRAME_MS", 3_600_000))
        if (times[i] + tf_ms) % bucket_ms != 0:
            equity_curve.append(equity)
            continue
        # Regime window: every MTF candle whose bucket ends at or before this bar's
        # close. decide() applies the same "completed candles only" rule itself, so
        # the backtest and the engine cannot disagree about the in-progress candle.
        htf_win = daily_df[daily_df["open_time"] < times[i] + tf_ms]
        ltf_win = df.iloc[i - ltf_bars + 1: i + 1]
        signal, atr = sg.decide(htf_win, ltf_win, symbol=symbol)
        if signal != "BUY":
            equity_curve.append(equity)
            continue

        entry = closes[i]
        # Bracket from the SHARED policy (fixed % by default, ATR-adaptive when
        # SL_ATR_MULTIPLIER > 0) — identical math to trade_logic.enter_trade.
        stop, tp = effective_bracket(entry, cfg, atr)
        sl_dist = entry - stop
        if sl_dist <= 0:
            equity_curve.append(equity)
            continue
        qty = min((equity * risk_per_trade) / sl_dist, (equity * alloc_cap_frac) / entry)
        if qty * entry < min_notional:
            # Below the exchange floor: the live engine would skip this entry too.
            equity_curve.append(equity)
            continue
        position = {"entry": entry, "stop": stop, "tp": tp, "qty": qty,
                    "entry_fee": entry * qty * fee_leg,
                    "entry_index": i, "entry_ms": times[i],
                    "initial_stop": stop, "atr": atr,
                    "trailing_stop": stop, "trailing_active": False,
                    "breakeven_activated": False, "scale_out_done": False}
        equity_curve.append(equity)

    # ---- force-close any open position at the last close ----
    if position is not None:
        i = n - 1
        leg = record(i, "END_OF_DATA", closes[i], position["qty"], position["entry"],
                     position["stop"], position["entry_fee"])
        equity += leg["pnl"]
        trades.append(leg)
        equity_curve.append(equity)

    return _summarize(symbol, preset_name, cfg, start_equity, equity, trades,
                      equity_curve, len(rows), quiet, funding_total=funding_total,
                      market=market)


def _summarize(symbol, preset_name, cfg, start_equity, equity, trades, curve, n_bars, quiet,
               funding_total=0.0, market="spot"):
    # Position-level statistics: scale-out legs (PARTIAL_EXIT) are realized PnL and
    # count toward equity/fees, but a win/loss is decided by the FULL exit — a
    # partial +1R leg followed by a stopped-out runner is one losing trade, not a
    # win and a loss.
    total = len([t for t in trades if not t.get("partial")])
    wins = [t for t in trades if not t.get("partial") and t["pnl"] > 0]
    losses = [t for t in trades if not t.get("partial") and t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    total_fees = sum(t["fees"] for t in trades)
    win_rate = len(wins) / total * 100 if total else 0.0
    total_pnl = sum(t["pnl"] for t in trades)
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0
    expectancy = (total_pnl / total) if total else 0.0
    avg_r = (sum(t["r"] for t in trades) / total) if total else 0.0
    peak, max_dd = start_equity, 0.0
    for v in curve:
        peak = max(peak, v)
        max_dd = max(max_dd, (peak - v) / peak * 100 if peak > 0 else 0)
    ret_pct = (equity - start_equity) / start_equity * 100

    result = {
        "symbol": symbol, "preset": preset_name, "bars": n_bars,
        "timeframe": cfg["TIMEFRAME"], "market": market,
        "funding_paid": round(funding_total, 4),
        "start_equity": start_equity, "end_equity": round(equity, 2),
        "return_pct": round(ret_pct, 2), "max_drawdown_pct": round(max_dd, 2),
        "trades": total, "wins": len(wins), "losses": len(losses),
        "partials": len([t for t in trades if t.get("partial")]),
        "win_rate": round(win_rate, 1), "profit_factor": round(pf, 2) if pf != float("inf") else None,
        "expectancy_per_trade": round(expectancy, 2),
        "avg_r_multiple": round(avg_r, 2),
        "total_fees": round(total_fees, 2),
    }
    if not quiet:
        print(json.dumps(result, indent=2))
        print("\n  exit reasons:", {r: sum(1 for t in trades if t["reason"] == r) for r in
                                    ("TAKE_PROFIT", "STOP_LOSS", "TIME_STOP", "PARTIAL_EXIT", "END_OF_DATA")})
    else:
        print(f"  {symbol} {preset_name}: ret={ret_pct:+.2f}% trades={total} wr={win_rate:.0f}% "
              f"pf={pf:.2f} exp={expectancy:+.2f} fees={total_fees:.1f} dd={max_dd:.1f}%")
    # Research aid: dump the raw trade list (entry/exit ms + reason + pnl) so it
    # can be diffed bar-by-bar against a research lab's own trade list.
    _dump = _env_str("BACKTEST_DUMP_TRADES")
    if _dump:
        with open(_dump, "w") as f:
            json.dump([{"entry_ms": t.get("entry_ms"), "exit_index": t.get("exit_index"),
                        "reason": t.get("reason"), "pnl": round(t.get("pnl", 0.0), 6),
                        "entry_price": t.get("entry_price"), "exit_price": t.get("exit_price")}
                       for t in trades], f)
        print(f"  trades dumped -> {_dump}")
    return result


def _env_str(name, default=None):
    """Env-var default for a CLI option; empty string counts as unset."""
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _env_num(name, cast, default=None):
    """Env-var default cast to int/float; unset or unparsable -> default."""
    value = os.getenv(name)
    if value in (None, ""):
        return default
    try:
        return cast(value)
    except (TypeError, ValueError):
        return default


def _env_bool(name, default=False):
    """Env-var default parsed as a boolean flag."""
    value = os.getenv(name)
    if value in (None, ""):
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def main():
    # Every option below accepts a BACKTEST_* default from .env (documented in
    # .env.example). Explicit CLI flags always win, so the persisted defaults
    # only decide what a bare `python backtest.py` reproduces.
    parser = argparse.ArgumentParser(description="Strategy backtest on real Binance klines (intraday_rsi)")
    parser.add_argument("--symbol", default=_env_str("BACKTEST_SYMBOL", "NEARUSDT"))
    parser.add_argument("--preset", default=_env_str("BACKTEST_PRESET", "intraday_rsi"), choices=list(PRESETS.keys()))
    parser.add_argument("--pages", type=int, default=_env_num("BACKTEST_PAGES", int, 3), help="number of 1000-bar pages to fetch")
    parser.add_argument("--end", type=int, default=_env_num("BACKTEST_END_TIME", int), help="endTime ms (for reproducible runs)")
    parser.add_argument("--sl", type=float, default=_env_num("BACKTEST_SL_PERCENT", float), help="override SL_PERCENT (fixed %% stop)")
    parser.add_argument("--tp", type=float, default=_env_num("BACKTEST_TP_PERCENT", float), help="override TP_PERCENT (fixed %% target)")
    parser.add_argument("--min-tp", type=float, default=_env_num("BACKTEST_MIN_TP", float), help="override MIN_TP_PERCENT (TP floor as fraction)")
    parser.add_argument("--cooldown-bars", type=int, default=_env_num("BACKTEST_COOLDOWN_BARS", int), help="bars to wait after any exit before re-entry (default: derived from COOLDOWN_LOSS)")
    parser.add_argument("--max-hold", type=float, default=_env_num("BACKTEST_MAX_HOLD", float), help="override MAX_HOLD_TIME in seconds")
    parser.add_argument("--min-notional", type=float, default=_env_num("BACKTEST_MIN_NOTIONAL", float), help="override MIN_NOTIONAL (USDT floor below which a trade can't be placed; default 5 = live exchange NOTIONAL.minNotional)")
    parser.add_argument("--equity", type=float, default=_env_num("BACKTEST_EQUITY", float), help="override starting equity (default 1000 USDT)")
    parser.add_argument("--balance-usage-percent", type=float, default=_env_num("BACKTEST_BALANCE_USAGE_PERCENT", float), help="override BALANCE_USAGE_PERCENT")
    parser.add_argument("--max-symbol-allocation-percent", type=float, default=_env_num("BACKTEST_MAX_SYMBOL_ALLOCATION_PERCENT", float), help="override MAX_SYMBOL_ALLOCATION_PERCENT")
    parser.add_argument("--quiet", action="store_true", default=_env_bool("BACKTEST_QUIET"), help="one-line summary instead of full JSON")
    args = parser.parse_args()

    print(f"Backtesting {args.symbol} | preset={args.preset} | pages={args.pages} (up to {args.pages * 1000} bars)")
    t0 = time.time()
    result = run_backtest(args.symbol, args.preset, args.pages, end_time=args.end,
                          quiet=args.quiet,
                          sl_percent=args.sl, tp_percent=args.tp, min_tp=args.min_tp,
                          cooldown_bars=args.cooldown_bars, max_hold=args.max_hold,
                          min_notional=args.min_notional, equity=args.equity,
                          balance_usage_percent=args.balance_usage_percent,
                          max_symbol_allocation_percent=args.max_symbol_allocation_percent)
    if result is None:
        return 2
    print(f"\nCompleted in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
