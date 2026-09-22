import asyncio
import time
import base64
import hashlib
import hmac
import json
import logging
from pathlib import Path
from urllib.parse import urlencode, quote
from src.core.backoff import NonRetryableError
import aiohttp
from aiolimiter import AsyncLimiter
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from src.core.backoff import async_retry

class RestClient:
    BASE_URL = "https://api.binance.com"
    BASE_URL_TESTNET = "https://testnet.binance.vision"

    def __init__(self, config):
        self.config = config
        self.api_key = config["API_KEY"]
        self.api_secret = config.get("API_SECRET")
        self.private_key_path = config.get("PRIVATE_KEY_PATH")
        self.use_testnet = config.get("USE_TESTNET", False)
        self.base_url = self.BASE_URL_TESTNET if self.use_testnet else self.BASE_URL
        self.logger = logging.getLogger(__name__)
        self.session = None
        self.limiter = AsyncLimiter(config.get("REST_WEIGHT_LIMIT", 1200), 60)
        self.exchange_info_cache = {}
        self.symbol_info_cache = {}
        self._exchange_info_ts = 0.0
        self.timeout = aiohttp.ClientTimeout(total=15)
        self.time_offset = 0
        self.last_time_sync = 0
        self._private_key = None
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._time_sync_task = None
        if not config.get("PAPER_TRADE", False):
            pem_path = Path(self.private_key_path) if self.private_key_path else None
            if pem_path and pem_path.exists():
                with open(pem_path, "rb") as f:
                    key = serialization.load_pem_private_key(f.read(), password=None)
                if not isinstance(key, Ed25519PrivateKey):
                    raise ValueError("Private key is not Ed25519")
                self._private_key = key
                self.logger.info("RestClient configured with Ed25519 asymmetric signature.")
            elif self.api_secret:
                self.logger.info("RestClient configured with HMAC-SHA256 API secret.")
            else:
                raise ValueError("Live trading requires either Ed25519 private key or BINANCE_API_SECRET.")

    async def _ensure_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=self.timeout)

    async def _load_exchange_info(self):
        # Refresh exchangeInfo every 24h: Binance occasionally delists symbols or
        # changes LOT_SIZE/minNotional filters — a stale cache causes -1013 rejections.
        now = time.time()
        if self.exchange_info_cache and now - self._exchange_info_ts < 86400:
            return
        data = await self._request_internal("GET", "/api/v3/exchangeInfo")
        self.exchange_info_cache = data
        self.symbol_info_cache = {s["symbol"]: s for s in data.get("symbols", [])}
        self._exchange_info_ts = now
        self.logger.info(f"Exchange Info cached for {len(self.symbol_info_cache)} symbols.")

    async def init(self):
        async with self._init_lock:
            if self._initialized:
                return
            await self._ensure_session()
            await self.sync_time()
            await self._load_exchange_info()
            self._initialized = True
            self._time_sync_task = asyncio.create_task(self._periodic_time_sync())

    async def _periodic_time_sync(self):
        while True:
            await asyncio.sleep(600)
            try:
                await self.sync_time()
            except Exception as e:
                self.logger.warning(f"Periodic time sync failed: {e}")

    async def close(self):
        # Cancel the periodic time-sync loop so it can't outlive the client or
        # wedge the event loop as a pending task during shutdown.
        if self._time_sync_task:
            self._time_sync_task.cancel()
            try:
                await self._time_sync_task
            except asyncio.CancelledError:
                pass
            self._time_sync_task = None
        if self.session:
            await self.session.close()
            self.session = None
            self._initialized = False

    async def sync_time(self):
        try:
            resp = await self._request_internal("GET", "/api/v3/time")
            self.time_offset = resp["serverTime"] - int(time.time() * 1000)
            self.last_time_sync = int(time.time())
            self.logger.debug(f"Time offset set to {self.time_offset} ms")
        except Exception as e:
            self.logger.warning(f"Time sync failed: {e}")

    async def _get_timestamp(self):
        if self.time_offset == 0 or (int(time.time()) - self.last_time_sync) > 300:
            await self.sync_time()
        return int(time.time() * 1000) + self.time_offset

    async def _request_internal(self, method, endpoint, params=None, signed=False):
        await self._ensure_session()
        url = f"{self.base_url}{endpoint}"
        headers = {"X-MBX-APIKEY": self.api_key} if self.api_key else {}
        # Copy: never mutate the caller's dict. async_retry re-invokes this
        # method with the SAME dict; a mutated dict would carry the stale
        # 'signature' from the previous attempt into the new signed payload
        # (-> permanent -1022 on every retry of a failed signed request).
        params = dict(params) if params else {}
        if signed:
            params["timestamp"] = await self._get_timestamp()
            # Sign EXACTLY the string that will be transmitted. aiohttp encodes
            # params in insertion order (not sorted), so signing a sorted query
            # while sending an unsorted one invalidates the signature for any
            # request with more than one param (Binance error -1022).
            # The signature param itself is appended after signing, so it is
            # excluded from the signed payload — same contract as sorted signing.
            query_string = urlencode(list(params.items()))
            if self._private_key:
                params["signature"] = self._sign_ed25519(query_string)
            elif self.api_secret:
                params["signature"] = self._sign_hmac_sha256(query_string)
            else:
                raise NonRetryableError("Neither Ed25519 private key nor API secret available for signing.")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            # Transmit the pre-encoded signed query verbatim so Binance verifies
            # byte-for-byte the same string we signed. The Ed25519 signature is
            # base64 (may contain +/=) so it must be percent-encoded in the URL,
            # while Binance verifies it over the raw base64 string.
            url = f"{url}?{query_string}&signature={quote(params['signature'], safe='')}"
            params = None
        async with self.limiter:
            async with self.session.request(method, url, params=params, headers=headers) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    self.logger.error(f"REST error {resp.status}: {text}")
                    if resp.status == 429:
                        retry_after = resp.headers.get("Retry-After")
                        wait_s = int(retry_after) if retry_after and retry_after.isdigit() else 30
                        self.logger.warning(f"Rate limited (HTTP 429)! Backing off for {wait_s}s...")
                        await asyncio.sleep(wait_s)
                        raise Exception(f"Rate limited (HTTP 429), backed off {wait_s}s")
                    elif resp.status == 418:
                        self.logger.critical("Binance IP ban triggered (HTTP 418)! Backing off 120s...")
                        await asyncio.sleep(120)
                        raise Exception("Binance IP ban (HTTP 418), backed off 120s")
                    try:
                        data = json.loads(text)
                        code = data.get("code")
                        msg = data.get("msg", "")
                        if code == -1021:
                            await self.sync_time()
                            raise Exception("Timestamp error, retry after sync")
                        elif code == -2010:
                            raise Exception("Insufficient balance")
                        elif "maintenance" in msg.lower() or "system busy" in msg.lower():
                            raise Exception("Exchange maintenance")
                    except json.JSONDecodeError:
                        pass
                    raise Exception(f"REST error {resp.status}: {text}")
                return await resp.json()

    async def _request(self, method, endpoint, params=None, signed=False):
        return await self._request_with_retry(method, endpoint, params, signed)

    @async_retry(max_retries=3, backoff=2)
    async def _request_with_retry(self, method, endpoint, params, signed):
        return await self._request_internal(method, endpoint, params, signed)

    def _sign_ed25519(self, message: str) -> str:
        return base64.b64encode(self._private_key.sign(message.encode())).decode()

    def _sign_hmac_sha256(self, message: str) -> str:
        return hmac.new(self.api_secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()

    async def ping(self):
        return await self._request("GET", "/api/v3/ping")

    async def get_symbol_info(self, symbol):
        await self._load_exchange_info()
        return self.symbol_info_cache.get(symbol)

    async def get_filters(self, symbol):
        info = await self.get_symbol_info(symbol)
        if not info:
            # Unknown/delisted symbol: raise a clear, NON-retryable error instead of
            # an opaque AttributeError. Callers treat this as "cannot trade this
            # symbol" and skip it (never retried, never mistaken for a network blip).
            raise NonRetryableError(f"Symbol {symbol} is not present in exchangeInfo (unknown or delisted).")
        return {f["filterType"]: f for f in info.get("filters", [])}

    async def get_ticker(self, symbol):
        return await self._request("GET", "/api/v3/ticker/price", {"symbol": symbol})

    async def get_tickers_bulk(self, symbols):
        """Current prices for many symbols in ONE request (weight 4 for <=100 syms).

        Cheap enough to poll every PRICE_REFRESH_INTERVAL seconds to keep the
        screener table's prices (and the web monitor) aligned with the exchange.
        Returns {symbol: float_price} on success, {} on failure (callers skip).
        """
        try:
            rows = await self._request(
                "GET", "/api/v3/ticker/price",
                # Compact separators are REQUIRED: default json.dumps inserts a space
                # ("AAA", "BBB") which URL-encodes to '+' and Binance rejects with -1100.
                {"symbols": json.dumps(list(symbols), separators=(",", ":"))},
            )
            return {r["symbol"]: float(r["price"]) for r in rows if r.get("symbol")}
        except (ValueError, TypeError, KeyError) as e:
            self.logger.warning(f"Bulk ticker fetch failed: {e}")
            return {}

    async def get_24hr_tickers(self):
        return await self._request("GET", "/api/v3/ticker/24hr")

    # NOTE: the REST user-data stream (POST/PUT/DELETE /api/v3/userDataStream,
    # i.e. listen-key create/keepalive/close) was removed. It only fed the old
    # REST-polled fill path, and this network cannot reach that endpoint anyway
    # (all Binance hosts return nginx-410 through the WARP egress). User-data
    # events — fills and balances — now arrive on the authenticated WS API
    # session via `userDataStream.subscribe` (see ws_api_client.py), which needs
    # no listen key.

    async def get_klines(self, symbol, interval, limit=500):
        return await self._request("GET", "/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})

    async def get_account(self):
        return await self._request("GET", "/api/v3/account", signed=True)

    async def get_order(self, symbol, order_id):
        return await self._request("GET", "/api/v3/order", {"symbol": symbol, "orderId": order_id}, signed=True)

    async def place_order(self, symbol, side, order_type, quantity, reduce_only=False):
        # `reduce_only` is a FUTURES-only flag (see FuturesRestClient.place_order).
        # It is accepted here purely for call-site symmetry with the WS API and
        # futures clients — on spot a SELL always reduces the base-asset holding,
        # so the flag is intentionally ignored. Without it the engine's shared
        # REST fallback call (order_manager.place_market_order) raised TypeError
        # on every live spot order whenever the WS API session was down.
        if isinstance(quantity, (float, int)):
            qty_str = f"{quantity:.8f}"
        else:
            qty_str = str(quantity)
        if "." in qty_str:
            qty_str = qty_str.rstrip("0").rstrip(".")
        if not qty_str:
            qty_str = "0"
        params = {"symbol": symbol, "side": side, "type": order_type, "quantity": qty_str}
        return await self._request("POST", "/api/v3/order", params, signed=True)

    async def cancel_order(self, symbol, order_id):
        return await self._request("DELETE", "/api/v3/order", {"symbol": symbol, "orderId": order_id}, signed=True)
