"""USDⓈ-M Futures REST client (fapi.binance.com).

Implements the Binance USDⓈ-M Futures REST API surface used by the engine:
  account  — /fapi/v3/account, /fapi/v3/balance, /fapi/v3/positionRisk
  trade    — /fapi/v1/order (+test), /fapi/v1/leverage, /fapi/v1/marginType,
             /fapi/v1/positionSide/dual, /fapi/v1/openOrders, /fapi/v1/allOpenOrders
  market   — /fapi/v1/klines, /fapi/v1/ticker/price, /fapi/v1/exchangeInfo
  streams  — POST/PUT/DELETE /fapi/v1/listenKey (user-data stream key)

Docs:
  https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account
  https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade
  https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info

Key differences from spot that this class MUST respect:
  * Base host is fapi.binance.com (testnet: demo-fapi.binance.com); streams differ too.
  * Orders carry `positionSide` (BOTH in one-way mode) and optional `reduceOnly`.
  * Leverage and margin type are explicit endpoints, set per symbol before trading.
  * minNotional floor comes from exchangeInfo per symbol (5 USDT on most
    perps, higher on a few like LINK) — never hard-coded.
  * Timestamps/recvWindow/signing semantics are identical to spot (HMAC or Ed25519).

Nothing here touches the spot path: the engine selects between clients via
config["MARKET"] ("spot" | "futures"). This module is inert unless selected.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from urllib.parse import urlencode, quote

import aiohttp
from aiolimiter import AsyncLimiter
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pathlib import Path

from src.core.backoff import async_retry, NonRetryableError

# Fallback NOTIONAL floor when a symbol's exchangeInfo lacks MIN_NOTIONAL
# (rare, e.g. GUSDT). Matches the live-exchange majority and backtest.py:
# 5 USDT on 719 of 725 USDⓈ-M perps (queried 2026-09-19) — the old "$100"
# assumption was folklore. Real per-symbol floors are always preferred.
FUTURES_DEFAULT_MIN_NOTIONAL = 5.0


class FuturesRestClient:
    BASE_URL = "https://fapi.binance.com"
    BASE_URL_TESTNET = "https://demo-fapi.binance.com"

    def __init__(self, config):
        self.config = config
        self.api_key = config["API_KEY"]
        self.api_secret = config.get("API_SECRET")
        self.private_key_path = config.get("PRIVATE_KEY_PATH")
        self.use_testnet = config.get("USE_TESTNET", False)
        self.base_url = self.BASE_URL_TESTNET if self.use_testnet else self.BASE_URL
        self.logger = logging.getLogger(__name__)
        self.session = None
        # Futures IP weight budget (REQUEST_WEIGHT/min). Configurable like the
        # spot client's REST_WEIGHT_LIMIT; fapi's default cap is 2400/min.
        try:
            weight = int(config.get("FUTURES_REST_WEIGHT_LIMIT", 2400) or 2400)
        except (TypeError, ValueError):
            weight = 2400
        self.limiter = AsyncLimiter(max(60, min(weight, 2400)), 60)
        self.exchange_info_cache = {}
        self.symbol_info_cache = {}
        self._exchange_info_ts = 0.0
        self.timeout = aiohttp.ClientTimeout(total=15)
        # None = never measured (see the spot client: a genuine 0 ms offset must
        # not be mistaken for "unmeasured", or every signed order re-syncs first
        # — and this endpoint's GET bypasses the rate limiter).
        self.time_offset = None
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
                self.logger.info("FuturesRestClient configured with Ed25519 asymmetric signature.")
            elif self.api_secret:
                self.logger.info("FuturesRestClient configured with HMAC-SHA256 API secret.")
            else:
                raise ValueError("Live futures trading requires Ed25519 private key or BINANCE_API_SECRET.")

    # ------------------------------------------------------------------ core

    async def _ensure_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=self.timeout)

    async def _load_exchange_info(self):
        # Same 24h-refresh contract as the spot client: delistings and filter
        # changes (futures tickSize/minNotional move often) must not go stale.
        now = time.time()
        if self.exchange_info_cache and now - self._exchange_info_ts < 86400:
            return
        data = await self._request_internal("GET", "/fapi/v1/exchangeInfo")
        self.exchange_info_cache = data
        self.symbol_info_cache = {s["symbol"]: s for s in data.get("symbols", [])}
        self._exchange_info_ts = now
        self.logger.info(f"Futures exchangeInfo cached for {len(self.symbol_info_cache)} symbols.")

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
        # /fapi/v1/time mirrors the spot /api/v3/time contract.
        try:
            resp = await self._request_internal("GET", "/fapi/v1/time")
            self.time_offset = resp["serverTime"] - int(time.time() * 1000)
            self.last_time_sync = int(time.time())
        except Exception as e:
            self.logger.warning(f"Time sync failed: {e}")

    async def _get_timestamp(self):
        if self.time_offset is None or (int(time.time()) - self.last_time_sync) > 300:
            await self.sync_time()
        return int(time.time() * 1000) + (self.time_offset or 0)

    async def _request_internal(self, method, endpoint, params=None, signed=False):
        await self._ensure_session()
        url = f"{self.base_url}{endpoint}"
        headers = {"X-MBX-APIKEY": self.api_key} if self.api_key else {}
        # Copy, never mutate the caller's dict (see spot client: a stale
        # 'signature' from a previous attempt would poison every retry).
        params = dict(params) if params else {}
        if signed:
            params["timestamp"] = await self._get_timestamp()
            query_string = urlencode(list(params.items()))
            if self._private_key:
                params["signature"] = self._sign_ed25519(query_string)
            elif self.api_secret:
                params["signature"] = self._sign_hmac_sha256(query_string)
            else:
                raise NonRetryableError("Neither Ed25519 private key nor API secret available for signing.")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            # Transmit the signed query verbatim (Ed25519 signature is base64
            # and must be percent-encoded in the URL, signed over the raw form).
            url = f"{url}?{query_string}&signature={quote(params['signature'], safe='')}"
            params = None
        async with self.limiter:
            async with self.session.request(method, url, params=params, headers=headers) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    # Endpoint + params in every error line: without this, a
                    # failing call (e.g. -1121 Invalid symbol) is impossible
                    # to attribute in the logs.
                    self.logger.error(
                        f"Futures REST error {resp.status} [{method} {endpoint}]"
                        f"{(' params=' + str(params)) if params else ''}: {text}")
                    if resp.status == 429:
                        retry_after = resp.headers.get("Retry-After")
                        wait_s = int(retry_after) if retry_after and retry_after.isdigit() else 30
                        self.logger.warning(f"Futures rate limited (HTTP 429)! Backing off for {wait_s}s...")
                        await asyncio.sleep(wait_s)
                        raise Exception(f"Futures rate limited (HTTP 429), backed off {wait_s}s")
                    elif resp.status == 418:
                        self.logger.critical("Binance Futures IP ban triggered (HTTP 418)! Backing off 120s...")
                        await asyncio.sleep(120)
                        raise Exception("Binance Futures IP ban (HTTP 418), backed off 120s")
                    try:
                        data = json.loads(text)
                        code = data.get("code")
                        msg = data.get("msg", "")
                        if code == -1021:
                            await self.sync_time()
                            raise Exception("Timestamp error, retry after sync")
                        elif code == -1121:
                            # Invalid symbol can never succeed on retry — fail fast.
                            raise NonRetryableError("Invalid symbol (code -1121)")
                        elif code == -4046:
                            # 'No need to change margin type' = already configured.
                            # Benign and idempotent — every retry would return the
                            # same answer, so fail fast (caller logs it as success).
                            raise NonRetryableError("Margin type already set (code -4046)")
                        elif code == -4059:
                            # 'No need to change position side' = already one-way/BOTH.
                            raise NonRetryableError("Position mode already set (code -4059)")
                        elif code == -2019:
                            raise Exception("Insufficient futures margin")
                        elif code == -2022:
                            raise NonRetryableError("ReduceOnly rejection (position already closed/reduced)")
                        elif code == -4061:
                            raise NonRetryableError("Order position side mismatch (check one-way vs hedge mode)")
                        elif "maintenance" in msg.lower() or "system busy" in msg.lower():
                            raise Exception("Exchange maintenance")
                    except json.JSONDecodeError:
                        pass
                    raise Exception(f"Futures REST error {resp.status}: {text}")
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

    # ------------------------------------------------------------ market data

    async def ping(self):
        return await self._request("GET", "/fapi/v1/ping")

    async def get_symbol_info(self, symbol):
        await self._load_exchange_info()
        return self.symbol_info_cache.get(symbol)

    async def get_filters(self, symbol):
        info = await self.get_symbol_info(symbol)
        if not info:
            raise NonRetryableError(
                f"Futures symbol {symbol} is not present in exchangeInfo (unknown or delisted).")
        filters = {f["filterType"]: f for f in info.get("filters", [])}
        # Shape parity with spot: futures names the floor filter MIN_NOTIONAL
        # with a `notional` field, while the engine's shared code paths read
        # NOTIONAL.minNotional (spot). Alias it so every downstream check works
        # unchanged and never falls back to the $5 spot default.
        mn = filters.get("MIN_NOTIONAL")
        if mn and "minNotional" not in mn:
            mn["minNotional"] = mn.get("notional", FUTURES_DEFAULT_MIN_NOTIONAL)
            filters.setdefault("NOTIONAL", mn)
        return filters

    async def get_klines(self, symbol, interval, limit=500):
        return await self._request("GET", "/fapi/v1/klines",
                                   {"symbol": symbol, "interval": interval, "limit": limit})

    async def get_ticker(self, symbol):
        return await self._request("GET", "/fapi/v1/ticker/price", {"symbol": symbol})

    async def get_tickers_bulk(self, symbols):
        """Bulk current prices in ONE request; {} on failure (callers skip)."""
        try:
            rows = await self._request(
                "GET", "/fapi/v1/ticker/price",
                {"symbol": symbols[0]} if len(symbols) == 1
                else {},
            )
            if isinstance(rows, dict):
                rows = [rows]
            wanted = set(symbols)
            return {r["symbol"]: float(r["price"]) for r in rows if r.get("symbol") in wanted}
        except (ValueError, TypeError, KeyError) as e:
            self.logger.warning(f"Futures bulk ticker fetch failed: {e}")
            return {}

    async def get_24hr_tickers(self):
        return await self._request("GET", "/fapi/v1/ticker/24hr")

    # ---------------------------------------------------------------- account

    async def get_position_mode(self):
        """GET /fapi/v1/positionSide/dual — True when Hedge Mode is on."""
        out = await self._request("GET", "/fapi/v1/positionSide/dual", signed=True)
        return bool(out.get("dualSidePosition")) if isinstance(out, dict) else False

    async def get_multi_assets_mode(self):
        """GET /fapi/v1/multiAssetsMargin — True when Multi-Assets Mode is on.

        Binance forbids ISOLATED margin per symbol while this is enabled
        (error -4168), so boot uses this to pick the effective margin type.
        """
        out = await self._request("GET", "/fapi/v1/multiAssetsMargin", signed=True)
        return bool(out.get("multiAssetsMargin")) if isinstance(out, dict) else False

    async def get_account(self):
        """GET /fapi/v3/account — full snapshot, NORMALIZED to the spot shape.

        The engine's single balance path (balance_cache.get_account) and all
        its consumers (risk manager sizing, order manager pre-trade free-quote
        check, dashboard) expect `{'balances': [{asset, free, locked}, ...]}`.
        The raw futures v3 payload has no such array, so without this mapping
        every futures BUY would compute $0.00 free and be rejected. Mapping:
          free  = availableBalance (margin not locked by positions)
          locked = walletBalance - availableBalance (position margin)
        Provenance keys (source/age_s) are added by the caller; raw extras
        (totalWalletBalance, totalUnrealizedProfit, ...) are preserved so the
        risk manager's equity math keeps working.
        """
        raw = await self._request("GET", "/fapi/v3/account", signed=True)
        try:
            assets = raw.get("assets") or []
            balances = []
            for b in assets:
                try:
                    asset = str(b.get("asset") or "")
                    if not asset:
                        continue
                    wallet = float(b.get("walletBalance") or 0.0)
                    avail = float(b.get("availableBalance") or 0.0)
                    locked = max(0.0, wallet - avail)
                    if wallet <= 0 and avail <= 0:
                        continue
                    balances.append({"asset": asset, "free": avail,
                                     "locked": locked, "total": wallet})
                except (TypeError, ValueError):
                    continue
            raw["balances"] = balances
        except (AttributeError, TypeError):
            pass
        return raw

    async def get_balance(self):
        """GET /fapi/v3/balance — per-asset wallet/available/PnL array."""
        return await self._request("GET", "/fapi/v3/balance", signed=True)

    async def get_position_risk(self, symbol=None):
        """GET /fapi/v3/positionRisk — current positions with liq price and leverage."""
        params = {"symbol": symbol} if symbol else {}
        return await self._request("GET", "/fapi/v3/positionRisk", params, signed=True)

    async def get_premium_index(self, symbol=None):
        """GET /fapi/v1/premiumIndex — mark price + live funding rate per symbol
        (or the full market when symbol is None). Funding is charged every 8h
        (some symbols 4h); a long PAYS when the rate is positive and is PAID
        when negative."""
        params = {"symbol": symbol} if symbol else {}
        return await self._request("GET", "/fapi/v1/premiumIndex", params)

    # ------------------------------------------------------- account settings

    async def set_leverage(self, symbol, leverage):
        """POST /fapi/v1/leverage — must be set per symbol before trading."""
        return await self._request("POST", "/fapi/v1/leverage",
                                   {"symbol": symbol, "leverage": int(leverage)}, signed=True)

    async def set_margin_type(self, symbol, margin_type):
        """POST /fapi/v1/marginType — 'ISOLATED' or 'CROSSED' (idempotent-ish:
        -4046 'No need to change margin type' is surfaced to callers to ignore)."""
        return await self._request("POST", "/fapi/v1/marginType",
                                   {"symbol": symbol, "marginType": margin_type}, signed=True)

    async def set_position_mode(self, one_way: bool):
        """POST /fapi/v1/positionSide/dual — account-wide; one_way=True means
        dualSidePosition=false (BOTH side), which this engine assumes."""
        return await self._request("POST", "/fapi/v1/positionSide/dual",
                                   {"dualSidePosition": "false" if one_way else "true"}, signed=True)

    # ------------------------------------------------------------------ trade

    async def place_order(self, symbol, side, order_type, quantity,
                          position_side="BOTH", reduce_only=False,
                          price=None, stop_price=None, close_position=False):
        """POST /fapi/v1/order.

        qty/price formatting follows the spot client: strip trailing zeros so
        LOT_SIZE/PRICE_FILTER reject nothing for formatting reasons.
        """
        def fmt(v):
            s = f"{v:.8f}" if isinstance(v, (float, int)) else str(v)
            if "." in s:
                s = s.rstrip("0").rstrip(".")
            return s or "0"

        params = {"symbol": symbol, "side": side, "type": order_type,
                  "quantity": fmt(quantity), "positionSide": position_side}
        if order_type in ("LIMIT", "STOP", "TAKE_PROFIT"):
            params["timeInForce"] = "GTC"
            params["price"] = fmt(price)
        if reduce_only:
            params["reduceOnly"] = "true"
        if stop_price is not None:
            params["stopPrice"] = fmt(stop_price)
        if close_position:
            params["closePosition"] = "true"
        return await self._request("POST", "/fapi/v1/order", params, signed=True)


    async def get_order(self, symbol, order_id):
        return await self._request("GET", "/fapi/v1/order",
                                   {"symbol": symbol, "orderId": order_id}, signed=True)

    async def cancel_order(self, symbol, order_id):
        return await self._request("DELETE", "/fapi/v1/order",
                                   {"symbol": symbol, "orderId": order_id}, signed=True)

    async def get_user_trades(self, symbol, limit=200):
        return await self._request("GET", "/fapi/v1/userTrades",
                                   {"symbol": symbol, "limit": limit}, signed=True)

    # ------------------------------------------------- user-data stream key

    async def create_listen_key(self):
        """POST /fapi/v1/listenKey — valid 60 min; keepalive extends 60 min.

        Returns the listenKey STRING. The raw response is {'listenKey': ...};
        returning the dict silently produced the URL '.../ws/{dict}' and a
        permanent HTTP 403 reconnect loop on the user-data socket.
        """
        out = await self._request("POST", "/fapi/v1/listenKey", signed=False)
        if isinstance(out, dict):
            key = out.get("listenKey")
            if not key:
                raise Exception(f"listenKey creation returned no key: {out}")
            return str(key)
        return out

    async def keepalive_listen_key(self, listen_key=None):
        """PUT /fapi/v1/listenKey — extend validity 60 min. Per the futures docs
        the endpoint operates on the listenKey issued to the calling API key;
        `listen_key` is accepted for call-site symmetry but not required."""
        return await self._request("PUT", "/fapi/v1/listenKey", {}, signed=False)

    async def close_listen_key(self, listen_key=None):
        """DELETE /fapi/v1/listenKey — invalidate the calling key's stream key."""
        return await self._request("DELETE", "/fapi/v1/listenKey", {}, signed=False)
