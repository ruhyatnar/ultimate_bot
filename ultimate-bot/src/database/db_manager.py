import asyncio
import logging
import os
from datetime import datetime
import aiosqlite

class DatabaseManager:
    def __init__(self, db_path):
        self.db_path = db_path
        self.conn = None
        self.read_conn = None
        self.logger = logging.getLogger(__name__)
        self.write_queue = asyncio.Queue(maxsize=1000)
        self._writer_task = None

    async def init(self):
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self.conn = await aiosqlite.connect(self.db_path, isolation_level=None)
        await self.conn.execute("PRAGMA foreign_keys = ON")
        await self.conn.execute("PRAGMA journal_mode = WAL")
        await self.conn.execute("PRAGMA synchronous = NORMAL")
        await self.conn.execute("PRAGMA busy_timeout = 5000")
        self.read_conn = await aiosqlite.connect(self.db_path, isolation_level=None)
        await self.read_conn.execute("PRAGMA query_only = ON")
        await self.read_conn.execute("PRAGMA busy_timeout = 5000")
        await self._create_tables()
        self._writer_task = asyncio.create_task(self._write_worker())
        self.logger.info("Database initialized.")

    async def _write_worker(self):
        while True:
            try:
                batch = []
                query, params = await self.write_queue.get()
                batch.append((query, params))
                while len(batch) < 20 and not self.write_queue.empty():
                    q, p = self.write_queue.get_nowait()
                    batch.append((q, p))
                if batch:
                    await self.conn.execute("BEGIN TRANSACTION")
                    try:
                        for q, p in batch:
                            await self.conn.execute(q, p)
                        await self.conn.commit()
                    except Exception as e:
                        await self.conn.rollback()
                        self.logger.error(f"Batch write failed: {e}")
                    finally:
                        for _ in batch:
                            self.write_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Writer worker error: {e}")

    async def execute(self, query, params=None):
        await self.write_queue.put((query, params or ()))

    async def fetch_all(self, query, params=None):
        async with self.read_conn.execute(query, params or ()) as cursor:
            return await cursor.fetchall()

    async def fetch_one(self, query, params=None):
        async with self.read_conn.execute(query, params or ()) as cursor:
            return await cursor.fetchone()

    async def close(self):
        # Allow writer worker to drain any queued writes before shutting down
        try:
            if not self.write_queue.empty():
                await asyncio.wait_for(self.write_queue.join(), timeout=3.0)
        except Exception:
            pass
        if self._writer_task:
            self._writer_task.cancel()
            try:
                await self._writer_task
            except asyncio.CancelledError:
                pass
        if self.conn:
            await self.conn.close()
        if self.read_conn:
            await self.read_conn.close()

    async def _create_tables(self):
        await self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT UNIQUE,
                symbol TEXT, side TEXT, order_type TEXT,
                price REAL, stop_price REAL, quantity REAL, executed_qty REAL,
                status TEXT, created_at INTEGER, updated_at INTEGER,
                profit_loss REAL DEFAULT 0,
                avg_fill_price REAL
            );
            CREATE TABLE IF NOT EXISTS active_trades (
                symbol TEXT PRIMARY KEY,
                entry_price REAL, side TEXT, quantity REAL, entry_time INTEGER,
                stop_price REAL, take_profit REAL, atr REAL,
                trailing_active INTEGER, trailing_stop REAL, breakeven_activated INTEGER,
                order_id TEXT,
                initial_qty REAL, initial_stop_price REAL, scale_out_done INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS risk_state (
                key TEXT PRIMARY KEY, value TEXT, updated_at INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at);
        """)
        # Idempotent migration for databases created before the scale-out anchors
        # were persisted. Without these columns a restart loses initial_qty /
        # initial_stop_price / scale_out_done, so the +1R scale-out can never
        # fire again (and could double-fire) on a restored position.
        cur = await self.conn.execute("PRAGMA table_info(active_trades)")
        existing_cols = {row[1] for row in await cur.fetchall()}
        for col, decl in (("initial_qty", "REAL"),
                          ("initial_stop_price", "REAL"),
                          ("scale_out_done", "INTEGER DEFAULT 0")):
            if col not in existing_cols:
                await self.conn.execute(f"ALTER TABLE active_trades ADD COLUMN {col} {decl}")
        # Migration for databases created before risk_state gained updated_at.
        # Without it every set_risk_state batch write fails forever on old files.
        cur = await self.conn.execute("PRAGMA table_info(risk_state)")
        rs_cols = {row[1] for row in await cur.fetchall()}
        if "updated_at" not in rs_cols:
            await self.conn.execute("ALTER TABLE risk_state ADD COLUMN updated_at INTEGER")
        await self.conn.commit()

    async def save_order(self, order_data):
        query = """INSERT INTO orders (order_id, symbol, side, order_type, price, stop_price, quantity, executed_qty, status, created_at, updated_at, profit_loss, avg_fill_price)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(order_id) DO UPDATE SET
                       symbol=excluded.symbol, side=excluded.side, order_type=excluded.order_type,
                       price=excluded.price, stop_price=excluded.stop_price, quantity=excluded.quantity,
                       executed_qty=excluded.executed_qty, status=excluded.status, updated_at=excluded.updated_at,
                       profit_loss=excluded.profit_loss, avg_fill_price=excluded.avg_fill_price"""
        await self.execute(query, tuple(order_data.get(k) for k in [
            "order_id","symbol","side","order_type","price","stop_price","quantity",
            "executed_qty","status","created_at","updated_at","profit_loss","avg_fill_price"
        ]))

    async def update_order_status(self, order_id, status, executed_qty=None, avg_fill_price=None, profit_loss=None):
        fields, params = ["status = ?", "updated_at = ?"], [status, int(datetime.now().timestamp()*1000)]
        if executed_qty is not None:
            fields.append("executed_qty = ?"); params.append(executed_qty)
        if avg_fill_price is not None:
            fields.append("avg_fill_price = ?"); params.append(avg_fill_price)
        if profit_loss is not None:
            fields.append("profit_loss = ?"); params.append(profit_loss)
        params.append(order_id)
        await self.execute(f"UPDATE orders SET {', '.join(fields)} WHERE order_id = ?", params)

    async def save_active_trade(self, trade):
        await self.execute("""INSERT OR REPLACE INTO active_trades
            (symbol, entry_price, side, quantity, entry_time, stop_price, take_profit, atr,
             trailing_active, trailing_stop, breakeven_activated, order_id,
             initial_qty, initial_stop_price, scale_out_done)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (trade["symbol"], trade["entry_price"], trade["side"], trade["quantity"],
             trade["entry_time"], trade["stop_price"], trade["take_profit"], trade["atr"],
             1 if trade.get("trailing_active", False) else 0,
             trade.get("trailing_stop", trade["stop_price"]),
             1 if trade.get("breakeven_activated", False) else 0,
             trade.get("order_id"),
             trade.get("initial_qty", trade["quantity"]),
             trade.get("initial_stop_price", trade["stop_price"]),
             1 if trade.get("scale_out_done", False) else 0))

    async def get_active_trades(self):
        keys = ["symbol","entry_price","side","quantity","entry_time","stop_price","take_profit","atr","trailing_active","trailing_stop","breakeven_activated","order_id","initial_qty","initial_stop_price","scale_out_done"]
        query = f"SELECT {', '.join(keys)} FROM active_trades"
        rows = await self.fetch_all(query)
        result = []
        for row in rows:
            d = dict(zip(keys, row))
            d["trailing_active"] = bool(d.get("trailing_active"))
            d["breakeven_activated"] = bool(d.get("breakeven_activated"))
            d["scale_out_done"] = bool(d.get("scale_out_done"))
            result.append(d)
        return result

    async def delete_active_trade(self, symbol):
        await self.execute("DELETE FROM active_trades WHERE symbol = ?", (symbol,))

    async def get_risk_state(self, key):
        row = await self.fetch_one("SELECT value FROM risk_state WHERE key = ?", (key,))
        return row[0] if row else None

    async def set_risk_state(self, key, value):
        await self.execute("INSERT OR REPLACE INTO risk_state (key, value, updated_at) VALUES (?, ?, ?)",
                           (key, value, int(datetime.now().timestamp() * 1000)))
