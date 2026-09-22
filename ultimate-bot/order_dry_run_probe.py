#!/usr/bin/env python3
"""
order_dry_run_probe.py — live-order dry-run: exercise the REAL spot & futures
order builders end to end WITHOUT ever transmitting an order.

Every order the engine can send is built by one of four clients:

  * RestClient.place_order            spot      REST  POST /api/v3/order
  * FuturesRestClient.place_order     USDⓈ-M    REST  POST /fapi/v1/order
  * WSApiClient.place_order           spot      WS-API order.place
  * FuturesWSApiClient.place_order    USDⓈ-M    WS-API order.place

This probe drives each of those builders for an entry BUY and an exit SELL, then
verifies the BUILT request — symbol, side, MARKET type, LOT_SIZE-quantized
quantity, a fresh timestamp, and positionSide/reduceOnly on futures — while a
stub transport captures the request instead of sending it:

  * with credentials  : a fake aiohttp session / send_request replaces the
                        network, so the probe walks the REAL signing + URL
                        assembly path;
  * without credentials: the parameter builder is still exercised (signing is
                        referenced but not performed).

NO order is ever placed. The transport is stubbed at the last possible layer,
so even a bug in the probe cannot reach the exchange's order endpoint.

Usage:
  ./venv/bin/python3 order_dry_run_probe.py                 # spot + futures
  ./venv/bin/python3 order_dry_run_probe.py --market futures
  ./venv/bin/python3 order_dry_run_probe.py --symbol BTCUSDT
  ./venv/bin/python3 order_dry_run_probe.py --testnet       # demo endpoints
  ./venv/bin/python3 order_dry_run_probe.py --self-test     # offline harness check
  ./venv/bin/python3 order_dry_run_probe.py --json          # machine-readable

Exit codes: 0 = every built request is valid, 1 = a builder produced an invalid
request (or a leg could not be probed).
"""

import argparse
import asyncio
import json
import os
import sys
import time
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

GREEN, RED, AMBER, DIM, BOLD = "\033[92m", "\033[91m", "\033[93m", "\033[2m", "\033[1m"

# Target notional for the synthetic order. Comfortably above every market's
# minNotional floor ($5 on the vast majority of pairs) and below most accounts'
# position sizes, so sanitize_order() produces an exchange-legal quantity.
TARGET_NOTIONAL_USDT = 20.0
FALLBACK_SYMBOL = "BTCUSDT"


def line(ok, label, detail=""):
    mark = f"{GREEN}✓{DIM}" if ok is True else f"{RED}✗{DIM}" if ok is False else f"{AMBER}•{DIM}"
    print(f" {mark} {label}{f' — {detail}' if detail else ''}")


# --------------------------------------------------------------------------- #
# Fake transport — captures the request, returns a synthetic FILLED order.
# --------------------------------------------------------------------------- #
class _FakeResponse:
    def __init__(self, payload):
        self.status = 200
        self.headers = {}
        self._payload = payload

    async def json(self):
        return self._payload

    async def text(self):
        return json.dumps(self._payload)


class _FakeContext:
    """Async context manager matching aiohttp's `session.request(...)` usage."""

    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    """Stand-in for aiohttp.ClientSession that records instead of transmitting."""

    def __init__(self, sink, response_factory):
        self._sink = sink
        self._response_factory = response_factory

    def request(self, method, url, params=None, headers=None):
        self._sink.append({"method": method, "url": url,
                           "params": params, "headers": dict(headers or {})})
        return _FakeContext(self._response_factory())

    async def get(self, url):  # used only by _get_server_time
        self._sink.append({"method": "GET", "url": url, "params": None, "headers": {}})
        return _FakeContext(self._response_factory())

    async def close(self):
        pass


def _synthetic_order(symbol, side, qty, price):
    """Response shape shared by the spot and futures order endpoints."""
    return {
        "symbol": symbol, "orderId": 900001, "clientOrderId": "dryrun-probe",
        "transactTime": int(time.time() * 1000), "price": "0",
        "origQty": str(qty), "executedQty": str(qty), "status": "FILLED",
        "avgPrice": (f"{price:.8f}".rstrip("0").rstrip(".") or "0"),
        "type": "MARKET", "side": side,
    }


