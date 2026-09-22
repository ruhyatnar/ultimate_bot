import logging
import time
import numpy as np
import pandas as pd


class SignalGenerator:
    """The bot's single signal engine: daily regime + RSI dip.

    Rules (the backtest-proven `intraday_rsi` edge — one strategy, no drift):
      1. REGIME (htf_df = daily candles): the last COMPLETED day closed above its
         EMA-50 and the EMA is rising over REGIME_SLOPE_DAYS. Today's partial
         daily candle is dropped first, so the regime never peeks at an
         unfinished day.
      2. TRIGGER (ltf_df = execution-TF candles): RSI(RSI_PERIOD) computed on the
         execution-TF close series and SAMPLED at the last completed bar of each
         RSI_TIMEFRAME bucket ("1h RSI(7) on 5m closes, read at :55"). BUY when
         RSI < RSI_OVERSOLD and turning up — buy the dip, not the top.

    Exits are the fixed % bracket owned by trade_logic (SL_PERCENT / TP_PERCENT).

    Every decision is also snapshotted into `last_signals` so the engine can
    publish the REAL signal state to the web monitor (via trade_logic ->
    risk_state) instead of the dashboard guessing/synthesising it.
    """

    # Cap the kline cache so dynamic screening over hundreds of symbols cannot grow
    # memory without bound on extended VPS runs. Evicts the oldest entries (FIFO) once
    # the cap is hit; entries also expire after 60s via the timestamp check below.
    KLINE_CACHE_MAX_ENTRIES = 64

    def __init__(self, config, rest):
        self.config = config
        self.rest = rest
        self.logger = logging.getLogger(__name__)
        self.atr_period = config["ATR_PERIOD"]
        self.klines_cache = {}
        # symbol -> latest decision snapshot (published to the web monitor).
        self.last_signals = {}

    async def _get_cached_klines(self, symbol, interval, limit):
        key = (symbol, interval)
        now = int(time.time() * 1000)
        if key in self.klines_cache:
            cached_time, df = self.klines_cache[key]
            if now - cached_time < 60000:
                return df
        klines = await self.rest.get_klines(symbol, interval, limit)
        if not klines:
            self.logger.warning(f"No klines returned for {symbol} {interval}")
            return None
        df = self._to_df(klines)
        self.klines_cache[key] = (now, df)
        while len(self.klines_cache) > self.KLINE_CACHE_MAX_ENTRIES:
            oldest_key = next(iter(self.klines_cache))
            self.klines_cache.pop(oldest_key, None)
        return df

    def get_last_signals(self):
        """Latest per-symbol signal snapshots (list, newest decision first)."""
        return list(self.last_signals.values())

    def _record(self, symbol, **state):
        """Store one decision snapshot for the web monitor (never raises)."""
        try:
            state.setdefault("symbol", symbol)
            state["time"] = int(time.time() * 1000)
            state.setdefault("rsi_period", int(self.config.get("RSI_PERIOD", 14)))
            state.setdefault("rsi_timeframe", str(self.config.get("RSI_TIMEFRAME", "1h")))
            state.setdefault("oversold", float(self.config.get("RSI_OVERSOLD", 40.0)))
            self.last_signals[symbol] = state
        except Exception as e:  # pragma: no cover - defensive
            self.logger.debug(f"{symbol}: could not record signal state: {e}")

    async def generate_signal(self, symbol):
        """Fetch the daily regime candles + execution-TF window, then decide."""
        self.logger.debug(f"Generating signal for {symbol}...")
        htf_df = await self._get_cached_klines(symbol, self.config["MTF_TIMEFRAME"], 200)
        ltf_df = await self._get_cached_klines(symbol, self.config["TIMEFRAME"], 100)
        if htf_df is None or ltf_df is None:
            self._record(symbol, regime="NO_DATA", rsi=None, rsi_prev=None,
                         trigger=False, signal="NEUTRAL", atr=0.0,
                         reason="no kline data available")
            return "NEUTRAL", 0
        return self.decide(htf_df, ltf_df, symbol=symbol)

    def decide(self, htf_df, ltf_df, symbol: str = ""):
        """Pure decision core — returns (signal, current_atr).

        Shared verbatim by the LIVE engine (generate_signal) and the backtest
        bar replay, so there is zero drift between what is tested and what trades.
        """
        current_atr = self._calculate_atr(ltf_df)
        last_atr = current_atr.iloc[-1] if len(current_atr) else float("nan")
        if pd.isna(last_atr) or last_atr <= 0:
            current_atr_val = float(ltf_df["close"].iloc[-1]) * 0.001
        else:
            current_atr_val = float(last_atr)

        # ---- 1) regime on COMPLETED candles only ----
        # The regime timeframe is whatever MTF_TIMEFRAME says (1d by default, 4h/1h
        # supported). A regime candle is kept only once it has fully closed by the
        # signal bar's close, so the filter can never peek at an in-progress
        # candle — the same rule for every timeframe (previously the drop was
        # hardcoded to "same UTC day", which silently mis-handled sub-daily MTFs).
        tf_ms = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
                 "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000}.get(
            self.config.get("TIMEFRAME", "5m"), 300_000)
        ltf_close_ms = int(ltf_df["open_time"].iloc[-1]) + tf_ms
        htf_ms = {"5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000,
                  "4h": 14_400_000, "1d": 86_400_000}.get(
            self.config.get("MTF_TIMEFRAME", "1d"), 86_400_000)
        htf = htf_df.copy()
        htf = htf[htf["open_time"].astype("int64") + htf_ms <= ltf_close_ms]
        regime_ema = int(self.config.get("REGIME_EMA", 50))
        slope_days = int(self.config.get("REGIME_SLOPE_DAYS", 3))
        need = max(regime_ema + slope_days + 5, 20)
        if len(htf) < need:
            self.logger.debug(f"{symbol}: rsi_dip regime not ready ({len(htf)} regime candles < {need}).")
            self._record(symbol, regime="NOT_READY", regime_ema=regime_ema, rsi=None, rsi_prev=None,
                         trigger=False, signal="NEUTRAL", atr=current_atr_val,
                         reason=(f"regime not ready ({len(htf)}/{need} "
                                 f"{self.config.get('MTF_TIMEFRAME', '1d')} candles)"))
            return "NEUTRAL", current_atr_val
        ema_series = htf["close"].ewm(span=regime_ema, adjust=False).mean()
        ema_now = float(ema_series.iloc[-1])
        ema_prev = float(ema_series.iloc[-1 - slope_days])
        regime_up = bool(htf["close"].iloc[-1] > ema_now and ema_now > ema_prev)
        if not regime_up:
            self.logger.debug(f"{symbol}: rsi_dip regime DOWN/flat — no bullish entries.")
            self._record(symbol, regime="DOWN", regime_ema=regime_ema,
                         regime_price=float(htf["close"].iloc[-1]), regime_ema_value=ema_now,
                         rsi=None, rsi_prev=None, trigger=False, signal="NEUTRAL",
                         atr=current_atr_val,
                         reason=f"daily regime down/flat (close {float(htf['close'].iloc[-1]):.6g} vs EMA{regime_ema} {ema_now:.6g})")
            return "NEUTRAL", current_atr_val

        # ---- 2) RSI pullback trigger (RSI on execution-TF closes, sampled per bucket) ----
        rsi_period = int(self.config.get("RSI_PERIOD", 14))
        oversold = float(self.config.get("RSI_OVERSOLD", 40))
        bucket_ms = int(self.config.get("RSI_TIMEFRAME_MS", 3_600_000))

        l = ltf_df.copy()
        l["bucket"] = l["open_time"].astype("int64") // bucket_ms
        # The LTF window already contains only CLOSED bars ending at the signal
        # bar, so the current bucket's last bar IS the completed read.
        l = l.assign(_rsi=self._rsi_wilder(l["close"], rsi_period).values)
        rsi = l.groupby("bucket")["_rsi"].last()
        if len(rsi) < 2:
            self._record(symbol, regime="UP", regime_ema=regime_ema, rsi=None, rsi_prev=None,
                         trigger=False, signal="NEUTRAL", atr=current_atr_val,
                         reason="RSI not ready (fewer than 2 buckets)")
            return "NEUTRAL", current_atr_val
        rsi_last, rsi_prev = float(rsi.iloc[-1]), float(rsi.iloc[-2])
        rsi_prev2 = float(rsi.iloc[-3]) if len(rsi) >= 3 else None
        if pd.isna(rsi_last) or pd.isna(rsi_prev):
            self._record(symbol, regime="UP", regime_ema=regime_ema, rsi=None, rsi_prev=None,
                         trigger=False, signal="NEUTRAL", atr=current_atr_val,
                         reason="RSI not ready (NaN)")
            return "NEUTRAL", current_atr_val
        self.logger.debug(
            f"{symbol}: rsi_dip regime UP, RSI(ltf)={rsi_last:.1f} (prev {rsi_prev:.1f}, "
            f"oversold<{oversold})")

        def _snap(reason, trigger, signal, extra=None):
            st = {"regime": "UP", "regime_ema": regime_ema, "regime_price": float(htf["close"].iloc[-1]),
                  "regime_ema_value": float(ema_series.iloc[-1]), "rsi": round(rsi_last, 2),
                  "rsi_prev": round(rsi_prev, 2), "oversold": oversold, "trigger": trigger,
                  "signal": signal, "atr": current_atr_val, "reason": reason}
            if extra:
                st.update(extra)
            self._record(symbol, **st)

        def _entry_gates():
            """Config-gated quality filters applied to an otherwise-valid BUY.

            Every gate is OFF by default (0/false) so the proven baseline is
            byte-for-byte unchanged until a gate is switched on — the backtest
            and the live engine share this exact code.
              ENTRY_MAX_EXT_ATR    skip when price is extended >N ATR above the
                                   execution-TF EMA(ENTRY_EXT_EMA) — no chasing
              ENTRY_VOL_MULT       require the trigger bar's volume >= N x the
                                   mean of the previous ENTRY_VOL_LOOKBACK bars
              ENTRY_RSI_MIN        skip RSI below N (collapsing knife, not a dip)
              ENTRY_REQUIRE_RSI_RISE2  require RSI to rise across TWO buckets
            Returns (allowed, metrics, block_reason).
            """
            metrics = {}
            try:
                max_ext = float(self.config.get("ENTRY_MAX_EXT_ATR", 0) or 0)
                if max_ext > 0 and current_atr_val > 0:
                    span = int(self.config.get("ENTRY_EXT_EMA", 20) or 20)
                    ema_ltf = float(ltf_df["close"].ewm(span=span, adjust=False).mean().iloc[-1])
                    ext = (float(ltf_df["close"].iloc[-1]) - ema_ltf) / current_atr_val
                    metrics["ext_atr"] = round(ext, 2)
                    metrics["ext_ema"] = span
                    if ext > max_ext:
                        return False, metrics, (f"entry blocked: price is {ext:.1f} ATR above "
                                                f"EMA{span} (max {max_ext:.1f}) — not chasing")
                vol_mult = float(self.config.get("ENTRY_VOL_MULT", 0) or 0)
                if vol_mult > 0:
                    look = int(self.config.get("ENTRY_VOL_LOOKBACK", 20) or 20)
                    vols = ltf_df["volume"].astype(float)
                    ref = float(vols.iloc[-1 - look:-1].mean()) if len(vols) > 1 else 0.0
                    ratio = (float(vols.iloc[-1]) / ref) if ref > 0 else 0.0
                    metrics["vol_ratio"] = round(ratio, 2)
                    if ratio < vol_mult:
                        return False, metrics, (f"entry blocked: trigger volume {ratio:.2f}x "
                                                f"< {vol_mult:.2f}x the {look}-bar average")
                rsi_min = float(self.config.get("ENTRY_RSI_MIN", 0) or 0)
                metrics["rsi_min"] = rsi_min
                if rsi_min > 0 and rsi_last < rsi_min:
                    return False, metrics, (f"entry blocked: RSI {rsi_last:.1f} below floor "
                                            f"{rsi_min:.0f} (downtrend, not a dip)")
                if self.config.get("ENTRY_REQUIRE_RSI_RISE2", False):
                    metrics["rsi_prev2"] = round(rsi_prev2, 2) if rsi_prev2 is not None else None
                    if rsi_prev2 is None or not (rsi_last > rsi_prev > rsi_prev2):
                        return False, metrics, (f"entry blocked: RSI rise not confirmed over 2 "
                                                f"buckets ({rsi_prev2} → {rsi_prev:.1f} → "
                                                f"{rsi_last:.1f})")
            except Exception as e:  # never let a filter crash the decision path
                self.logger.debug(f"{symbol}: entry gate evaluation failed ({e}); allowing entry.")
                return True, metrics, ""
            return True, metrics, ""

        # Fire ONCE per bucket: the last completed bucket must close exactly at
        # this signal bar's close (fresh bucket — no re-triggering every bar).
        last_bucket_close = int(rsi.index[-1]) * bucket_ms + bucket_ms
        bucket_close_iso = pd.Timestamp(last_bucket_close, unit="ms", tz="UTC").strftime("%H:%M")
        if last_bucket_close != ltf_close_ms:
            _snap(f"waiting for the {self.config.get('RSI_TIMEFRAME', '1h')} bucket to close "
                  f"(last read {bucket_close_iso} UTC)", False, "NEUTRAL")
            return "NEUTRAL", current_atr_val
        if rsi_last < oversold and rsi_last > rsi_prev:
            allowed, metrics, block = _entry_gates()
            if not allowed:
                _snap(block, False, "NEUTRAL", extra={**metrics, "dip_rsi": round(rsi_last, 2)})
                return "NEUTRAL", current_atr_val
            _snap(f"dip confirmed: RSI {rsi_last:.1f} < {oversold} and rising (prev {rsi_prev:.1f})",
                  True, "BUY", extra=metrics or None)
            return "BUY", current_atr_val
        if rsi_last >= oversold:
            _snap(f"RSI {rsi_last:.1f} >= oversold {oversold} — no dip", False, "NEUTRAL")
        else:
            _snap(f"RSI {rsi_last:.1f} < {oversold} but still falling (prev {rsi_prev:.1f})",
                  False, "NEUTRAL")
        return "NEUTRAL", current_atr_val

    def _rsi_wilder(self, close, period):
        """Wilder RSI on a close series (alpha = 1/period), zero-loss bars pin to 100.
        Matches the research-lab RSI formula exactly (the proven edge)."""
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1.0 / period, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1.0 / period, adjust=False).mean()
        rs = gain / loss.replace(0, np.nan)
        out = 100 - 100 / (1 + rs)
        out[loss == 0] = 100.0
        return out

    def _to_df(self, klines):
        df = pd.DataFrame(klines, columns=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_buy_base','taker_buy_quote','ignore'])
        for col in ['open','high','low','close','volume','quote_volume','taker_buy_base','taker_buy_quote']:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)
        return df

    def _calculate_atr(self, df):
        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift()).abs()
        low_close = (df['low'] - df['close'].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return tr.rolling(self.atr_period, min_periods=1).mean()
