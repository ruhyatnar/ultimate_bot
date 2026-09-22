import asyncio
import logging
import math
import re
import numpy as np
import pandas as pd
from scipy.stats import pearsonr

class TrendDetector:
    def __init__(self, config, rest):
        self.config = config
        self.rest = rest
        self.logger = logging.getLogger(__name__)
        self.quote_asset = config["QUOTE_ASSET"]
        self.adx_period = config["ADX_PERIOD"]
        self.adx_threshold = config["ADX_THRESHOLD"]
        self.lookback = config["TREND_LOOKBACK"]
        self.semaphore = asyncio.Semaphore(20)

    def _is_valid_symbol(self, symbol):
        base = symbol[:-len(self.quote_asset)] if symbol.endswith(self.quote_asset) else symbol
        return bool(re.match(r'^[A-Z0-9]+$', base))

    async def _get_klines(self, symbol, interval, limit):
        async with self.semaphore:
            return await self.rest.get_klines(symbol, interval, limit)

    async def get_top_symbols(self):
        self.logger.info("Scanning for trending pairs...")
        tickers = await self.rest.get_24hr_tickers()
        if not tickers:
            return self.config["STATIC_SYMBOLS"]
        exclude = [ex.upper().strip() for ex in self.config["EXCLUDE_SYMBOLS"] if ex.strip()]
        candidates = []
        for t in tickers:
            symbol = t["symbol"]
            if not symbol.endswith(self.quote_asset):
                continue
            base = symbol[: -len(self.quote_asset)] if self.quote_asset else symbol
            # Match excluded tokens only on the base-asset boundary (exact base or a
            # suffix like leveraged "BTCUP"). A raw substring test wrongly drops legit
            # pairs whose ticker merely contains those letters (e.g. SUPERUSDT).
            if base in exclude or any(base.endswith(ex) for ex in exclude if ex):
                continue
            if not self._is_valid_symbol(symbol):
                continue
            try:
                volume = float(t["quoteVolume"])
                price_change = abs(float(t["priceChangePercent"]))
                high = float(t["highPrice"]); low = float(t["lowPrice"]); last = float(t["lastPrice"])
                volatility = (high - low) / last * 100 if last > 0 else 0
            except (ValueError, TypeError, KeyError):
                continue
            if volume < self.config["MIN_VOLUME_USDT"] or price_change < self.config["MIN_PRICE_CHANGE_PERCENT"] or volatility < self.config["MIN_VOLATILITY_PERCENT"]:
                continue
            candidates.append({
                "symbol": symbol,
                "volume": volume,
                "price_change": price_change,
                "raw_price_change": float(t.get("priceChangePercent", 0.0)),
                "volatility": volatility,
                "last_price": last,
                "high": high,
                "low": low
            })
        if not candidates:
            return self.config["STATIC_SYMBOLS"]
        candidates.sort(key=lambda x: x["volume"], reverse=True)
        candidates = candidates[:self.config["TOP_CANDIDATES"]]

        # Fetch at least 100 candles on MTF to allow EMA20, EMA50, ADX and Breakouts to compute reliably
        candle_limit = max(100, self.lookback + self.adx_period + 60)
        tasks = [self._get_klines(c["symbol"], self.config["MTF_TIMEFRAME"], candle_limit) for c in candidates]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        detailed = []
        for c, klines in zip(candidates, results):
            if isinstance(klines, Exception) or not klines or len(klines) < 50:
                continue
            df = self._to_df(klines)
            adx = self._calculate_adx(df)
            if adx < self.adx_threshold: continue
            c["adx"] = adx
            c["trend_dir"] = self._get_trend_direction(df)
            c["breakout"] = self._detect_breakout(df)
            c["closes"] = df['close'].tolist()
            detailed.append(c)
        if not detailed:
            return self.config["STATIC_SYMBOLS"]
        scored = self._calculate_z_scores(detailed)
        scored = await self._apply_correlation_penalty(scored)
        scored.sort(key=lambda x: x.get("final_score", 0.0), reverse=True)
        self.last_scored = scored
        return [item["symbol"] for item in scored[:self.config["MAX_SYMBOLS"]]]

    def get_last_scanned(self):
        if not hasattr(self, "last_scored") or not self.last_scored:
            return []
        items = []
        for idx, s in enumerate(self.last_scored):
            items.append({
                "symbol": s.get("symbol"),
                "price": float(s.get("last_price", 0.0)),
                "price_change_24h": float(s.get("raw_price_change", s.get("price_change", 0.0))),
                "volume_24h": float(s.get("volume", 0.0)),
                "volatility": float(s.get("volatility", 0.0)),
                "adx": round(float(s.get("adx", 0.0)), 2),
                "trend_dir": str(s.get("trend_dir", "NEUTRAL")),
                "breakout": bool(s.get("breakout", False)),
                "z_score": round(float(s.get("final_score", 0.0)), 3),
                "momentum_rank": idx + 1
            })
        return items

    async def refresh_prices(self, ticker_cache=None):
        """Refresh screener prices in place WITHOUT a full rescan.

        A full scan (klines for every candidate + scoring) only runs every
        SYMBOL_REFRESH_INTERVAL, so the last_price snapshots shown on the web
        monitor drift from the exchange within the hour. Prices come FIRST
        from the realtime all-market WS ticker cache (ticker_cache, ~1s old,
        zero REST weight); the bulk REST request is only the fallback when
        the stream is down. Stats, ranks and signals are untouched, then
        scanned_pairs is re-persisted by the caller so /api/status, the /ws
        push and the dashboard all serve fresh prices.
        """
        if not getattr(self, "last_scored", None):
            return 0
        symbols = [s.get("symbol") for s in self.last_scored if s.get("symbol")]
        if not symbols:
            return 0
        prices = {}
        if ticker_cache:
            for sym in symbols:
                t = ticker_cache.get(sym)
                if t and t.get("price", 0) > 0:
                    prices[sym] = t["price"]
        if not prices:
            prices = await self.rest.get_tickers_bulk(symbols)
        if not prices:
            return 0
        updated = 0
        for s in self.last_scored:
            p = prices.get(s.get("symbol"))
            if p is not None and p > 0:
                s["last_price"] = p
                updated += 1
        return updated

    def _to_df(self, klines):
        df = pd.DataFrame(klines, columns=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_buy_base','taker_buy_quote','ignore'])
        for col in ['open','high','low','close','volume']:
            df[col] = df[col].astype(float)
        return df

    def _calculate_adx(self, df):
        high, low, close = df['high'], df['low'], df['close']
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        up_move = high.diff(); down_move = -low.diff()
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        atr = tr.rolling(self.adx_period).mean()
        plus_di = 100 * (pd.Series(plus_dm).rolling(self.adx_period).mean() / atr)
        minus_di = 100 * (pd.Series(minus_dm).rolling(self.adx_period).mean() / atr)
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
        adx = dx.rolling(self.adx_period).mean().iloc[-1]
        return adx if not pd.isna(adx) else 0.0

    def _get_trend_direction(self, df):
        if len(df) < 50: return "NEUTRAL"
        df = df.copy()
        df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
        ema20_last = df['ema20'].iloc[-1]
        ema50_last = df['ema50'].iloc[-1]
        if pd.isna(ema20_last) or pd.isna(ema50_last):
            return "NEUTRAL"
        if ema20_last > ema50_last: return "UP"
        elif ema20_last < ema50_last: return "DOWN"
        return "NEUTRAL"

    def _detect_breakout(self, df):
        if len(df) < 21: return "NEUTRAL"
        prior = df.iloc[-21:-1]
        if prior.empty: return "NEUTRAL"
        high, low = prior['high'].max(), prior['low'].min()
        close = df['close'].iloc[-1]
        if close > high: return "BREAKOUT_UP"
        elif close < low: return "BREAKOUT_DOWN"
        return "CONSOLIDATION"

    def _calculate_z_scores(self, data):
        volumes = [math.log10(d["volume"]+1) for d in data]
        changes = [d["price_change"] for d in data]
        volatilities = [d["volatility"] for d in data]
        adxs = [d["adx"] for d in data]
        def z(values):
            mean, std = np.mean(values), np.std(values)
            return [0]*len(values) if std == 0 else [(v-mean)/std for v in values]
        z_vol, z_chg, z_vola, z_adx = z(volumes), z(changes), z(volatilities), z(adxs)
        for i, d in enumerate(data):
            d["final_score"] = (z_vol[i]*self.config["Z_SCORE_WEIGHT_VOLUME"] +
                                z_chg[i]*self.config["Z_SCORE_WEIGHT_CHANGE"] +
                                z_vola[i]*self.config["Z_SCORE_WEIGHT_VOLATILITY"] +
                                z_adx[i]*self.config["Z_SCORE_WEIGHT_ADX"])
            if d["breakout"] == "BREAKOUT_UP": d["final_score"] += 0.5
            elif d["breakout"] == "BREAKOUT_DOWN": d["final_score"] -= 0.25
            if d["trend_dir"] == "UP": d["final_score"] += 0.3
            elif d["trend_dir"] == "DOWN": d["final_score"] -= 0.2
        return data

    async def _apply_correlation_penalty(self, data):
        if len(data) < 2: return data
        sym_list = [d["symbol"] for d in data if "closes" in d]
        for i in range(len(sym_list)):
            for j in range(i+1, len(sym_list)):
                s1, s2 = sym_list[i], sym_list[j]
                try:
                    closes1 = next(d["closes"] for d in data if d["symbol"]==s1)
                    closes2 = next(d["closes"] for d in data if d["symbol"]==s2)
                    min_len = min(len(closes1), len(closes2))
                    if min_len < 10:
                        continue
                    c1 = closes1[-min_len:]
                    c2 = closes2[-min_len:]
                    if np.std(c1) == 0 or np.std(c2) == 0:
                        continue
                    corr, _ = pearsonr(c1, c2)
                    if not math.isnan(corr) and abs(corr) > self.config["CORRELATION_THRESHOLD"]:
                        score1 = next(d["final_score"] for d in data if d["symbol"]==s1)
                        score2 = next(d["final_score"] for d in data if d["symbol"]==s2)
                        if score1 < score2:
                            for d in data:
                                if d["symbol"] == s1: d["final_score"] *= self.config["CORRELATION_PENALTY"]
                        else:
                            for d in data:
                                if d["symbol"] == s2: d["final_score"] *= self.config["CORRELATION_PENALTY"]
                except (StopIteration, ValueError, TypeError):
                    # Missing/invalid data for this pair — skip correlation check
                    continue
        return data