def _prime_clock(client):
    """Fresh timestamps without a network hop (the dry run never transmits).

    `_get_timestamp` re-syncs when the offset is unset/zero or the last sync is
    stale; priming avoids that measure call while leaving the real signed path
    (timestamp -> query -> signature) fully intact.
    """
    offset = getattr(client, "time_offset", None)
    client.time_offset = 1 if offset in (None, 0) else offset
    client.last_time_sync = int(time.time())


# --------------------------------------------------------------------------- #
# Builder dry runs
# --------------------------------------------------------------------------- #
async def _dry_run_rest(client, market, symbol, side, qty, price,
                        reduce_only=False, full=True):
    """Drive `place_order` and return the captured request (nothing leaves the box).

    full=True  -> fake aiohttp session: real timestamp + signature + URL assembly.
    full=False -> stub `_request`: parameter builder only (works without keys).
    """
    sink = []
    synthetic = _synthetic_order(symbol, side, qty, price)
    if full:
        real_session = client.session
        client.session = _FakeSession(sink, lambda: _FakeResponse(synthetic))
        _prime_clock(client)
        try:
            await client.place_order(symbol, side, "MARKET", qty, reduce_only=reduce_only)
        finally:
            client.session = real_session
        return sink, True

    async def _capture(method, endpoint, params=None, signed=False):
        sink.append({"method": method, "endpoint": endpoint,
                     "params": dict(params or {}), "signed": signed})
        return synthetic

    real_request = client._request
    client._request = _capture
    try:
        await client.place_order(symbol, side, "MARKET", qty, reduce_only=reduce_only)
    finally:
        client._request = real_request
    return sink, False


async def _dry_run_ws(client, symbol, side, qty, reduce_only=False):
    """Drive the WS API order builder; `send_request` is stubbed so nothing sends."""
    captured = {}
    if getattr(client, "_private_key", None) is None:
        return None, None

    async def _capture(method, params=None):
        captured["method"] = method
        captured["params"] = dict(params or {})
        return {"status": 200, "result": {"orderId": 900002, "status": "FILLED",
                                          "executedQty": str(qty), "avgPrice": "0",
                                          "symbol": symbol}}

    real_send = client.send_request
    client.send_request = _capture
    _prime_clock(client)
    try:
        await client.place_order(symbol, side, "MARKET", qty, reduce_only=reduce_only)
    finally:
        client.send_request = real_send
    return captured, True


def _normalize_rest(entry, full):
    if full:
        parsed = urlparse(entry["url"])
        return {"path": parsed.path,
                "fields": {k: v[0] for k, v in parse_qs(parsed.query).items()}}
    return {"path": entry["endpoint"], "fields": dict(entry["params"])}


def _verify_built(built, expected_path, market, side, symbol, signed):
    """Return a list of problems with a built order request ([] = valid)."""
    problems = []
    path = built["path"]
    fields = {k: str(v) for k, v in built["fields"].items()}
    if path != expected_path:
        problems.append(f"path {path!r} != {expected_path!r}")
    for required in ("symbol", "side", "type", "quantity"):
        if not fields.get(required):
            problems.append(f"missing {required}")
    if fields.get("symbol") != symbol:
        problems.append(f"symbol {fields.get('symbol')!r} != {symbol!r}")
    if fields.get("side") != side:
        problems.append(f"side {fields.get('side')!r} != {side!r}")
    if fields.get("type") != "MARKET":
        problems.append(f"type {fields.get('type')!r} != 'MARKET'")
    if signed:
        if not fields.get("signature"):
            problems.append("missing signature")
        if not fields.get("timestamp"):
            problems.append("missing timestamp")
        else:
            try:
                age = abs(int(time.time() * 1000) - int(fields["timestamp"]))
                if age > 5000:
                    problems.append(f"timestamp {age}ms outside recvWindow(5000ms)")
            except ValueError:
                problems.append("timestamp is not an integer")
    if market == "futures":
        if fields.get("positionSide") != "BOTH":
            problems.append(f"positionSide {fields.get('positionSide')!r} != 'BOTH'")
        if side == "SELL":
            if fields.get("reduceOnly") != "true":
                problems.append("exit SELL must carry reduceOnly=true")
        elif fields.get("reduceOnly") not in (None, "false", ""):
            problems.append("entry BUY must not carry reduceOnly")
    return problems


