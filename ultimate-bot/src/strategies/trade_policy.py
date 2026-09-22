"""Entry/exit trade policy — ONE source of truth for live trading and backtests.

`trade_logic.manage_trade` (live) and `backtest.run_backtest` (proof) both call
the pure helpers here, so a rule can never drift between what is backtested and
what trades real money:

  * `effective_bracket(entry, config, atr)` — the bullish bracket. Fixed % by
    default (`SL_PERCENT`/`TP_PERCENT`, the proven model); an ATR-multiple stop
    when `SL_ATR_MULTIPLIER > 0`; TP floored at `MIN_TP_PERCENT` and widened to
    `MIN_RISK_REWARD` so a tiny bracket can never be fee-negative.
  * `ratchet_stops(...)` — the profit-protection ladder: breakeven lock, then
    the trailing stop. Returns the stop fields to persist; it never sells.
  * `effective_stop(...)` — the stop a SELL would actually trigger at, i.e. the
    higher of the hard stop and the active trailing stop.
  * `scale_out_plan(...)` — the 1R partial-profit leg (how many units to bank).
  * `bar_exit(...)` — evaluate one price bar against the bracket: stop → TP →
    time stop, with the intrabar assumption made explicit so a backtest cannot
    quietly assume the favourable path.

All helpers are pure (no I/O, no async) so they are trivially unit-testable and
safe to call from the engine's hot path.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExitPlan:
    """Outcome of evaluating one bar against an open bullish position."""
    reason: str | None = None            # STOP_LOSS | TAKE_PROFIT | TIME_STOP
    fill: float | None = None
    partial_units: float = 0.0           # scale-out: units to sell now
    partial_price: float | None = None


def _fnum(value, default=0.0):
    """float() that never raises (config/DB values can be None or junk)."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out == out and abs(out) != float("inf") else default


def effective_bracket(entry_price, config, atr=None):
    """Return the bullish bracket ``(stop_price, take_profit)``.

    The stop is the strategy's own level: fixed fraction by default, or
    `SL_ATR_MULTIPLIER × ATR` when that tunable is > 0 and an ATR is available
    (volatility-adaptive stop). The take-profit is `TP_PERCENT`, floored at
    `MIN_TP_PERCENT` and widened to `MIN_RISK_REWARD` of the stop distance.
    """
    entry_price = float(entry_price)
    sl_atr = _fnum(config.get("SL_ATR_MULTIPLIER"), 0.0)
    atr_val = _fnum(atr, 0.0)
    if sl_atr > 0 and atr_val > 0:
        stop_price = entry_price - sl_atr * atr_val
        # Never let a volatility spike produce an unbounded risk unit: clamp the
        # ATR stop to SL_ATR_MAX_PERCENT of entry (default: 2x the fixed stop).
        price_floor = entry_price * (1 - _fnum(config.get("SL_ATR_MAX_PERCENT"), 0.0))
        if price_floor < entry_price:
            stop_price = max(stop_price, price_floor)
    else:
        stop_price = entry_price * (1 - _fnum(config.get("SL_PERCENT"), 0.02))
    if stop_price >= entry_price:
        # Degenerate (ATR > price / bad config): fall back to the fixed % stop so
        # every downstream risk calculation still has a positive stop distance.
        stop_price = entry_price * (1 - _fnum(config.get("SL_PERCENT"), 0.02))

    take_profit = entry_price * (1 + _fnum(config.get("TP_PERCENT"), 0.04))
    min_tp_dist = entry_price * _fnum(config.get("MIN_TP_PERCENT"), 0.0)
    if take_profit - entry_price < min_tp_dist:
        take_profit = entry_price + min_tp_dist
    sl_dist = entry_price - stop_price
    min_rr = _fnum(config.get("MIN_RISK_REWARD"), 1.5)
    if sl_dist > 0 and (take_profit - entry_price) / sl_dist < min_rr:
        take_profit = entry_price + sl_dist * min_rr
    return stop_price, take_profit


def effective_stop(stop_price, trailing_stop, trailing_active):
    """The price a protective SELL actually triggers at."""
    stop = _fnum(stop_price)
    if trailing_active:
        stop = max(stop, _fnum(trailing_stop))
    return stop


def scale_out_plan(entry_price, quantity, initial_stop_price, stop_price, config):
    """Units to sell for the 1R partial-profit leg (0.0 = no scale-out).

    R is measured against the INITIAL stop: the breakeven lock raises the live
    stop long before +1R, which would otherwise make (entry − stop) negative and
    permanently disable scale-out.
    """
    if not config.get("SCALE_OUT_ENABLED", False):
        return 0.0
    qty = _fnum(quantity)
    if qty <= 0:
        return 0.0
    anchor = _fnum(initial_stop_price) or _fnum(stop_price)
    risk_per_unit = _fnum(entry_price) - anchor
    if risk_per_unit <= 0:
        return 0.0
    fraction = min(max(_fnum(config.get("SCALE_OUT_FRACTION"), 0.5), 0.0), 0.95)
    return qty * fraction


