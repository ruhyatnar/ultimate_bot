#!/usr/bin/env python3
"""
live_signed_probe.py — one-command go-live credential check.

Exercises the engine's REAL production clients (RestClient / FuturesRestClient)
against the exchange and verifies, in order:

  1. Credentials present and loadable (Ed25519 key parse / HMAC secret)
  2. Offline signature self-test (sign + verify with the loaded key)
  3. Connectivity + measured clock offset vs the exchange (the -1021 guard)
  4. A SIGNED account read (GET /api/v3/account or /fapi/v3/account)

NO order is ever placed — the write path is intentionally not probed here;
paper trading is the write-path test. This tool runs OUTSIDE the engine
(no lock, no DB, no PM2) and is safe to re-run any time.

Usage:
  ./venv/bin/python3 live_signed_probe.py                 # spot, production
  ./venv/bin/python3 live_signed_probe.py --market futures
  ./venv/bin/python3 live_signed_probe.py --testnet       # demo endpoints
  ./venv/bin/python3 live_signed_probe.py --json          # machine-readable

Exit codes: 0 = all green (safe to go live), 1 = something failed (see report).
"""

import argparse
import asyncio
import base64
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

GREEN, RED, AMBER, DIM, BOLD = "\033[92m", "\033[91m", "\033[93m", "\033[2m", "\033[1m"


def line(ok, label, detail=""):
    mark = f"{GREEN}✓{DIM}" if ok is True else f"{RED}✗{DIM}" if ok is False else f"{AMBER}•{DIM}"
    print(f" {mark} {label}{f' — {detail}' if detail else ''}")


def extract_code(exc):
    m = re.search(r"\{.*\}", str(exc))
    if m:
        try:
            return json.loads(m.group(0)).get("code")
        except Exception:
            pass
    return None