def _describe(built):
    """Compact one-line view of the built request for the report."""
    fields = built["fields"]
    keys = [k for k in ("symbol", "side", "type", "quantity",
                        "positionSide", "reduceOnly", "timestamp", "signature") if k in fields]
    return " ".join(f"{k}={fields[k]}" for k in keys)


# --------------------------------------------------------------------------- #
# Per-market probe
# --------------------------------------------------------------------------- #
async def _probe_market(market, args, results, creds_ok):
    from src.trade.order_manager import OrderManager

    if market == "futures":
        from src.exchange.futures_rest_client import FuturesRestClient as RestCls
        from src.exchange.futures_ws_api_client import FuturesWSApiClient as WsCls
        rest_path, ws_path = "/fapi/v1/order", "order.place"
    else:
        from src.exchange.rest_client import RestClient as RestCls
        from src.exchange.ws_api_client import WSApiClient as WsCls
        rest_path, ws_path = "/api/v3/order", "order.place"

    print(f"\n{BOLD}── {market.upper()} order builders{DIM}")

    os.environ["MARKET"] = market
    from config import load_config
    config = load_config()

    client = RestCls(config)
    try:
        await client.init()  # unsigned: time + exchangeInfo (+ symbol universe)
    except Exception as e:
        results[f"{market}.connectivity"] = False
        line(False, f"{market} connectivity", str(e))
        return

    # Pick a symbol that exists on THIS market, with a real exchangeInfo read.
    symbol = (args.symbol or (config.get("STATIC_SYMBOLS") or [FALLBACK_SYMBOL])[0]).upper()
    if not await client.get_symbol_info(symbol):
        line(None, f"{market} symbol", f"{symbol} not on {market}; using {FALLBACK_SYMBOL}")
        symbol = FALLBACK_SYMBOL
    if not await client.get_symbol_info(symbol):
        results[f"{market}.symbol"] = False
        line(False, f"{market} symbol", f"neither {symbol} nor {FALLBACK_SYMBOL} is tradable")
        await client.close()
        return

    try:
        filters = await client.get_filters(symbol)
        ticker = await client.get_ticker(symbol)
        price = float(ticker["price"])
    except Exception as e:
        results[f"{market}.market_data"] = False
        line(False, f"{market} market data", str(e))
        await client.close()
        return

    lot = filters.get("LOT_SIZE", {})
    notional = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {}))
    line(True, f"{market} {symbol}",
         f"price {price:.6g} · step {lot.get('stepSize', '?')} · minQty {lot.get('minQty', '?')} "
         f"· minNotional {notional.get('minNotional', '?')}")

    # Quantize a real quantity through the engine's OWN sanitizer. Size the
    # request above the symbol's actual floors (minQty and minNotional vary a
    # lot — e.g. BTCUSDT futures needs $50 notional), doubling until legal.
    om = OrderManager(config, None, client, None)

    def _f(d, key, default):
        try:
            return float(d.get(key) or default)
        except (TypeError, ValueError):
            return float(default)

    min_qty = _f(lot, "minQty", 0.0)
    min_notional = _f(notional, "minNotional", 5.0)
    target = max(TARGET_NOTIONAL_USDT, min_notional * 1.1)
    qty = None
    for _ in range(6):
        qty, _ = await om.sanitize_order(symbol, max(target / price, min_qty), price)
        if qty:
            break
        target *= 2
    if not qty:
        results[f"{market}.sanitize"] = False
        line(False, "sanitize_order",
             f"could not build a legal quantity above minQty {min_qty} / minNotional {min_notional}")
        await client.close()
        return
    results[f"{market}.sanitize"] = True
    line(True, "sanitize_order (LOT_SIZE/minNotional)", f"qty={qty} ≈ ${float(qty) * price:.2f}")

    full = creds_ok  # signed path only when a key/secret is loadable

    # ── REST BUY (entry) ────────────────────────────────────────────────────
    sink, is_full = await _dry_run_rest(client, market, symbol, "BUY", qty, price, False, full)
    built = _normalize_rest(sink[0], is_full)
    problems = _verify_built(built, rest_path, market, "BUY", symbol, is_full)
    results[f"{market}.rest_buy"] = not problems
    line(not problems, f"{market} REST BUY builder" + ("" if is_full else " (unsigned)"),
         _describe(built) if not problems else "; ".join(problems))

    # ── REST SELL (exit; reduceOnly on futures) ─────────────────────────────
    sink, is_full = await _dry_run_rest(client, market, symbol, "SELL", qty, price,
                                        market == "futures", full)
    built = _normalize_rest(sink[0], is_full)
    problems = _verify_built(built, rest_path, market, "SELL", symbol, is_full)
    results[f"{market}.rest_sell"] = not problems
    line(not problems, f"{market} REST SELL builder" + ("" if is_full else " (unsigned)"),
         _describe(built) if not problems else "; ".join(problems))

    await client.close()

    # ── WS API builders (Ed25519 only) ──────────────────────────────────────
    if not creds_ok:
        results[f"{market}.ws_buy"] = False
        line(None, f"{market} WS API builders", "skipped — no credentials in .env")
        return
    ws = WsCls(config)
    if getattr(ws, "_private_key", None) is None:
        results[f"{market}.ws_buy"] = False
        line(None, f"{market} WS API builders",
             "skipped — WS API session auth requires an Ed25519 key (HMAC uses REST)")
        return
    for side, reduce_only in (("BUY", False), ("SELL", market == "futures")):
        captured, _ = await _dry_run_ws(ws, symbol, side, qty, reduce_only)
        if not captured:
            results[f"{market}.ws_{side.lower()}"] = False
            line(False, f"{market} WS API {side} builder", "builder did not produce a request")
            continue
        built = {"path": captured["method"], "fields": dict(captured["params"])}
        problems = _verify_built(built, ws_path, market, side, symbol, True)
        results[f"{market}.ws_{side.lower()}"] = not problems
        line(not problems, f"{market} WS API {side} builder",
             _describe(built) if not problems else "; ".join(problems))


