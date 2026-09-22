import math
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    _env_file = Path(__file__).resolve().parent / ".env"
    if _env_file.exists():
        try:
            with open(_env_file, "r") as _f:
                for _line in _f:
                    _line = _line.strip()
                    if _line and not _line.startswith("#") and "=" in _line:
                        _k, _v = _line.split("=", 1)
                        _k = _k.strip()
                        _v = _v.strip().strip("'\"")
                        if _k not in os.environ:
                            os.environ[_k] = _v
        except Exception:
            pass


def _env_int(key, default):
    """Parse an integer env var. A malformed value falls back to `default`
    with a loud warning instead of raising — a raising parser turned a single
    bad .env value (MAX_TRADES_PER_DAY=NaN) into a PM2 crash loop."""
    raw = os.getenv(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        print(f"[config] WARNING: {key}={raw!r} is not a valid integer; using default {default!r}.", flush=True)
        return default


def _env_float(key, default):
    """Parse a float env var. Rejects NaN/Inf (float('NaN') parses fine but
    poisons every comparison downstream) and falls back to `default`."""
    raw = os.getenv(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        val = float(str(raw).strip())
    except (TypeError, ValueError):
        print(f"[config] WARNING: {key}={raw!r} is not a valid number; using default {default!r}.", flush=True)
        return default
    if not math.isfinite(val):
        print(f"[config] WARNING: {key}={raw!r} is not finite; using default {default!r}.", flush=True)
        return default
    return val


# The engine runs ONE strategy: `intraday_rsi`. Its signal mode is `rsi_dip` —
# a daily-EMA50 regime gate + an RSI oversold dip trigger with a FIXED % bracket.
# Everything else (the 5-factor confluence engine, the scalping/day/swing ATR
# presets and the old swing_rsi preset) has been removed; see README changelog.
#
# Proven configuration (2026-09-14, NEARUSDT, 30 pages ≈ 104 days, $22 equity,
# honest taker fees + exchange minNotional, same-UTC-day close): +10.77%,
# 42 trades, WR 42.9%, PF 1.43, max DD 6.8% — with 2 entries/day (frequency
# sweep: robust across a 3x3 SL/TP neighborhood and 3 time sub-windows; 15m
# buckets and 3+/day FAILED the same battery and were rejected).
PRESETS = {
    "intraday_rsi": {
        "TIMEFRAME": "5m",
        "MTF_TIMEFRAME": "1d",
        "ATR_PERIOD": 14,
        "TRAILING_STOP_ACTIVATE": 0.01,
        "TRAILING_STOP_CALLBACK": 0.01,
        "TRAILING_ATR_MULTIPLIER": 2.0,
        "MAX_HOLD_TIME": 84600,
        "MIN_TP_PERCENT": 0.03,
        "STRATEGY_MODE": "rsi_dip",
        "SL_PERCENT": 0.012,
        "TP_PERCENT": 0.03,
        "RSI_PERIOD": 7,
        "RSI_OVERSOLD": 40.0,
        "RSI_TIMEFRAME": "1h",
        "ENTRY_RSI_MIN": 35.0,
        "REGIME_EMA": 50,
        "REGIME_SLOPE_DAYS": 3,
        # 2 entries per UTC day is the proven frequency (sweep 2026-09-14: 1/day
        # +6.8%, 2/day +10.8% with HIGHER PF and LOWER DD; 15m buckets and 3+/day
        # failed the robustness battery). The general streak cooldowns
        # (COOLDOWN_LOSS/WIN) are global risk settings, not strategy parameters.
        "MAX_TRADES_PER_DAY": 2,
        "BREAKEVEN_ENABLED": False,
        "CLOSE_AT_UTC_DAY_END": True,
    }
}
DEFAULT_PRESET = "intraday_rsi"


def load_config():
    preset_name = os.getenv("PRESET", DEFAULT_PRESET).lower()
    if preset_name not in PRESETS:
        preset_name = DEFAULT_PRESET
    preset = PRESETS[preset_name]
    private_key_path = Path(os.getenv("BINANCE_PRIVATE_KEY_PATH", "./keys/private_key.pem"))

    config = {
        "API_KEY": os.getenv("BINANCE_API_KEY"),
        "API_SECRET": os.getenv("BINANCE_API_SECRET"),
        "PRIVATE_KEY_PATH": private_key_path,
        "DB_PATH": os.getenv("DB_PATH", "./data/trading.db"),
        "CONTROL_FILE": os.getenv("CONTROL_FILE", "./data/engine_control.json"),
        "DYNAMIC_SYMBOLS": os.getenv("DYNAMIC_SYMBOLS", "false").lower() == "true",
        "STATIC_SYMBOLS": [s.strip() for s in os.getenv("STATIC_SYMBOLS", "NEARUSDT").split(",") if s.strip()],
        "MAX_SYMBOLS": _env_int("MAX_SYMBOLS", 5),
        "TOP_CANDIDATES": _env_int("TOP_CANDIDATES", 50),
        "MIN_VOLUME_USDT": _env_float("MIN_VOLUME_USDT", 1000000),
        "MIN_PRICE_CHANGE_PERCENT": _env_float("MIN_PRICE_CHANGE_PERCENT", 0.5),
        "MIN_VOLATILITY_PERCENT": _env_float("MIN_VOLATILITY_PERCENT", 0.3),
        "EXCLUDE_SYMBOLS": [s.strip() for s in os.getenv("EXCLUDE_SYMBOLS", "USDC,BUSD,UP,DOWN").split(",") if s.strip()],
        "SYMBOL_REFRESH_INTERVAL": _env_int("SYMBOL_REFRESH_INTERVAL", 3600),
        # Fast cadence for in-place screener price refresh (see trade_logic.refresh_symbols_loop).
        # Keeps web-monitor prices aligned with the exchange between full rescans.
        "PRICE_REFRESH_INTERVAL": _env_int("PRICE_REFRESH_INTERVAL", 10),
        # Max age (seconds) of the WS user-data balance cache before the engine
        # falls back to a REST /api/v3/account snapshot (see src/exchange/balance_cache.py).
        "WS_BALANCE_MAX_AGE": _env_int("WS_BALANCE_MAX_AGE", 90),
        "ADX_THRESHOLD": _env_float("ADX_THRESHOLD", 25),
        "ADX_PERIOD": _env_int("ADX_PERIOD", 14),
        "Z_SCORE_WEIGHT_VOLUME": _env_float("Z_SCORE_WEIGHT_VOLUME", 0.20),
        "Z_SCORE_WEIGHT_CHANGE": _env_float("Z_SCORE_WEIGHT_CHANGE", 0.20),
        "Z_SCORE_WEIGHT_VOLATILITY": _env_float("Z_SCORE_WEIGHT_VOLATILITY", 0.20),
        "Z_SCORE_WEIGHT_ADX": _env_float("Z_SCORE_WEIGHT_ADX", 0.40),
        "CORRELATION_THRESHOLD": _env_float("CORRELATION_THRESHOLD", 0.70),
        "CORRELATION_PENALTY": _env_float("CORRELATION_PENALTY", 0.90),
        "TREND_LOOKBACK": _env_int("TREND_LOOKBACK", 20),
        "QUOTE_ASSET": os.getenv("QUOTE_ASSET", "USDT"),
        # Market selection: 'spot' (default, the proven engine path) or 'futures'
        # (USDⓈ-M via fapi — see src/exchange/futures_*.py). Everything downstream
        # (main.py client selection) keys off this single value.
        "MARKET": os.getenv("MARKET", "spot").strip().lower(),
        # --- USDⓈ-M futures settings (used only when MARKET=futures) ---
        # Position mode: one-way (BOTH side) — the engine's brackets assume a
        # single net position per symbol, not hedge-mode LONG/SHORT books.
        "FUTURES_ONE_WAY_MODE": os.getenv("FUTURES_ONE_WAY_MODE", "true").lower() == "true",
        # Margin mode per symbol: ISOLATED caps the loss at the position's margin
        # (recommended) vs CROSSED (whole futures wallet at risk).
        "FUTURES_MARGIN_TYPE": os.getenv("FUTURES_MARGIN_TYPE", "ISOLATED").strip().upper(),
        # Initial leverage per symbol (1-125; the engine does NOT raise this
        # beyond what the account's leverage brackets allow).
        "FUTURES_LEVERAGE": _env_int("FUTURES_LEVERAGE", 1),
        # Funding-rate gate for futures longs: skip entries when the pair's live
        # rate exceeds this fraction per interval (a long PAYS positive funding).
        # 0 disables the gate. 0.0005 ≈ 0.05%/8h — one funding interval must not
        # eat more than a sixth of the 0.3% average winner.
        "FUNDING_RATE_MAX": _env_float("FUNDING_RATE_MAX", 0.0005),
        # Notional caps are a SECONDARY ceiling on top of the 1% fixed-fractional
        # risk size. On a small account they must stay at 1.0: a 0.2 cap on $22 is
        # $4.40, below Binance's NOTIONAL.minNotional ($5), and every entry is then
        # silently skipped.
        "BALANCE_USAGE_PERCENT": _env_float("BALANCE_USAGE_PERCENT", 1.0),
        "MAX_SYMBOL_ALLOCATION_PERCENT": _env_float("MAX_SYMBOL_ALLOCATION_PERCENT", 1.0),
        "MAX_HOLD_TIME": _env_int("MAX_HOLD_TIME", preset["MAX_HOLD_TIME"]),
        "RISK_PER_TRADE": _env_float("RISK_PER_TRADE", 0.01),
        "MIN_RISK_REWARD": _env_float("MIN_RISK_REWARD", 1.5),
        "SCALE_OUT_ENABLED": os.getenv("SCALE_OUT_ENABLED",
                                       "false" if preset.get("STRATEGY_MODE") == "rsi_dip" else "true").lower() == "true",
        "SCALE_OUT_R_MULTIPLE": _env_float("SCALE_OUT_R_MULTIPLE", 1.0),
        "SCALE_OUT_FRACTION": _env_float("SCALE_OUT_FRACTION", 0.5),
        "MAX_DAILY_DRAWDOWN": _env_float("MAX_DAILY_DRAWDOWN", 0.05),
        "MAX_LOSS_STREAK": _env_int("MAX_LOSS_STREAK", 3),
        "MAX_WIN_STREAK": _env_int("MAX_WIN_STREAK", 5),
        "COOLDOWN_LOSS": _env_int("COOLDOWN_LOSS", 3600),
        # Per-symbol wait after ANY stop-out before that symbol may re-enter
        # (revenge-trade brake; streak cooldowns only cover N-loss runs).
        "LOSS_REENTRY_COOLDOWN": _env_int("LOSS_REENTRY_COOLDOWN", 900),
        "COOLDOWN_WIN": _env_int("COOLDOWN_WIN", 1800),
        "MAX_TRADES_PER_DAY": _env_int("MAX_TRADES_PER_DAY", preset.get("MAX_TRADES_PER_DAY", 0)),
        "TIMEFRAME": os.getenv("TIMEFRAME", preset["TIMEFRAME"]),
        "MTF_TIMEFRAME": os.getenv("MTF_TIMEFRAME", preset["MTF_TIMEFRAME"]),
        "ATR_PERIOD": _env_int("ATR_PERIOD", preset["ATR_PERIOD"]),
        "TRAILING_STOP_ACTIVATE": _env_float("TRAILING_STOP_ACTIVATE", preset["TRAILING_STOP_ACTIVATE"]),
        "TRAILING_STOP_CALLBACK": _env_float("TRAILING_STOP_CALLBACK", preset["TRAILING_STOP_CALLBACK"]),
        "STRATEGY_MODE": os.getenv("STRATEGY_MODE", preset.get("STRATEGY_MODE", "rsi_dip")).lower(),
        "SL_PERCENT": _env_float("SL_PERCENT", preset.get("SL_PERCENT", 0.02)),
        "TP_PERCENT": _env_float("TP_PERCENT", preset.get("TP_PERCENT", 0.04)),
        # --- Exit policy (src/strategies/trade_policy.py — shared with backtest) ---
        # Volatility-adaptive stop: when > 0 the stop is SL_ATR_MULTIPLIER x ATR
        # instead of the fixed SL_PERCENT, clamped to SL_ATR_MAX_PERCENT of entry
        # (0 = fixed-% stop, the proven baseline).
        "SL_ATR_MULTIPLIER": _env_float("SL_ATR_MULTIPLIER", 0.0),
        "SL_ATR_MAX_PERCENT": _env_float("SL_ATR_MAX_PERCENT", 0.024),
        # Trailing stop: % callback behind the favourable mark, or an ATR
        # multiple when TRAILING_ATR_MULTIPLIER > 0 (0 = % callback).
        "TRAILING_ATR_MULTIPLIER": _env_float("TRAILING_ATR_MULTIPLIER", preset.get("TRAILING_ATR_MULTIPLIER", 0.0)),
        # Breakeven lock levels: trigger (+1% profit) and offset above entry
        # (0.25% = covers the 0.2% round-trip taker fee with a small cushion).
        "BREAKEVEN_TRIGGER": _env_float("BREAKEVEN_TRIGGER", 0.01),
        "BREAKEVEN_OFFSET": _env_float("BREAKEVEN_OFFSET", 0.0025),
        # --- Entry quality gates (SignalGenerator.decide; all off by default) ---
        "ENTRY_MAX_EXT_ATR": _env_float("ENTRY_MAX_EXT_ATR", 0.0),
        "ENTRY_EXT_EMA": _env_int("ENTRY_EXT_EMA", 20),
        "ENTRY_VOL_MULT": _env_float("ENTRY_VOL_MULT", 0.0),
        "ENTRY_VOL_LOOKBACK": _env_int("ENTRY_VOL_LOOKBACK", 20),
        "ENTRY_RSI_MIN": _env_float("ENTRY_RSI_MIN", 0.0),
        "ENTRY_REQUIRE_RSI_RISE2": os.getenv("ENTRY_REQUIRE_RSI_RISE2", "false").lower() == "true",
        "RSI_PERIOD": _env_int("RSI_PERIOD", preset.get("RSI_PERIOD", 14)),
        "RSI_OVERSOLD": _env_float("RSI_OVERSOLD", preset.get("RSI_OVERSOLD", 40.0)),
        "RSI_TIMEFRAME": os.getenv("RSI_TIMEFRAME", preset.get("RSI_TIMEFRAME", "1h")),
        # Bucket size (ms) of the RSI sampling window, derived from RSI_TIMEFRAME.
        # An explicit RSI_TIMEFRAME_MS in .env overrides the map so nonstandard
        # timeframes are genuinely tunable.
        # _env_int(K, 0) -> unset/invalid yields 0 -> falsy -> the timeframe map
        # decides. A garbage value can no longer crash boot.
        "RSI_TIMEFRAME_MS": _env_int("RSI_TIMEFRAME_MS", 0) or {"1m": 60_000, "3m": 180_000, "5m": 300_000,
                             "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000}.get(
            os.getenv("RSI_TIMEFRAME", preset.get("RSI_TIMEFRAME", "1h")), 3_600_000),
        "REGIME_EMA": _env_int("REGIME_EMA", preset.get("REGIME_EMA", 50)),
        "REGIME_SLOPE_DAYS": _env_int("REGIME_SLOPE_DAYS", preset.get("REGIME_SLOPE_DAYS", 3)),
        # Fee-aware breakeven lock (+1% profit -> stop to entry*1.0025). The
        # intraday_rsi edge was proven WITHOUT it (BE exits before the +3% TP).
        "BREAKEVEN_ENABLED": os.getenv("BREAKEVEN_ENABLED",
                                       "true" if preset.get("BREAKEVEN_ENABLED", True) else "false").lower() == "true",
        # Force-close open positions at the UTC day end (the research exits every
        # trade the same day it opens — parity with the backtest).
        "CLOSE_AT_UTC_DAY_END": os.getenv("CLOSE_AT_UTC_DAY_END",
                                          "true" if preset.get("CLOSE_AT_UTC_DAY_END", False) else "false").lower() == "true",
        "MAX_SLIPPAGE_PERCENT": _env_float("MAX_SLIPPAGE_PERCENT", 0.5),
        "MIN_TP_PERCENT": _env_float("MIN_TP_PERCENT", preset.get("MIN_TP_PERCENT", 0.03)),
        "SIGNAL_INTERVAL": _env_int("SIGNAL_INTERVAL", 10),
        "ENTRY_TIMEOUT": _env_int("ENTRY_TIMEOUT", 15),
        "DISCORD_WEBHOOK_URL": os.getenv("DISCORD_WEBHOOK_URL", ""),
        "DISCORD_COOLDOWN": _env_int("DISCORD_COOLDOWN", 30),
        "TICKERS_REST_FALLBACK_S": _env_float("TICKERS_REST_FALLBACK_S", 5),
        "LOG_LEVEL": os.getenv("LOG_LEVEL", "INFO"),
        "LOG_FILE": os.getenv("LOG_FILE", "./logs/trading.log"),
        "HEALTH_CHECK_INTERVAL": _env_int("HEALTH_CHECK_INTERVAL", 60),
        "REST_WEIGHT_LIMIT": _env_int("REST_WEIGHT_LIMIT", 1200),
        "PAPER_TRADE": os.getenv("PAPER_TRADE", "true").lower() == "true",
        "USE_TESTNET": os.getenv("USE_TESTNET", "false").lower() == "true",
        "AUTO_LIQUIDATE_ORPHANS": os.getenv("AUTO_LIQUIDATE_ORPHANS", "false").lower() == "true",
        "ORPHAN_ADOPT_WINDOW_HOURS": _env_int("ORPHAN_ADOPT_WINDOW_HOURS", "48"),
        "PRESET": preset_name,
    }

    # Validations
    if not config["PAPER_TRADE"] and not config["API_KEY"]:
        raise ValueError("BINANCE_API_KEY is required for live trading.")
    if config["STRATEGY_MODE"] != "rsi_dip":
        raise ValueError("STRATEGY_MODE must be 'rsi_dip' (the only supported strategy).")
    if not isinstance(config["SIGNAL_INTERVAL"], int) or config["SIGNAL_INTERVAL"] < 1:
        raise ValueError("SIGNAL_INTERVAL must be a positive integer (>= 1 second).")
    if not isinstance(config["MAX_SYMBOLS"], int) or config["MAX_SYMBOLS"] < 1:
        raise ValueError("MAX_SYMBOLS must be a positive integer (>= 1).")
    if not isinstance(config["TOP_CANDIDATES"], int) or config["TOP_CANDIDATES"] < 1:
        raise ValueError("TOP_CANDIDATES must be a positive integer (>= 1).")
    if not isinstance(config["SYMBOL_REFRESH_INTERVAL"], int) or config["SYMBOL_REFRESH_INTERVAL"] < 60:
        raise ValueError("SYMBOL_REFRESH_INTERVAL must be at least 60 seconds.")
    if not isinstance(config["PRICE_REFRESH_INTERVAL"], int) or config["PRICE_REFRESH_INTERVAL"] < 10:
        raise ValueError("PRICE_REFRESH_INTERVAL must be at least 10 seconds.")
    if not isinstance(config["WS_BALANCE_MAX_AGE"], int) or config["WS_BALANCE_MAX_AGE"] < 5:
        raise ValueError("WS_BALANCE_MAX_AGE must be at least 5 seconds.")
    if not isinstance(config["ENTRY_TIMEOUT"], int) or config["ENTRY_TIMEOUT"] < 5:
        raise ValueError("ENTRY_TIMEOUT must be at least 5 seconds.")
    if not isinstance(config["HEALTH_CHECK_INTERVAL"], int) or config["HEALTH_CHECK_INTERVAL"] < 5:
        raise ValueError("HEALTH_CHECK_INTERVAL must be at least 5 seconds.")
    if not isinstance(config["REST_WEIGHT_LIMIT"], int) or config["REST_WEIGHT_LIMIT"] < 1:
        raise ValueError("REST_WEIGHT_LIMIT must be a positive integer.")
    if not isinstance(config["DISCORD_COOLDOWN"], int) or config["DISCORD_COOLDOWN"] < 0:
        raise ValueError("DISCORD_COOLDOWN must be a non-negative integer.")
    if not isinstance(config["LOG_LEVEL"], str) or config["LOG_LEVEL"].upper() not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError("LOG_LEVEL must be one of DEBUG/INFO/WARNING/ERROR/CRITICAL.")
    if not isinstance(config["MAX_SLIPPAGE_PERCENT"], (int, float)) or not (0 < config["MAX_SLIPPAGE_PERCENT"] <= 10.0):
        raise ValueError("MAX_SLIPPAGE_PERCENT must be between 0 (exclusive) and 10.0 (percent).")
    if not isinstance(config["MIN_TP_PERCENT"], (int, float)) or config["MIN_TP_PERCENT"] < 0:
        raise ValueError("MIN_TP_PERCENT must be non-negative.")
    if not isinstance(config["BALANCE_USAGE_PERCENT"], (int, float)) or not (0 < config["BALANCE_USAGE_PERCENT"] <= 1):
        raise ValueError("BALANCE_USAGE_PERCENT must be between 0 (exclusive) and 1.")
    if not isinstance(config["MAX_SYMBOL_ALLOCATION_PERCENT"], (int, float)) or not (0 < config["MAX_SYMBOL_ALLOCATION_PERCENT"] <= 1):
        raise ValueError("MAX_SYMBOL_ALLOCATION_PERCENT must be between 0 (exclusive) and 1.")
    if not isinstance(config["MAX_DAILY_DRAWDOWN"], (int, float)) or not (0 < config["MAX_DAILY_DRAWDOWN"] <= 1):
        raise ValueError("MAX_DAILY_DRAWDOWN must be between 0 (exclusive) and 1.")
    if not isinstance(config["MAX_LOSS_STREAK"], int) or config["MAX_LOSS_STREAK"] < 1:
        raise ValueError("MAX_LOSS_STREAK must be a positive integer.")
    if not isinstance(config["MAX_WIN_STREAK"], int) or config["MAX_WIN_STREAK"] < 1:
        raise ValueError("MAX_WIN_STREAK must be a positive integer.")
    if not isinstance(config["COOLDOWN_LOSS"], int) or config["COOLDOWN_LOSS"] < 0:
        raise ValueError("COOLDOWN_LOSS must be a non-negative integer.")
    if not isinstance(config["COOLDOWN_WIN"], int) or config["COOLDOWN_WIN"] < 0:
        raise ValueError("COOLDOWN_WIN must be a non-negative integer.")
    if not isinstance(config["MAX_TRADES_PER_DAY"], int) or config["MAX_TRADES_PER_DAY"] < 0:
        raise ValueError("MAX_TRADES_PER_DAY must be a non-negative integer (0 = unlimited).")
    if not isinstance(config["ADX_THRESHOLD"], (int, float)) or not (0 <= config["ADX_THRESHOLD"] <= 100):
        raise ValueError("ADX_THRESHOLD must be between 0 and 100.")
    if not isinstance(config["ADX_PERIOD"], int) or config["ADX_PERIOD"] < 1:
        raise ValueError("ADX_PERIOD must be a positive integer.")
    if not isinstance(config["TREND_LOOKBACK"], int) or config["TREND_LOOKBACK"] < 1:
        raise ValueError("TREND_LOOKBACK must be a positive integer.")
    for _w in ("Z_SCORE_WEIGHT_VOLUME", "Z_SCORE_WEIGHT_CHANGE", "Z_SCORE_WEIGHT_VOLATILITY", "Z_SCORE_WEIGHT_ADX"):
        if not isinstance(config[_w], (int, float)) or not (0 <= config[_w] <= 1):
            raise ValueError(f"{_w} must be between 0 and 1.")
    if not isinstance(config["CORRELATION_THRESHOLD"], (int, float)) or not (0 <= config["CORRELATION_THRESHOLD"] <= 1):
        raise ValueError("CORRELATION_THRESHOLD must be between 0 and 1.")
    if not isinstance(config["CORRELATION_PENALTY"], (int, float)) or not (0 < config["CORRELATION_PENALTY"] <= 1):
        raise ValueError("CORRELATION_PENALTY must be between 0 (exclusive) and 1.")
    if not isinstance(config["MIN_VOLUME_USDT"], (int, float)) or config["MIN_VOLUME_USDT"] < 0:
        raise ValueError("MIN_VOLUME_USDT must be non-negative.")
    if not isinstance(config["MIN_PRICE_CHANGE_PERCENT"], (int, float)) or config["MIN_PRICE_CHANGE_PERCENT"] < 0:
        raise ValueError("MIN_PRICE_CHANGE_PERCENT must be non-negative.")
    if not isinstance(config["MIN_VOLATILITY_PERCENT"], (int, float)) or config["MIN_VOLATILITY_PERCENT"] < 0:
        raise ValueError("MIN_VOLATILITY_PERCENT must be non-negative.")
    if not isinstance(config["ATR_PERIOD"], int) or config["ATR_PERIOD"] < 1:
        raise ValueError("ATR_PERIOD must be a positive integer.")
    # rsi_dip strategy parameters
    if not (0 < config["SL_PERCENT"] < config["TP_PERCENT"] <= 1):
        raise ValueError("SL_PERCENT/TP_PERCENT: need 0 < SL_PERCENT < TP_PERCENT <= 1.")
    if config["SL_ATR_MULTIPLIER"] < 0:
        raise ValueError("SL_ATR_MULTIPLIER must be >= 0 (0 = fixed-% stop).")
    if not (0 < config["SL_ATR_MAX_PERCENT"] < 1):
        raise ValueError("SL_ATR_MAX_PERCENT must be a fraction in (0, 1).")
    if config["TRAILING_ATR_MULTIPLIER"] < 0:
        raise ValueError("TRAILING_ATR_MULTIPLIER must be >= 0 (0 = % callback).")
    if config["BREAKEVEN_TRIGGER"] < 0 or config["BREAKEVEN_OFFSET"] < 0:
        raise ValueError("BREAKEVEN_TRIGGER / BREAKEVEN_OFFSET must be >= 0.")
    if config["ENTRY_MAX_EXT_ATR"] < 0:
        raise ValueError("ENTRY_MAX_EXT_ATR must be >= 0 (0 = gate off).")
    if config["ENTRY_VOL_MULT"] < 0:
        raise ValueError("ENTRY_VOL_MULT must be >= 0 (0 = gate off).")
    if not (0 <= config["ENTRY_RSI_MIN"] < 100):
        raise ValueError("ENTRY_RSI_MIN must be in [0, 100) (0 = gate off).")
    if config["ENTRY_EXT_EMA"] < 2 or config["ENTRY_VOL_LOOKBACK"] < 2:
        raise ValueError("ENTRY_EXT_EMA / ENTRY_VOL_LOOKBACK must be >= 2.")
    if not isinstance(config["RSI_PERIOD"], int) or config["RSI_PERIOD"] < 2:
        raise ValueError("RSI_PERIOD must be an integer >= 2.")
    if not (0 < config["RSI_OVERSOLD"] < 100):
        raise ValueError("RSI_OVERSOLD must be between 0 and 100 (exclusive).")
    if not isinstance(config["REGIME_EMA"], int) or config["REGIME_EMA"] < 2:
        raise ValueError("REGIME_EMA must be an integer >= 2.")
    if not isinstance(config["REGIME_SLOPE_DAYS"], int) or config["REGIME_SLOPE_DAYS"] < 1:
        raise ValueError("REGIME_SLOPE_DAYS must be a positive integer.")
    if not isinstance(config["TRAILING_STOP_ACTIVATE"], (int, float)) or not (0 < config["TRAILING_STOP_ACTIVATE"] <= 1):
        raise ValueError("TRAILING_STOP_ACTIVATE must be between 0 (exclusive) and 1.")
    if not isinstance(config["TRAILING_STOP_CALLBACK"], (int, float)) or config["TRAILING_STOP_CALLBACK"] < 0:
        raise ValueError("TRAILING_STOP_CALLBACK must be non-negative.")
    # In callback mode (% of mark) the callback must be tighter than the
    # activation threshold. In ATR-trail mode (TRAILING_ATR_MULTIPLIER > 0) the
    # % callback is unused, so the relation is not enforced.
    if config["TRAILING_ATR_MULTIPLIER"] <= 0 and config["TRAILING_STOP_CALLBACK"] >= config["TRAILING_STOP_ACTIVATE"]:
        raise ValueError("TRAILING_STOP_CALLBACK must be less than TRAILING_STOP_ACTIVATE (callback mode).")
    if not isinstance(config["MAX_HOLD_TIME"], int) or config["MAX_HOLD_TIME"] < 60:
        raise ValueError("MAX_HOLD_TIME must be at least 60 seconds.")
    if not isinstance(config["RISK_PER_TRADE"], (int, float)) or not (0 < config["RISK_PER_TRADE"] <= 0.1):
        raise ValueError("RISK_PER_TRADE must be between 0 (exclusive) and 0.1 (10% of equity per trade — do not go higher).")
    if not isinstance(config["MIN_RISK_REWARD"], (int, float)) or config["MIN_RISK_REWARD"] < 1.0:
        raise ValueError("MIN_RISK_REWARD must be at least 1.0 (TP distance vs SL distance).")
    if not (0 < config["SCALE_OUT_FRACTION"] < 1):
        raise ValueError("SCALE_OUT_FRACTION must be between 0 (exclusive) and 1 (exclusive).")
    if config["SCALE_OUT_R_MULTIPLE"] <= 0:
        raise ValueError("SCALE_OUT_R_MULTIPLE must be positive (1.0 = take profit at 1x the stop distance).")
    if config["SCALE_OUT_R_MULTIPLE"] >= config["TP_PERCENT"] / config["SL_PERCENT"]:
        raise ValueError("SCALE_OUT_R_MULTIPLE must be below the strategy's full R:R (TP_PERCENT / SL_PERCENT) so the runner leg still has room.")
    if not config["STATIC_SYMBOLS"] and not config["DYNAMIC_SYMBOLS"]:
        raise ValueError("At least one symbol must be provided.")
    if config["MARKET"] not in ("spot", "futures"):
        raise ValueError("MARKET must be 'spot' or 'futures'.")
    if config["MARKET"] == "futures":
        if config["FUTURES_MARGIN_TYPE"] not in ("ISOLATED", "CROSSED"):
            raise ValueError("FUTURES_MARGIN_TYPE must be ISOLATED or CROSSED.")
        if not (1 <= config["FUTURES_LEVERAGE"] <= 125):
            raise ValueError("FUTURES_LEVERAGE must be between 1 and 125.")
    if not config["PAPER_TRADE"]:
        has_pem = private_key_path and private_key_path.exists()
        has_secret = bool(config.get("API_SECRET"))
        if not has_pem and not has_secret:
            raise ValueError(
                f"Live trading requires either BINANCE_API_SECRET (for standard HMAC-SHA256) "
                f"or BINANCE_PRIVATE_KEY_PATH (for Ed25519; file not found at {private_key_path})."
            )
    if config["PAPER_TRADE"]:
        # Even in paper mode, validate that any non-default paths point at plausible files
        # so a mis-typed DB_PATH or LOG_FILE is caught early rather than mid-run.
        if config.get("DB_PATH") and not config["DB_PATH"].endswith(".db"):
            raise ValueError("DB_PATH should end with .db (e.g. ./data/trading.db).")
        if config.get("LOG_FILE") and not config["LOG_FILE"].endswith(".log"):
            raise ValueError("LOG_FILE should end with .log (e.g. ./logs/trading.log).")
        if config.get("CONTROL_FILE") and not config["CONTROL_FILE"].endswith(".json"):
            raise ValueError("CONTROL_FILE should end with .json (e.g. ./data/engine_control.json).")

    # Drift guard: an explicit RSI_TIMEFRAME_MS that disagrees with RSI_TIMEFRAME
    # silently re-times the RSI gate (e.g. 15m bucket on a 1h gate -> 4.6x the
    # trade frequency). Warn so the mismatch is visible at boot.
    _tf = str(config.get("RSI_TIMEFRAME", "1h"))
    _expected_ms = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
                    "30m": 1_800_000, "1h": 3_600_000}.get(_tf)
    if (_expected_ms is not None and config.get("RSI_TIMEFRAME_MS")
            and int(config["RSI_TIMEFRAME_MS"]) != _expected_ms):
        print(f"CONFIG WARNING: RSI_TIMEFRAME_MS={config['RSI_TIMEFRAME_MS']} disagrees with "
              f"RSI_TIMEFRAME={_tf} (expected {_expected_ms}). "
              "Comment out RSI_TIMEFRAME_MS in .env to derive it from RSI_TIMEFRAME.")

    return config
