import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
try:
    import pandas as pd
except ImportError:
    pd = None
from src.exchange.balance_cache import get_account
from src.strategies.trade_policy import (
    effective_bracket,
    evaluate_exit,
    ratchet_stops,
)


def _f(s):
    """Tolerant float (handles str from risk_state / JSON round-trips)."""
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def _split_symbol(symbol, quote="USDT"):
    """'B2USDT' -> ('B2', 'USDT'); returns (symbol, '') when no quote suffix."""
    if symbol.endswith(quote):
        return symbol[:-len(quote)], quote
    return symbol, ""


def _min_qty_from_filters(filters):
    try:
        return float(filters.get("LOT_SIZE", {}).get("minQty", "0.00001"))
    except (TypeError, ValueError):
        return 0.00001


def _min_notional_from_filters(filters):
    try:
        return float(filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {})).get("minNotional", 5.0))
    except (TypeError, ValueError):
        return 5.0


def _post_entry_wick_range(klines, entry_ms):
    """(low, high) over candles that OPENED at/after entry_ms.

    Wicks that happened before the entry must never stop a trade out: for a
    dip strategy the entry signal candle's low is below the fresh stop by
    construction. Returns (None, None) when no post-entry candle exists yet
    (caller falls back to the live tick).
    """
    try:
        if not klines:
            return None, None
        min_low = None
        max_high = None
        entry_val = float(entry_ms)
        for row in klines:
            if not isinstance(row, (list, tuple)) or len(row) < 5:
                continue
            try:
                open_time = float(row[0])
                if open_time < entry_val:
                    continue
                low = float(row[3])
                high = float(row[2])
                if min_low is None or low < min_low:
                    min_low = low
                if max_high is None or high > max_high:
                    max_high = high
            except (TypeError, ValueError):
                continue
        if min_low is None or max_high is None:
            return None, None
        return min_low, max_high
    except Exception:
        return None, None

