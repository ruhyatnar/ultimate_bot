"""USDⓈ-M Futures WebSocket-API client (wss://ws-fapi.binance.com/ws-fapi/v1).

Implements the Binance USDⓈ-M Futures **WebSocket API** (request/response over
a signed session) and bridges the **user data stream** (separate socket):

  * session.logon / session.status / session.logout — Ed25519-only session auth
  * order.place / order.cancel — signed trade ops on the session
  * user data stream — wss://fstream.binance.com/ws/<listenKey> carrying
    ACCOUNT_UPDATE (balances/positions) and ORDER_TRADE_UPDATE (fills)

Docs:
  https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-api/account
  https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-api-general-info
  https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/user-data-streams

Contract notes from the docs that this client honours:
  * Signature payload = all params except signature, sorted by name (alphabetical).
  * Session auth is Ed25519-only (no HMAC fallback on the WS API).
  * A connection is valid for 24h; expect disconnects — reconnect+logon is built in.
  * DECIMAL params (price/quantity) are JSON strings; INT (timestamp) is a JSON int.
  * User data arrives on a SEPARATE connection keyed by a listenKey issued via
    fapi REST (POST /fapi/v1/listenKey), kept alive every 30 min (valid 60 min).

The balance/fill caches mirror the spot WSApiClient shape exactly
(balances / _order_states / wait_for_fill_event) so the engine's risk and
order layers consume both markets through one interface.
"""
import asyncio
import json
import time

import websockets

from src.exchange.ws_api_client import WSApiClient


