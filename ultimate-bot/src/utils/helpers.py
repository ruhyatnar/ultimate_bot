import logging
import sys
import os
from logging.handlers import RotatingFileHandler

def setup_logging(config):
    log_level = getattr(logging, config["LOG_LEVEL"].upper(), logging.INFO)
    handlers = [logging.StreamHandler(sys.stdout)]
    if config.get("LOG_FILE"):
        log_dir = os.path.dirname(config["LOG_FILE"])
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        handlers.append(RotatingFileHandler(config["LOG_FILE"], maxBytes=10*1024*1024, backupCount=5))
    logging.basicConfig(level=log_level, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', handlers=handlers)
    # Third-party libraries dump every raw frame/request at DEBUG (the websockets
    # client logs each aggTrade/kline payload verbatim). At LOG_LEVEL=DEBUG that
    # flooded the log at ~10 MB/min with zero strategy value, filling the disk and
    # swamping the PM2 stdout log. Pin them to WARNING regardless of LOG_LEVEL.
    for noisy in ("websockets", "aiohttp", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
