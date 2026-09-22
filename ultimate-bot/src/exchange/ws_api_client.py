import asyncio
import json
import logging
import os
import time
import base64
import aiohttp
import websockets
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

class WSApiClient:
    WS_URL = "wss://ws-api.binance.com:443/ws-api/v3"
    WS_URL_TESTNET = "wss://testnet.binance.vision/ws-api/v3"

    def __init__(self, config):
        self.config = config
        self.api_key = config["API_KEY"]
        self.private_key_path = config["PRIVATE_KEY_PATH"]
        self.use_testnet = config.get("USE_TESTNET", False)
        self.ws_url = self.WS_URL_TESTNET if self.use_testnet else self.WS_URL
        self.base_url = "https://testnet.binance.vision" if self.use_testnet else "https://api.binance.com"
        self.logger = logging.getLogger(__name__)
        self.websocket = None
        self.connected = False
        self.request_id = 1
        # Server-clock offset (ms) measured against Binance and applied to every
        # signed request timestamp. Sentinel is None (never synced), NOT 0 —
        # a measured offset of exactly 0 is legitimate and must not re-trigger
        # syncs. Refreshed every TIME_SYNC_INTERVAL_S and on timestamp rejections.
        self.time_offset = None
        self.last_time_sync = 0.0
        self._time_sync_task = None
        self._session = None
        self._private_key = None
        self._monitor_task = None
        self._lock = asyncio.Lock()
        # --- User-data stream (events ride the SAME authenticated socket) ---
        # After session.logon, userDataStream.subscribe delivers
        # executionReport / outboundAccountPosition frames on this connection
        # (no listen key, no REST). Responses are matched by request id;
        # events are dispatched to fill waiters / balance cache.
        self._listen_task = None
        self._pending = {}        # request id(str) -> Future for send_request
        self._waiters = {}        # order id(str) -> Future for fill events
        self._order_states = {}   # order id(str) -> last executionReport
        self.balances = {}        # asset -> {free, locked, time} (merged REST seed + WS deltas)
        self.user_stream_active = False
        self.last_user_event_ms = 0        # newest user-data frame (stream liveness)
        self._balances_seeded_at = 0       # ms of the last REST account snapshot merged in
        try:
            self.balance_max_age = float(config.get("WS_BALANCE_MAX_AGE", 90) or 90)
        except (TypeError, ValueError):
            self.balance_max_age = 90.0
        if self.balance_max_age < 5:
            self.balance_max_age = 5.0
        if not config.get("PAPER_TRADE", False):
            if self.private_key_path and os.path.exists(str(self.private_key_path)):
                try:
                    with open(self.private_key_path, "rb") as f:
                        key = serialization.load_pem_private_key(f.read(), password=None)
                    if isinstance(key, Ed25519PrivateKey):
                        self._private_key = key
                    else:
                        self.logger.warning("Configured private key is not Ed25519; WS API order execution disabled.")
                except Exception as e:
                    self.logger.warning(f"Could not load Ed25519 key from {self.private_key_path} ({e}); WS API order execution disabled.")

    def can_connect(self):
        return self._private_key is not None

    async def _get_session(self):
        if not self._session:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _get_server_time(self):
        session = await self._get_session()
        async with session.get(f"{self.base_url}/api/v3/time") as resp:
            data = await resp.json()
            return data["serverTime"]

    # Binance rejects signed requests whose timestamp is outside
    # recvWindow (default 5000ms) of the server clock (error -1021). Local
    # clocks drift, so every signed timestamp is compensated by a measured
    # offset. Midpoint estimate: serverTime was stamped somewhere in
    # [t0, t1]; serverTime - midpoint has ±rtt/2 error.
    TIME_SYNC_INTERVAL_S = 300  # same cadence as the REST clients

    async def sync_time(self):
        """Measure the local-clock offset vs Binance. Never raises: a failed
        sync keeps the previous offset (best known estimate)."""
        try:
            t0 = time.time() * 1000
            server_time = await self._get_server_time()
            t1 = time.time() * 1000
            self.time_offset = server_time - (t0 + t1) / 2
            self.last_time_sync = time.time()
            self.logger.debug(f"WS API time offset set to {self.time_offset:+.0f} ms (rtt {t1 - t0:.0f} ms)")
        except Exception as e:
            self.logger.warning(f"WS API time sync failed: {e}")

    async def _get_timestamp(self):
        if self.time_offset is None or (time.time() - self.last_time_sync) > self.TIME_SYNC_INTERVAL_S:
            await self.sync_time()
        return int(time.time() * 1000 + self.time_offset)

    async def _time_sync_loop(self):
        """Re-measure the offset periodically while connected. Clocks drift
        (VM migration, NTP steps, thermal jitter); a signed order hours into a
        session must not inherit the logon-time offset."""
        while self.connected:
            await asyncio.sleep(self.TIME_SYNC_INTERVAL_S)
            if self.connected:
                await self.sync_time()

    async def connect(self):
        if not self.can_connect():
            self.logger.info("WebSocket API authentication requires an Ed25519 private key; orders will execute via REST.")
            return
        retry_count = 0
        while True:
            try:
                self.logger.info(f"Connecting to WebSocket API (attempt {retry_count+1})...")
                self.websocket = await websockets.connect(self.ws_url, ping_interval=20, ping_timeout=10)
                self.connected = True
                # A previous listener (reconnect path) must not linger on the old socket.
                if self._listen_task and not self._listen_task.done():
                    self._listen_task.cancel()
                # The event listener must run BEFORE logon: it resolves the
                # logon response future too.
                self._listen_task = asyncio.create_task(self._listen())
                await self.logon()
                self.logger.info("WebSocket API connected and authenticated.")
                await self._subscribe_user_stream()
                self._monitor_task = asyncio.create_task(self._monitor_connection())
                # Offset re-measurement loop (see sync_time). Cancel any stray
                # from a previous connection before starting a new one.
                if self._time_sync_task and not self._time_sync_task.done():
                    self._time_sync_task.cancel()
                self._time_sync_task = asyncio.create_task(self._time_sync_loop())
                return
            except Exception as e:
                self.logger.error(f"WebSocket API connection failed: {e}")
                await self.disconnect()
                retry_count += 1
                if retry_count > 5:
                    raise
                await asyncio.sleep(min(2 ** retry_count, 30))

    async def _subscribe_user_stream(self):
        """Subscribe to user-data events on this authenticated session.
        Failure is non-fatal: fill confirmation stays on REST polling."""
        try:
            res = await self.send_request("userDataStream.subscribe")
            self.user_stream_active = True
            sub_id = (res.get("result") or {}).get("subscriptionId")
            self.logger.info(f"User-data stream subscribed on WS API session (subscriptionId={sub_id}).")
        except Exception as e:
            self.logger.warning(f"userDataStream.subscribe failed ({e}); fill confirmation stays on REST polling.")
            self.user_stream_active = False

    async def disconnect(self):
        current_task = asyncio.current_task()
        if self._listen_task and self._listen_task is not current_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None
        if self._monitor_task and self._monitor_task is not current_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        self._monitor_task = None
        # Stop the periodic clock-offset re-measurement with the connection.
        if self._time_sync_task and self._time_sync_task is not current_task:
            self._time_sync_task.cancel()
            try:
                await self._time_sync_task
            except asyncio.CancelledError:
                pass
        self._time_sync_task = None
        for fut in self._waiters.values():
            if not fut.done():
                fut.cancel()
        self._waiters.clear()
        self._pending.clear()
        self.user_stream_active = False
        if self.websocket:
            # Bounded close so a torn-down session can never hang the shutdown path.
            try:
                await asyncio.wait_for(self.websocket.close(), timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
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
        if self._session:
            try:
                await asyncio.wait_for(self._session.close(), timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            except Exception:
                pass
            self._session = None

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

    async def _monitor_connection(self):
        while self.connected:
            await asyncio.sleep(15)
            if not self.is_connected():
                self.logger.warning("WebSocket API connection lost. Reconnecting...")
                self.connected = False
                await self.connect()
                break

    async def send_request(self, method, params=None):
        """Send a WS API request; the response arrives via _listen() and is
        matched here by request id (events never consume responses)."""
        async with self._lock:
            req_id = str(self.request_id)
            self.request_id += 1
            fut = asyncio.get_running_loop().create_future()
            self._pending[req_id] = fut
            try:
                await self.websocket.send(json.dumps({"id": req_id, "method": method, "params": params or {}}))
                resp = await asyncio.wait_for(fut, timeout=15)
            except asyncio.TimeoutError:
                raise Exception(f"WS API request {method} timed out")
            finally:
                self._pending.pop(req_id, None)
            if resp.get("status") != 200:
                # -1021: our timestamp fell outside the server's recvWindow.
                # Re-measure the offset immediately so the @async_retry caller
                # (order placement retries with a fresh timestamp) can succeed.
                err = resp.get("error") or {}
                err_code = err.get("code") if isinstance(err, dict) else None
                if err_code == -1021:
                    await self.sync_time()
                raise Exception(f"API error {resp.get('status')}: {resp.get('error')}")
            return resp

    async def _listen(self):
        """Single reader for the WS API socket. Matches RESPONSES by request id
        and dispatches EVENTS (user-data stream) to the handlers."""
        while self.connected:
            try:
                msg = await self.websocket.recv()
                data = json.loads(msg)
            except (websockets.exceptions.ConnectionClosed, websockets.exceptions.WebSocketException,
                    OSError, asyncio.TimeoutError):
                break
            except asyncio.CancelledError:
                return
            except (json.JSONDecodeError, TypeError):
                continue
            if "id" in data and str(data.get("id")) in self._pending:
                fut = self._pending.pop(str(data["id"]))
                if not fut.done():
                    fut.set_result(data)
            elif data.get("event") is not None:
                self._dispatch_event(data.get("event"))
            # Frames with neither id nor event are ignored (e.g. pongs as text).

    def _dispatch_event(self, ev):
        self.last_user_event_ms = int(time.time() * 1000)
        etype = ev.get("e")
        if etype == "executionReport":
            self._on_execution_report(ev)
        elif etype == "outboundAccountPosition":
            self._on_account_position(ev)

    def _on_execution_report(self, d):
        try:
            oid = str(d.get("i"))
            rec = {
                "symbol": d.get("s"),
                "status": d.get("X"),
                "executedQty": float(d.get("z") or 0),
                "price": float(d.get("L") or d.get("p") or 0),
                "side": d.get("S"),
                "time": int(d.get("T") or time.time() * 1000),
            }
        except (ValueError, TypeError):
            return
        if not oid or not rec["status"]:
            return
        self._order_states[oid] = rec
        fut = self._waiters.get(oid)
        if fut and not fut.done() and rec["status"] in ("FILLED", "CANCELED", "REJECTED", "EXPIRED"):
            self._waiters.pop(oid, None)
            fut.set_result(rec)

    def _on_account_position(self, d):
        try:
            for b in d.get("B", []):
                self.balances[b.get("a")] = {
                    "free": float(b.get("f") or 0),
                    "locked": float(b.get("l") or 0),
                    "time": int(d.get("E") or time.time() * 1000),
                }
        except (ValueError, TypeError):
            pass

    # --- WS-first account balance snapshot ---------------------------------
    # The user-data stream pushes outboundAccountPosition whenever a balance
    # changes, which is strictly fresher than REST polling and costs no request
    # weight. A REST /api/v3/account snapshot is merged in whenever one is taken
    # (seed_balances), so every asset keeps a known value; WS deltas then update
    # exactly the assets the exchange reports as changed.

    def seed_balances(self, account):
        """Merge a REST account snapshot into the WS balance cache.

        A REST /api/v3/account response is a full authoritative read of every
        asset at that instant, so it re-stamps the whole cache; WS deltas
        received afterwards update exactly the assets the exchange reported as
        changed (strictly newer data wins).
        """
        if not isinstance(account, dict):
            return
        now_ms = int(time.time() * 1000)
        for b in account.get("balances") or []:
            try:
                asset = str(b.get("asset") or "")
                if not asset:
                    continue
                self.balances[asset] = {
                    "free": float(b.get("free") or 0),
                    "locked": float(b.get("locked") or 0),
                    "time": now_ms,
                }
            except (TypeError, ValueError):
                continue
        self._balances_seeded_at = now_ms

    def balance_age_s(self):
        """Seconds since the newest confirmation of the cached balances.

        Balances only change on user-data events, so the freshest of (per-asset
        WS delta, REST seed, ANY user-data frame) is the honest trust window:
        a frame proves the stream is live and the cache reconciled with it.
        Returns None when nothing has ever been cached.
        """
        stamps = [self._balances_seeded_at, getattr(self, "last_user_event_ms", 0)]
        stamps += [int(v.get("time") or 0) for v in self.balances.values()]
        newest = max(stamps)
        if newest <= 0 or not self.balances:
            return None
        return max(0.0, (time.time() * 1000 - newest) / 1000)

    def cached_account(self, max_age=None):
        """Account-shaped dict served from the WS cache, or None when the cache
        cannot be trusted (stream down / no data / older than max_age).

        Returns {'balances': [...], 'source': 'ws', 'age_s': float}.
        """
        if not self.is_connected() or not self.user_stream_active or not self.balances:
            return None
        age = self.balance_age_s()
        limit = self.balance_max_age if max_age is None else float(max_age)
        if age is None or age > limit:
            return None
        balances = [
            {"asset": asset, "free": v.get("free", 0.0), "locked": v.get("locked", 0.0)}
            for asset, v in self.balances.items()
            if (v.get("free", 0.0) + v.get("locked", 0.0)) > 0
        ]
        if not balances:
            return None
        return {"balances": balances, "source": "ws", "age_s": round(age, 2)}

    async def wait_for_fill_event(self, symbol, order_id, timeout):
        """Wait for an order's terminal executionReport. Returns the event dict
        or None on timeout / when the user-data subscription isn't active."""
        if not self.user_stream_active or not self.is_connected():
            return None
        oid = str(order_id)
        rec = self._order_states.get(oid)
        if rec and rec.get("symbol") == symbol and rec["status"] in ("FILLED", "CANCELED", "REJECTED", "EXPIRED"):
            return rec
        fut = asyncio.get_running_loop().create_future()
        self._waiters[oid] = fut
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._waiters.pop(oid, None)
            return None
        except asyncio.CancelledError:
            self._waiters.pop(oid, None)
            raise

    async def logon(self):
        # Measure (or re-measure) the offset first so the logon signature
        # itself carries a server-anchored timestamp.
        await self.sync_time()
        params = {"timestamp": await self._get_timestamp(), "apiKey": self.api_key}
        query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
        if not self._private_key:
            raise Exception("Private key not available")
        params["signature"] = self._sign_ed25519(query_string)
        result = await self.send_request("session.logon", params)
        if result.get("status") != 200:
            raise Exception(f"session.logon failed: {result}")

    def _sign_ed25519(self, message):
        return base64.b64encode(self._private_key.sign(message.encode())).decode()

    async def place_order(self, symbol, side, order_type, quantity, reduce_only=False):
        # reduce_only is futures-only (see FuturesWSApiClient.place_order); spot
        # ignores it — a spot SELL always reduces the base-asset holding.
        if isinstance(quantity, (float, int)):
            qty_str = f"{quantity:.8f}"
        else:
            qty_str = str(quantity)
        if "." in qty_str:
            qty_str = qty_str.rstrip("0").rstrip(".")
        if not qty_str:
            qty_str = "0"
        params = {"symbol": symbol, "side": side, "type": order_type, "quantity": qty_str,
                  "timestamp": await self._get_timestamp(), "apiKey": self.api_key}
        query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
        params["signature"] = self._sign_ed25519(query_string)
        return await self.send_request("order.place", params)

    async def cancel_order(self, symbol, order_id):
        params = {"symbol": symbol, "orderId": order_id,
                  "timestamp": await self._get_timestamp(), "apiKey": self.api_key}
        query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
        params["signature"] = self._sign_ed25519(query_string)
        return await self.send_request("order.cancel", params)