# --------------------------------------------------------------------------- #
# Offline self-test — validates the harness itself (no network, no keys).
# --------------------------------------------------------------------------- #
async def _self_test():
    """Drive every builder with a stubbed signer + fake transport, offline."""
    os.environ["PAPER_TRADE"] = "true"
    from config import load_config
    from src.exchange.rest_client import RestClient
    from src.exchange.futures_rest_client import FuturesRestClient
    from src.exchange.ws_api_client import WSApiClient
    from src.exchange.futures_ws_api_client import FuturesWSApiClient

    config = load_config()
    symbol, qty, price = "BTCUSDT", "0.00012300", 43000.0
    failures = []

    for market, RestCls, WsCls, path in (
        ("spot", RestClient, WSApiClient, "/api/v3/order"),
        ("futures", FuturesRestClient, FuturesWSApiClient, "/fapi/v1/order"),
    ):
        client = RestCls(config)
        client._private_key = object()  # paper clients load no key
        client._sign_ed25519 = lambda _m: "TESTSIG"
        sink, is_full = await _dry_run_rest(client, market, symbol, "BUY", qty, price,
                                            False, full=True)
        built = _normalize_rest(sink[0], is_full)
        problems = _verify_built(built, path, market, "BUY", symbol, True)
        line(not problems, f"self-test {market} REST BUY", "; ".join(problems) or _describe(built))
        if problems:
            failures.append(f"{market}.rest_buy")

        sink, is_full = await _dry_run_rest(client, market, symbol, "SELL", qty, price,
                                            market == "futures", full=True)
        built = _normalize_rest(sink[0], is_full)
        problems = _verify_built(built, path, market, "SELL", symbol, True)
        line(not problems, f"self-test {market} REST SELL", "; ".join(problems) or _describe(built))
        if problems:
            failures.append(f"{market}.rest_sell")

        ws = WsCls(config)
        ws._private_key = object()
        ws._sign_ed25519 = lambda _m: "TESTSIG"
        for side, reduce_only in (("BUY", False), ("SELL", market == "futures")):
            captured, _ = await _dry_run_ws(ws, symbol, side, qty, reduce_only)
            built = {"path": captured["method"], "fields": dict(captured["params"])}
            problems = _verify_built(built, "order.place", market, side, symbol, True)
            line(not problems, f"self-test {market} WS {side}",
                 "; ".join(problems) or _describe(built))
            if problems:
                failures.append(f"{market}.ws_{side.lower()}")

    # A deliberately-wrong side must be rejected (proves the verifier bites).
    bad = {"path": "/api/v3/order",
           "fields": {"symbol": symbol, "side": "BUY", "type": "MARKET",
                      "quantity": qty, "timestamp": str(int(time.time() * 1000)),
                      "signature": "X"}}
    caught = bool(_verify_built(bad, "/api/v3/order", "spot", "SELL", symbol, True))
    line(caught, "self-test verifier rejects a wrong-side request")
    if not caught:
        failures.append("verifier")

    print()
    if failures:
        print(f"{RED}{BOLD}❌ self-test FAILED: {failures}{DIM}")
        return 1
    print(f"{GREEN}{BOLD}✅ self-test OK — all four builders produce valid requests.{DIM}")
    return 0


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
async def probe(args):
    os.environ["PAPER_TRADE"] = "true"  # validate config first, creds load separately
    if args.testnet is not None:
        os.environ["USE_TESTNET"] = "true" if args.testnet else "false"
    from config import load_config
    config = load_config()

    results = {}
    scope = "spot + futures" if args.market == "both" else args.market
    print(f"\n{BOLD}Order dry-run probe — {scope} builders"
          f"{' · TESTNET' if config['USE_TESTNET'] else ' · PRODUCTION'}{DIM}\n"
          f"{DIM} Nothing is ever sent: the transport is stubbed, not the order builder.{DIM}")

    # ── Credentials (used to pick full-signing vs parameter-only fidelity) ────
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = secret = None
    pem_path = config.get("PRIVATE_KEY_PATH")
    if pem_path and os.path.exists(pem_path):
        try:
            key = serialization.load_pem_private_key(open(pem_path, "rb").read(), password=None)
        except Exception:
            key = None
        if key is not None and not isinstance(key, Ed25519PrivateKey):
            key = None
    secret = config.get("API_SECRET")
    has_key = bool(config.get("API_KEY"))

    if has_key and (key or secret):
        line(True, "Credentials", f"API key …{str(config['API_KEY'])[-4:]} + "
                                  f"{'Ed25519' if key else 'HMAC'}")
        creds_ok = True
    else:
        creds_ok = False
        line(False, "Credentials", "missing BINANCE_API_KEY and/or key/secret — REST builders "
                                   "will run in parameter-only mode, WS API skipped")

    if creds_ok:
        try:
            os.environ["PAPER_TRADE"] = "false"
            config = load_config()  # live client loads & signs with its own credentials
            line(True, "Live config parse", f"validated in live mode ({config['MARKET']})")
        except Exception as e:
            line(False, "Live config parse", str(e))
            creds_ok = False

    results["credentials"] = creds_ok

    markets = ["spot", "futures"] if args.market == "both" else [args.market]
    for market in markets:
        await _probe_market(market, args, results, creds_ok)

    return results


