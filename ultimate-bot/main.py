#!/usr/bin/env python3
import asyncio
import logging
import os
import signal
import sys
import fcntl
from dotenv import load_dotenv
from src.core.health_check import HealthCheck
from src.core.error_handler import ErrorHandler
from src.exchange.rest_client import RestClient
from src.exchange.ws_api_client import WSApiClient
from src.exchange.ws_stream_client import WSStreamClient
from src.exchange.futures_rest_client import FuturesRestClient
from src.exchange.futures_ws_api_client import FuturesWSApiClient
from src.exchange.futures_ws_stream_client import FuturesWSStreamClient
from src.database.db_manager import DatabaseManager
from src.risk.risk_manager import RiskManager
from src.strategies.signal_generator import SignalGenerator
from src.strategies.trend_detector import TrendDetector
from src.trade.order_manager import OrderManager
from src.trade.trade_logic import TradeLogic
from src.reporting.discord_webhook import DiscordWebhook
from config import load_config
from src.utils.helpers import setup_logging

load_dotenv()
config = load_config()

# Singleton lock — scoped by MODE: live (spot or futures) engines share one lock
# so two real-money engines can never run together; paper engines get their own
# lock file so paper/live and paper/paper can coexist (isolated DBs assumed).
# The old single global lock forced every paper test battery to stop the live
# engine just to boot an isolated paper instance.
# LOCK_SCOPE override lets the test batteries take a unique scope so they can
# run alongside BOTH a live engine and the futures soak (see smoke_test.py).
_lock_scope = os.getenv("LOCK_SCOPE") or ("live" if not config["PAPER_TRADE"] else "paper")
lock_file = open(f"/tmp/ultimate_bot.{_lock_scope}.lock", "w")
try:
    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    print(f"Another {_lock_scope} instance is already running. Exiting.")
    sys.exit(1)

shutdown_event = asyncio.Event()


def request_shutdown(sig):
    """Signal entry point: only flag the shutdown event.

    Deliberately does NOT cancel tasks or stop the loop here. Cancelling tasks
    from the signal handler races with main()'s own cleanup (it previously
    cancelled main() itself mid-`finally`, then called loop.stop(), leaving
    ws_stream.disconnect() hung on a torn-down websocket forever — PM2 reloads
    would hang and the lock would never be released). main() observes the event,
    stops the trading loop, cancels background tasks itself, and then tears down
    connections/database in dependency order.
    """
    logging.getLogger(__name__).info(f"Received signal {sig}, shutting down...")
    shutdown_event.set()

