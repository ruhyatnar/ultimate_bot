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


def effective_bracket(entry_price, config, atr=None, side="long"):
    """Return the bracket ``(stop_price, take_profit)`` for ``side``.

    Long (default): the stop is BELOW entry and the take-profit ABOVE it, sized
    from `SL_PERCENT`/`TP_PERCENT`. Short: the bracket is mirrored (stop ABOVE
    entry, TP BELOW) and sized from the `SHORT_SL_PERCENT`/`SHORT_TP_PERCENT`
    tunables, so short risk can be re-tuned without disturbing the proven long
    config. Both sides use the same `MIN_TP_PERCENT` floor and `MIN_RISK_REWARD`
    widening, so a mirrored bracket can never be fee-negative either.

    The stop is the strategy's own level: fixed fraction by default, or
    `SL_ATR_MULTIPLIER × ATR` when that tunable is > 0 and an ATR is available
    (volatility-adaptive stop).
    """
    if side == "short":
        return _short_bracket(entry_price, config, atr)
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


def _short_bracket(entry_price, config, atr=None):
    """Mirror of the long bracket: stop above entry, take-profit below it.

    Risk levels come from `SHORT_SL_PERCENT`/`SHORT_TP_PERCENT` (falling back to
    the long keys if a caller has not supplied them). The ATR stop, when armed,
    is clamped so a volatility spike cannot push the stop further than
    `SL_ATR_MAX_PERCENT` ABOVE entry; the TP keeps the same floor/risk-reward
    guarantees as the long side.
    """
    entry_price = float(entry_price)
    sl_atr = _fnum(config.get("SL_ATR_MULTIPLIER"), 0.0)
    atr_val = _fnum(atr, 0.0)
    sl_frac = _fnum(config.get("SHORT_SL_PERCENT"), _fnum(config.get("SL_PERCENT"), 0.02))
    tp_frac = _fnum(config.get("SHORT_TP_PERCENT"), _fnum(config.get("TP_PERCENT"), 0.04))
    if sl_atr > 0 and atr_val > 0:
        stop_price = entry_price + sl_atr * atr_val
        price_ceiling = entry_price * (1 + _fnum(config.get("SL_ATR_MAX_PERCENT"), 0.0))
        if price_ceiling > entry_price:
            stop_price = min(stop_price, price_ceiling)
    else:
        stop_price = entry_price * (1 + sl_frac)
    if stop_price <= entry_price:
        # Degenerate (ATR < 0 / bad config): fall back to the fixed % stop so the
        # risk distance stays positive for sizing.
        stop_price = entry_price * (1 + sl_frac)

    take_profit = entry_price * (1 - tp_frac)
    min_tp_dist = entry_price * _fnum(config.get("MIN_TP_PERCENT"), 0.0)
    if entry_price - take_profit < min_tp_dist:
        take_profit = entry_price - min_tp_dist
    sl_dist = stop_price - entry_price
    min_rr = _fnum(config.get("MIN_RISK_REWARD"), 1.5)
    if sl_dist > 0 and (entry_price - take_profit) / sl_dist < min_rr:
        take_profit = entry_price - sl_dist * min_rr
    return stop_price, take_profit


def effective_stop(stop_price, trailing_stop, trailing_active, side="long"):
    """The price a protective exit actually triggers at.

    Long: the HIGHER of the hard stop and the active trail (both sit below
    entry). Short: the LOWER of the two (both sit above entry) — a short's stop
    ratchets DOWN, so "most protective" is the minimum.
    """
    stop = _fnum(stop_price)
    if trailing_active:
        if side == "short":
            stop = min(stop, _fnum(trailing_stop))
        else:
            stop = max(stop, _fnum(trailing_stop))
    return stop


