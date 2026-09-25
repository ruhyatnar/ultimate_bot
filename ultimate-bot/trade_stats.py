#!/usr/bin/env python3
"""One definition of "an exit" — and of "a trade" — for every DB reader.

Five readers answered "how many trades closed, and for how much?" with their own
SQL: the web monitor's stats (`status.py`), the futures-soak card
(`_futures_soak_snapshot`), `soak_watchdog.py`'s Discord summaries,
`soak_report.py`'s post-run report, and `smoke_test.py`'s cross-check. They
drifted, and the drift was invisible because each copy looked plausible on its
own:

  * identifying an exit by `side='SELL'` silently drops every SHORT's exit — a
    short position is closed with a BUY — so a FUTURES soak (whose whole point
    is to exercise shorts) under-counted exactly the trades it exists to
    observe;
  * counting exit ROWS as trades double-counts a scale-out (a partial leg plus a
    final leg is ONE trade) and can file that single trade as both a win and a
    loss;
  * `soak_watchdog` asked for `status='CLOSED'`, a status the engine has never
    written (it writes NEW/FILLED/PARTIALLY_FILLED/CANCELED/EXPIRED/REJECTED),
    so every soak summary reported 0 closed trades and $0.00 PnL.

Readers get the predicate and the attribution CTE from here instead of
re-deriving them. The ENGINE keeps its own copy (`src/trade/trade_logic.py`)
because it runs as a package and must not import a monitor-side script; the two
copies are checked against each other by the suites rather than shared.
"""

# An exit is a row the engine LABELLED (`exit_reason`, written by close_trade) or
# a legacy row with a non-zero realized PnL. Side is deliberately not part of the
# test: a long exits with a SELL, a short with a BUY.
EXIT_PREDICATE = (
    "(exit_reason IS NOT NULL OR (profit_loss IS NOT NULL AND profit_loss != 0))"
)
# Pre-migration DBs have no `orders.exit_reason` column at all, so the predicate
# must not reference it there (the monitor opens such a file read-only and must
# never migrate it).
LEGACY_EXIT_PREDICATE = "(profit_loss IS NOT NULL AND profit_loss != 0)"


def has_exit_reason(conn):
    """Has this DB been migrated to persist exit reasons?

    Accepts a connection or a cursor — both expose `execute`.
    """
    try:
        return any(
            row[1] == "exit_reason"
            for row in conn.execute("PRAGMA table_info(orders)")
        )
    except Exception:
        return False


def exit_predicate(has_reason=True):
    """The SQL test for 'this orders row is an exit'."""
    return EXIT_PREDICATE if has_reason else LEGACY_EXIT_PREDICATE


def trade_cte(exit_pred):
    """The `WITH exits/legs/trades` prefix — legs attributed to their trade.

    A scale-out writes a partial exit leg (CANCELED) and then a final one
    (FILLED) for the same position, so trailing each leg to the NEXT FILLED exit
    on that symbol (via a window function) is what turns legs into trades; the
    trade is then classified by its NET PnL. A leg with no FILLED exit after it
    belongs to a still-open position (a banked partial): it falls back to its own
    timestamp so its money is still counted and the trade nets keep adding up to
    the realized total.

    Callers append their own `SELECT ... FROM trades` (or `FROM exits` for a raw
    leg listing).
    """
    return (
        "WITH exits AS ("
        f"  SELECT id, symbol, status, profit_loss, created_at FROM orders WHERE {exit_pred}"
        "), legs AS ("
        "  SELECT symbol, created_at, profit_loss,"
        "         MIN(CASE WHEN status = 'FILLED' THEN created_at END) OVER ("
        "           PARTITION BY symbol ORDER BY created_at, id"
        "           ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING"
        "         ) AS trade_key"
        "  FROM exits"
        "), trades AS ("
        "  SELECT symbol, COALESCE(trade_key, created_at) AS trade_key,"
        "         SUM(profit_loss) AS pnl FROM legs"
        "  GROUP BY symbol, COALESCE(trade_key, created_at)"
        ")"
    )


def trades_totals_sql(exit_pred):
    """Per-TRADE totals: closed / wins / losses / pnl.

    `closed` counts trades (not exit legs), so `closed == wins + losses +
    breakevens` always holds and the PnL sum covers the same rows the counts do.
    """
    return trade_cte(exit_pred) + (
        " SELECT COUNT(*) AS closed,"
        "  COALESCE(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END), 0) AS wins,"
        "  COALESCE(SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END), 0) AS losses,"
        "  COALESCE(SUM(pnl), 0) AS pnl"
        " FROM trades"
    )


def recent_exits_sql(exit_pred, limit=10):
    """Newest exit LEGS (each with its own PnL) for a 'recent exits' listing."""
    try:
        limit = max(1, int(limit))
    except (TypeError, ValueError):
        limit = 10
    return (
        "SELECT symbol, side, status, profit_loss, created_at FROM orders"
        f" WHERE {exit_pred} ORDER BY created_at DESC LIMIT {limit}"
    )
