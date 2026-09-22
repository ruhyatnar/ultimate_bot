"""WS-first account balance provider.

Every engine balance question (equity refresh, pre-trade free-quote check,
sell-quantity clamp, orphan-position sync) goes through `get_account()`:

  1. The WebSocket user-data stream cache (`outboundAccountPosition` events) is
     used when the stream is connected/active and its newest datum is within
     WS_BALANCE_MAX_AGE — zero REST weight, ~1s freshness.
  2. Otherwise a REST /api/v3/account snapshot is taken and merged into the WS
     cache (`seed_balances`), so the next read is WS-served again.

The returned shape stays `/api/v3/account`-compatible (``{"balances": [...]}``)
plus provenance keys (``source``/``age_s``) so callers can log which transport
answered — and the web monitor can show it.
"""
import logging

logger = logging.getLogger(__name__)


async def get_account(rest, ws_api=None, config=None, max_age=None, prefer_ws=True):
    """Return account balances, WS cache first, REST snapshot as fallback.

    Never raises for a *cache* miss (falls through to REST); REST errors
    propagate exactly as before so each call site keeps its own guard.
    """
    if prefer_ws and ws_api is not None:
        try:
            cached = ws_api.cached_account(max_age)
        except Exception as e:  # a broken cache must never block a balance read
            logger.debug(f"WS balance cache read failed ({e}); falling back to REST.")
            cached = None
        if cached:
            return cached

    account = await rest.get_account()
    try:
        if ws_api is not None and hasattr(ws_api, "seed_balances"):
            ws_api.seed_balances(account)
    except Exception as e:
        logger.debug(f"Could not seed WS balance cache from REST snapshot: {e}")
    if isinstance(account, dict):
        account.setdefault("source", "rest")
        account.setdefault("age_s", 0.0)
    return account