def scale_out_plan(entry_price, quantity, initial_stop_price, stop_price, config, side="long"):
    """Units to close for the 1R partial-profit leg (0.0 = no scale-out).

    R is measured against the INITIAL stop: the breakeven lock moves the live
    stop long before +1R, which would otherwise make the risk distance negative
    and permanently disable scale-out.
    """
    if not config.get("SCALE_OUT_ENABLED", False):
        return 0.0
    qty = _fnum(quantity)
    if qty <= 0:
        return 0.0
    anchor = _fnum(initial_stop_price) or _fnum(stop_price)
    entry = _fnum(entry_price)
    risk_per_unit = (anchor - entry) if side == "short" else (entry - anchor)
    if risk_per_unit <= 0:
        return 0.0
    fraction = min(max(_fnum(config.get("SCALE_OUT_FRACTION"), 0.5), 0.0), 0.95)
    return qty * fraction


def scale_out_price(entry_price, initial_stop_price, stop_price, config, side="long"):
    """Price level of the scale-out leg (+R multiple of the initial stop)."""
    anchor = _fnum(initial_stop_price) or _fnum(stop_price)
    entry = _fnum(entry_price)
    risk_per_unit = (anchor - entry) if side == "short" else (entry - anchor)
    if risk_per_unit <= 0:
        return None
    multiple = _fnum(config.get("SCALE_OUT_R_MULTIPLE"), 1.0)
    if side == "short":
        return entry - risk_per_unit * multiple
    return entry + risk_per_unit * multiple


def ratchet_stops(entry_price, favorable_price, stop_price, config,
                  trailing_stop=None, trailing_active=False,
                  breakeven_activated=False, atr=None, side="long"):
    """Profit-protection ladder evaluated on a favourable mark.

    Order matches the live engine: breakeven lock first, then the trailing stop.
    Long: only ever RAISES the stop. Short: only ever LOWERS it (the mirror — a
    short's protective stop sits above entry and moves down as price falls).

    `favorable_price` is the best mark seen since the last call (for a long that
    is the highest print; for a short it is the LOWEST). Returns the fields to
    persist: ``stop_price``, ``trailing_stop``, ``trailing_active``,
    ``breakeven_activated``.
    """
    if side == "short":
        return _ratchet_stops_short(entry_price, favorable_price, stop_price, config,
                                    trailing_stop, trailing_active,
                                    breakeven_activated, atr)
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


