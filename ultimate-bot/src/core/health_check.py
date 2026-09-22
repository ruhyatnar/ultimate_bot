import asyncio
import logging

class HealthCheck:
    def __init__(self, config, rest, ws_api, ws_stream, db, webhook, get_symbols_func, enable_ws=True):
        self.config = config
        self.rest = rest
        self.ws_api = ws_api
        self.ws_stream = ws_stream
        self.db = db
        self.webhook = webhook
        self.get_symbols_func = get_symbols_func
        self.logger = logging.getLogger(__name__)
        self.interval = config["HEALTH_CHECK_INTERVAL"]
        self.enable_ws = enable_ws
        self._last_reconnect_time = {}
        self.pause_trading = False

    async def run(self):
        while True:
            try:
                await self.check()
            except Exception as e:
                self.logger.error(f"Health check failed: {e}")
            await asyncio.sleep(self.interval)

    async def check(self):
        try:
            await self.rest.ping()
            self.pause_trading = False
        except Exception as e:
            self.logger.error(f"REST health check failed: {e}")
            await self.webhook.send(f"REST health check failed: {e}")
            await self.reconnect("rest")
            if "maintenance" in str(e).lower() or "system busy" in str(e).lower():
                self.pause_trading = True
                await self.webhook.send("⚠️ Exchange maintenance detected. Pausing trading.")
        if self.enable_ws:
            ws_can_connect = getattr(self.ws_api, "can_connect", lambda: True)() if self.ws_api else False
            if self.ws_api and not self.config.get("PAPER_TRADE", False) and ws_can_connect and not self.ws_api.is_connected():
                await self.reconnect("ws_api")
            if self.ws_stream and not self.ws_stream.is_connected():
                await self.reconnect("ws_stream")
        try:
            # fetch_one goes through the dedicated read connection, so this is a
            # real liveness probe (execute() only enqueues onto the write queue).
            await self.db.fetch_one("SELECT 1")
        except Exception as e:
            self.logger.error(f"DB health check failed: {e}")

    async def reconnect(self, component):
        self.logger.info(f"Reconnecting {component}...")
        now = asyncio.get_event_loop().time()
        if now - self._last_reconnect_time.get(component, 0) < 10:
            self.logger.warning(f"Reconnect for {component} attempted too soon; skipping.")
            return
        self._last_reconnect_time[component] = now
        for attempt in range(3):
            try:
                if component == "ws_api":
                    if self.ws_api:
                        await self.ws_api.disconnect()
                        await self.ws_api.connect()
                elif component == "ws_stream":
                    if self.ws_stream:
                        await self.ws_stream.disconnect()
                        symbols = self.get_symbols_func() or ["BTCUSDT"]
                        await self.ws_stream.connect(symbols)
                elif component == "rest":
                    await self.rest.close()
                    await self.rest.init()
                return
            except Exception as e:
                self.logger.error(f"Reconnect attempt {attempt+1} for {component} failed: {e}")
                await asyncio.sleep(5 * (attempt+1))