def scale_out_price(entry_price, initial_stop_price, stop_price, config):
    """Price level of the scale-out leg (+R multiple of the initial stop)."""
    anchor = _fnum(initial_stop_price) or _fnum(stop_price)
    risk_per_unit = _fnum(entry_price) - anchor
    if risk_per_unit <= 0:
        return None
    multiple = _fnum(config.get("SCALE_OUT_R_MULTIPLE"), 1.0)
    return _fnum(entry_price) + risk_per_unit * multiple


def ratchet_stops(entry_price, favorable_price, stop_price, config,
                  trailing_stop=None, trailing_active=False,
                  breakeven_activated=False, atr=None):
    """Profit-protection ladder evaluated on a favourable mark.

    Order matches the live engine: breakeven lock first, then the trailing stop.
    Only ever RAISES the stop — a ratchet is never loosened.

    `favorable_price` is the best mark seen since the last call (the live engine
    passes the tick; the backtest passes the bar high). Returns the fields to
    persist: ``stop_price``, ``trailing_stop``, ``trailing_active``,
    ``breakeven_activated``.
    """
    entry = _fnum(entry_price)
    mark = _fnum(favorable_price)
    stop = _fnum(stop_price)
    trail = _fnum(trailing_stop, stop) if trailing_stop is not None else stop
    out = {
        "stop_price": stop,
        "trailing_stop": trail,
        "trailing_active": bool(trailing_active),
        "breakeven_activated": bool(breakeven_activated),
    }
    if entry <= 0 or mark <= 0:
        return out

    # 1) Breakeven lock: once price travels BREAKEVEN_TRIGGER in favour, move the
    #    stop to entry × (1 + BREAKEVEN_OFFSET) — covering round-trip fees.
    if config.get("BREAKEVEN_ENABLED", True) and not out["breakeven_activated"]:
        if (mark - entry) / entry >= _fnum(config.get("BREAKEVEN_TRIGGER"), 0.01):
            out["breakeven_activated"] = True
            be_price = entry * (1 + _fnum(config.get("BREAKEVEN_OFFSET"), 0.0025))
            if be_price > out["stop_price"]:
                out["stop_price"] = be_price
            if be_price > out["trailing_stop"]:
                out["trailing_stop"] = be_price

    # 2) Trailing stop: arms at TRAILING_STOP_ACTIVATE profit, then ratchets up
    #    behind the favourable mark (callback is a % of the mark, or an ATR
    #    multiple when TRAILING_ATR_MULTIPLIER > 0 and an ATR is supplied).
    activate = _fnum(config.get("TRAILING_STOP_ACTIVATE"), 1.0)
    if activate > 0 and (mark - entry) / entry >= activate:
        out["trailing_active"] = True
    if out["trailing_active"]:
        atr_mult = _fnum(config.get("TRAILING_ATR_MULTIPLIER"), 0.0)
        atr_val = _fnum(atr, 0.0)
        if atr_mult > 0 and atr_val > 0:
            new_trail = mark - atr_mult * atr_val
        else:
            new_trail = mark * (1 - _fnum(config.get("TRAILING_STOP_CALLBACK"), 0.01))
        if new_trail > out["trailing_stop"]:
            out["trailing_stop"] = new_trail
    return out


def bar_exit(bar, entry_price, quantity, stop_price, take_profit, config,
             now_ms=None, entry_ms=None, trailing_stop=None, trailing_active=False,
             initial_stop_price=None, scale_out_done=False):
    """Evaluate one price bar (open/high/low/close/ms) against the bracket.

    Evaluation order is deliberately pessimistic — the stop is checked before
    the take-profit, and a gap through the stop fills at the bar's OPEN (what a
    market order would actually get), not at the stop price.

    Returns an `ExitPlan`; `reason is None` means the position stays open.
    Scale-out is returned as `partial_units` (the position remains open).
    """
    high = _fnum(bar.get("high"))
    low = _fnum(bar.get("low"))
    open_ = _fnum(bar.get("open"))
    close = _fnum(bar.get("close"))
    stop = effective_stop(stop_price, trailing_stop, trailing_active)
    tp = _fnum(take_profit)

    # 1) Protective stop (gap-aware).
    if stop > 0 and low > 0 and low <= stop:
        fill = open_ if (open_ > 0 and open_ < stop) else stop
        return ExitPlan(reason="STOP_LOSS", fill=fill)

    # 2) Take-profit (a gap ABOVE the target fills at the open — better price).
    if tp > 0 and high >= tp:
        fill = open_ if (open_ > 0 and open_ > tp) else tp
        return ExitPlan(reason="TAKE_PROFIT", fill=fill)

    # 3) Scale-out leg at +R (bank a partial profit ONCE; position stays open).
    units = 0.0 if scale_out_done else scale_out_plan(
        entry_price, quantity, initial_stop_price, stop_price, config)
    level = scale_out_price(entry_price, initial_stop_price, stop_price, config)
    if units > 0 and level and high >= level and (tp <= 0 or level < tp):
        return ExitPlan(partial_units=units,
                        partial_price=max(level, open_) if open_ > 0 else level)

    # 4) Time stop.
    max_hold = _fnum(config.get("MAX_HOLD_TIME"), 0.0)
    if max_hold > 0 and now_ms and entry_ms and (now_ms - entry_ms) / 1000.0 > max_hold:
        return ExitPlan(reason="TIME_STOP", fill=close)

    return ExitPlan()
