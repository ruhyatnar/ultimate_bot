import logging
import time

class ErrorHandler:
    def __init__(self, webhook):
        self.webhook = webhook
        self.logger = logging.getLogger(__name__)
        self._last_webhook_time = 0
        self._webhook_cooldown = 60

    async def handle(self, error, context=""):
        self.logger.error(f"Error in {context}: {error}")
        now = time.time()
        if now - self._last_webhook_time >= self._webhook_cooldown:
            await self.webhook.send(f"Error: {context}\n{str(error)}")
            self._last_webhook_time = now