async def main():
    p = argparse.ArgumentParser(description="Live-order dry-run probe (never sends an order).")
    p.add_argument("--market", choices=["spot", "futures", "both"], default="both",
                   help="which order builders to exercise (default: both)")
    p.add_argument("--symbol", help="symbol to build the dry-run order for (default: first STATIC_SYMBOLS)")
    p.add_argument("--testnet", dest="testnet", action="store_true", help="use demo/testnet endpoints")
    p.add_argument("--mainnet", dest="testnet", action="store_false", help="force production endpoints")
    p.add_argument("--self-test", dest="self_test", action="store_true",
                   help="validate the probe harness offline (no network, no keys)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    args = p.parse_args()

    if args.self_test:
        return await _self_test()

    results = await probe(args)

    if args.json:
        print(json.dumps(results, indent=2))
        return 0 if all(results.values()) else 1

    ok = all(results.values())
    print(f"\n{BOLD}{'✅ ALL GREEN — every spot & futures order builder produced a valid request.'
                      if ok else '❌ DRY RUN FAILED — fix the ✗ items above, then re-run.'}{DIM}\n"
          f"{DIM} No order was sent. This probe validates request construction only; run the\n"
          f" engine in PAPER mode to validate the full order lifecycle, then follow GO_LIVE.md.{DIM}\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