async def main():
    setup_logging(config)
    logger = logging.getLogger(__name__)
    mode_str = "PAPER TRADING" if config["PAPER_TRADE"] else "LIVE EXECUTION"
    logger.info(f"Starting MARKET-ONLY BOT [{mode_str}] with PRESET={config['PRESET']}")

    db = DatabaseManager(config["DB_PATH"])
    await db.init()

    # Market selection — spot (proven default) or USDⓈ-M futures. The futures
    # clients mirror the spot interfaces, so everything downstream (risk, order
    # manager, trade logic, monitor) is market-agnostic.
    is_futures = config.get("MARKET", "spot") == "futures"
    rest = FuturesRestClient(config) if is_futures else RestClient(config)
    await rest.init()

    if is_futures and not config["PAPER_TRADE"]:
        # One-time futures account setup — pre-checked and idempotent: the
        # GET-first pattern means "already configured" costs one cheap read
        # (weight 30 total) instead of 3 write retries per already-set item.
        # - position mode: one-way (BOTH) — engine assumes a net position/symbol
        # - margin type + leverage: applied per configured symbol
        # - Multi-Assets Mode forces CROSSED margin (Binance -4168 otherwise),
        #   so the effective margin type is resolved at boot and reused below.
        effective_margin = config.get("FUTURES_MARGIN_TYPE", "ISOLATED")
        try:
            # get_position_mode() returns the HEDGE flag (True = hedge mode).
            # One-way == NOT hedge — invert before comparing with FUTURES_ONE_WAY_MODE.
            is_one_way = not await rest.get_position_mode()
            if is_one_way == bool(config.get("FUTURES_ONE_WAY_MODE", True)):
                logger.info("Futures position mode already correct (pre-checked, no write).")
            else:
                await rest.set_position_mode(config.get("FUTURES_ONE_WAY_MODE", True))
                logger.info(f"Futures position mode set (one_way={config.get('FUTURES_ONE_WAY_MODE', True)}).")
        except Exception as e:
            # -4059 'No need to change position side' = already set; treat as success
            if "-4059" in str(e):
                logger.info("Futures position mode already correct (confirmed by -4059).")
            else:
                logger.warning(f"Futures position mode setup skipped ({e}).")
        try:
            if await rest.get_multi_assets_mode():
                if effective_margin != "CROSSED":
                    logger.warning(
                        "Multi-Assets Mode is ON on this account: Binance forbids ISOLATED "
                        "margin per symbol (-4168). Using CROSSED margin for this session "
                        "(update FUTURES_MARGIN_TYPE=CROSSED in .env to silence this).")
                    effective_margin = "CROSSED"
        except Exception as e:
            logger.debug(f"Multi-Assets Mode check failed ({e}); keeping configured margin type.")
        # Publish the effective margin into the live config so every consumer
        # (trade_logic's futures_state snapshot, dashboard) reports what the
        # account actually trades with, not the stale .env value.
        config["FUTURES_MARGIN_TYPE"] = effective_margin
        for sym in config.get("STATIC_SYMBOLS") or []:
            # Always attempt the write (idempotent): Binance answers -4046 'No need
            # to change margin type' when the symbol already matches, which is the
            # normal case. Comparing config against the value we just published
            # into config was always true, so the requested ISOLATED margin was
            # never actually applied to the account (dashboard could then report
            # ISOLATED while the exchange ran CROSSED).
            try:
                await rest.set_margin_type(sym, effective_margin)
                logger.info(f"Futures margin type for {sym}: {effective_margin}")
            except Exception as e:
                # -4046 'No need to change margin type' is the normal already-set case
                if "-4046" in str(e):
                    logger.debug(f"Futures margin type for {sym} already {effective_margin} (-4046).")
                else:
                    logger.warning(f"Futures margin type for {sym} not set ({e}).")
            try:
                lev = int(config.get("FUTURES_LEVERAGE", 1))
                await rest.set_leverage(sym, lev)
                logger.info(f"Futures leverage for {sym}: {lev}x")
            except Exception as e:
                logger.warning(f"Futures leverage for {sym} not set ({e}).")

    has_private_key = bool(config.get("PRIVATE_KEY_PATH") and config["PRIVATE_KEY_PATH"].exists())
    if is_futures:
        ws_api = None
        if not config["PAPER_TRADE"] and has_private_key:
            ws_api = FuturesWSApiClient(config)
            ws_api.futures_rest = rest  # listenKey lifecycle via the fapi client
        ws_stream = FuturesWSStreamClient(config)
    else:
        ws_api = WSApiClient(config) if (not config["PAPER_TRADE"] and has_private_key) else None
        ws_stream = WSStreamClient(config)
    risk = RiskManager(config, db, rest, ws_api=ws_api)
    await risk.load_state()

    trend_detector = TrendDetector(config, rest)
    signal_gen = SignalGenerator(config, rest)
    order_mgr = OrderManager(config, db, rest, ws_api, risk_mgr=risk)
    webhook = DiscordWebhook(config["DISCORD_WEBHOOK_URL"], config["DISCORD_COOLDOWN"])

    trade_logic = TradeLogic(
        config, order_mgr, risk, signal_gen, trend_detector,
        db, rest, ws_stream, webhook
    )

    def get_current_symbols():
        base = trade_logic.current_symbols if trade_logic.current_symbols else config["STATIC_SYMBOLS"]
        active = list(trade_logic.active_trades.keys())
        return list(dict.fromkeys(base + active))

    health = HealthCheck(
        config, rest, ws_api, ws_stream, db, webhook,
        get_symbols_func=get_current_symbols,
        enable_ws=True
    )
    trade_logic.health_check = health

    error_handler = ErrorHandler(webhook)

    background_tasks = []
    try:
        if not config["PAPER_TRADE"] and ws_api:
            try:
                await ws_api.connect()
            except Exception as e:
                # Order execution falls back to REST automatically (OrderManager
                # checks is_connected()); do NOT let a WS API outage at startup
                # kill the bot. HealthCheck keeps retrying the connection.
                logger.error(f"WebSocket API unavailable at startup ({e}); continuing with REST-only order execution.")
                try:
                    await ws_api.disconnect()
                except Exception:
                    pass

        await trade_logic.reconcile_positions()
        await trade_logic.update_symbols()

        # Connect public WebSocket market stream for real-time tick prices
        try:
            await ws_stream.connect(get_current_symbols())
        except Exception as e:
            logger.warning(f"WebSocket stream initial connect issue (falling back to REST): {e}")

        # Dedicated all-market mini-ticker stream: realtime prices for the
        # screener/web monitor (and price fallback for ANY symbol). Failure is
        # silent — refresh falls back to the cheap bulk REST request.
        try:
            await ws_stream.connect_all_market_tickers()
        except Exception as e:
            logger.warning(f"All-market ticker stream initial connect issue (REST fallback): {e}")

        # Network-path insurance: some tunnels complete the fstream handshake
        # and SUBSCRIBE ACK but never deliver market-data frames (silent drop).
        # Give the ARR socket a grace window; if no frames land, feed the same
        # all_tickers cache via the cheap bulk REST request so the screener,
        # price fallback and dashboard keep working. The WS path remains
        # preferred — rows written by frames carry no transport tag.
        try:
            if hasattr(ws_stream, "start_rest_ticker_fallback"):
                async def _arr_grace_check():
                    await asyncio.sleep(20)
                    if not ws_stream.is_arr_stream_alive():
                        ws_stream.start_rest_ticker_fallback(
                            rest, list(trade_logic.current_symbols or config["STATIC_SYMBOLS"]))
                asyncio.create_task(_arr_grace_check())
        except Exception as e:
            logger.warning(f"REST ticker fallback setup failed ({e}); continuing without it.")

        # User-data events (executionReport fills + account balances) arrive on
        # the WS API session itself via userDataStream.subscribe — attempted
        # inside ws_api.connect(); if it fails, fill confirmation stays on REST.

        # Keep strong references to background loops so they are never garbage-collected mid-run
        background_tasks = [
            asyncio.create_task(health.run()),
            asyncio.create_task(trade_logic.run()),
            asyncio.create_task(trade_logic.refresh_symbols_loop()),
            asyncio.create_task(trade_logic.send_daily_report_loop()),
        ]

        while not shutdown_event.is_set():
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received.")
    except Exception as e:
        await error_handler.handle(e, "main_loop")
    finally:
        # 1. Stop the engine loops first so no new work is enqueued.
        for task in background_tasks:
            task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)
        # 2. Tear down connections and the database in dependency order.
        if ws_api:
            await ws_api.disconnect()
        await ws_stream.disconnect()
        await db.close()
        await rest.close()
        await webhook.close()
        # 3. Release the single-instance lock LAST, only once cleanup finished.
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()
        logger.info("Shutdown complete.")

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    # Signal handlers only flag the shutdown event (see request_shutdown) — they
    # never cancel tasks or stop the loop themselves, so a SIGTERM/SIGINT cannot
    # hang or leave the engine half-torn-down.
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, request_shutdown, sig)
    try:
        loop.run_until_complete(main())
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        loop.close()
