import { BotConfig, SignalState } from '../types';

/**
 * One source of truth for "which side is this symbol's engine decision on?" and
 * "what bracket does that side use?".
 *
 * Both the Signal State card (SignalInspector) and the per-symbol detail modal
 * (SymbolDetailModal) answer those two questions, and they had drifted apart:
 * the card keyed direction off a fired trigger while the engine keys it off the
 * regime, and the modal mirrored the bracket by hand. Reproducing the rule in
 * two places is exactly how the card ended up describing a LONG dip — with the
 * long bracket — for a symbol the engine was armed to SHORT. Keep the rule here
 * so the two surfaces cannot disagree again.
 *
 * The engine's contract (signal_generator.decide()):
 *   - `is_short = regime_down` is set BEFORE the RSI is read, and is only
 *     reachable with ALLOW_SHORTS armed, so the ACTIVE DIRECTION is a property
 *     of the regime — not of whether a signal has already fired.
 *   - A down/flat regime with ALLOW_SHORTS off returns at the regime gate: the
 *     RSI stays null and no entry is evaluated at all.
 */

export interface SignalMode {
  allowShorts: boolean;
  regimeUp: boolean;
  regimeDown: boolean;
  /** A BUY has already fired this scan. */
  isBuy: boolean;
  /** A SELL (short) has already fired this scan. */
  isShort: boolean;
  /** Either side has fired. */
  isTrigger: boolean;
  /** The engine is hunting a SHORT (down regime with the mirror armed). */
  shortMode: boolean;
  /** Down regime with the mirror disarmed: the engine evaluates no entry. */
  blockedMode: boolean;
  /**
   * True when the served price sits below the served EMA — the engine labels a
   * flat tape "DOWN" too, so only claim a falling EMA when the numbers agree.
   * null when either value is missing.
   */
  priceBelowEma: boolean | null;
  /** The RSI read is present (not null/undefined). */
  rsiKnown: boolean;
  /** Effective oversold threshold (engine-published value wins over config). */
  oversold: number;
  /** Effective overbought threshold (engine-published value, then config, then 60). */
  overbought: number;
}

export function deriveSignalMode(s: SignalState, config: BotConfig): SignalMode {
  const allowShorts = config.allowShorts ?? false;
  const isBuy = s.trigger && s.signal === 'BUY';
  const isShort = s.trigger && s.signal === 'SELL';
  const regimeUp = s.regime === 'UP';
  const regimeDown = s.regime === 'DOWN';
  const priceBelowEma =
    s.regime_price !== undefined && s.regime_ema_value !== undefined
      ? s.regime_price < s.regime_ema_value
      : null;

  return {
    allowShorts,
    regimeUp,
    regimeDown,
    isBuy,
    isShort,
    isTrigger: isBuy || isShort,
    shortMode: allowShorts && regimeDown,
    blockedMode: regimeDown && !allowShorts,
    priceBelowEma,
    rsiKnown: s.rsi !== null && s.rsi !== undefined,
    oversold: s.oversold ?? config.rsiOversold,
    overbought: s.overbought ?? config.shortRsiOverbought ?? 60,
  };
}

export interface BracketFractions {
  /** Stop-distance fraction for the chosen side (always positive). */
  slFrac: number;
  /** Take-profit-distance fraction for the chosen side (always positive). */
  tpFrac: number;
}

/**
 * Pick the fixed-% bracket fractions for a direction. Long reads
 * SL_PERCENT/TP_PERCENT; short reads the SHORT_* mirror. Centralised so the card
 * and modal can never disagree about which config key drives which side.
 */
export function bracketFracs(config: BotConfig, shortMode: boolean): BracketFractions {
  return shortMode
    ? { slFrac: config.shortSlPercent, tpFrac: config.shortTpPercent }
    : { slFrac: config.slPercent, tpFrac: config.tpPercent };
}

export interface BracketLevels extends BracketFractions {
  /** Absolute stop price (0 when entry price is unknown). */
  stopLoss: number;
  /** Absolute take-profit price (0 when entry price is unknown). */
  takeProfit: number;
}

/**
 * The fixed-% bracket the engine applies, mirrored for a short: stop ABOVE
 * entry / take-profit BELOW, with the same MIN_TP_PERCENT floor on the
 * profitable leg (see trade_policy._compute_bracket / _short_bracket).
 */
export function fixedBracket(config: BotConfig, price: number, shortMode: boolean): BracketLevels {
  const { slFrac, tpFrac } = bracketFracs(config, shortMode);
  const stopLoss = price > 0 ? price * (shortMode ? 1 + slFrac : 1 - slFrac) : 0;
  let takeProfit = price > 0 ? price * (shortMode ? 1 - tpFrac : 1 + tpFrac) : 0;
  const minTpDist = price * config.minTpPercent;
  if (shortMode) {
    if (price - takeProfit < minTpDist) takeProfit = price - minTpDist;
  } else if (takeProfit - price < minTpDist) {
    takeProfit = price + minTpDist;
  }
  return { slFrac, tpFrac, stopLoss, takeProfit };
}
