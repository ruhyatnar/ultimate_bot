"""USDⓈ-M Futures public WebSocket market streams (fstream.binance.com).

Implements the Binance USDⓈ-M Futures **WebSocket Streams** surface used by
the engine — the public market-data transport:

  * per-symbol `<symbol>@aggTrade` — tick prices
  * per-symbol `<symbol>@kline_<interval>` — execution-timeframe klines
  * `!miniTicker@arr` — all-market mini-tickers (~1s push, zero REST weight)

Docs:
  https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/public
  (base endpoint wss://fstream.binance.com — combined streams at /stream,
   raw streams at /ws; testnet wss://demo-fstream.binance.com)

Contract notes from the docs honoured here:
  * Raw streams: payload is the event object itself (no {stream,data} wrapper)
    — matches the spot raw-stream parsing in the engine.
  * A single connection is only valid for 24 hours; reconnect logic required.
  * Server pings every 3 min; a missing pong for 10 min drops the connection
    (websockets lib answers pings automatically with ping_interval set).
  * 24h ticker 'o' (open) differs in semantics from spot 24hr stats only in
    window alignment; the screener treats it the same way.

Mirror of WSStreamClient — same public interface (connect/subscribe/
unsubscribe/get_current_price/get_all_tickers/is_arr_stream_alive), futures
hosts and kline payload mapping only. The engine selects between the two via
config["MARKET"].
"""
import asyncio
import json
import logging
import time

import websockets