async def probe(args) -> dict:
    # Config: honor CLI overrides, keep PAPER_TRADE so engine validation passes;
    # credentials load independently of the trading mode.
    os.environ["PAPER_TRADE"] = "true"
    if args.market:
        os.environ["MARKET"] = args.market
    if args.testnet is not None:
        os.environ["USE_TESTNET"] = "true" if args.testnet else "false"
    from config import load_config
    config = load_config()

    results, failed = {}, False

    def check(name, ok, fatal=True):
        results[name] = ok
        nonlocal failed
        if not ok and fatal:
            failed = True
        return ok

    print(f"\n{BOLD}Live signed probe — {config['MARKET']}"
          f"{' · TESTNET' if config['USE_TESTNET'] else ' · PRODUCTION'}{DIM}\n")

    # ── 1. Credentials ────────────────────────────────────────────────────────
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization

    key = secret = None
    pem_path = config.get("PRIVATE_KEY_PATH")
    if pem_path and os.path.exists(pem_path):
        try:
            key = serialization.load_pem_private_key(open(pem_path, "rb").read(), password=None)
            if not isinstance(key, Ed25519PrivateKey):
                key = None
                check("ed25519_key", False)
                line(False, "Ed25519 key", f"{pem_path} exists but is not an Ed25519 key")
        except Exception as e:
            check("ed25519_key", False)
            line(False, "Ed25519 key", f"cannot parse {pem_path}: {e}")
    secret = config.get("API_SECRET")

    if key:
        check("ed25519_key", True)
        line(True, "Ed25519 key", f"loaded from {pem_path}")
    elif secret:
        line(True, "HMAC secret", "BINANCE_API_SECRET present (Ed25519 key preferred)")
    else:
        check("credentials", False)
        line(False, "Credentials", "none found — set BINANCE_API_KEY and either "
                                    "BINANCE_PRIVATE_KEY_PATH (Ed25519, preferred) or BINANCE_API_SECRET in .env")

    if not config.get("API_KEY"):
        check("api_key", False)
        line(False, "API key", "BINANCE_API_KEY is not set")
    else:
        check("api_key", True)
        line(True, "API key", f"…{str(config['API_KEY'])[-4:]}")

    # Connectivity/clock checks below are UNSIGNED — run them even without
    # credentials so the tool doubles as a pre-key network diagnostic.
    creds_ok = not failed
    failed = False

    # ── 2. Offline signature self-test (only when credentials exist) ──────────
    if creds_ok:
        try:
            if key:
                sig = base64.b64encode(key.sign(b"probe")).decode()
                # cryptography's Ed25519 PRIVATE key exposes sign(); verification
                # lives on the derived public key.
                key.public_key().verify(base64.b64decode(sig), b"probe")
            else:
                import hmac, hashlib
                sig = hmac.new(secret.encode(), b"probe", hashlib.sha256).hexdigest()
                assert hmac.new(secret.encode(), b"probe", hashlib.sha256).hexdigest() == sig
            check("signature_selftest", True)
            line(True, "Signature self-test", "sign+verify round-trip OK (offline)")
        except Exception as e:
            check("signature_selftest", False)
            line(False, "Signature self-test", str(e))
            return results

    # ── 3/4. Live checks through the real client ──────────────────────────────
    # When credentials exist, re-load the config in LIVE mode so the production
    # client loads and signs with its own credentials (the paper config never
    # signs). This doubles as GO_LIVE step 5.1: a validation error here is a
    # genuine go-live blocker, surfaced before the flip.
    if creds_ok:
        try:
            os.environ["PAPER_TRADE"] = "false"
            config = load_config()
            check("live_config_parse", True)
            line(True, "Live config parse", f"{config['MARKET']} validated in live mode (PAPER_TRADE=false)")
        except Exception as e:
            check("live_config_parse", False)
            line(False, "Live config parse", str(e))
            return results

    if config["MARKET"] == "futures":
        from src.exchange.futures_rest_client import FuturesRestClient as Client
    else:
        from src.exchange.rest_client import RestClient as Client

    client = Client(config)
    try:
        await client.init()  # session + sync_time + exchangeInfo (all unsigned)
    except Exception as e:
        check("connectivity", False)
        line(False, "Connectivity", f"client init failed: {e}")
        return results

    check("connectivity", True)
    line(True, "Connectivity", f"exchangeInfo cached ({len(client.symbol_info_cache)} symbols) — "
                               + ("live-mode client, signing enabled" if creds_ok else "paper-mode client (unsigned diagnostics)"))

    age = time.time() - getattr(client, "last_time_sync", 0)
    # `None` = never measured (the clients' sentinel; 0 ms is a valid measurement
    # and must not read as "unset"). A failed sync must be reported as such
    # rather than crashing this very check with abs(None).
    off = getattr(client, "time_offset", None)
    measured = isinstance(off, (int, float))
    ok = measured and age <= 60 and abs(off) < 2500
    check("clock_offset", ok)
    if not measured:
        line(False, "Clock offset", f"no measurement ({age:.0f}s since the last sync attempt "
                                     "failed) — investigate NTP before going live")
    else:
        line(ok, "Clock offset", f"{off:+.0f} ms vs exchange (synced {age:.1f}s ago)"
             if ok else f"{off:+.0f} ms, {age:.0f}s old — investigate NTP before going live")

    # ── 4. THE signed read (credentials required) ─────────────────────────────
    if not creds_ok:
        line(None, "Signed account read", "skipped — set credentials in .env first")
        await client.close()
        return results

    t0 = time.perf_counter()
    try:
        acct = await client.get_account()  # THE signed read
        rtt = (time.perf_counter() - t0) * 1000
        check("signed_read", True)
        if config["MARKET"] == "futures":
            assets = acct.get("assets", []) if isinstance(acct, dict) else acct
            usdt = next((a for a in assets if a.get("asset") == "USDT"), {})
            # /fapi/v3/account rows use 'walletBalance'; /fapi/v3/balance rows use 'balance'.
            wallet = float(usdt.get("walletBalance") or usdt.get("balance") or 0)
            avail = float(usdt.get("availableBalance") or usdt.get("withdrawAvailable") or 0)
            detail = f"wallet {wallet:.2f} USDT, available {avail:.2f}"
        else:
            usdt = next((b for b in acct.get("balances", []) if b.get("asset") == "USDT"), {})
            detail = f"free {float(usdt.get('free', 0)):.2f} USDT, locked {float(usdt.get('locked', 0)):.2f}"
            if acct.get("canTrade") is False:
                line(False, "Account permission", "canTrade=false — trading disabled on this key")
                results["can_trade"] = False
        line(True, f"Signed account read ({rtt:.0f} ms)", detail)
        line(True, "Timestamp accepted", "no -1021 — signed timestamps within recvWindow")
    except Exception as e:
        code = extract_code(e)
        check("signed_read", False)
        hints = {
            -2014: "API key format invalid — re-check BINANCE_API_KEY",
            -2015: "key invalid or IP-restricted — add this VPS IP to the key's whitelist on Binance",
            -1021: "timestamp outside recvWindow — clock sync failed unexpectedly",
            -1022: "signature invalid — key/secret mismatch, or the key was regenerated",
            -40102: "key has no futures permission — enable futures on the API key",
        }
        line(False, "Signed account read", f"{hints.get(code, '')} ({e})" if code else str(e))
    finally:
        await client.close()

    return results


async def main():
    p = argparse.ArgumentParser(description="Go-live signed probe (read-only).")
    p.add_argument("--market", choices=["spot", "futures"], help="override MARKET for this probe")
    p.add_argument("--testnet", dest="testnet", action="store_true", help="probe demo/testnet endpoints")
    p.add_argument("--mainnet", dest="testnet", action="store_false", help="force production endpoints")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    args = p.parse_args()

    results = await probe(args)

    if args.json:
        print(json.dumps(results, indent=2))
        return 0 if all(results.values()) else 1

    ok = all(results.values())
    print(f"\n{BOLD}{'✅ ALL GREEN — credentials, signature, clock and signed read verified. Safe to go live.' if ok else '❌ PROBE FAILED — fix the ✗ items above, then re-run.'}\n{DIM}"
          f" Write-path note: this probe never places orders by design — validate order flow in PAPER mode,\n"
          f" then follow GO_LIVE.md for the flip.{DIM}\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
