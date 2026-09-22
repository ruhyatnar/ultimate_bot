import json
import logging
import math
from datetime import datetime, timezone
from decimal import Decimal
from src.exchange.balance_cache import get_account

class RiskManager:
    def __init__(self, config, db, rest, ws_api=None):
        self.config = config
        self.db = db
        self.rest = rest
        # WS-first balance source: when the user-data stream cache is fresh the
        # equity refresh costs no REST weight (REST remains the fallback).
        self.ws_api = ws_api
        self.logger = logging.getLogger(__name__)
        self.daily_pnl = 0.0
        self.last_reset_date = None
        self.symbol_states = {}
        self.total_equity = 0.0
        self.unrealized_pnl = 0.0
        self.paper_balance = 1000.0
        # Daily-drawdown circuit breaker: armed when daily PnL (incl. unrealized)
        # breaches MAX_DAILY_DRAWDOWN; cleared only by the UTC daily reset.
        self.drawdown_breaker = False
        self.breaker_reason = ""
        # Account-level streak counters. Per-symbol streaks cannot trip a breaker
        # when the dynamic screener rotates pairs — every loss lands on a fresh
        # symbol with streak 0 — so the account-wide consecutive outcome is
        # tracked here and feeds the same COOLDOWN_LOSS / COOLDOWN_WIN breakers.
        self.loss_streak = 0
        self.win_streak = 0
        self.cooldown_until = 0
        self.cooldown_kind = "loss"
        self._last_snap_json = ""

    async def load_state(self):
        # Every persisted value is defensively parsed: a corrupt or NaN row
        # (float("NaN") parses fine but poisons every drawdown/limit comparison)
        # must degrade to a safe default at boot, never crash the engine.
        daily_pnl_str = await self.db.get_risk_state("daily_pnl")
        if daily_pnl_str:
            try:
                val = float(daily_pnl_str)
                self.daily_pnl = val if math.isfinite(val) else 0.0
            except (TypeError, ValueError):
                self.logger.warning(f"Corrupt risk_state daily_pnl={daily_pnl_str!r}; resetting to 0.0")
        last_reset = await self.db.get_risk_state("last_reset_date")
        if last_reset:
            try:
                self.last_reset_date = datetime.fromisoformat(last_reset)
            except (TypeError, ValueError):
                self.logger.warning(f"Corrupt risk_state last_reset_date={last_reset!r}; ignoring")
        # Account-level streaks + global cooldown (defensively parsed).
        for key, attr in (("loss_streak", "loss_streak"), ("win_streak", "win_streak")):
            raw = await self.db.get_risk_state(key)
            if raw:
                try:
                    val = int(float(raw))
                    if math.isfinite(val) and val > 0:
                        setattr(self, attr, val)
                except (TypeError, ValueError):
                    self.logger.warning(f"Corrupt risk_state {key}={raw!r}; resetting to 0")
        cd_raw = await self.db.get_risk_state("cooldown_until")
        if cd_raw:
            try:
                self.cooldown_until = int(float(cd_raw))
            except (TypeError, ValueError):
                self.cooldown_until = 0
        kind_raw = await self.db.get_risk_state("cooldown_kind")
        if kind_raw in ("win", "loss"):
            self.cooldown_kind = kind_raw
        paper_balance_str = await self.db.get_risk_state("paper_balance")
        if paper_balance_str:
            try:
                val = float(paper_balance_str)
                if math.isfinite(val) and val > 0:
                    self.paper_balance = val
                else:
                    self.logger.warning(f"Invalid risk_state paper_balance={paper_balance_str!r}; keeping default {self.paper_balance}")
            except (TypeError, ValueError):
                self.logger.warning(f"Corrupt risk_state paper_balance={paper_balance_str!r}; keeping default {self.paper_balance}")
        # Restore per-symbol streak/cooldown state for EVERY persisted symbol, not just
        # the static list. Dynamic-screen symbols are stored as risk_<SYMBOL> rows too;
        # ignoring them on restart would silently reset cooldowns and let a symbol that
        # was cooling down re-enter immediately after a restart.
        try:
            rows = await self.db.fetch_all("SELECT key, value FROM risk_state")
            for key, val in (rows or []):
                # "risk_state" is a legacy/aggregate key name, not a symbol row.
                if key.startswith("risk_") and key != "risk_state" and val:
                    symbol = key[len("risk_"):]
                    try:
                        state = json.loads(val)
                        if isinstance(state, dict):
                            self.symbol_states[symbol] = state
                    except (ValueError, TypeError):
                        continue
        except Exception as e:
            self.logger.warning(f"Could not restore per-symbol risk state from DB: {e}")
        for symbol in self.config["STATIC_SYMBOLS"]:
            self.symbol_states.setdefault(symbol, {"loss_streak": 0, "win_streak": 0, "cooldown_until": 0})
        if self.config.get("PAPER_TRADE", False):
            self.total_equity = self.paper_balance
            self.logger.info(f"Paper trading mode: simulated equity {self.total_equity} USDT.")
        else:
            await self._fetch_equity()
        self._check_notional_feasibility()

    def _check_notional_feasibility(self):
        """Boot-time sanity check: equity × allocation caps must reach the Binance
        spot minNotional floor, otherwise EVERY entry is rejected (silently in the
        screener loop) and the bot does nothing while appearing healthy."""
        try:
            alloc_cap = min(float(self.config.get("BALANCE_USAGE_PERCENT", 1.0)),
                            float(self.config.get("MAX_SYMBOL_ALLOCATION_PERCENT", 1.0)))
            min_notional = 5.0  # Binance spot floor (also the code's fallback)
            max_notional = float(self.total_equity) * alloc_cap
            if max_notional < min_notional:
                self.logger.warning(
                    f"CONFIG WARNING: max position notional ${max_notional:.2f} "
                    f"(equity ${self.total_equity:.2f} × alloc {alloc_cap:.0%}) is below "
                    f"the ${min_notional:.2f} Binance minNotional floor — ALL entries will "
                    f"be rejected. Raise BALANCE_USAGE_PERCENT / MAX_SYMBOL_ALLOCATION_PERCENT "
                    f"or add capital.")
        except Exception:
            pass  # never block boot on a sanity check

    async def _fetch_equity(self):
        try:
            account = await get_account(self.rest, self.ws_api, self.config)
            total_equity = 0.0
            free_quote = 0.0
            locked_quote = 0.0
            balances_summary = []
            quote = self.config.get("QUOTE_ASSET", "USDT")

            for b in account.get("balances", []):
                asset = b.get("asset", "")
                free = float(b.get("free", 0.0))
                locked = float(b.get("locked", 0.0))
                total = free + locked
                if total <= 0:
                    continue

                usd_val = 0.0
                if asset == quote:
                    total_equity += total
                    free_quote = free
                    locked_quote = locked
                    usd_val = total
                else:
                    symbol = asset + quote
                    # Only price pairs that exist on this market. The futures
                    # wallet holds bonus-rate assets (U, BFUSD, RWUSD, ...) whose
                    # '<ASSET>USDT' pairs are NOT tradable symbols — probing
                    # them burned REST retries (-1121) every equity cycle.
                    try:
                        universe = getattr(self.rest, "symbol_info_cache", None)
                        if universe is not None and symbol not in universe:
                            usd_val = 0.0
                            balances_summary.append({
                                "asset": asset,
                                "free": free,
                                "locked": locked,
                                "total": total,
                                "usd_value": usd_val
                            })
                            continue
                    except Exception:
                        pass
                    try:
                        ticker = await self.rest.get_ticker(symbol)
                        price = float(ticker.get("price", 0.0))
                        usd_val = total * price
                        total_equity += usd_val
                    except Exception:
                        usd_val = 0.0

                balances_summary.append({
                    "asset": asset,
                    "free": free,
                    "locked": locked,
                    "total": total,
                    "usd_value": usd_val
                })

            # Futures: the v3 account payload carries authoritative totals that
            # already include unrealized PnL (the per-asset wallet rows do not).
            # Prefer them so sizing and the daily-drawdown breaker see true equity.
            if self.config.get("MARKET", "spot") == "futures":
                try:
                    tmb = float(account.get("totalMarginBalance") or 0.0)
                    if tmb > 0:
                        total_equity = tmb
                except (TypeError, ValueError):
                    pass

            self.total_equity = total_equity
            self.free_quote = free_quote
            self.locked_quote = locked_quote
            self.balances_summary = balances_summary
            self.logger.info(f"Live account equity updated: ${self.total_equity:.2f} {quote} (Free {quote}: ${self.free_quote:.2f}, via {account.get('source', 'rest')})")
        except Exception as e:
            self.logger.warning(f"Could not fetch live account equity from exchange: {e}")

    async def save_state(self):
        await self.db.set_risk_state("daily_pnl", str(self.daily_pnl))
        if self.last_reset_date:
            await self.db.set_risk_state("last_reset_date", self.last_reset_date.isoformat())
        await self.db.set_risk_state("unrealized_pnl", str(self.unrealized_pnl))
        # Account-level streaks + global cooldown (the web monitor's Streak
        # Monitor reads these; per-symbol blobs stay for per-symbol cooldowns).
        await self.db.set_risk_state("loss_streak", str(self.loss_streak))
        await self.db.set_risk_state("win_streak", str(self.win_streak))
        await self.db.set_risk_state("cooldown_until", str(self.cooldown_until))
        await self.db.set_risk_state("cooldown_kind", self.cooldown_kind)
        for symbol, state in self.symbol_states.items():
            await self.db.set_risk_state(f"risk_{symbol}", json.dumps(state))

        # Always persist live & total equity so all monitors (CLI, status.py, web npx) sync accurately
        await self.db.set_risk_state("total_equity", str(self.total_equity))
        await self.db.set_risk_state("live_equity", str(self.total_equity))
        await self.db.set_risk_state("equity", str(self.total_equity))

        free_q = getattr(self, "free_quote", self.total_equity)
        locked_q = getattr(self, "locked_quote", 0.0)
        await self.db.set_risk_state("free_quote", str(free_q))
        await self.db.set_risk_state("locked_quote", str(locked_q))

        # Mirror paper_balance so any client reading paper_balance stays in sync regardless of mode
        self.paper_balance = self.total_equity
        await self.db.set_risk_state("paper_balance", str(self.paper_balance))

        if hasattr(self, "balances_summary") and self.balances_summary:
            await self.db.set_risk_state("account_balances", json.dumps(self.balances_summary))

    async def _publish_risk_snapshot(self):
        """Publish one JSON snapshot of breaker/streak/cooldown state to risk_state
        so status.py (and therefore the web monitor) shows the engine's REAL risk
        state instead of guessing it client-side. Cheap: skipped when unchanged."""
        try:
            now = int(datetime.now(timezone.utc).timestamp())
            max_cooldown = 0
            cooldown_kind = "loss"
            active_cooldowns = {}
            for sym, st in self.symbol_states.items():
                try:
                    cd = int(st.get("cooldown_until", 0) or 0)
                except (TypeError, ValueError):
                    continue
                if cd > max_cooldown:
                    max_cooldown = cd
                    cooldown_kind = str(st.get("cooldown_kind", "loss"))
                if cd > now:
                    active_cooldowns[sym] = {"until": cd, "kind": str(st.get("cooldown_kind", "loss"))}
            drawdown_used = 0.0
            if self.total_equity > 0:
                drawdown_used = max(0.0, -self.daily_pnl) / self.total_equity
            # Account-level streaks are what the monitor's Streak Monitor shows;
            # per-symbol worst streaks stay available as per-symbol cooldown data.
            # While a global cooldown is active, display the value that TRIPPED it
            # (the live counter was reset when the breaker armed), so the monitor
            # shows e.g. "Loss Streak 3/3" for the whole pause instead of 1.
            loss_disp, win_disp = self.loss_streak, self.win_streak
            if self.cooldown_until > now:
                if self.cooldown_kind == "loss":
                    loss_disp = max(loss_disp, int(self.config["MAX_LOSS_STREAK"]))
                else:
                    win_disp = max(win_disp, int(self.config["MAX_WIN_STREAK"]))
            snap = {
                # Top-level streaks are ACCOUNT-level only — mixing per-symbol
                # worsts here would mask the true account run (e.g. a win after
                # a global cooldown showed "Loss Streak 1" from stale symbol
                # counters). Per-symbol streaks still flow via active_cooldowns
                # and the per-symbol risk_<SYMBOL> rows.
                "loss_streak": loss_disp,
                "win_streak": win_disp,
                "cooldown_until": max(max_cooldown, self.cooldown_until),
                "cooldown_kind": self.cooldown_kind if self.cooldown_until >= max_cooldown else cooldown_kind,
                "active_cooldowns": active_cooldowns,
                "drawdown_used": round(drawdown_used, 6),
                "drawdown_breaker": bool(self.drawdown_breaker),
                "breaker_reason": self.breaker_reason,
                "daily_pnl": round(self.daily_pnl, 8),
            }
            snap_json = json.dumps(snap, sort_keys=True)
            if snap_json != self._last_snap_json:
                await self.db.set_risk_state("engine_risk", snap_json)
                self._last_snap_json = snap_json
        except Exception as e:
            self.logger.warning(f"Risk snapshot publish failed: {e}")

    async def check_risk(self, symbol, unrealized_pnl=0.0):
        self.unrealized_pnl = unrealized_pnl
        await self._reset_daily_if_needed()
        total_loss = self.daily_pnl + self.unrealized_pnl
        self.logger.debug(f"Risk check: daily_pnl={self.daily_pnl}, unrealized={unrealized_pnl}, total_loss={total_loss}")

        # Daily-drawdown circuit breaker. Stays armed until the UTC daily reset
        # (not just while the instantaneous loss is below the limit), and the
        # trip is published so the web monitor can show WHY entries are blocked.
        if self.total_equity > 0:
            breach = total_loss <= -self.config["MAX_DAILY_DRAWDOWN"] * self.total_equity
            if self.drawdown_breaker or breach:
                if not self.drawdown_breaker:
                    self.drawdown_breaker = True
                    self.breaker_reason = (
                        f"Daily PnL {total_loss / self.total_equity * 100:+.2f}% breached "
                        f"-{self.config['MAX_DAILY_DRAWDOWN'] * 100:.1f}% equity limit"
                    )
                    self.logger.warning(
                        f"CIRCUIT BREAKER TRIPPED: {self.breaker_reason} — entries blocked until UTC daily reset"
                    )
                    await self.save_state()
                await self._publish_risk_snapshot()
                return False

        if symbol not in self.symbol_states:
            self.symbol_states[symbol] = {"loss_streak":0, "win_streak":0, "cooldown_until":0}
        state = self.symbol_states[symbol]
        self.logger.debug(f"{symbol} risk state: {state}")
        # Per-symbol loss re-entry cooldown: after ANY stop-out on this symbol,
        # wait LOSS_REENTRY_COOLDOWN seconds before re-entering it — regardless
        # of streaks. Without this, a fresh signal on the very next bucket close
        # re-entered ONEUSDT 4 minutes after its stop-out (10s monitor cooldown
        # was the only brake), doubling the loss on a pair that is still dumping.
        try:
            loss_cd = int(self.config.get("LOSS_REENTRY_COOLDOWN", 900) or 0)
        except (TypeError, ValueError):
            loss_cd = 900
        if loss_cd > 0:
            try:
                last_loss_at = float(state.get("last_loss_at", 0) or 0)
            except (TypeError, ValueError):
                last_loss_at = 0.0
            if last_loss_at > 0 and (int(datetime.now().timestamp()) - last_loss_at) < loss_cd:
                self.logger.debug(
                    f"{symbol} in loss re-entry cooldown "
                    f"({loss_cd - (int(datetime.now().timestamp()) - last_loss_at)}s left)")
                await self._publish_risk_snapshot()
                return False
        # Streak cooldowns are armed with the streak RESET (update_trade_result),
        # so blocking is purely time-based: cooldown_until in the future => block.
        # Two levels:
        #   1. Account-global cooldown (armed by consecutive ACCOUNT losses/wins —
        #      what actually trips when the dynamic screener rotates pairs).
        #   2. Per-symbol cooldowns (a single pair losing repeatedly).
        now = int(datetime.now().timestamp())
        if self.cooldown_until > now:
            self.logger.debug(f"Account in {self.cooldown_kind}-streak cooldown until {self.cooldown_until}")
            await self._publish_risk_snapshot()
            return False
        if state["cooldown_until"] > now:
            self.logger.debug(f"{symbol} in streak cooldown until {state['cooldown_until']}")
            await self._publish_risk_snapshot()
            return False
        self.logger.debug("Risk check PASSED")
        await self._publish_risk_snapshot()
        return True

    async def _reset_daily_if_needed(self):
        today = datetime.now(timezone.utc).date()
        if self.last_reset_date is None or self.last_reset_date.date() != today:
            self.daily_pnl = 0.0
            self.unrealized_pnl = 0.0
            self.last_reset_date = datetime.now(timezone.utc)
            if self.drawdown_breaker:
                self.logger.info("Daily reset: drawdown circuit breaker cleared — entries unblocked")
            self.drawdown_breaker = False
            self.breaker_reason = ""
            await self.save_state()
            await self._publish_risk_snapshot()
            self.logger.info("Daily PnL reset.")

    async def update_trade_result(self, pnl, symbol=None):
        self.daily_pnl += pnl
        if self.config.get("PAPER_TRADE", False):
            self.total_equity += pnl
        else:
            try:
                await self._fetch_equity()
            except Exception as e:
                self.logger.warning(f"Failed to refresh live equity after trade: {e}")
                self.total_equity += pnl
        await self.save_state()
        # ---- Account-level streak (what the Streak Monitor displays) ----
        # The per-symbol counters below cannot trip when the dynamic screener
        # rotates pairs (each loss lands on a fresh symbol), so consecutive
        # ACCOUNT outcomes are tracked here and arm the same breakers.
        if pnl > 0:
            self.win_streak += 1
            self.loss_streak = 0
        else:
            self.loss_streak += 1
            self.win_streak = 0
        now = int(datetime.now().timestamp())
        if pnl > 0 and self.win_streak >= self.config["MAX_WIN_STREAK"]:
            self.cooldown_until = now + self.config["COOLDOWN_WIN"]
            self.cooldown_kind = "win"
            self.win_streak = 0
            self.logger.warning(
                f"ACCOUNT win streak hit {self.config['MAX_WIN_STREAK']} — global cooldown until "
                f"{datetime.fromtimestamp(self.cooldown_until, tz=timezone.utc).isoformat()}"
            )
        elif pnl < 0 and self.loss_streak >= self.config["MAX_LOSS_STREAK"]:
            self.cooldown_until = now + self.config["COOLDOWN_LOSS"]
            self.cooldown_kind = "loss"
            self.loss_streak = 0
            self.logger.warning(
                f"ACCOUNT loss streak hit {self.config['MAX_LOSS_STREAK']} — global cooldown until "
                f"{datetime.fromtimestamp(self.cooldown_until, tz=timezone.utc).isoformat()}"
            )
        await self.save_state()
        await self._publish_risk_snapshot()
        if symbol:
            state = self.symbol_states.setdefault(symbol, {"loss_streak":0, "win_streak":0, "cooldown_until":0})
            if pnl > 0:
                state["win_streak"] += 1
                state["loss_streak"] = 0
            else:
                state["loss_streak"] += 1
                state["win_streak"] = 0
            now = int(datetime.now().timestamp())
            state["last_loss_at"] = now if pnl < 0 else state.get("last_loss_at", 0)
            # When a streak trips its circuit breaker, the streak is RESET as the
            # cooldown arms. Without this, loss_streak stays >= MAX_LOSS_STREAK
            # forever, so EVERY subsequent loss re-arms the cooldown and the symbol
            # effectively trades once per cooldown window until a win happens —
            # not the documented "N consecutive losses → pause → fresh start".
            if pnl > 0 and state["win_streak"] >= self.config["MAX_WIN_STREAK"]:
                state["cooldown_until"] = now + self.config["COOLDOWN_WIN"]
                state["win_streak"] = 0
                state["cooldown_kind"] = "win"
                self.logger.warning(
                    f"{symbol}: win streak hit {self.config['MAX_WIN_STREAK']} — cooldown until "
                    f"{datetime.fromtimestamp(state['cooldown_until'], tz=timezone.utc).isoformat()}"
                )
            elif pnl < 0 and state["loss_streak"] >= self.config["MAX_LOSS_STREAK"]:
                state["cooldown_until"] = now + self.config["COOLDOWN_LOSS"]
                state["loss_streak"] = 0
                state["cooldown_kind"] = "loss"
                self.logger.warning(
                    f"{symbol}: loss streak hit {self.config['MAX_LOSS_STREAK']} — cooldown until "
                    f"{datetime.fromtimestamp(state['cooldown_until'], tz=timezone.utc).isoformat()}"
                )
            await self.save_state()
            await self._publish_risk_snapshot()

    async def calculate_position_size(self, symbol, entry_price, stop_price):
        """Fixed-fractional risk position sizing (the professional standard).

        Primary formula: qty = (equity * RISK_PER_TRADE) / (entry - stop).
        This makes every trade risk the same fixed fraction of equity (default 1%)
        regardless of how wide the ATR stop is — a 2x-ATR swing stop and a 1x-ATR
        scalp stop both lose exactly RISK_PER_TRADE of equity when hit.

        The notional allocation cap (BALANCE_USAGE_PERCENT / per-symbol cap) is kept
        as a SECONDARY ceiling so sizing can never exceed portfolio limits — but it
        no longer blindly decides the size, which previously allowed a wide stop to
        silently risk 4-6% of equity per trade.
        """
        if self.total_equity <= 0 or entry_price <= 0:
            self.logger.warning(f"Position sizing skipped for {symbol}: total_equity={self.total_equity}, entry_price={entry_price}")
            return 0.0

        # --- Primary: risk-based sizing ---
        stop_dist = entry_price - stop_price
        risk_per_unit = stop_dist if stop_dist > 0 else 0.0
        if risk_per_unit <= 0:
            self.logger.warning(f"Position sizing skipped for {symbol}: stop_price {stop_price} >= entry_price {entry_price} (no defined risk).")
            return 0.0
        risk_amount = float(self.total_equity) * float(self.config.get("RISK_PER_TRADE", 0.01))
        risk_qty = risk_amount / risk_per_unit

        # --- Secondary ceiling: portfolio notional caps ---
        allocation = Decimal(str(self.total_equity)) * Decimal(str(self.config["BALANCE_USAGE_PERCENT"]))
        max_symbol_alloc = Decimal(str(self.total_equity)) * Decimal(str(self.config["MAX_SYMBOL_ALLOCATION_PERCENT"]))
        allocation = min(allocation, max_symbol_alloc)

        # Safeguard: Never allocate more than available free quote currency (e.g. USDT)
        free_quote = None
        if not self.config.get("PAPER_TRADE", False):
            try:
                account = await get_account(self.rest, self.ws_api, self.config)
                for b in account.get("balances", []):
                    if b["asset"] == self.config["QUOTE_ASSET"]:
                        free_quote = float(b["free"])
                        break
                if free_quote is not None:
                    max_spendable = Decimal(str(max(0.0, free_quote * 0.99)))
                    allocation = min(allocation, max_spendable)
            except Exception as e:
                self.logger.warning(f"Failed to check free quote asset balance: {e}")

        alloc_qty = allocation / Decimal(str(entry_price))

        # CRITICAL: take the more conservative of the two sizings — the risk-based
        # size is PRIMARY and must never be overwritten by the notional cap, or a
        # wide ATR stop silently risks far more than RISK_PER_TRADE of equity.
        qty_dec = Decimal(str(risk_qty))
        if alloc_qty < qty_dec:
            qty_dec = alloc_qty
            self.logger.debug(
                f"{symbol}: notional cap limited size to {float(alloc_qty):.6f} (risk-based size was {risk_qty:.6f})."
            )

        filters = await self.rest.get_filters(symbol)
        step_size = Decimal(str(filters.get("LOT_SIZE", {}).get("stepSize", "0.000001")))
        min_qty = Decimal(str(filters.get("LOT_SIZE", {}).get("minQty", "0.00001")))

        # Check NOTIONAL filter
        notional_filter = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {}))
        min_notional = float(notional_filter.get("minNotional", 5.0))

        # Never let exchange floors override the risk cap: if the risk-based size
        # steps below the exchange minimum, the trade cannot be taken safely at
        # RISK_PER_TRADE risk — skipping is the professional move, not up-sizing
        # to minNotional and blowing through the risk budget.
        if qty_dec < min_qty or float(qty_dec) * entry_price < min_notional:
            self.logger.info(
                f"{symbol}: risk-based size {float(qty_dec):.6f} is below exchange minimums "
                f"(minQty {float(min_qty)}, minNotional {min_notional:.2f}); entry skipped "
                f"(risking the minimum would exceed the {self.config.get('RISK_PER_TRADE', 0.01)*100:.1f}% risk cap)."
            )
            return 0.0

        qty_dec = (qty_dec // step_size) * step_size
        qty_dec = max(qty_dec, min_qty)
        if "maxQty" in filters.get("LOT_SIZE", {}):
            qty_dec = min(qty_dec, Decimal(str(filters["LOT_SIZE"]["maxQty"])))

        order_cost = float(qty_dec) * entry_price
        if order_cost < min_notional:
            qty_step_up = qty_dec + step_size
            cost_step_up = float(qty_step_up) * entry_price
            # A one-step bump may only be taken if it stays inside the risk budget.
            risk_budget_ok = float(qty_step_up) * risk_per_unit <= risk_amount
            if risk_budget_ok and (free_quote is None or cost_step_up <= free_quote) and cost_step_up <= float(self.total_equity):
                qty_dec = qty_step_up
                order_cost = cost_step_up
            else:
                self.logger.warning(f"Calculated cost {order_cost:.2f} USDT is below minNotional {min_notional} for {symbol}. Order skipped.")
                return 0.0

        if free_quote is not None and order_cost > free_quote:
            self.logger.warning(
                f"Calculated cost {order_cost:.2f} USDT exceeds available free quote ({free_quote:.2f} USDT) for {symbol}. Skipped."
            )
            return 0.0

        if order_cost > float(self.total_equity):
            self.logger.warning(f"Calculated size {float(qty_dec)} ({order_cost:.2f} USDT) exceeds total equity ({self.total_equity:.2f} USDT) for {symbol}. Skipped.")
            return 0.0
        return float(qty_dec)