def _ratchet_stops_short(entry_price, favorable_price, stop_price, config,
                         trailing_stop=None, trailing_active=False,
                         breakeven_activated=False, atr=None):
    """Short mirror of `ratchet_stops`: the ladder only ever moves DOWN.

    `favorable_price` is the LOWEST print seen (the "bar low" for a replay, the
    wick low for the live engine). Breakeven sits at entry × (1 − offset) and the
    trail hangs `TRAILING_STOP_CALLBACK` (or an ATR multiple) ABOVE the mark.
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

    # 1) Breakeven lock: once price falls BREAKEVEN_TRIGGER in favour, move the
    #    stop to entry × (1 − BREAKEVEN_OFFSET) — covering round-trip fees.
    if config.get("BREAKEVEN_ENABLED", True) and not out["breakeven_activated"]:
        if (entry - mark) / entry >= _fnum(config.get("BREAKEVEN_TRIGGER"), 0.01):
            out["breakeven_activated"] = True
            be_price = entry * (1 - _fnum(config.get("BREAKEVEN_OFFSET"), 0.0025))
            if be_price < out["stop_price"]:
                out["stop_price"] = be_price
            if be_price < out["trailing_stop"]:
                out["trailing_stop"] = be_price

    # 2) Trailing stop: arms at TRAILING_STOP_ACTIVATE profit, then ratchets down
    #    behind the favourable (low) mark.
    activate = _fnum(config.get("TRAILING_STOP_ACTIVATE"), 1.0)
    if activate > 0 and entry > 0 and (entry - mark) / entry >= activate:
        out["trailing_active"] = True
    if out["trailing_active"]:
        atr_mult = _fnum(config.get("TRAILING_ATR_MULTIPLIER"), 0.0)
        atr_val = _fnum(atr, 0.0)
        if atr_mult > 0 and atr_val > 0:
            new_trail = mark + atr_mult * atr_val
        else:
            new_trail = mark * (1 + _fnum(config.get("TRAILING_STOP_CALLBACK"), 0.01))
        if new_trail < out["trailing_stop"]:
            out["trailing_stop"] = new_trail
    return out


def evaluate_exit(entry_price, quantity, stop_price, take_profit, config,
                  low=None, high=None, reference_price=None,
                  now_ms=None, entry_ms=None, trailing_stop=None,
                  trailing_active=False, initial_stop_price=None,
                  scale_out_done=False, eod=False, side="long"):
    """The single exit DECISION for an open position (`side` aware).

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

    A short mirrors every comparison: its stop triggers on `high >= stop`, its
    take-profit on `low <= tp`, and the trailing stop is armed when the trail has
    moved BELOW the hard stop.
    """
    if side == "short":
        return _evaluate_exit_short(entry_price, quantity, stop_price, take_profit,
                                    config, low=low, high=high, reference_price=reference_price,
                                    now_ms=now_ms, entry_ms=entry_ms, trailing_stop=trailing_stop,
                                    trailing_active=trailing_active, initial_stop_price=initial_stop_price,
                                    scale_out_done=scale_out_done, eod=eod)
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


def _evaluate_exit_short(entry_price, quantity, stop_price, take_profit, config, **kw):
    """Short mirror of `evaluate_exit` — same priority order, reversed evidence.

    1. protective stop on `high >= stop` (trail reports TRAILING_STOP)
    2. take-profit on `low <= tp`
    3. time stop
    4. day-end flatten
    5. +R scale-out on `low <= level`
    Everything else (pessimistic intrabar fill, terminal exits pre-empting the
    partial) is identical to the long path.
    """
    entry = _fnum(entry_price)
    low = _fnum(kw.get("low"))
    high = _fnum(kw.get("high"))
    ref = _fnum(kw.get("reference_price"))
    stop = effective_stop(stop_price, kw.get("trailing_stop"), kw.get("trailing_active", False), side="short")
    tp = _fnum(take_profit)
    trail_armed = bool(kw.get("trailing_active")) and stop < _fnum(stop_price)

    # 1) Protective stop: a short is stopped when price trades UP through it.
    if stop > 0 and high > 0 and high >= stop:
        return ExitPlan(reason="TRAILING_STOP" if trail_armed else "STOP_LOSS",
                        fill=ref if ref > stop else stop)
    # 2) Take-profit: a short profits when price trades DOWN through the target.
    if tp > 0 and low > 0 and low <= tp:
        return ExitPlan(reason="TAKE_PROFIT", fill=ref if 0 < ref < tp else tp)
    # 3) Time stop.
    now_ms = kw.get("now_ms")
    entry_ms = kw.get("entry_ms")
    max_hold = _fnum(config.get("MAX_HOLD_TIME"), 0.0)
    if max_hold > 0 and now_ms and entry_ms and (now_ms - entry_ms) / 1000.0 > max_hold:
        return ExitPlan(reason="TIME_STOP", fill=ref if ref > 0 else None)
    # 4) Scheduled day-end flatten.
    if kw.get("eod"):
        return ExitPlan(reason="EOD_CLOSE", fill=ref if ref > 0 else None)
    # 5) Scale-out leg at +R (bank a partial profit ONCE; position stays open).
    units = 0.0 if kw.get("scale_out_done") else scale_out_plan(
        entry, quantity, kw.get("initial_stop_price"), stop_price, config, side="short")
    level = scale_out_price(entry, kw.get("initial_stop_price"), stop_price, config, side="short")
    if units > 0 and level and low <= level and (tp <= 0 or level > tp):
        return ExitPlan(partial_units=units,
                        partial_price=min(level, ref) if ref > 0 else level)
    return ExitPlan()