class TradeLogic:
    def __init__(self, config, order_mgr, risk_mgr, signal_gen, trend_detector, db, rest, ws_stream, webhook, health_check=None):
        self.config = config
        self.order_mgr = order_mgr
        self.risk_mgr = risk_mgr
        self.signal_gen = signal_gen
        self.trend_detector = trend_detector
        self.db = db
        self.rest = rest
        self.ws_stream = self._ws_stream_proxy(ws_stream, self.rest)
        self.webhook = webhook
        self.health_check = health_check
        self.logger = logging.getLogger(__name__)
        self.active_trades = {}
        # Last values of each active trade actually written to SQLite, so
        # manage_trade can flush on CHANGE instead of on a wall-clock window
        # (see _persist_active_trade_if_changed) — the web monitor reads this
        # table, so anything left in memory only renders as a stale position.
        self._saved_trade_sig = {}
        self.current_symbols = []
        self.symbol_cooldowns = {}
        self.cooldown = max(10, int(config.get("SIGNAL_INTERVAL", 10)))
        # Futures mode reads POSITION risk (positionAmt) instead of wallet
        # balances — the wallet only holds margin, never the coin itself.
        self.is_futures = config.get("MARKET", "spot") == "futures"
        self.quote_asset = config.get("QUOTE_ASSET", "USDT")
        self.last_atr_update = {}
        self.last_exchange_sync_time = 0
        # Remote-control channel: the web monitor (status.py) writes this JSON
        # file; the engine polls it every cycle. Supports pause/resume and
        # one-shot close_all / close_symbol commands (deduped via command_id).
        self.control_file = config.get("CONTROL_FILE", "./data/engine_control.json")
        self._control = {}
        self._control_mtime = 0.0
        self._last_command_id = None
        # Daily entry cap (overtrading guard): counts entries per UTC day.
        # 0 = unlimited (the intraday_rsi preset sets 1 entry/day).
        self.max_trades_per_day = int(config.get("MAX_TRADES_PER_DAY", 0) or 0)
        self._entries_day_key = None
        self._entries_today = 0
        # Last signal-state blob written to risk_state, so the web monitor always
        # renders the ENGINE's real decision (regime/RSI/trigger) and we skip
        # redundant writes when nothing changed.
        self._last_signal_state_json = None
        # Monotonic counter for the loop heartbeat (see _publish_loop_state).
        self._loop_cycle = 0

    def _read_control(self):
        """Read the control file only when it changed (cheap mtime check)."""
        try:
            mtime = os.path.getmtime(self.control_file)
            if mtime == self._control_mtime:
                return self._control
            with open(self.control_file, "r", encoding="utf-8") as f:
                self._control = json.load(f)
            self._control_mtime = mtime
        except FileNotFoundError:
            self._control = {}
            self._control_mtime = 0.0
        except Exception as e:
            self.logger.warning(f"Could not read engine control file {self.control_file}: {e}")
            self._control = {}
            self._control_mtime = 0.0
        return self._control

    def _write_control(self, data):
        """Atomically persist control state (used to clear executed commands)."""
        try:
            os.makedirs(os.path.dirname(self.control_file) or ".", exist_ok=True)
            tmp = self.control_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, self.control_file)
            self._control = data
            self._control_mtime = os.path.getmtime(self.control_file)
        except Exception as e:
            self.logger.error(f"Failed to write engine control file: {e}")

    async def _process_control_commands(self):
        """Execute one-shot remote commands (close_all / close_symbol).

        Commands are deduplicated via command_id, but a command is only cleared once it
        has fully succeeded. If an exit order is rejected (network outage, dust balance,
        exchange hiccup) close_trade() re-arms the position, so the command stays pending
        and is retried on the next loop iteration instead of silently dropping a position
        the operator asked to liquidate.

        Returns True while a close command is still pending (entries stay blocked).
        """
        control = self._read_control()
        cmd_id = control.get("command_id")
        if not cmd_id:
            return False
        is_new = cmd_id != self._last_command_id
        self._last_command_id = cmd_id

        if control.get("close_all"):
            open_symbols = list(self.active_trades.keys())
            if is_new and open_symbols:
                self.logger.warning(f"REMOTE CONTROL: closing ALL active positions ({len(open_symbols)})")
                await self.webhook.send(f"🛑 REMOTE CONTROL: Close ALL requested ({len(open_symbols)} positions).")
            for symbol in list(open_symbols):
                if symbol in self.active_trades:
                    await self.close_trade(symbol, "REMOTE_CLOSE_ALL")
            # If any exit was rejected and re-armed, keep the command so it retries.
            if any(s in self.active_trades for s in open_symbols):
                self.logger.warning("REMOTE CONTROL: some close-all exits were rejected; command stays pending and will retry.")
                return True
            control.pop("close_all", None)
            control.pop("command_id", None)
            self._write_control(control)
            return False

        if control.get("close_symbol"):
            symbol = str(control.get("close_symbol", "")).strip().upper()
            if is_new:
                self.logger.warning(f"REMOTE CONTROL: closing {symbol}")
                await self.webhook.send(f"🛑 REMOTE CONTROL: Close {symbol} requested.")
            if symbol in self.active_trades:
                await self.close_trade(symbol, "REMOTE_CLOSE")
                if symbol in self.active_trades:
                    self.logger.warning(f"REMOTE CONTROL: exit rejected for {symbol}; command stays pending and will retry.")
                    return True
            control.pop("close_symbol", None)
            control.pop("command_id", None)
            self._write_control(control)
            return False

        # Stale/malformed command (no recognized action): clear it so it never wedges.
        control.pop("command_id", None)
        control.pop("close_all", None)
        control.pop("close_symbol", None)
        self._write_control(control)
        return False

    def _ws_stream_proxy(self, stream, rest):
        """Wrap the stream client so price reads transparently fall back to REST
        when the public WebSocket is disconnected or the tick cache is stale."""
        class _StreamProxy:
            def __init__(self, stream, rest):
                self._stream = stream
                self._rest = rest

            async def _fallback_price(self, symbol):
                try:
                    ticker = await self._rest.get_ticker(symbol)
                    return float(ticker["price"])
                except Exception:
                    return None

            async def get_current_price(self, symbol):
                price = None
                if self._stream is not None and self._stream.is_connected():
                    price = await self._stream.get_current_price(symbol)
                if not price:
                    price = await self._fallback_price(symbol)
                return price

            def is_connected(self):
                return self._stream is not None and self._stream.is_connected()

            def __getattr__(self, name):
                return getattr(self._stream, name)

        return _StreamProxy(stream, rest)

    def _is_valid_symbol(self, symbol):
        base = symbol[:-len(self.config["QUOTE_ASSET"])] if symbol.endswith(self.config["QUOTE_ASSET"]) else symbol
        return bool(re.match(r'^[A-Z0-9]+$', base))

    def _ws_streams_health(self):
        """Snapshot of every realtime transport, for the web monitor."""
        def _age_ms(t):
            try:
                t = float(t)
                return int(max(0.0, time.time() * 1000 - t))
            except (TypeError, ValueError):
                return None
        arr = {"connected": False, "last_frame_age_s": None, "transport": ""}
        try:
            arr["connected"] = bool(self.ws_stream.is_arr_stream_alive())
            # Provenance: 'ws' when frames flow, 'rest' on the fallback refresher
            # (network paths that drop fstream data frames), '' when stale/empty.
            arr["transport"] = (getattr(self.ws_stream, "arr_transport", None) or (lambda: ""))()
            ages = [_age_ms(v.get("time")) for v in self.ws_stream.get_all_tickers().values()]
            ages = [a for a in ages if a is not None]
            if ages:
                arr["last_frame_age_s"] = round(min(ages) / 1000, 1)
        except Exception:
            pass
        api = {"connected": False, "user_stream": False}
        try:
            ws_api = getattr(self.order_mgr, "ws_api", None)
            api["connected"] = bool(ws_api and ws_api.is_connected())
            api["user_stream"] = bool(ws_api and getattr(ws_api, "user_stream_active", False))
            # Clock-sync provenance for the web monitor: the engine's measured
            # offset vs the exchange clock. Preferred source is the WS API
            # client (midpoint estimator, see WSApiClient.sync_time); when it
            # has no measurement (paper mode, or live without an Ed25519 key)
            # fall back to the REST client's always-on sync — the server-time
            # endpoint is unauthenticated, so the offset is measurable in
            # EVERY mode.
            offset = getattr(ws_api, "time_offset", None)
            last_sync = getattr(ws_api, "last_time_sync", 0)
            interval = getattr(ws_api, "TIME_SYNC_INTERVAL_S", 300)
            if not isinstance(offset, (int, float)):
                rest_off = getattr(self.rest, "time_offset", None)
                if isinstance(rest_off, (int, float)) and (time.time() - getattr(self.rest, "last_time_sync", 0)) <= 600:
                    offset = rest_off
                    last_sync = getattr(self.rest, "last_time_sync", 0)
                    interval = 300
            api["time_offset_ms"] = round(offset) if isinstance(offset, (int, float)) else None
            api["clock_synced"] = bool(
                isinstance(offset, (int, float)) and (time.time() - last_sync) <= interval * 2
            )
        except Exception:
            pass
        market = {"connected": False, "symbols": 0}
        try:
            market["connected"] = bool(self.ws_stream.is_connected())
            market["symbols"] = len(getattr(self.ws_stream, "_subscribed_symbols", []) or [])
        except Exception:
            pass
        return {
            "market": market,
            "all_tickers": arr,
            "order_api": api,
            # Publish time: the monitor uses it to tell "stream down" from
            # "engine stopped republishing" (a dead engine leaves the last
            # snapshot in the DB, which would otherwise keep the lights green).
            "updated_ms": int(time.time() * 1000),
        }

    def _ws_balances_snapshot(self):
        """Live account balances for the web monitor, served from the WS cache.

        The user-data stream (outboundAccountPosition) keeps a merged account
        snapshot (REST seeds + WS deltas) on the WS API client; this publishes it
        with USD valuations and provenance so the dashboard shows the same
        balances the engine trades on — without the monitor taking its own REST
        account snapshot. Falls back to the risk manager's last equity snapshot
        when no WS cache exists (paper mode / user stream inactive).
        """
        quote = self.config.get("QUOTE_ASSET", "USDT")
        now_ms = int(time.time() * 1000)
        ws_api = None
        try:
            ws_api = getattr(self.order_mgr, "ws_api", None)
        except Exception:
            ws_api = None

        prices = {}
        try:
            for sym, t in (self.ws_stream.get_all_tickers() or {}).items():
                p = float(t.get("price") or 0)
                if p > 0:
                    prices[sym] = p
        except Exception:
            prices = {}

        raw = {}
        source = "none"
        age_s = None
        user_stream = False
        try:
            if ws_api is not None:
                user_stream = bool(getattr(ws_api, "user_stream_active", False))
                age_s = ws_api.balance_age_s()
                raw = dict(getattr(ws_api, "balances", {}) or {})
                if raw:
                    limit = float(self.config.get("WS_BALANCE_MAX_AGE", 90) or 90)
                    # 'ws' only while the cache is trustworthy; older than the
                    # limit it is a stale last-known value and is labelled so.
                    source = "ws" if (user_stream and age_s is not None and age_s <= limit) else "stale"
        except Exception:
            raw = {}

        if not raw:
            # No WS cache (paper mode, or the user stream is down): reuse the
            # risk manager's last equity snapshot, which itself is WS-first.
            for b in (getattr(self.risk_mgr, "balances_summary", None) or []):
                try:
                    asset = str(b.get("asset") or "")
                    if not asset:
                        continue
                    raw[asset] = {"free": float(b.get("free") or 0),
                                  "locked": float(b.get("locked") or 0),
                                  "usd_value": float(b.get("usd_value") or 0)}
                except (TypeError, ValueError):
                    continue
            if raw and source == "none":
                source = "rest"

        balances = []
        for asset, v in raw.items():
            try:
                free = float(v.get("free") or 0)
                locked = float(v.get("locked") or 0)
            except (TypeError, ValueError):
                continue
            total = free + locked
            if total <= 0:
                continue
            if asset == quote:
                usd_val = total
            elif "usd_value" in v:
                usd_val = float(v.get("usd_value") or 0)
            else:
                usd_val = total * prices.get(asset + quote, 0.0)
            balances.append({
                "asset": asset, "free": free, "locked": locked,
                "total": total, "usd_value": round(usd_val, 4)
            })
        balances.sort(key=lambda x: (x["asset"] != quote, -x["usd_value"]))
        return {
            "source": source,
            "age_s": round(age_s, 2) if age_s is not None else None,
            "user_stream": user_stream,
            "updated_ms": now_ms,
            "quote_asset": quote,
            "balances": balances,
        }

    async def update_symbols(self):
        if self.config["DYNAMIC_SYMBOLS"]:
            candidates = await self.trend_detector.get_top_symbols()
            active_symbols = set(self.active_trades.keys())
            new_symbols = [s for s in candidates if s not in active_symbols]
            allowed_new = max(0, self.config["MAX_SYMBOLS"] - len(active_symbols))
            new_symbols = list(active_symbols) + new_symbols[:allowed_new]
        else:
            # In static mode, always retain any currently active trades so they stay monitored
            active_symbols = list(self.active_trades.keys())
            new_symbols = list(dict.fromkeys(active_symbols + self.config["STATIC_SYMBOLS"]))

        valid_symbols = []
        for s in new_symbols:
            if not self._is_valid_symbol(s):
                self.logger.warning(f"Invalid symbol format ignored: {s}")
                continue
            info = await self.rest.get_symbol_info(s)
            if info and info.get("status") == "TRADING":
                valid_symbols.append(s)
            elif s in self.active_trades:
                # If an active trade is open, keep it in valid_symbols even if info lookup had a glitch
                valid_symbols.append(s)
            else:
                self.logger.warning(f"Symbol {s} not tradable; skipping.")
        if set(valid_symbols) != set(self.current_symbols):
            self.logger.info(f"Symbols updated: {valid_symbols}")
            old_symbols = set(self.current_symbols)
            self.current_symbols = valid_symbols
            if not self.config["PAPER_TRADE"] and self.ws_stream.is_connected():
                add_syms = [s for s in valid_symbols if s not in old_symbols]
                remove_syms = [s for s in old_symbols if s not in valid_symbols and s not in self.active_trades]
                if add_syms: await self.ws_stream.subscribe(add_syms)
                if remove_syms: await self.ws_stream.unsubscribe(remove_syms)

        # Persist monitored symbols and scanned pairs to SQLite for status.py and web monitor
        try:
            await self.db.set_risk_state("monitored_symbols", json.dumps(self.current_symbols))
            scanned = self.trend_detector.get_last_scanned()
            if scanned:
                await self.db.set_risk_state("scanned_pairs", json.dumps(scanned))
        except Exception as e:
            self.logger.debug(f"Could not persist scanned pairs to db: {e}")

    async def refresh_symbols_loop(self):
        """Two-cadence screener maintenance loop.

        Fast cadence (PRICE_REFRESH_INTERVAL): refresh screener prices in place
        with ONE cheap bulk REST request and re-persist scanned_pairs, so the
        web monitor's prices track the exchange between full scans instead of
        drifting for up to SYMBOL_REFRESH_INTERVAL.
        Slow cadence (SYMBOL_REFRESH_INTERVAL): full rescan — fresh klines,
        scoring and pair selection.
        """
        price_every = max(10, int(self.config.get("PRICE_REFRESH_INTERVAL", 10)))
        full_every = max(60, int(self.config["SYMBOL_REFRESH_INTERVAL"]))
        if price_every > full_every:
            price_every = full_every
        elapsed = 0
        while True:
            await asyncio.sleep(price_every)
            elapsed += price_every
            try:
                if elapsed >= full_every:
                    elapsed = 0
                    await self.update_symbols()
                else:
                    # Realtime first: prices from the all-market WS ticker stream
                    # (~1s old, zero REST weight). REST bulk request only when
                    # the stream is down (cache stale/empty).
                    cache = None
                    try:
                        if self.ws_stream.is_arr_stream_alive():
                            cache = self.ws_stream.get_all_tickers()
                    except Exception:
                        cache = None
                    updated = await self.trend_detector.refresh_prices(cache)
                    if updated:
                        scanned = self.trend_detector.get_last_scanned()
                        if scanned:
                            await self.db.set_risk_state("scanned_pairs", json.dumps(scanned))
                    # Publish WS transport health + live balances for the web
                    # monitor (balances come from the WS user-data cache).
                    try:
                        await self.db.set_risk_state("ws_streams", json.dumps(self._ws_streams_health()))
                    except Exception:
                        pass
                    try:
                        await self.db.set_risk_state("ws_balances", json.dumps(self._ws_balances_snapshot()))
                    except Exception:
                        pass
                    # Futures account state (positions/liq/leverage/funding) —
                    # published only when MARKET=futures; spot boots skip it.
                    if self.config.get("MARKET", "spot") == "futures":
                        try:
                            await self.db.set_risk_state("futures_state", json.dumps(await self._futures_state_snapshot()))
                        except Exception:
                            pass
            except Exception as e:
                self.logger.warning(f"Screener refresh cycle failed: {e}")

    # ------------------------------------------------------------------
    # Futures position helpers — the futures wallet never holds the base
    # coin (BUYs open a POSITION), so every balance-shaped check must read
    # positionAmt on futures. Centralized here so reconciliation, sizing,
    # exits and orphan adoption all agree.
    # ------------------------------------------------------------------
    async def _futures_position_amount(self, symbol):
        """positionAmt for symbol on futures (0.0 when flat/unavailable)."""
        try:
            rows = await self.rest.get_position_risk(symbol) or []
        except Exception:
            return 0.0
        for r in rows:
            if r.get("symbol") == symbol:
                try:
                    return float(r.get("positionAmt") or 0.0)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    async def _current_position_qty(self, symbol):
        """Tracked qty for our long on this symbol, per market.

        Futures: positionAmt from positionRisk (user-data WS deltas mirror it
        into the same number between REST reads). Spot: free wallet balance.
        """
        if self.is_futures:
            return await self._futures_position_amount(symbol)
        account = await get_account(self.rest, self.order_mgr.ws_api, self.config)
        base, _ = _split_symbol(symbol, self.quote_asset)
        return next((_f(b.get("free")) for b in account.get("balances", [])
                      if b.get("asset") == base), 0.0)

    async def _read_positions_map(self):
        """{SYMBOLUSDT: qty} of every non-flat futures position."""
        out = {}
        try:
            rows = await self.rest.get_position_risk() or []
        except Exception:
            return out
        for r in rows:
            try:
                amt = float(r.get("positionAmt") or 0.0)
            except (TypeError, ValueError):
                continue
            if abs(amt) > 1e-12 and r.get("symbol"):
                out[r["symbol"]] = amt
        return out

    async def _verify_recent_futures_qty(self, symbol, want_qty, window_ms):
        """True when OUR recent futures fills on symbol sum to ~want_qty bought.

        Uses /fapi/v1/userTrades (isBuyer, real fills) — proof-based, so manual
        positions are never adopted.
        """
        try:
            trades = await self.rest.get_user_trades(symbol, limit=200) or []
        except Exception:
            return False
        cutoff = int(time.time() * 1000) - window_ms
        # fapi names the field 'buyer' (spot's /api/v3/myTrades uses 'isBuyer');
        # accepting either keeps this correct if the client shape changes.
        bought = sum(float(t.get("qty") or 0.0) for t in trades
                     if (t.get("buyer") or t.get("isBuyer"))
                     and int(t.get("time") or 0) >= cutoff)
        return abs(bought - want_qty) <= max(want_qty * 0.01, 1e-9)

    async def _futures_state_snapshot(self):
        """Futures account/position state for the web monitor.

        Sources: the WS user-data cache (positions/balances, realtime, no REST
        weight) when live; in paper mode the DB's own open trades. Reads
        positionRisk REST only in live mode when the WS cache is empty.
        """
        state = {
            "positions": [],
            "leverage": int(self.config.get("FUTURES_LEVERAGE", 1) or 1),
            "margin_type": self.config.get("FUTURES_MARGIN_TYPE", "ISOLATED"),
            "one_way": bool(self.config.get("FUTURES_ONE_WAY_MODE", True)),
            "updated_ms": int(time.time() * 1000),
        }
        ws_api = getattr(self.order_mgr, "ws_api", None)
        positions = []
        cached = getattr(ws_api, "positions", None) if ws_api else None
        if not cached and self.active_trades and not self.config.get("PAPER_TRADE", False):
            # User-data WS cache not populated yet (ACCOUNT_UPDATE only fires on
            # a change — a position opened before this boot leaves it empty).
            # Fall back to positionRisk so the dashboard never shows a naked
            # "no open positions" while a managed trade exists.
            try:
                rows = await self.rest.get_position_risk() or []
                cached = {r.get("symbol"): {"amount": r.get("positionAmt"),
                                            "entry_price": r.get("entryPrice"),
                                            "unrealized_pnl": r.get("unRealizedProfit"),
                                            "leverage": r.get("leverage"),
                                            "margin_type": r.get("marginType")}
                          for r in rows if abs(float(r.get("positionAmt") or 0)) > 1e-12}
            except Exception:
                cached = None
        if cached:
            for sym, pos in cached.items():
                try:
                    positions.append({
                        "symbol": sym,
                        "amount": float(pos.get("amount") or 0),
                        "entry_price": float(pos.get("entry_price") or 0),
                        "unrealized_pnl": float(pos.get("unrealized_pnl") or 0),
                        "leverage": pos.get("leverage"),
                        "margin_type": pos.get("margin_type"),
                    })
                except (TypeError, ValueError):
                    continue
        elif not self.config.get("PAPER_TRADE", False):
            try:
                rows = await self.rest.get_position_risk()
                for r in rows or []:
                    amt = float(r.get("positionAmt") or 0)
                    if abs(amt) < 1e-12:
                        continue
                    positions.append({
                        "symbol": r.get("symbol"),
                        "amount": amt,
                        "entry_price": float(r.get("entryPrice") or 0),
                        "unrealized_pnl": float(r.get("unRealizedProfit") or 0),
                        "leverage": r.get("leverage"),
                        "margin_type": r.get("marginType"),
                        "liquidation_price": float(r.get("liquidationPrice") or 0) or None,
                        "mark_price": float(r.get("markPrice") or 0) or None,
                    })
            except Exception as e:
                self.logger.debug(f"futures positionRisk read failed: {e}")
        # Paper mode: mirror the DB's open trades as flat positions so the
        # dashboard has something honest to render (no invented liq prices).
        if not positions and self.config.get("PAPER_TRADE", False):
            try:
                for sym, trade in (self.active_trades or {}).items():
                    positions.append({
                        "symbol": sym,
                        "amount": float(trade.get("quantity") or 0),
                        "entry_price": float(trade.get("entry_price") or 0),
                        "unrealized_pnl": None,
                        "leverage": int(self.config.get("FUTURES_LEVERAGE", 1) or 1),
                        "margin_type": self.config.get("FUTURES_MARGIN_TYPE", "ISOLATED"),
                        "paper": True,
                    })
            except Exception:
                pass
        state["positions"] = positions
        return state

    async def _persist_signal_state(self):
        """Publish the engine's latest per-symbol signal snapshots to SQLite.

        The web monitor reads this via /api/status (and the /ws push), so the
        dashboard shows the ACTUAL regime/RSI/trigger state the engine decided on
        rather than re-deriving anything in the browser. Only written when the
        payload changes (the decision cadence is every SIGNAL_INTERVAL).
        """
        try:
            signals = self.signal_gen.get_last_signals()
            if not signals:
                return
            payload = json.dumps(signals)
            if payload == self._last_signal_state_json:
                return
            await self.db.set_risk_state("signal_state", payload)
            self._last_signal_state_json = payload
        except Exception as e:
            self.logger.debug(f"Could not persist signal state: {e}")

    async def run(self):
        while True:
            # Remote web-monitor commands first so emergency closes always win
            # over every other gate below. Returns True while a close command is
            # still being retried (some exits were rejected) — keep blocking new
            # entries until the liquidation fully completes.
            pending_close = await self._process_control_commands()

            control = self._read_control()
            if control.get("paused") or pending_close:
                # Web-monitor pause: block NEW entries but keep managing open
                # positions so stops/TPs/trailing stays armed while the operator
                # reviews the market. The same holds while a remote close command
                # is still retrying rejected exits.
                self.logger.debug("Web-monitor pause ACTIVE — new entries blocked; managing open positions only.")
                for symbol in list(self.active_trades.keys()):
                    try:
                        await self.manage_trade(symbol)
                    except Exception as e:
                        self.logger.error(f"Error managing {symbol} during web pause: {e}")
                await self._publish_loop_state("paused", paused_requested=True, paused_applied=True)
                await asyncio.sleep(self.config["SIGNAL_INTERVAL"])
                continue

            if self.health_check and self.health_check.pause_trading:
                self.logger.warning("Trading paused by health check")
                await self._publish_loop_state("health-pause", paused_applied=True)
                await asyncio.sleep(30)
                continue
            if not self.config["PAPER_TRADE"]:
                now = time.time()
                if now - self.last_exchange_sync_time >= 60:
                    await self.sync_positions_from_exchange()
                    await self.risk_mgr._fetch_equity()
                    await self.risk_mgr.save_state()
                    self.last_exchange_sync_time = now
            if not self.current_symbols:
                await self.update_symbols()

            # 1. ALWAYS manage all open active trades first to guarantee stops, TPs, trailing stops
            # and time stops are evaluated every single cycle regardless of whether the symbol is in current_symbols.
            for symbol in list(self.active_trades.keys()):
                try:
                    await self.manage_trade(symbol)
                except Exception as e:
                    self.logger.error(f"Error managing active trade {symbol}: {e}")

            # 2. Evaluate un-entered symbols for potential entry
            for symbol in self.current_symbols:
                if symbol in self.active_trades:
                    continue
                try:
                    await self.process_symbol(symbol)
                except Exception as e:
                    self.logger.error(f"Error processing {symbol}: {e}")
                    await asyncio.sleep(1)
            # Publish the real signal state for the web monitor (no-op when unchanged).
            await self._persist_signal_state()
            await self._publish_loop_state("trading")
            await asyncio.sleep(self.config["SIGNAL_INTERVAL"])

    async def _publish_loop_state(self, phase, paused_requested=False, paused_applied=False):
        """Publish a decision-loop HEARTBEAT so the monitor can tell "the engine is
        deliberately skipping entries" from "the engine stopped looping".

        The dashboard's "Decision loop" light used to be inferred from the newest
        per-symbol signal snapshot, whose timestamp only advances when a symbol gets
        PAST every gate: `process_symbol` returns before `generate_signal` for active
        trades, cooling-down symbols, a full slot list and a tripped breaker, and the
        pause branch never evaluates symbols at all. A perfectly healthy engine could
        therefore report a frozen loop within one cycle — pausing from the dashboard
        was enough to make it claim the strategy loop was wedged, and so was holding a
        full book. Liveness has to be reported by the thing that is alive: this row is
        written once per iteration on EVERY path (trading, paused, health-pause).

        One small row per cycle (~10s) — deliberately never skipped when unchanged,
        unlike signal_state, because a heartbeat that coalesces is not a heartbeat.
        """
        self._loop_cycle += 1
        snap = {
            "cycle_ms": int(time.time() * 1000),
            "cycle": self._loop_cycle,
            "interval_s": int(self.config.get("SIGNAL_INTERVAL", 10) or 10),
            "phase": phase,
            "paused_requested": bool(paused_requested),
            "paused_applied": bool(paused_applied),
            "active_trades": len(self.active_trades),
            "symbols": len(self.current_symbols),
        }
        try:
            await self.db.set_risk_state("loop_state", json.dumps(snap))
        except Exception as e:
            # Never let monitor telemetry disturb trading.
            self.logger.debug(f"Could not publish loop heartbeat: {e}")

    async def process_symbol(self, symbol):
        self.logger.debug(f"Processing symbol {symbol}")
        if symbol in self.active_trades:
            self.logger.debug(f"{symbol} already active, managing...")
            await self.manage_trade(symbol)
            return
        if len(self.active_trades) >= self.config["MAX_SYMBOLS"]:
            self.logger.debug(f"Max active trades reached ({len(self.active_trades)}/{self.config['MAX_SYMBOLS']}). Skipping {symbol}")
            return
        if symbol in self.symbol_cooldowns and time.time() < self.symbol_cooldowns[symbol]:
            self.logger.debug(f"{symbol} in cooldown until {self.symbol_cooldowns[symbol]}")
            return
        unrealized = await self.calculate_unrealized_pnl()
        risk_ok = await self.risk_mgr.check_risk(symbol, unrealized)
        self.logger.debug(f"Risk check for {symbol}: {'PASSED' if risk_ok else 'FAILED'}")
        if not risk_ok: return
        signal, atr = await self.signal_gen.generate_signal(symbol)
        self.logger.debug(f"Signal for {symbol}: {signal}, ATR={atr:.6f}")
        if signal == "NEUTRAL":
            self.logger.debug(f"{symbol} signal NEUTRAL, skipping entry")
            return
        if signal == "SELL":
            self.logger.debug(f"{symbol} signal SELL (ignored in spot mode)")
            return
        # Bucket latch: one BUY attempt per RSI bucket per symbol — a retry
        # inside the same bucket can only be a duplicate of a dead/resolving
        # entry, never a fresh signal (signals only change at bucket closes).
        # Latch BEFORE the attempt: success or fail, the attempt consumes the
        # bucket. Failed attempts get a fresh chance at the next bucket.
        bucket_ms = int(self.config.get("RSI_TIMEFRAME_MS", 3_600_000)) or 3_600_000
        self._bucket_entry_latch = getattr(self, "_bucket_entry_latch", {})
        if self._bucket_entry_latch.get(symbol) == int(time.time() * 1000 // bucket_ms):
            self.logger.debug(f"{symbol}: BUY already attempted this RSI bucket; skipping re-fire.")
            return
        self._bucket_entry_latch[symbol] = int(time.time() * 1000 // bucket_ms)
        self.logger.info(f"BUY signal for {symbol}, entering trade...")
        await self.enter_trade(symbol, signal, atr)
        self.symbol_cooldowns[symbol] = time.time() + self.cooldown

    def _compute_bracket(self, entry_price, atr=None):
        """Bullish bracket (stop_price, take_profit) — delegated to the SHARED policy.

        rsi_dip is the ONLY strategy. The bracket is built by
        `trade_policy.effective_bracket` — the exact function the backtest replays
        — so the proven levels and the live levels can never disagree: fixed %
        by default (SL_PERCENT / TP_PERCENT), or a volatility-adaptive ATR stop
        when SL_ATR_MULTIPLIER > 0. The TP is floored at MIN_TP_PERCENT and
        widened to MIN_RISK_REWARD so a tiny bracket can never be fee-negative.
        """
        return effective_bracket(entry_price, self.config, atr)

    async def enter_trade(self, symbol, signal, atr):
        # Professional guard: only process bullish entries in spot mode.
        if signal != "BUY":
            self.logger.debug(f"{symbol}: enter_trade called with signal={signal}; spot bullish-only engine ignores it.")
            return
        # Daily entry cap: reset the counter on a new UTC day, then block
        # further entries once MAX_TRADES_PER_DAY is reached (0 = unlimited).
        # The counter is PERSISTED (risk_state) and restored at boot: keeping it
        # in memory only meant a mid-day PM2 restart handed the engine a fresh
        # allowance, silently doubling the proven 1-entry/day discipline.
        today_key = datetime.now(timezone.utc).date()
        if self._entries_day_key != today_key:
            self._entries_day_key = today_key
            self._entries_today = 0
            await self._save_entry_cap()
        if self.max_trades_per_day > 0 and self._entries_today >= self.max_trades_per_day:
            self.logger.info(f"{symbol}: daily entry cap reached ({self._entries_today}/{self.max_trades_per_day}); entry skipped.")
            return
        # Funding-rate gate (futures only): a long PAYS funding when the rate is
        # positive. Skipping pairs whose live rate exceeds FUNDING_RATE_MAX frees
        # capital for pairs where funding is neutral or pays the long. 0 disables.
        if self.is_futures:
            try:
                fr_max = float(self.config.get("FUNDING_RATE_MAX", 0) or 0)
            except (TypeError, ValueError):
                fr_max = 0.0
            if fr_max > 0:
                try:
                    px = await self.rest.get_premium_index(symbol)
                    fr = float((px if isinstance(px, dict) else {}).get("lastFundingRate", 0) or 0)
                    if fr > fr_max:
                        self.logger.info(
                            f"{symbol}: funding {fr*100:.4f}% > cap {fr_max*100:.4f}% — long would pay; entry skipped.")
                        return
                except Exception as e:
                    # Unreadable funding must never block a proven setup.
                    self.logger.debug(f"{symbol}: funding check unavailable ({e}); proceeding.")
        price = await self.ws_stream.get_current_price(symbol)
        if not price:
            self.logger.warning(f"{symbol}: no live price available (WS stale & REST ticker failed); skipping entry.")
            return
        entry_price = price
        # Fixed-% bracket (the strategy's own levels; MIN_TP_PERCENT floor and
        # MIN_RISK_REWARD widening are applied inside the helper). Both legs are
        # software-managed MARKET exits, so both pay the taker fee — the backtest
        # models the same rate (no maker TP assumption).
        stop_price, take_profit = self._compute_bracket(entry_price, atr)
        side = "BUY"

        # Hard cap total capital deployed (equity * BALANCE_USAGE_PERCENT).
        # Computed AFTER the live price is known so the cap shares the same benchmark.
        deployed = sum(t["quantity"] * t["entry_price"] for t in self.active_trades.values())
        remaining = self.config["BALANCE_USAGE_PERCENT"] * self.risk_mgr.total_equity - deployed
        if remaining <= 0:
            self.logger.info(f"{symbol}: no remaining allocation headroom (${remaining:.2f}); skipping entry.")
            return

        qty = await self.risk_mgr.calculate_position_size(symbol, entry_price, stop_price)
        if not qty or qty <= 0:
            self.logger.info(f"{symbol}: position size 0 (equity/fee/minNotional caps) — entry skipped.")
            return
        # Enforce the total-capital cap after sizing (planned notional vs remaining headroom)
        if qty * entry_price > remaining:
            self.logger.info(f"{symbol}: planned notional ${qty * entry_price:.2f} exceeds remaining allocation headroom ${remaining:.2f}; skipping entry.")
            return
        order_id = await self.order_mgr.place_market_order(symbol, side, qty, expected_price=entry_price)
        if order_id is None: return
        self._entries_today += 1
        await self._save_entry_cap()
        self.logger.info(f"Entry market order placed: {order_id} for {symbol}")
        filled, executed_qty = await self.order_mgr.wait_for_fill(symbol, order_id)
        if not filled:
            self.logger.warning(f"Entry order {order_id} not filled. Aborting.")
            return
        if executed_qty and executed_qty > 0:
            if executed_qty < qty:
                self.logger.warning(f"Partial fill: {executed_qty} of {qty}. Adjusting position size.")
                qty = executed_qty
            if not self.config["PAPER_TRADE"]:
                try:
                    order_info = await self.rest.get_order(symbol, order_id)
                    avg_price = float(order_info.get("avgPrice", order_info.get("price", entry_price)))
                    if avg_price > 0:
                        entry_price = avg_price
                        # Re-anchor the bracket on the ACTUAL fill price with the
                        # SAME policy math as the pre-trade bracket.
                        stop_price, take_profit = self._compute_bracket(entry_price, atr)
                except Exception as e:
                    self.logger.warning(f"Could not fetch avg fill price: {e}")

                # Verify the actual position/exchange qty credited (spot taker fee
                # is deducted from the base asset; futures fees hit USDT margin)
                try:
                    live_qty = await self._current_position_qty(symbol)
                    if live_qty and 0 < live_qty < qty:
                        self.logger.info(f"{symbol}: Net position after fees is {live_qty}. Updating tracked size from {qty} to {live_qty}.")
                        qty = live_qty
                except Exception as e:
                    self.logger.warning(f"Could not verify net position size: {e}")

        trade = {
            "symbol": symbol, "entry_price": entry_price, "side": side, "quantity": qty,
            "entry_time": time.time(), "stop_price": stop_price, "take_profit": take_profit,
            # R-multiple anchor for the scale-out check. The breakeven lock raises
            # stop_price to entry*1.0025 long before +1R in every preset, which made
            # (entry - stop_price) negative and permanently disabled scale-out.
            # Measuring R against the INITIAL stop keeps both features working.
            "initial_stop_price": stop_price,        "atr": atr, "trailing_active": False, "trailing_stop": stop_price,
                "breakeven_activated": False, "order_id": order_id,
                "initial_qty": qty,
                "scale_out_done": False,
            }
        # Anti-spam latch: mark this RSI bucket as already-attempted for the
        # symbol. The cooldown (one monitor interval) alone allowed log-flooding
        # re-fire storms when an entry died between order and tracking (the
        # futures orphan bug did exactly that, 12 times in a row).
        bucket_ms = int(self.config.get("RSI_TIMEFRAME_MS", 3_600_000)) or 3_600_000
        self._bucket_entry_latch = getattr(self, "_bucket_entry_latch", {})
        self._bucket_entry_latch[symbol] = int(time.time() * 1000 // bucket_ms)
        self.active_trades[symbol] = trade
        await self.db.save_active_trade(trade)
        self._mark_trade_persisted(symbol, trade)
        await self.webhook.send(f"ENTRY {symbol} ({side}) Price: {entry_price:.2f} SL: {stop_price:.2f} TP: {take_profit:.2f} Size: {qty:.4f} ID: {order_id}")

    async def manage_trade(self, symbol):
        trade = self.active_trades[symbol]
        price = await self.ws_stream.get_current_price(symbol)
        if not price:
            try:
                ticker = await self.rest.get_ticker(symbol)
                price = float(ticker["price"])
            except Exception:
                return
        now = time.time()
        if symbol not in self.last_atr_update or (now - self.last_atr_update[symbol]) > 1800:
            klines = await self.rest.get_klines(symbol, self.config["TIMEFRAME"], 100)
            if klines:
                atr = await self._calculate_atr_from_klines(klines)
                if atr > 0:
                    trade["atr"] = atr
                    self.last_atr_update[symbol] = now

        # ---- exit decision (ONE policy: trade_policy.evaluate_exit) ----
        # Evidence is normalised to what the engine actually observes between
        # polls: the wick range of the post-entry candles — so a wick that spiked
        # through the stop between cycles is still caught — folded together with
        # the live tick, plus the tick as the price a market order placed right now
        # would fill at. evaluate_exit is the exact function the backtest replays,
        # so the ordering (stop → TP → time stop → day-end → scale-out) and the
        # exit reasons cannot drift from the backtested model. Nothing here
        # short-circuits before the policy: a missing kline response degrades to
        # tick evidence instead of skipping the stop check entirely.
        entry_ms = float(trade.get("entry_time", 0)) * 1000.0
        recent = await self.rest.get_klines(symbol, self.config["TIMEFRAME"], 2)
        # Only wicks that occurred AFTER the entry may stop us out (see
        # _post_entry_wick_range); with no post-entry candle yet the tick is the
        # only evidence there is.
        low, high = _post_entry_wick_range(recent, entry_ms) if recent else (None, None)
        if low is None or high is None:
            low = high = price
        else:
            low, high = min(low, price), max(high, price)

        # intraday_rsi: force-close positions at the UTC day end so every trade
        # matches the backtest's same-day-exit convention. The trigger is the only
        # venue-specific part (a wall clock here, the first bar of a new UTC day in
        # a replay), which is why the policy takes it as an input; the ORDER — after
        # the stop/TP checks, so a breach in the same cycle always wins — is shared.
        eod = False
        if self.config.get("CLOSE_AT_UTC_DAY_END", False):
            now_utc = datetime.now(timezone.utc)
            secs_into_day = now_utc.hour * 3600 + now_utc.minute * 60 + now_utc.second
            eod = secs_into_day >= 86100   # last 5 minutes of the UTC day (23:55:00)

        plan = evaluate_exit(
            trade["entry_price"], trade["quantity"], trade["stop_price"],
            trade["take_profit"], self.config,
            low=low, high=high, reference_price=price,
            now_ms=now * 1000.0, entry_ms=entry_ms,
            trailing_stop=trade.get("trailing_stop"),
            trailing_active=trade.get("trailing_active", False),
            initial_stop_price=trade.get("initial_stop_price"),
            scale_out_done=trade.get("scale_out_done", False),
            eod=eod,
        )
        if plan.reason:
            if plan.reason == "EOD_CLOSE":
                self.logger.info(f"{symbol}: UTC day end (intraday close) — closing position.")
            await self.close_trade(symbol, plan.reason, fill_override=plan.fill)
            return
        # Scale-out: banks a partial profit at +1R and keeps the runner open, so it
        # is the only non-terminal outcome. Long-only (the policy's R maths assumes
        # a long bracket).
        if plan.partial_units > 0 and trade.get("side") == "BUY":
            await self._scale_out(symbol, trade, plan.partial_units)

        # Profit-protection ladder (shared policy: breakeven lock, then trailing
        # stop). Ratchets only ever RAISE the stop, and they apply from the NEXT
        # evaluation — the decision above always used the stop as it stood when the
        # cycle began, which is exactly the backtest's pessimistic convention.
        # The favourable extreme fed in is `high` — the same observable the backtest
        # feeds as its bar high — not the bare tick, so both sides ratchet the trail
        # off the most favourable price seen rather than the last one sampled.
        was_trailing = bool(trade.get("trailing_active"))
        was_breakeven = bool(trade.get("breakeven_activated"))
        prev_stop = float(trade["stop_price"])
        updates = ratchet_stops(
            trade["entry_price"], high, trade["stop_price"], self.config,
            trailing_stop=trade.get("trailing_stop"),
            trailing_active=was_trailing,
            breakeven_activated=was_breakeven,
            atr=trade.get("atr"),
        )
        trade["stop_price"] = updates["stop_price"]
        trade["trailing_stop"] = updates["trailing_stop"]
        trade["trailing_active"] = updates["trailing_active"]
        trade["breakeven_activated"] = updates["breakeven_activated"]

        if not was_breakeven and updates["breakeven_activated"]:
            self.logger.info(f"Breakeven locked for {symbol} at {trade['stop_price']:.4f} (fees covered).")
            await self.webhook.send(f"🛡️ Breakeven lock engaged for {symbol} at {trade['stop_price']:.4f} (fees covered).")
        if not was_trailing and updates["trailing_active"]:
            profit_pct = (price - trade["entry_price"]) / trade["entry_price"]
            self.logger.info(f"Trailing stop activated for {symbol} at profit {profit_pct*100:.2f}%.")
        elif updates["trailing_active"] and trade["trailing_stop"] > float(trade["stop_price"]):
            self.logger.debug(f"{symbol}: trailing stop ratcheted to {trade['trailing_stop']:.6f} (hard stop {trade['stop_price']:.6f}).")
        if trade["stop_price"] > prev_stop:
            self.logger.debug(f"{symbol}: protective stop raised {prev_stop:.6f} → {trade['stop_price']:.6f}.")

        # The ratchet can itself reveal a breach: with an ATR trail (`new = high -
        # 2×ATR`) the raised stop can sit above the current tick when the favourable
        # extreme is well above it. The backtest catches that on its next bar, since
        # its decision also runs before the ratchet — this is the same rule evaluated
        # one cycle sooner, not a second one.
        if trade["trailing_active"] and price <= trade["trailing_stop"]:
            await self.close_trade(symbol, "TRAILING_STOP"); return

        await self._persist_active_trade_if_changed(symbol, trade)

    # Every field the web monitor renders for an open position (and therefore
    # every field whose staleness would make the dashboard disagree with the
    # engine). A change in any of them must reach SQLite promptly.
    _PERSISTED_TRADE_FIELDS = (
        "entry_price", "quantity", "stop_price", "take_profit", "atr",
        "trailing_active", "trailing_stop", "breakeven_activated",
        "scale_out_done", "initial_qty", "initial_stop_price",
    )

    async def _persist_active_trade_if_changed(self, symbol, trade, heartbeat=60.0):
        """Flush a managed trade to SQLite when a rendered field ACTUALLY changed.

        The engine keeps the live position in memory and the web monitor reads
        only the DB row, so a field left unpublished renders as a stale position:
        the dashboard's bracket ladder, trailing/breakeven locks and size all come
        straight from this row, while the exits are decided from memory.

        This replaces `if int(time.time()) % 30 == 0: save_active_trade(trade)`,
        which fired only inside a one-second window every 30s and — with the 10s
        decision cadence — routinely skipped it entirely, letting a ratcheted
        trail sit unpublished for minutes (and indefinitely on a slower cadence).
        A signature compare writes exactly once per real change, with a slow
        heartbeat so an interrupted write cannot leave the row behind forever.
        Failures are non-fatal and retried on the next cycle: a DB hiccup must
        never block stop/TP management.
        """
        try:
            sig = tuple(trade.get(f) for f in self._PERSISTED_TRADE_FIELDS)
        except Exception:
            return
        now = time.time()
        prev = self._saved_trade_sig.get(symbol)
        if prev is not None and prev[0] == sig and (now - prev[1]) < heartbeat:
            return
        try:
            await self.db.save_active_trade(trade)
            self._mark_trade_persisted(symbol, trade, now)
        except Exception as e:
            self.logger.warning(f"{symbol}: could not persist active trade state: {e}")

    def _mark_trade_persisted(self, symbol, trade, now=None):
        """Record that `trade`'s rendered fields are now in SQLite for `symbol`."""
        try:
            self._saved_trade_sig[symbol] = (
                tuple(trade.get(f) for f in self._PERSISTED_TRADE_FIELDS),
                now if now is not None else time.time(),
            )
        except Exception:
            pass

    async def _scale_out(self, symbol, trade, scale_qty):
        """Sell a fraction of the position at +1R and mark the trade as scaled out.

        The realized PnL of the partial leg is recorded like any other exit; the
        remaining "runner" keeps its stop (which the breakeven/trailing logic will
        ratchet up) and rides toward the full take-profit. If the scale-out order
        fails, the flag stays False and it retries on the next cycle — never at the
        cost of the protective stops, which are re-checked immediately after.
        """
        try:
            filters = await self.rest.get_filters(symbol)
            step_size = float(filters.get("LOT_SIZE", {}).get("stepSize", "0.000001"))
            min_qty = float(filters.get("LOT_SIZE", {}).get("minQty", "0.00001"))
            min_notional = float(filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {})).get("minNotional", 5.0))
        except Exception:
            step_size, min_qty, min_notional = 0.000001, 0.00001, 5.0

        price = trade.get("entry_price", 0.0)
        try:
            price = await self.ws_stream.get_current_price(symbol) or price
        except Exception:
            pass

        scale_qty = (int(scale_qty / step_size)) * step_size if step_size > 0 else scale_qty
        runner_qty = trade["quantity"] - scale_qty
        # Never scale out into dust: the runner must stay marketable, otherwise a
        # later full exit would be below minNotional and rejected on Binance.
        if scale_qty < min_qty or runner_qty * price < min_notional:
            trade["scale_out_done"] = True  # position too small to split — manage as one unit
            self.logger.debug(f"{symbol}: position too small for scale-out split; managing as single unit.")
            return

        order_id = await self.order_mgr.place_market_order(symbol, "SELL", scale_qty)
        if order_id is None:
            self.logger.warning(f"{symbol}: scale-out order rejected; will retry next cycle.")
            return
        filled, executed_qty = await self.order_mgr.wait_for_fill(symbol, order_id, timeout=10)
        if not filled or executed_qty <= 0:
            self.logger.warning(f"{symbol}: scale-out order {order_id} not filled; will retry next cycle.")
            return

        fill_price = price
        if not self.config["PAPER_TRADE"]:
            try:
                order_info = await self.rest.get_order(symbol, order_id)
                avg = float(order_info.get("avgPrice", 0) or 0)
                if avg > 0: fill_price = avg
            except Exception:
                pass

        # Net PnL for the scaled-out leg (entry side was already fee-deducted at entry)
        fee_rate = 0.001
        gross = (fill_price - trade["entry_price"]) * executed_qty
        fees = (trade["entry_price"] * executed_qty + fill_price * executed_qty) * fee_rate
        pnl = gross - fees

        trade["quantity"] = max(0.0, trade["quantity"] - executed_qty)
        trade["scale_out_done"] = True
        # After banking 1R, the runner is effectively risk-free: lock breakeven
        # immediately instead of waiting for the +1% trigger.
        be_offset = float(self.config.get("BREAKEVEN_OFFSET", 0.0025) or 0.0)
        be_price = trade["entry_price"] * (1 + be_offset)
        if be_price > trade["stop_price"]:
            trade["stop_price"] = be_price
        if be_price > trade.get("trailing_stop", 0):
            trade["trailing_stop"] = be_price
        trade["breakeven_activated"] = True

        await self.db.update_order_status(order_id, "CANCELED", executed_qty, fill_price, profit_loss=pnl)
        await self.db.save_active_trade(trade)
        await self.risk_mgr.update_trade_result(pnl, symbol)
        await self.webhook.send(
            f"💰 SCALE-OUT {symbol}: sold {executed_qty:.6f} @ {fill_price:.4f} (+{pnl:+.2f} USDT net). "
            f"Runner {trade['quantity']:.6f} rides with stop {trade['stop_price']:.4f}."
        )
        self.logger.info(f"Scale-out executed for {symbol}: {executed_qty} @ {fill_price:.4f}, net {pnl:+.2f} USDT.")

    async def _calculate_atr_from_klines(self, klines):
        if not klines:
            return 0.0
        period = int(self.config.get("ATR_PERIOD", 14))
        try:
            if pd is not None:
                df = pd.DataFrame(klines, columns=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_buy_base','taker_buy_quote','ignore'])
                for col in ['open','high','low','close']:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
                tr = pd.concat([df['high']-df['low'], (df['high']-df['close'].shift()).abs(), (df['low']-df['close'].shift()).abs()], axis=1).max(axis=1)
                atr = tr.rolling(period).mean().iloc[-1]
                return float(atr) if not pd.isna(atr) else 0.0
        except Exception:
            pass

        try:
            trs = []
            prev_close = None
            for row in klines:
                if not isinstance(row, (list, tuple)) or len(row) < 5:
                    continue
                try:
                    high = float(row[2])
                    low = float(row[3])
                    close = float(row[4])
                    if prev_close is None:
                        tr = high - low
                    else:
                        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                    trs.append(tr)
                    prev_close = close
                except (TypeError, ValueError):
                    continue
            if not trs:
                return 0.0
            if len(trs) < period:
                return sum(trs) / len(trs)
            return sum(trs[-period:]) / period
        except Exception:
            return 0.0

    async def close_trade(self, symbol, reason, fill_override=None):
        trade = self.active_trades.pop(symbol, None)
        if not trade:
            return
        original_qty = trade["quantity"]
        try:
            exit_side = "SELL"
            exit_order_id = await self.order_mgr.place_market_order(symbol, exit_side, original_qty)
            if exit_order_id is None:
                # Failsafe: never silently drop a position we intended to close.
                # Re-arm the stop locally and alert loudly — the next manage_trade pass
                # (or sync_positions_from_exchange) retries the liquidation.
                self.active_trades[symbol] = trade
                self.symbol_cooldowns[symbol] = time.time() + self.cooldown
                self.logger.critical(f"{symbol}: EXIT ORDER REJECTED ({reason}). Position remains open with stop ${trade['stop_price']:.4f}. Will retry next cycle.")
                await self.webhook.send(f"🚨 CRITICAL: exit order REJECTED for {symbol} ({reason}). Position remains open — retrying. Stop: {trade['stop_price']:.4f}")
                return
            filled, executed_qty = await self.order_mgr.wait_for_fill(symbol, exit_order_id, timeout=10)
            if not filled:
                self.logger.warning(f"Exit order {exit_order_id} not filled? Keeping position.")
                self.active_trades[symbol] = trade
                return
        except Exception as e:
            # CRITICAL SAFETY NET: an exception between pop and re-arm would lose
            # the position from memory while it stays open on the exchange — stops
            # stop being monitored and a duplicate bullish position could be entered. Re-arm
            # the trade and alert loudly so the next cycle retries the exit.
            self.active_trades[symbol] = trade
            self.symbol_cooldowns[symbol] = time.time() + self.cooldown
            self.logger.critical(f"{symbol}: EXIT FAILED with exception ({reason}): {e}. Position re-armed; will retry next cycle. Stop: {trade['stop_price']:.4f}")
            await self.webhook.send(f"🚨 CRITICAL: exit FAILED for {symbol} ({reason}): {e}. Position re-armed — retrying. Stop: {trade['stop_price']:.4f}")
            return

        fill_price = None
        if not self.config["PAPER_TRADE"] and exit_order_id:
            # Live mode: always query the exchange's actual average fill price so slippage
            # and market impact are accurately recorded in PnL.
            try:
                order_info = await self.rest.get_order(symbol, exit_order_id)
                avg = float(order_info.get("avgPrice", 0) or 0)
                if avg > 0:
                    fill_price = avg
            except Exception as e:
                self.logger.warning(f"Could not fetch exit avg fill price for {symbol}: {e}")

        if not fill_price and fill_override:
            fill_price = fill_override
        elif not fill_price and self.config["PAPER_TRADE"] and exit_order_id:
            row = await self.db.fetch_one("SELECT avg_fill_price FROM orders WHERE order_id = ?", (exit_order_id,))
            fill_price = float(row[0]) if row and row[0] else None
        if not fill_price:
            try:
                fill_price = await self.ws_stream.get_current_price(symbol)
                if not fill_price:
                    ticker = await self.rest.get_ticker(symbol)
                    fill_price = float(ticker["price"])
            except Exception as e:
                # Last resort: zero-gross assumption (fees still deducted) rather
                # than letting PnL accounting crash the exit finalization.
                self.logger.error(f"Could not resolve exit fill price for {symbol}: {e}")
                fill_price = trade["entry_price"]

        remaining_qty = max(0.0, original_qty - executed_qty)
        is_partial = False

        if remaining_qty > 0:
            if self.config["PAPER_TRADE"]:
                # In paper trading, only treat as partial if remaining notional is significant
                is_partial = (remaining_qty * fill_price >= 5.0)
            else:
                # In live trading, check if the remaining position/balance on
                # Binance is still tradable
                try:
                    filters = await self.rest.get_filters(symbol)
                    min_notional = _min_notional_from_filters(filters)
                    min_qty = _min_qty_from_filters(filters)

                    live_qty = await self._current_position_qty(symbol)

                    # If the remaining position is marketable, treat as partial.
                    # Otherwise it's dust/fee remainder: the position is closed!
                    if live_qty >= min_qty and live_qty * fill_price >= min_notional:
                        is_partial = True
                        remaining_qty = live_qty
                    else:
                        self.logger.info(f"{symbol}: Remaining position {live_qty} ({live_qty * fill_price:.2f} USDT) is non-tradable dust (< minNotional {min_notional} or < minQty {min_qty}). Marking trade as FULL EXIT.")
                        is_partial = False
                        remaining_qty = 0.0
                except Exception as e:
                    self.logger.warning(f"Could not verify remaining position for {symbol}: {e}")
                    is_partial = False
                    remaining_qty = 0.0

        # Net PnL: real taker commissions per leg (spot 0.1%; USDⓈ-M futures 0.05%)
        fee_rate = 0.0005 if self.is_futures else 0.001
        entry_cost = trade["entry_price"] * executed_qty
        exit_cost = fill_price * executed_qty
        total_fees = (entry_cost + exit_cost) * fee_rate
        gross_pnl = (fill_price - trade["entry_price"]) * executed_qty
        pnl = gross_pnl - total_fees
        # ---- PnL auto-verification against the exchange's own books ----
        # Our model is an estimate; the exchange's realizedPnl − commission is
        # ground truth (it knows the position's true avg entry — including any
        # adoption-basis drift — and the real fee tier). Disagreement beyond a
        # small tolerance corrects the recorded PnL so breakers, streaks and
        # the dashboard can never drift from reality (the B2USDT +0.90 class).
        if not self.config["PAPER_TRADE"] and fill_price > 0:
            try:
                ex_pnl = await self._exchange_pnl_for_exit(symbol, exit_order_id)
                if ex_pnl is not None:
                    tolerance = max(abs(pnl) * 0.05, 0.02)
                    if abs(ex_pnl - pnl) > tolerance:
                        self.logger.warning(
                            f"{symbol}: model PnL {pnl:+.4f} differs from exchange {ex_pnl:+.4f} "
                            f"(tol {tolerance:.4f}) — correcting to exchange truth.")
                        pnl = ex_pnl
                    else:
                        self.logger.info(
                            f"{symbol}: PnL verified vs exchange ({pnl:+.4f} ≈ {ex_pnl:+.4f})")
            except Exception as e:
                # Verification is best-effort; a failed lookup must never lose an exit.
                self.logger.debug(f"{symbol}: PnL auto-verification unavailable: {e}")
        # Sanity clamp: a wildly off fill_price (e.g. a 0 fallback) would produce nonsensical PnL.
        # Keep the calculation but flag it so logs are interpretable.
        if fill_price <= 0:
            self.logger.error(f"{symbol}: exit fill_price was non-positive ({fill_price}); PnL may be unreliable.")

        # In live trading, purge any lingering open orders for this symbol to
        # prevent orphan execution (endpoint differs per market).
        if not self.config["PAPER_TRADE"]:
            try:
                endpoint = "/fapi/v1/allOpenOrders" if self.is_futures else "/api/v3/openOrders"
                await self.rest._request("DELETE", endpoint, {"symbol": symbol}, signed=True)
            except Exception:
                pass

        # Partial exit = the order was canceled after a partial fill; full exit = FILLED.
        exit_status = "CANCELED" if (is_partial and remaining_qty > 0) else "FILLED"
        # Record the engine's own exit reason on the order. It is the `reason`
        # evaluate_exit returned (STOP_LOSS / TRAILING_STOP / TAKE_PROFIT /
        # TIME_STOP / EOD_CLOSE), written on every exit leg, so the dashboard can
        # label an exit for what it WAS rather than inferring it from the PnL sign.
        await self.db.update_order_status(exit_order_id, exit_status, executed_qty, fill_price,
                                          profit_loss=pnl, exit_reason=reason)
        if is_partial and remaining_qty > 0:
            trade["quantity"] = remaining_qty
            self.active_trades[symbol] = trade
            await self.db.save_active_trade(trade)
            self._saved_trade_sig.pop(symbol, None)
            await self.risk_mgr.update_trade_result(pnl, symbol)
            await self.webhook.send(f"⚠️ Partial exit for {symbol} ({reason}): {executed_qty} of {original_qty} sold. Net PnL (after fees): {pnl:+.2f} USDT. Remaining {remaining_qty} units.")
            return

        await self.db.delete_active_trade(symbol)
        # Forget the persisted signature: a later re-entry must write its own row
        # even if it lands on identical values (same stop/TP/size).
        self._saved_trade_sig.pop(symbol, None)
        await self.risk_mgr.update_trade_result(pnl, symbol)
        if not self.config["PAPER_TRADE"]:
            try:
                await self.risk_mgr._fetch_equity()
                await self.risk_mgr.save_state()
            except Exception as e:
                self.logger.warning(f"Could not refresh equity after exit: {e}")
        emoji = "✅" if pnl >= 0 else "❌"
        await self.webhook.send(f"{emoji} CLOSE {symbol} ({reason}) Net PnL: {pnl:+.2f} USDT (Gross: {gross_pnl:+.2f}, Fees: -{total_fees:.2f})")
        self.logger.info(f"Closed {symbol} due to {reason}, Net PnL: {pnl:.2f} (Gross: {gross_pnl:.2f}, Fees: -{total_fees:.2f})")

    async def _save_entry_cap(self):
        """Persist the daily entry counter so it survives an engine restart."""
        try:
            await self.db.set_risk_state("entries_day_key", self._entries_day_key.isoformat())
            await self.db.set_risk_state("entries_today", str(self._entries_today))
        except Exception as e:
            # Never let a bookkeeping write block a real entry — the in-memory
            # counter still guards the current process.
            self.logger.warning(f"Could not persist daily entry counter: {e}")

    async def _load_entry_cap(self):
        """Restore the daily entry counter persisted by _save_entry_cap()."""
        try:
            day = await self.db.get_risk_state("entries_day_key")
            count = await self.db.get_risk_state("entries_today")
            if day:
                self._entries_day_key = datetime.fromisoformat(day).date()
            if count not in (None, ""):
                self._entries_today = int(count)
            if self._entries_today:
                self.logger.info(
                    f"Daily entry counter restored: {self._entries_today}/{self.max_trades_per_day} "
                    f"entries on {self._entries_day_key}."
                )
        except Exception as e:
            self.logger.warning(f"Could not restore daily entry counter: {e}")

    async def reconcile_positions(self):
        self.logger.info("Reconciling positions...")
        await self._load_entry_cap()
        active = await self.db.get_active_trades()
        for trade in active:
            self.active_trades[trade["symbol"]] = trade
            self.logger.info(f"Restored active trade for {trade['symbol']} (entry={trade['entry_price']}, qty={trade['quantity']}, stop={trade['stop_price']}, tp={trade['take_profit']})")
        await self.sync_positions_from_exchange()

    async def sync_positions_from_exchange(self):
        if self.config["PAPER_TRADE"]: return
        try:
            quote_asset = self.config["QUOTE_ASSET"]
            if self.is_futures:
                # Futures: the wallet holds only margin — real exposure lives in
                # POSITIONS (positionAmt). Wallet-balance reconciliation saw 0
                # base for every futures trade and orphaned entries 12s after
                # each fill; positions are the source of truth here.
                positions = await self._read_positions_map()
            else:
                account = await get_account(self.rest, self.order_mgr.ws_api, self.config)
                positions = {}
                for b in account.get("balances", []):
                    asset = b.get("asset", "")
                    free = _f(b.get("free"))
                    locked = _f(b.get("locked"))
                    total = free + locked
                    if asset != quote_asset and total > 0:
                        positions[asset + quote_asset] = total

            managed_symbols = set(self.current_symbols) | set(self.active_trades.keys())
            auto_liquidate = self.config.get("AUTO_LIQUIDATE_ORPHANS", False)

            # 1. Check all currently tracked active trades against the exchange
            # (futures positionAmt / spot wallet balance)
            for symbol, trade in list(self.active_trades.items()):
                live_qty = positions.get(symbol, 0.0)

                price = await self.ws_stream.get_current_price(symbol)
                if not price:
                    try:
                        ticker = await self.rest.get_ticker(symbol)
                        price = float(ticker["price"])
                    except Exception:
                        price = trade.get("entry_price", 0.0)

                try:
                    filters = await self.rest.get_filters(symbol)
                except Exception as e:
                    # Delisted/unreadable symbol: we cannot read exchange floors, so
                    # fall back to defaults and keep reconciling. Aborting the whole
                    # sync here would freeze reconciliation for every OTHER symbol.
                    self.logger.warning(f"{symbol}: exchange filters unavailable ({e}); using fallback floors.")
                    filters = {}
                min_notional = _min_notional_from_filters(filters)
                min_qty = _min_qty_from_filters(filters)
                notional_val = live_qty * price

                # If the position/balance is zero or non-marketable dust, it was
                # closed or liquidated externally
                if live_qty < min_qty or notional_val < min_notional:
                    self.active_trades.pop(symbol, None)
                    await self.db.delete_active_trade(symbol)
                    self.logger.warning(f"Active trade for {symbol} has no marketable Binance position/balance (qty={live_qty}, value=${notional_val:.2f} < ${min_notional:.2f}); removed from tracking.")
                    await self.webhook.send(f"ℹ️ Active trade for {symbol} removed from tracking (position/balance below minNotional / closed externally).")
                    continue

                # If the live qty differs from tracked quantity (e.g. fees or a
                # partial manual trade), reconcile
                if abs(trade["quantity"] - live_qty) > min_qty and live_qty >= min_qty:
                    old_qty = trade["quantity"]
                    trade["quantity"] = live_qty
                    self.active_trades[symbol] = trade
                    await self.db.save_active_trade(trade)
                    self.logger.info(f"{symbol}: Reconciled tracked position quantity from {old_qty} to {live_qty} to match Binance.")

            # 2. Check for unmanaged/orphan balances on the exchange.
            # Adoption is attempted for ANY non-dust balance regardless of
            # `managed_symbols`: a symbol can rotate out of the dynamic screener
            # within ORPHAN_ADOPT_WINDOW_HOURS, and gating on the CURRENT scan
            # left a bot BUY fill on a rotated-out symbol naked forever —
            # exactly the failure adoption exists to rescue. Adoption stays
            # proof-based (the balance must match one of OUR recent MARKET BUY
            # fills locally AND on the exchange), so manual/deposit balances can
            # never be picked up. AUTO_LIQUIDATE_ORPHANS stays gated to symbols
            # we actually monitor, so a manual deposit is never sold.
            for symbol, live_qty in positions.items():
                if symbol in self.active_trades:
                    continue
                # Skip symbols that do not exist on this market's exchangeInfo.
                # The futures wallet carries bonus-rate assets (U, BFUSD, RWUSD,
                # USD1, ...) whose '<ASSET>USDT' pairs are NOT tradable symbols;
                # probing them burned 3 REST retries each (error -1121).
                try:
                    universe = getattr(self.rest, "symbol_info_cache", None)
                    if universe and symbol not in universe:
                        continue
                except Exception:
                    pass
                try:
                    free_balance = live_qty
                    price = await self.ws_stream.get_current_price(symbol)
                    if not price:
                        try:
                            ticker = await self.rest.get_ticker(symbol)
                            price = float(ticker["price"])
                        except Exception:
                            continue
                    filters = await self.rest.get_filters(symbol)
                    min_notional = _min_notional_from_filters(filters)
                    min_qty = _min_qty_from_filters(filters)

                    # Ignore dust orphan balances that cannot be traded on Binance
                    if free_balance < min_qty or free_balance * price < min_notional:
                        continue

                    if auto_liquidate and symbol in managed_symbols:
                        self.logger.warning(f"AUTO_LIQUIDATE_ORPHANS=True: closing orphan position {symbol} balance={free_balance} (${free_balance * price:.2f})")
                        await self.order_mgr.place_market_order(symbol, "SELL", free_balance)
                        await self.webhook.send(f"⚠️ Orphan position closed for {symbol}: {free_balance} units")
                        continue

                    # ADOPT before leaving unmanaged: if this balance came from one of
                    # OUR own recent BUY fills whose entry tracking was interrupted
                    # (API outage / restart mid-entry), re-anchor it as a managed
                    # trade with fresh SL/TP instead of leaving it naked. Only
                    # bot-originated fills (orders table + exchange myTrades, qty
                    # match, recent window) are adopted — manual/deposit balances
                    # never match.
                    adopted = await self._adopt_orphan_position(symbol, None, free_balance, price)
                    if not adopted and symbol in managed_symbols:
                        kind = "position" if self.is_futures else "balance"
                        self.logger.info(f"Unmanaged {kind} detected for {symbol}: {free_balance} (${free_balance * price:.2f}). Leaving untouched (AUTO_LIQUIDATE_ORPHANS=False, not a recent bot fill).")
                except Exception as e:
                    # One unreadable/delisted symbol must not abort the whole sync.
                    self.logger.warning(f"Orphan scan failed for {symbol}: {e}")
        except Exception as e:
            self.logger.error(f"Error during exchange sync: {e}")
            await self.webhook.send(f"❌ Exchange sync error: {e}")

    async def _adopt_orphan_position(self, symbol, base_asset, free_balance, price):
        """Re-adopt an orphaned exchange balance if it matches one of OUR recent
        market-BUY fills that lost entry tracking (poll failure / restart).

        Returns True when the position was adopted and is now SL/TP-managed.
        The bot's own fills are proven from the local orders table — the same
        window is cross-checked against Binance recent trades (qty match) so a
        manual buy or a deposit can never be adopted by mistake.
        """
        try:
            window_ms = int(self.config.get("ORPHAN_ADOPT_WINDOW_HOURS", 48)) * 3600 * 1000
            cutoff = int(time.time() * 1000) - window_ms
            row = await self.db.fetch_one(
                "SELECT order_id, executed_qty, created_at FROM orders "
                "WHERE symbol=? AND side='BUY' AND status='FILLED' AND created_at >= ? "
                "ORDER BY created_at DESC LIMIT 5",
                (symbol, cutoff))
            if not row:
                return False

            # Quantity must be explained by OUR recent bot BUY fills. Matching the
            # SUM of recent fills lets an orphan be adopted after several buys
            # merged into one position (double entry, fee-adjusted credits) — a
            # per-fill match would leave the merged position naked forever.
            fills = await self.db.fetch_all(
                "SELECT executed_qty, avg_fill_price FROM orders "
                "WHERE symbol=? AND side='BUY' AND status='FILLED' AND created_at >= ?",
                (symbol, cutoff))
            total_filled = sum(_f(r[0]) for r in fills) if fills else 0.0
            # Real cost basis: quantity-weighted average of OUR OWN fill prices.
            # Anchoring to the market price at adoption time silently inflates
            # every recorded PnL by the drift between real fills and adoption
            # (B2USDT 2026-09-20: +0.90 phantom PnL from basis 0.53106 vs 0.5208).
            _cost = sum(_f(q) * _f(p) for q, p in fills if _f(p) > 0)
            _qty_priced = sum(_f(q) for q, p in fills if _f(p) > 0)
            basis_price = (_cost / _qty_priced) if (_qty_priced > 0 and _cost > 0) else 0.0
            tolerance = max(total_filled * 0.01, 1e-9)
            if total_filled <= 0 or not (
                    abs(total_filled - free_balance) <= tolerance or
                    (0 < free_balance <= total_filled and
                     total_filled - free_balance <= max(total_filled * 0.005, 1e-9))):
                self.logger.info(f"{symbol}: orphan qty {free_balance} does not match our recent bot BUY fills (total {total_filled}); not adopting.")
                return False

            # Cross-check on-exchange: our recent trades for this symbol must
            # include this quantity as a BUY (isBuyer=True) in the window.
            if self.is_futures:
                if not await self._verify_recent_futures_qty(symbol, free_balance, window_ms):
                    self.logger.info(f"{symbol}: exchange futures trade list has no matching BUY for orphan qty {free_balance}; not adopting.")
                    return False
            else:
                try:
                    trades = await self.rest._request("GET", "/api/v3/myTrades",
                                                      {"symbol": symbol, "limit": 20}, signed=True)
                    recent_buys = [float(t["qty"]) for t in trades
                                   if t.get("isBuyer") and int(t.get("time", 0)) >= cutoff]
                    if not any(abs(q - free_balance) <= max(q * 0.005, tolerance) for q in recent_buys):
                        self.logger.info(f"{symbol}: exchange trade list has no matching BUY for orphan balance {free_balance}; not adopting.")
                        return False
                except Exception as e:
                    self.logger.warning(f"{symbol}: could not verify orphan fill on exchange ({e}); not adopting.")
                    return False

            # Fresh bracket from the position's REAL cost basis (falls back to
            # market price only when our own fills carry no usable price).
            entry_price = basis_price if basis_price > 0 else price
            atr = 0.0
            try:
                klines = await self.signal_gen._get_cached_klines(symbol, self.config["TIMEFRAME"], self.config["ATR_PERIOD"] * 10)
                atr_series = self.signal_gen._calculate_atr(self.signal_gen._to_df(klines))
                atr = float(atr_series.iloc[-1]) if len(atr_series) else 0.0
                if not atr or atr <= 0:
                    atr = entry_price * 0.01
            except Exception:
                atr = entry_price * 0.01
            stop_price, take_profit = self._compute_bracket(entry_price, atr)

            trade = {
                "symbol": symbol, "entry_price": entry_price, "side": "BUY",
                # entry_time is SECONDS everywhere (enter_trade uses time.time());
                # storing milliseconds here made the time-stop comparison
                # `time.time() - entry_time > MAX_HOLD_TIME` permanently negative.
                "quantity": free_balance, "entry_time": time.time(),
                "stop_price": stop_price, "take_profit": take_profit, "atr": atr,
                "initial_stop_price": stop_price, "initial_qty": free_balance,
                "scale_out_done": False,
                "trailing_active": False, "trailing_stop": stop_price,
                "breakeven_activated": False, "order_id": str(row[0]),
            }
            self.active_trades[symbol] = trade
            await self.db.save_active_trade(trade)
            drift = f" (basis {basis_price:.6g} vs market {price:.6g})" if basis_price > 0 and abs(basis_price - price) > price * 0.001 else ""
            msg = (f"✅ {symbol}: ADOPTED orphan position {free_balance} @ cost basis {entry_price:.6g}{drift} "
                   f"(bot fill recovered). SL={stop_price:.6f} TP={take_profit:.6f}")
            self.logger.warning(msg)
            await self.webhook.send(msg)
            return True
        except Exception as e:
            self.logger.error(f"{symbol}: orphan adoption failed: {e}")
            return False

    async def _exchange_pnl_for_exit(self, symbol, exit_order_id):
        """Net PnL the EXCHANGE booked for this exit order: realizedPnl minus
        commissions of its fills (futures only — spot myTrades has no realizedPnl
        and the spot fee model has proven exact). Returns None when unavailable.
        """
        if not self.is_futures:
            return None
        try:
            trades = await self.rest.get_user_trades(symbol, limit=50)
        except Exception:
            return None
        rows = [t for t in (trades or [])
                if str(t.get("orderId", "")) == str(exit_order_id)]
        if not rows:
            return None
        def _f2(x):
            try:
                return float(x)
            except (TypeError, ValueError):
                return 0.0
        realized = sum(_f2(t.get("realizedPnl")) for t in rows)
        commission = sum(_f2(t.get("commission")) for t in rows)
        return realized - commission

    async def calculate_unrealized_pnl(self):
        total = 0.0
        tracked = set()
        for symbol, trade in self.active_trades.items():
            tracked.add(symbol)
            price = await self.ws_stream.get_current_price(symbol)
            if not price:
                try:
                    ticker = await self.rest.get_ticker(symbol)
                    price = float(ticker["price"])
                except Exception:
                    continue
            total += (price - trade["entry_price"]) * trade["quantity"]
        # Futures: positions without an active trade (pre-boot fills, adopted
        # candidates) still move equity — include their uPnL so risk checks and
        # the daily-drawdown breaker see the whole account, not just tracked trades.
        if self.is_futures:
            try:
                for r in await self.rest.get_position_risk() or []:
                    if r.get("symbol") in tracked:
                        continue
                    try:
                        amt = float(r.get("positionAmt") or 0.0)
                        up = float(r.get("unRealizedProfit") or 0.0)
                    except (TypeError, ValueError):
                        continue
                    if abs(amt) > 1e-12 and up:
                        total += up
            except Exception:
                pass
        return total

    async def send_daily_report_loop(self):
        while True:
            now = datetime.now(timezone.utc)
            target = now.replace(hour=23, minute=59, second=59, microsecond=0)
            if now >= target: target += timedelta(days=1)
            await asyncio.sleep((target - now).total_seconds())
            await self._send_daily_report()

    async def _daily_closed_stats(self, day):
        """(trades, total_pnl, wins) for one UTC day.

        Deliberately the SAME definition the web monitor's stats use, because this
        report is the operator's second view of the same book:

          * an exit is a row the engine labelled (`exit_reason`, written by
            close_trade) or a legacy row with a non-zero realized PnL — never
            `side='SELL'`, which silently drops any exit that is not a spot-style
            SELL (a futures short is closed with a BUY);
          * a TRADE is not a leg: scale-out writes a partial leg plus a final leg,
            and counting legs reported one trade as both a win and a loss.

        Returns counts or None when neither query can run, so a stats hiccup can
        never stop the daily report from being sent.
        """
        exit_pred = (
            "(exit_reason IS NOT NULL"
            " OR (profit_loss IS NOT NULL AND profit_loss != 0))"
        )
        trade_sql = (
            "WITH exits AS ("
            "  SELECT id, symbol, status, profit_loss, created_at FROM orders"
            f"  WHERE {exit_pred} AND date(created_at/1000, 'unixepoch') = ?"
            "), legs AS ("
            "  SELECT symbol, created_at, profit_loss,"
            "         MIN(CASE WHEN status = 'FILLED' THEN created_at END) OVER ("
            "           PARTITION BY symbol ORDER BY created_at, id"
            "           ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING"
            "         ) AS trade_key FROM exits"
            "), trades AS ("
            # A leg with no FILLED exit after it belongs to a still-open position
            # (a scale-out partial): its own timestamp makes it its own trade, so
            # the trade nets always add up to the realized total.
            "  SELECT symbol, COALESCE(trade_key, created_at) AS trade_key,"
            "         SUM(profit_loss) AS pnl FROM legs"
            "  GROUP BY symbol, COALESCE(trade_key, created_at)"
            ")"
            " SELECT COUNT(*), COALESCE(SUM(pnl), 0),"
            "  COALESCE(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END), 0) FROM trades"
        )
        try:
            row = await self.db.fetch_one(trade_sql, (day,))
            if row:
                return (
                    int(row[0] or 0),
                    float(row[1] or 0.0),
                    int(row[2] or 0),
                )
        except Exception as exc:  # noqa: BLE001 - degraded stats must not stop the report
            # Window functions need SQLite >= 3.25; fall back to the legacy
            # leg-based aggregate rather than losing the report.
            self.logger.warning(f"Daily trade attribution failed ({exc}); using leg counts")
        try:
            row = await self.db.fetch_one(
                f"SELECT COUNT(*), COALESCE(SUM(profit_loss), 0), "
                f"COALESCE(SUM(CASE WHEN profit_loss > 0 THEN 1 ELSE 0 END), 0) "
                f"FROM orders WHERE {exit_pred} "
                "AND date(created_at/1000, 'unixepoch') = ?",
                (day,),
            )
            if row:
                return (int(row[0] or 0), float(row[1] or 0.0), int(row[2] or 0))
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"Daily stats unavailable: {exc}")
        return None

    async def _send_daily_report(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        daily = await self._daily_closed_stats(today)
        total_trades, total_pnl, wins = daily if daily else (0, 0.0, 0)
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        embed = {
            "title": "Daily Trading Report",
            "color": 0x00ff00 if total_pnl >= 0 else 0xff0000,
            "fields": [
                {"name": "Date", "value": today, "inline": True},
                {"name": "Total PnL", "value": f"${total_pnl:.2f}", "inline": True},
                {"name": "Win Rate", "value": f"{win_rate:.1f}% ({wins}/{total_trades})", "inline": True},
                {"name": "Active Positions", "value": str(len(self.active_trades)), "inline": True},
                {"name": "Status", "value": "RUNNING", "inline": True},
            ],
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        await self.webhook.send("", embed=embed)