class FuturesWSApiClient(WSApiClient):
    WS_URL = "wss://ws-fapi.binance.com/ws-fapi/v1"
    WS_URL_TESTNET = "wss://testnet.binancefuture.com/ws-fapi/v1"
    # User data stream (separate socket; listenKey appended)
    USER_STREAM_URL = "wss://fstream.binance.com/ws"
    USER_STREAM_URL_TESTNET = "wss://demo-fstream.binance.com/ws"
    # listenKey is valid 60 min; refresh at half-life
    LISTENKEY_KEEPALIVE_S = 1800

    def __init__(self, config):
        super().__init__(config)
        self.use_testnet = config.get("USE_TESTNET", False)
        self.ws_url = self.WS_URL_TESTNET if self.use_testnet else self.WS_URL
        self.user_stream_url = self.USER_STREAM_URL_TESTNET if self.use_testnet else self.USER_STREAM_URL
        # FuturesRestClient handle (set by main) — issues/keeps the listenKey
        self.futures_rest = None
        # positions cached from ACCOUNT_UPDATE: symbol -> {amt, entry, ...}
        self.positions = {}
        self._user_ws = None
        self._user_task = None
        self._keepalive_task = None
        self._listen_key = None

    def seed_balances(self, account):
        """Futures override of the WS balance-cache seed.

        Two reasons this cannot reuse the spot implementation unchanged:
          * /fapi/v3/account has NO 'balances' array (v3 keeps per-asset rows
            under 'assets'), so the inherited method would seed nothing and
            every cached_account() read would fall back to REST forever;
          * futures reports wallet vs available margin — merged here as
            free = availableBalance (margin not locked by positions) and
            locked = balance - availableBalance, so the pre-trade free-quote
            check reflects spendable margin, not the whole wallet.
        Accepts /fapi/v3/balance (array), /fapi/v3/account ('assets'), or the
        spot-style dict (tolerated).
        """
        try:
            now_ms = int(time.time() * 1000)
            if isinstance(account, list):
                rows = account
            elif isinstance(account, dict):
                rows = account.get("assets")
                if rows is None:
                    rows = account.get("balances")  # legacy/spot shape
            else:
                return
            if not rows:
                return
            for b in rows:
                try:
                    asset = str(b.get("asset") or "")
                    if not asset:
                        continue
                    # A row is a real holding only when the WALLET balance is
                    # non-zero. In multi-assets collateral mode Binance reports a
                    # collateral-equivalent `availableBalance` for every asset the
                    # wallet *could* use as margin (U/BFUSD/USDC/RWUSD/BNB/BTC/ETH/
                    # USD1/FDUSD/LDUSDT/...) even when it holds none of it — the same
                    # USDT wallet restated in other denominations. Counting those
                    # equivalents prices N units of BNB/BTC/ETH at the real ticker
                    # and inflates equity ~5x ($23 -> $111.75), which then feeds
                    # position sizing, the drawdown breaker and the dashboard.
                    if "balance" in b or "walletBalance" in b or "wb" in b:
                        bal = float(b.get("balance") or b.get("walletBalance")
                                    or b.get("wb") or 0.0)
                    else:  # legacy/spot shape: free + locked IS the holding
                        bal = float(b.get("free") or 0.0) + float(b.get("locked") or 0.0)
                    if bal <= 0:
                        continue
                    avail = float(b.get("availableBalance") or b.get("withdrawAvailable")
                                  or b.get("free") or 0.0)
                    locked = max(0.0, bal - avail) if bal > avail else 0.0
                    self.balances[asset] = {"free": avail, "locked": locked, "time": now_ms}
                except (TypeError, ValueError):
                    continue
            self._balances_seeded_at = now_ms
        except Exception as e:
            self.logger.debug(f"futures seed_balances failed: {e}")

    async def _initial_balance_seed(self):
        """One-shot REST seed right after the user stream starts.

        Futures ACCOUNT_UPDATE only fires on CHANGES (no periodic full
        snapshot like spot's outboundAccountPosition), so without this seed
        the WS cache would stay empty — and the dashboard would show the REST
        fallback — until the first fill or funding event.
        """
        try:
            if self.futures_rest is not None:
                self.seed_balances(await self.futures_rest.get_balance())
                self.logger.info("Futures WS balance cache seeded from /fapi/v3/balance.")
        except Exception as e:
            self.logger.debug(f"initial futures balance seed failed: {e}")

    # ------------------------------------------------------------------ core

    # ------------------------------------------------------------------ core

    async def _get_server_time(self):
        """fapi host/time endpoint (spot override hits /api/v3 on api.binance.com)."""
        import aiohttp
        session = await self._get_session()
        base = "https://demo-fapi.binance.com" if self.use_testnet else "https://fapi.binance.com"
        async with session.get(f"{base}/fapi/v1/time") as resp:
            data = await resp.json()
            return data["serverTime"]

    # Sync cadence matches the REST clients; see WSApiClient.sync_time for the
    # midpoint-estimator rationale. Measured offset is published by trade_logic
    # as ws_streams.order_api.time_offset_ms for the web monitor's Clock sync row.
    TIME_SYNC_INTERVAL_S = 300

    async def sync_time(self):
        """Measure the local-clock offset vs Binance Futures. Never raises: a
        failed sync keeps the previous offset (best known estimate)."""
        try:
            t0 = time.time() * 1000
            server_time = await self._get_server_time()
            t1 = time.time() * 1000
            self.time_offset = server_time - (t0 + t1) / 2
            self.last_time_sync = time.time()
            self.logger.debug(f"Futures WS API time offset set to {self.time_offset:+.0f} ms (rtt {t1 - t0:.0f} ms)")
        except Exception as e:
            self.logger.warning(f"Futures WS API time sync failed: {e}")

    async def _time_sync_loop(self):
        """Re-measure the offset periodically while connected (see spot client)."""
        while self.connected:
            await asyncio.sleep(self.TIME_SYNC_INTERVAL_S)
            if self.connected:
                await self.sync_time()

    async def _subscribe_user_stream(self):
        """Futures user data = SEPARATE socket via fapi listenKey (NOT the spot
        userDataStream.subscribe). Needs FuturesRestClient; failure is
        non-fatal — fill confirmation stays on REST polling."""
        if self.futures_rest is None:
            self.logger.warning(
                "Futures user data stream unavailable (no FuturesRestClient handle); "
                "fill confirmation stays on REST polling.")
            self.user_stream_active = False
            return
        try:
            self._listen_key = await self.futures_rest.create_listen_key()
        except Exception as e:
            self.logger.warning(f"listenKey creation failed ({e}); user data stream off, REST fallback.")
            self.user_stream_active = False
            return
        if self._user_task and not self._user_task.done():
            self._user_task.cancel()
        self._user_task = asyncio.create_task(self._user_stream_loop())
        self.user_stream_active = True
        self.logger.info("Futures user data stream starting on separate socket.")
        # Seed the balance cache immediately (ACCOUNT_UPDATE is change-only).
        asyncio.create_task(self._initial_balance_seed())

    async def _user_stream_loop(self):
        """Listen on wss://fstream.../ws/<listenKey>; reconnect forever with a
        FRESH listenKey when the old one expires (docs: listenKeyExpired event,
        or the socket simply dies). Non-fatal by design."""
        backoff = 1
        while True:
            try:
                if not self._listen_key:
                    # Inline key creation: calling _subscribe_user_stream here
                    # would recursively spawn ANOTHER loop task (cancelling this
                    # one), resetting backoff to 1 every cycle — a permanent
                    # ~1.3s retry storm against a blocked endpoint.
                    if self.futures_rest is None:
                        self.logger.warning("Futures user stream: no REST handle; retrying later.")
                        await asyncio.sleep(min(backoff, 60))
                        backoff = min(backoff * 2, 60)
                        continue
                    self._listen_key = await self.futures_rest.create_listen_key()
                url = f"{self.user_stream_url}/{self._listen_key}"
                self._user_ws = await websockets.connect(url, ping_interval=20, ping_timeout=10)
                backoff = 1
                self.user_stream_active = True
                self.logger.info("Futures user data stream connected.")
                # Half-life keepalive while this socket lives
                if self._keepalive_task and not self._keepalive_task.done():
                    self._keepalive_task.cancel()
                rest = self.futures_rest
                async def _keepalive():
                    while True:
                        await asyncio.sleep(self.LISTENKEY_KEEPALIVE_S)
                        try:
                            await rest.keepalive_listen_key(self._listen_key)
                        except Exception as e:
                            self.logger.warning(f"listenKey keepalive failed: {e}")
                self._keepalive_task = asyncio.create_task(_keepalive())
                # Read loop
                while True:
                    msg = await self._user_ws.recv()
                    data = json.loads(msg)
                    if isinstance(data, dict):
                        self._dispatch_user_event(data)
            except asyncio.CancelledError:
                return
            except Exception as e:
                # Honest state while down: consumers (fill waiters, balance
                # provenance) must not treat the cache as live-stream-fed.
                self.user_stream_active = False
                self.logger.warning(f"Futures user data stream dropped ({e}); reconnecting with a fresh listenKey...")
            finally:
                if self._keepalive_task and not self._keepalive_task.done():
                    self._keepalive_task.cancel()
                if self._user_ws is not None:
                    try:
                        await asyncio.wait_for(self._user_ws.close(), timeout=3.0)
                    except Exception:
                        pass
                    self._user_ws = None
            # A dead socket OR an expired listenKey both land here: force a new key
            self._listen_key = None
            await asyncio.sleep(min(backoff, 60))
            backoff = min(backoff * 2, 60)

    def _dispatch_user_event(self, data):
        self.last_user_event_ms = int(time.time() * 1000)
        etype = data.get("e")
        if etype == "ACCOUNT_UPDATE":
            self._on_account_update(data)
        elif etype == "ORDER_TRADE_UPDATE":
            self._on_order_trade_update(data)
        elif etype == "listenKeyExpired":
            self.logger.warning("listenKeyExpired — rotating the user data stream key.")
            self._listen_key = None
        # MARGIN_CALL / ACCOUNT_CONFIG_UPDATE / TRADE_LITE: surfaced in logs only
        elif etype == "MARGIN_CALL":
            self.logger.warning("MARGIN CALL received on futures account — margin ratio too high!")

    def _on_account_update(self, ev):
        """ACCOUNT_UPDATE → merge balance deltas ('a'.'B') and positions ('a'.'P')."""
        try:
            a = ev.get("a") or {}
            now = int(ev.get("E") or time.time() * 1000)
            for b in a.get("B") or []:
                asset = b.get("a")
                if not asset:
                    continue
                prev = self.balances.get(asset, {"free": 0.0, "locked": 0.0})
                wb = float(b.get("wb") or 0)          # wallet balance (authoritative)
                self.balances[asset] = {
                    "free": wb,
                    "locked": max(0.0, prev.get("locked", 0.0)),
                    "time": now,
                }
            for p in a.get("P") or []:
                sym = p.get("s")
                if not sym:
                    continue
                amt = float(p.get("pa") or 0)
                if abs(amt) < 1e-12:
                    self.positions.pop(sym, None)
                else:
                    self.positions[sym] = {
                        "amount": amt,
                        "entry_price": float(p.get("ep") or 0),
                        "unrealized_pnl": float(p.get("up") or 0),
                        "leverage": float(p.get("leverage") or 0) or None,
                        "margin_type": p.get("mt"),
                        "time": now,
                    }
        except (ValueError, TypeError):
            pass

    def _on_order_trade_update(self, ev):
        """ORDER_TRADE_UPDATE → same shape as the spot executionReport cache."""
        try:
            o = ev.get("o") or {}
            oid = str(o.get("i"))
            rec = {
                "symbol": o.get("s"),
                "status": o.get("X"),
                "executedQty": float(o.get("z") or 0),
                "price": float(o.get("L") or o.get("ap") or o.get("p") or 0),
                "side": o.get("S"),
                "time": int(o.get("T") or time.time() * 1000),
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

    # ----------------------------------------------------------------- trade

    async def place_order(self, symbol, side, order_type, quantity,
                          position_side="BOTH", reduce_only=False):
        """order.place on the futures WS API.

        Per docs: params sorted alphabetically for signing; price/quantity as
        strings; timestamp int; apiKey + signature included (works both with
        and without an authenticated session).
        """
        if isinstance(quantity, (float, int)):
            qty_str = f"{quantity:.8f}"
        else:
            qty_str = str(quantity)
        if "." in qty_str:
            qty_str = qty_str.rstrip("0").rstrip(".")
        if not qty_str:
            qty_str = "0"
        params = {"apiKey": self.api_key, "symbol": symbol, "side": side,
                  "type": order_type, "quantity": qty_str,
                  "positionSide": position_side,
                  "timestamp": await self._get_timestamp()}
        if reduce_only:
            params["reduceOnly"] = "true"
        query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
        if not self._private_key:
            raise Exception("Private key not available")
        params["signature"] = self._sign_ed25519(query_string)
        return await self.send_request("order.place", params)

    async def cancel_order(self, symbol, order_id):
        params = {"apiKey": self.api_key, "symbol": symbol, "orderId": order_id,
                  "timestamp": await self._get_timestamp()}
        query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
        params["signature"] = self._sign_ed25519(query_string)
        return await self.send_request("order.cancel", params)

    # ------------------------------------------------------------- shutdown

    async def disconnect(self):
        if self._user_task and not self._user_task.done():
            self._user_task.cancel()
            try:
                await self._user_task
            except asyncio.CancelledError:
                pass
            self._user_task = None
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass
            self._keepalive_task = None
        if self._user_ws is not None:
            try:
                await asyncio.wait_for(self._user_ws.close(), timeout=3.0)
            except Exception:
                pass
            self._user_ws = None
        # Invalidate the server-side listenKey too (best-effort)
        if self.futures_rest is not None and self._listen_key:
            try:
                await self.futures_rest.close_listen_key(self._listen_key)
            except Exception:
                pass
        self._listen_key = None
        self.user_stream_active = False
        await super().disconnect()