class FuturesWSStreamClient:
    STREAM_URL = "wss://fstream.binance.com/ws"
    STREAM_URL_TESTNET = "wss://demo-fstream.binance.com/ws"

    ALL_MARKET_STREAM = "!miniTicker@arr"  # push ~every 1s (1000ms cycle)

    MAX_CONNECT_ATTEMPTS = 5

    def __init__(self, config):
        self.config = config
        self.use_testnet = config.get("USE_TESTNET", False)
        self.stream_url = self.STREAM_URL_TESTNET if self.use_testnet else self.STREAM_URL
        self.logger = logging.getLogger(__name__)
        self.websocket = None
        self.connected = False
        self.last_price = {}
        self._listen_task = None
        self._reconnect_task = None
        self._subscribed_symbols = set()
        self._arr_websocket = None
        self._arr_listen_task = None
        # REST fallback for silent-frame network paths (see _rest_ticker_refresher).
        self.rest_fallback = None
        self._arr_refresh_task = None
        self._rest_fallback_syms = set()
        self.all_tickers = {}  # {symbol: {price, open, high, low, quote_volume, time, transport?}}

    async def connect(self, symbols):
        if len(symbols) > 500:
            self.logger.warning(f"Too many symbols ({len(symbols)}). Limit to 500.")
            symbols = symbols[:500]
        last_error = None
        for attempt in range(1, self.MAX_CONNECT_ATTEMPTS + 1):
            try:
                self.logger.info(
                    f"Connecting to Futures WebSocket Stream (attempt {attempt}/{self.MAX_CONNECT_ATTEMPTS}) "
                    f"for symbols: {symbols}")
                self.websocket = await websockets.connect(self.stream_url, ping_interval=20, ping_timeout=10)
                self.connected = True
                streams = []
                for symbol in symbols:
                    sym_lower = symbol.lower()
                    streams.append(f"{sym_lower}@aggTrade")
                    streams.append(f"{sym_lower}@kline_{self.config['TIMEFRAME']}")
                await self.websocket.send(json.dumps({"method": "SUBSCRIBE", "params": streams, "id": 1}))
                resp = await asyncio.wait_for(self.websocket.recv(), timeout=10)
                if '"result":null' in resp and '"error"' in resp:
                    raise Exception(f"Subscription error: {resp}")
                self._subscribed_symbols = set(symbols)
                self.logger.info(f"Subscribed to {len(symbols)} futures symbols.")
                self._listen_task = asyncio.create_task(self._listen())
                return
            except Exception as e:
                last_error = e
                self.logger.error(f"Futures stream connection attempt {attempt} failed: {e}")
                await self.disconnect()
                if attempt < self.MAX_CONNECT_ATTEMPTS:
                    await asyncio.sleep(min(2 ** attempt, 30))
        self.logger.error(
            f"Futures WebSocket Stream unavailable after {self.MAX_CONNECT_ATTEMPTS} attempts ({last_error}). "
            "Continuing with REST price polling; background reconnects will retry.")

    async def connect_all_market_tickers(self):
        """Dedicated socket for !miniTicker@arr — realtime prices for the
        screener/web monitor without REST polling (same design as spot)."""
        if self._arr_websocket is not None and self.is_connected_arr():
            return True
        try:
            ws = await websockets.connect(self.stream_url, ping_interval=20, ping_timeout=10)
            await ws.send(json.dumps({"method": "SUBSCRIBE", "params": [self.ALL_MARKET_STREAM], "id": 1}))
            resp = await asyncio.wait_for(ws.recv(), timeout=10)
            if '"error"' in resp:
                raise Exception(f"all-market subscribe error: {resp}")
            self._arr_websocket = ws
            self.logger.info(f"All-market futures mini-ticker stream ({self.ALL_MARKET_STREAM}) subscribed.")
            if self._arr_listen_task is None or self._arr_listen_task.done():
                self._arr_listen_task = asyncio.create_task(self._arr_listen())
            return True
        except Exception as e:
            self.logger.warning(f"All-market futures ticker stream unavailable ({e}); screener falls back to REST.")
            return False

    def is_connected_arr(self):
        """State check for the dedicated all-market socket."""
        try:
            state = getattr(self._arr_websocket, "state", None)
            if state is not None:
                state_name = getattr(state, "name", str(state))
                return state_name == "OPEN" or state == 1 or "OPEN" in str(state)
            if hasattr(self._arr_websocket, "open"):
                return bool(self._arr_websocket.open)
        except Exception:
            pass
        return False

    async def _arr_listen(self):
        backoff = 1
        while True:
            try:
                if self._arr_websocket is None:
                    if not await self.connect_all_market_tickers():
                        await asyncio.sleep(min(backoff, 60))
                        backoff = min(backoff * 2, 60)
                        continue
                backoff = 1
                msg = await self._arr_websocket.recv()
                data = json.loads(msg)
                # Raw stream: the array IS the payload (no {stream,data} wrapper).
                rows = data.get("data") if isinstance(data, dict) else data
                if isinstance(rows, list):
                    now = int(time.time() * 1000)
                    for t in rows:
                        try:
                            self.all_tickers[t["s"]] = {
                                "price": float(t["c"]),
                                "open": float(t["o"]),
                                "high": float(t["h"]),
                                "low": float(t["l"]),
                                "quote_volume": float(t.get("q", 0) or 0),
                                "time": now,
                            }
                        except (KeyError, ValueError, TypeError):
                            continue
            except (websockets.exceptions.ConnectionClosed, websockets.exceptions.WebSocketException,
                    OSError, asyncio.TimeoutError) as e:
                self.logger.warning(f"All-market futures ticker stream dropped ({e}); reconnecting...")
                self._arr_websocket = None
                await asyncio.sleep(min(backoff, 60))
                backoff = min(backoff * 2, 60)
            except asyncio.CancelledError:
                return

    def get_all_tickers(self):
        return self.all_tickers if self.all_tickers else {}

    def is_arr_stream_alive(self):
        """True when the all-market cache is fresh — via WS frames OR REST refresher."""
        t = max((v["time"] for v in self.all_tickers.values()), default=0)
        return bool(t) and (int(time.time() * 1000) - t) < 15000

    def arr_transport(self):
        """Provenance of the fresh cache rows: 'ws', 'rest', or '' (stale/empty)."""
        now = int(time.time() * 1000)
        fresh = [v for v in self.all_tickers.values()
                 if now - v.get("time", 0) < 15000]
        if not fresh:
            return ""
        if any(v.get("transport") != "rest" for v in fresh):
            return "ws"
        return "rest"

    async def _rest_ticker_refresher(self):
        """REST fallback feeding the SAME all_tickers cache the WS frames fill.

        Some network paths complete the fstream handshake + SUBSCRIBE ACK but
        never deliver market-data frames (silent middlebox drop): the listener
        blocks in recv() forever, no error fires, and the cache stays empty.
        This refresher keeps the screener, price fallback and dashboard alive
        via the cheap bulk REST endpoint, writing rows with transport="rest" so
        provenance stays honest vs WS frames.
        """
        interval = max(1.0, float(self.config.get("TICKERS_REST_FALLBACK_S", 5)))
        while True:
            try:
                symbols = [s for s in self._rest_fallback_syms]
                if symbols and self.rest_fallback is not None:
                    # Contract: {symbol: price} dict (single-symbol request when
                    # one symbol, whole-market bulk otherwise).
                    rows = await self.rest_fallback.get_tickers_bulk(symbols)
                    if rows:
                        now = int(time.time() * 1000)
                        for sym, price in rows.items():
                            try:
                                price = float(price)
                            except (TypeError, ValueError):
                                continue
                            if not sym or price <= 0:
                                continue
                            prev = self.all_tickers.get(sym) or {}
                            self.all_tickers[sym] = {
                                "price": price,
                                "open": float(prev.get("open") or price),
                                "high": float(prev.get("high") or price),
                                "low": float(prev.get("low") or price),
                                "quote_volume": float(prev.get("quote_volume") or 0),
                                "time": now,
                                "transport": "rest",
                            }
            except asyncio.CancelledError:
                return
            except Exception as e:
                self.logger.warning(f"REST ticker refresher failed ({e}); retrying...")
            await asyncio.sleep(interval)

    def start_rest_ticker_fallback(self, rest_client, symbols):
        """Enable the REST refresher for the given symbols (idempotent)."""
        self.rest_fallback = rest_client
        want = [s for s in (symbols or []) if s not in self._rest_fallback_syms]
        if not want:
            return
        self._rest_fallback_syms.update(want)
        if self._arr_refresh_task is None or self._arr_refresh_task.done():
            self._arr_refresh_task = asyncio.create_task(self._rest_ticker_refresher())
        self.logger.info(
            f"All-market ticker REST fallback enabled for {len(self._rest_fallback_syms)} "
            f"symbols (no fstream market-data frames on this network path).")

    async def subscribe(self, symbols):
        if not self.is_connected():
            return
        new_syms = [s for s in symbols if s not in self._subscribed_symbols]
        if not new_syms:
            return
        streams = []
        for symbol in new_syms:
            sym_lower = symbol.lower()
            streams.append(f"{sym_lower}@aggTrade")
            streams.append(f"{sym_lower}@kline_{self.config['TIMEFRAME']}")
        await self.websocket.send(json.dumps({"method": "SUBSCRIBE", "params": streams, "id": int(time.time() * 1000)}))
        self._subscribed_symbols.update(new_syms)

    async def unsubscribe(self, symbols):
        if not self.is_connected():
            return
        remove_syms = [s for s in symbols if s in self._subscribed_symbols]
        if not remove_syms:
            return
        streams = []
        for symbol in remove_syms:
            sym_lower = symbol.lower()
            streams.append(f"{sym_lower}@aggTrade")
            streams.append(f"{sym_lower}@kline_{self.config['TIMEFRAME']}")
        await self.websocket.send(json.dumps({"method": "UNSUBSCRIBE", "params": streams, "id": int(time.time() * 1000)}))
        for s in remove_syms:
            self._subscribed_symbols.discard(s)

    async def disconnect(self):
        current_task = asyncio.current_task()
        if self._reconnect_task and self._reconnect_task is not current_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None
        if self._listen_task and self._listen_task is not current_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None
        if self.websocket:
            try:
                await asyncio.wait_for(self.websocket.close(), timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self.logger.warning("Futures WebSocket close timed out during disconnect; aborting.")
            except Exception:
                pass
            finally:
                try:
                    transport = getattr(self.websocket, "transport", None)
                    if transport is not None:
                        transport.abort()
                except Exception:
                    pass
            self.websocket = None
        self.connected = False
        self._subscribed_symbols.clear()
        if self._arr_listen_task:
            self._arr_listen_task.cancel()
            self._arr_listen_task = None
        if self._arr_refresh_task:
            self._arr_refresh_task.cancel()
            self._arr_refresh_task = None
        if self._arr_websocket is not None:
            try:
                await self._arr_websocket.close()
            except Exception:
                pass
            self._arr_websocket = None

    def is_connected(self):
        if not self.connected or self.websocket is None:
            return False
        try:
            state = getattr(self.websocket, "state", None)
            if state is not None:
                state_name = getattr(state, "name", str(state))
                return state_name == "OPEN" or state == 1 or "OPEN" in str(state)
            if hasattr(self.websocket, "open"):
                return bool(self.websocket.open)
            if hasattr(self.websocket, "closed"):
                return not bool(self.websocket.closed)
        except Exception:
            pass
        return self.connected

    async def _listen(self):
        while self.connected:
            try:
                msg = await self.websocket.recv()
                data = json.loads(msg)
                await self._process(data)
            except (websockets.exceptions.ConnectionClosed, websockets.exceptions.WebSocketException,
                    OSError, asyncio.TimeoutError) as e:
                self.connected = False
                self.logger.warning(f"Futures WebSocket Stream disconnected ({e}). Reconnecting...")
                if not self._reconnect_task or self._reconnect_task.done():
                    self._reconnect_task = asyncio.create_task(
                        self._reconnect_loop(list(self._subscribed_symbols)))
                break
            except asyncio.CancelledError:
                break

    async def _reconnect_loop(self, symbols):
        """Exponential-backoff reconnect; never calls self.disconnect() (it
        would cancel this very task) — mirrors the spot client."""
        self.connected = False
        if self._listen_task:
            self._listen_task = None
        for attempt in range(1, 8):
            try:
                if self.websocket:
                    try:
                        await self.websocket.close()
                    except Exception:
                        pass
                    self.websocket = None
                await self.connect(symbols)
                if self.connected:
                    self.logger.info("Futures WebSocket Stream reconnected successfully.")
                    return
            except Exception as e:
                self.logger.warning(f"Futures stream reconnect attempt {attempt} failed: {e}")
            await asyncio.sleep(min(2 ** attempt, 60))
        self.logger.error("Futures WebSocket Stream reconnect abandoned; price reads fall back to REST.")

    async def _process(self, data):
        e = data.get("e")
        if e == "aggTrade":
            symbol = data.get("s")
            if symbol:
                self.last_price[symbol] = {"price": float(data.get("p", 0)), "time": int(time.time() * 1000)}
        elif e == "24hrMiniTicker":
            # Raw per-symbol miniTicker frames also flow here when subscribed
            # individually; keep the cache fresh from either path.
            symbol = data.get("s")
            if symbol:
                now = int(time.time() * 1000)
                self.all_tickers[symbol] = {
                    "price": float(data.get("c", 0) or 0),
                    "open": float(data.get("o", 0) or 0),
                    "high": float(data.get("h", 0) or 0),
                    "low": float(data.get("l", 0) or 0),
                    "quote_volume": float(data.get("q", 0) or 0),
                    "time": now,
                }
        # Kline events intentionally not cached — same decision as spot.

    async def get_current_price(self, symbol):
        now = int(time.time() * 1000)
        entry = self.last_price.get(symbol)
        if entry and (now - entry["time"] < 5000):
            return entry["price"]
        t = self.all_tickers.get(symbol)
        if t and (now - t["time"] < 10000):
            return t["price"]
        return None
