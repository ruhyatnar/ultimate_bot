import asyncio
import json
import logging
import time
import websockets

class WSStreamClient:
    STREAM_URL = "wss://stream.binance.com:9443/ws"
    STREAM_URL_TESTNET = "wss://testnet.binance.vision/ws"

    ALL_MARKET_STREAM = "!miniTicker@arr"  # push update ~every 1s, zero REST weight

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
        # All-market mini-ticker stream (dedicated socket, independent of the
        # per-symbol socket) — realtime prices for the screener/web monitor.
        self._arr_websocket = None
        self._arr_listen_task = None
        self.all_tickers = {}  # {symbol: {price, open, high, low, quote_volume, time}}

    # Bound initial connect attempts so a dead network fails fast into REST fallback
    # instead of hanging main() startup forever. Reconnect-after-drop uses its own
    # longer backoff loop (_reconnect_loop).
    MAX_CONNECT_ATTEMPTS = 5

    async def connect(self, symbols):
        if len(symbols) > 500:
            self.logger.warning(f"Too many symbols ({len(symbols)}). Limit to 500.")
            symbols = symbols[:500]
        last_error = None
        for attempt in range(1, self.MAX_CONNECT_ATTEMPTS + 1):
            try:
                self.logger.info(f"Connecting to WebSocket Stream (attempt {attempt}/{self.MAX_CONNECT_ATTEMPTS}) for symbols: {symbols}")
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
                self.logger.info(f"Subscribed to {len(symbols)} symbols.")
                self._listen_task = asyncio.create_task(self._listen())
                return
            except Exception as e:
                last_error = e
                self.logger.error(f"Stream connection attempt {attempt} failed: {e}")
                await self.disconnect()
                if attempt < self.MAX_CONNECT_ATTEMPTS:
                    await asyncio.sleep(min(2 ** attempt, 30))
        self.logger.error(
            f"WebSocket Stream unavailable after {self.MAX_CONNECT_ATTEMPTS} attempts ({last_error}). "
            "Continuing with REST price polling; background reconnects will retry."
        )

    async def connect_all_market_tickers(self):
        """Open a DEDICATED socket for the all-market !miniTicker@arr stream.

        Pushes every symbol's last price ~once per second, so the screener and
        the web monitor get realtime prices without ANY REST polling. Runs on
        its own socket so per-symbol subscribe/unsubscribe can never disturb
        it, and reconnects forever with capped backoff (silent — failure just
        degrades to REST fallback).
        """
        if self._arr_websocket is not None and self.is_connected():
            return True
        try:
            ws = await websockets.connect(self.stream_url, ping_interval=20, ping_timeout=10)
            await ws.send(json.dumps({"method": "SUBSCRIBE", "params": [self.ALL_MARKET_STREAM], "id": 1}))
            resp = await asyncio.wait_for(ws.recv(), timeout=10)
            if '"error"' in resp:
                raise Exception(f"all-market subscribe error: {resp}")
            self._arr_websocket = ws
            self.logger.info(f"All-market mini-ticker stream ({self.ALL_MARKET_STREAM}) subscribed.")
            if self._arr_listen_task is None or self._arr_listen_task.done():
                self._arr_listen_task = asyncio.create_task(self._arr_listen())
            return True
        except Exception as e:
            self.logger.warning(f"All-market ticker stream unavailable ({e}); screener falls back to REST.")
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
            except (websockets.exceptions.ConnectionClosed, websockets.exceptions.WebSocketException, OSError, asyncio.TimeoutError) as e:
                self.logger.warning(f"All-market ticker stream dropped ({e}); reconnecting...")
                self._arr_websocket = None
                await asyncio.sleep(min(backoff, 60))
                backoff = min(backoff * 2, 60)
            except asyncio.CancelledError:
                return

    def get_all_tickers(self):
        """Snapshot of the all-market ticker cache (read-only for callers).
        Empty dict until the stream has delivered its first frame."""
        return self.all_tickers if self.all_tickers else {}

    def is_arr_stream_alive(self):
        t = max((v["time"] for v in self.all_tickers.values()), default=0)
        return bool(t) and (int(time.time() * 1000) - t) < 15000

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
        await self.websocket.send(json.dumps({"method": "SUBSCRIBE", "params": streams, "id": int(time.time()*1000)}))
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
        await self.websocket.send(json.dumps({"method": "UNSUBSCRIBE", "params": streams, "id": int(time.time()*1000)}))
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
            # Bounded close: a listener cancelled mid-`recv()` can leave the
            # connection's `connection_lost_waiter` unset, which would otherwise
            # hang `websocket.close()` forever and wedge the engine's shutdown.
            try:
                await asyncio.wait_for(self.websocket.close(), timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self.logger.warning("WebSocket close timed out during disconnect; aborting connection.")
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
        # Tear down the dedicated all-market ticker socket as well.
        if self._arr_listen_task:
            self._arr_listen_task.cancel()
            self._arr_listen_task = None
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
            # Modern websockets (v12+ / v13+ / v14+ / v15+): uses State enum
            state = getattr(self.websocket, "state", None)
            if state is not None:
                state_name = getattr(state, "name", str(state))
                return state_name == "OPEN" or state == 1 or "OPEN" in str(state)
            # Legacy websockets: .open or .closed attribute
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
            except (websockets.exceptions.ConnectionClosed, websockets.exceptions.WebSocketException, OSError, asyncio.TimeoutError) as e:
                self.connected = False
                self.logger.warning(f"WebSocket Stream disconnected ({e}). Reconnecting...")
                # Reconnect on a fresh socket object. Calling self.connect() directly
                # here would block the listener and, worse, self.connect() reassigns
                # self.websocket while this coroutine still holds the dead one.
                if not self._reconnect_task or self._reconnect_task.done():
                    self._reconnect_task = asyncio.create_task(
                        self._reconnect_loop(list(self._subscribed_symbols))
                    )
                break
            except asyncio.CancelledError:
                break

    async def _reconnect_loop(self, symbols):
        """Exponential-backoff reconnect that always builds a fresh websocket.
        NOTE: deliberately avoids self.disconnect() — it would cancel this very task."""
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
                    self.logger.info("WebSocket Stream reconnected successfully.")
                    return
            except Exception as e:
                self.logger.warning(f"Stream reconnect attempt {attempt} failed: {e}")
            await asyncio.sleep(min(2 ** attempt, 60))
        self.logger.error("WebSocket Stream reconnect abandoned after repeated failures; price reads fall back to REST.")

    async def _process(self, data):
        e = data.get("e")
        if e == "aggTrade":
            symbol = data.get("s")
            if symbol:
                self.last_price[symbol] = {"price": float(data.get("p", 0)), "time": int(time.time() * 1000)}
        # Kline events are intentionally not cached: the engine reads OHLCV straight
        # from REST when needed (signal generation, ATR refresh, gap checks) and a
        # websocket-side kline buffer was never read by any consumer.

    async def get_current_price(self, symbol):
        now = int(time.time() * 1000)
        entry = self.last_price.get(symbol)
        if entry and (now - entry["time"] < 5000):
            return entry["price"]
        # Fall back to the all-market mini-ticker cache (any symbol, ~1s old).
        t = self.all_tickers.get(symbol)
        if t and (now - t["time"] < 10000):
            return t["price"]
        return None
