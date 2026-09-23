"""Entry/exit trade policy — ONE source of truth for live trading and backtests.

`trade_logic.manage_trade` (live, every poll) and `backtest.run_backtest` (proof,
every bar) both call `evaluate_exit` — the ONE exit decision — plus the pure
level helpers below. Nothing else decides when a position closes, so a rule can
never drift between what is backtested and what trades real money:

  * `effective_bracket(entry, config, atr)` — the bullish bracket. Fixed % by
    default (`SL_PERCENT`/`TP_PERCENT`, the proven model); an ATR-multiple stop
    when `SL_ATR_MULTIPLIER > 0`; TP floored at `MIN_TP_PERCENT` and widened to
    `MIN_RISK_REWARD` so a tiny bracket can never be fee-negative.
  * `ratchet_stops(...)` — the profit-protection ladder: breakeven lock, then
    the trailing stop. Returns the stop fields to persist; it never sells.
  * `effective_stop(...)` — the stop a SELL would actually trigger at, i.e. the
    higher of the hard stop and the active trailing stop.
  * `scale_out_plan(...)` — the 1R partial-profit leg (how many units to bank).
  * `evaluate_exit(...)` — the single exit DECISION: stop → take-profit → time
    stop → day-end flatten → +R scale-out, evaluated against normalised price
    evidence with the intrabar assumption made explicit, so a backtest cannot
    quietly assume the favourable path (and neither can the live engine).

All helpers are pure (no I/O, no async) so they are trivially unit-testable and
safe to call from the engine's hot path.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExitPlan:
    """Outcome of evaluating one bar against an open bullish position."""
    reason: str | None = None            # STOP_LOSS | TRAILING_STOP | TAKE_PROFIT
                                         # | TIME_STOP | EOD_CLOSE
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


def evaluate_exit(entry_price, quantity, stop_price, take_profit, config,
                  low=None, high=None, reference_price=None,
                  now_ms=None, entry_ms=None, trailing_stop=None,
                  trailing_active=False, initial_stop_price=None,
                  scale_out_done=False, eod=False):
    """The single exit DECISION for an open bullish position.

    `trade_logic.manage_trade` (live, once per poll) and `backtest.run_backtest`
    (proof, once per bar) both call this, so the rule cannot drift between what
    is backtested and what trades real money. Pure: no I/O, no async, no clock.

    Evidence is normalised so each caller supplies what it can observe:

      * `low` / `high` — most adverse and most favourable price seen since the
        last evaluation. Live: the post-entry wick range over the last two
        klines, folded together with the live tick. Backtest: the bar's low/high.
      * `reference_price` — where a market order placed at this decision moment
        fills. Live: the live tick. Backtest: the bar's open, because the bar is
        evaluated at its own `now_ms`, making the open the price the decision is
        actually priced at. A level that has already been traded through fills
        here instead of at a level the market has left behind.
      * `now_ms` / `entry_ms` — hold-time evidence, epoch milliseconds.
      * `eod` — the caller's "we must be flat for the day end" signal. The trigger
        is inherently venue-specific (live: the wall clock inside the last five
        minutes of the UTC day; backtest: the first bar of a new UTC day), so it
        is an input, not a clock read.

    Priority — deliberately pessimistic, and identical on both sides:

      1. protective stop — `TRAILING_STOP` once the trail is armed, else `STOP_LOSS`
      2. take-profit
      3. time stop (`MAX_HOLD_TIME`)
      4. day-end flatten (`CLOSE_AT_UTC_DAY_END`)
      5. +R scale-out — a partial: banks profit, the position stays open

    The stop is checked before every profit-taking action, including the
    scale-out: when one evidence window contains both a breach and a level above
    it, the intrabar path is unknowable, so the position is assumed stopped. The
    two scheduled exits are terminal, so they pre-empt a scale-out in the same
    window rather than banking a partial into a position that is closing anyway.

    Returns an `ExitPlan`; `reason is None and partial_units == 0` means hold.
    """
    entry = _fnum(entry_price)
    low = _fnum(low)
    high = _fnum(high)
    ref = _fnum(reference_price)
    stop = effective_stop(stop_price, trailing_stop, trailing_active)
    tp = _fnum(take_profit)
    trail_armed = bool(trailing_active) and stop > _fnum(stop_price)

    # 1) Protective stop. The trail reports under its own reason so live and
    #    backtest exit-reason histograms stay comparable instead of only
    #    sharing a label by accident.
    if stop > 0 and low > 0 and low <= stop:
        return ExitPlan(reason="TRAILING_STOP" if trail_armed else "STOP_LOSS",
                        fill=ref if 0 < ref < stop else stop)

    # 2) Take-profit (a reference already above the target is the better fill).
    if tp > 0 and high >= tp:
        return ExitPlan(reason="TAKE_PROFIT", fill=ref if ref > tp else tp)

    # 3) Time stop.
    max_hold = _fnum(config.get("MAX_HOLD_TIME"), 0.0)
    if max_hold > 0 and now_ms and entry_ms and (now_ms - entry_ms) / 1000.0 > max_hold:
        return ExitPlan(reason="TIME_STOP", fill=ref if ref > 0 else None)

    # 4) Scheduled day-end flatten.
    if eod:
        return ExitPlan(reason="EOD_CLOSE", fill=ref if ref > 0 else None)

    # 5) Scale-out leg at +R (bank a partial profit ONCE; position stays open).
    #    Never at or above the take-profit — the full exit owns that level.
    units = 0.0 if scale_out_done else scale_out_plan(
        entry, quantity, initial_stop_price, stop_price, config)
    level = scale_out_price(entry, initial_stop_price, stop_price, config)
    if units > 0 and level and high >= level and (tp <= 0 or level < tp):
        return ExitPlan(partial_units=units,
                        partial_price=max(level, ref) if ref > 0 else level)

    return ExitPlan()
