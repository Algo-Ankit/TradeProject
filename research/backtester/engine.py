import asyncio
from datetime import date, datetime
from typing import Dict, List, Optional
import polars as pl
import pytz
import structlog

logger = structlog.get_logger()
IST = pytz.timezone("Asia/Kolkata")


class LookaheadError(Exception):
    pass


class BacktestEngine:
    def __init__(self, clickhouse_writer, strategy, fill_model, config: dict) -> None:
        self._clickhouse = clickhouse_writer
        self._strategy = strategy
        self._fill_model = fill_model
        self._config = config
        self.current_time: Optional[datetime] = None
        self._starting_capital = float(config.get("starting_capital", 1_000_000))
        self._cash = self._starting_capital
        self._positions: Dict[str, int] = {}
        self._current_prices: Dict[str, float] = {}
        self._fills: List[dict] = []
        self._equity_curve: List[dict] = []
        self._last_equity_snap: Optional[datetime] = None
        self._commission_bps = float(config.get("commission_bps", 3))

    async def run(self, symbols: List[str], start_date: date, end_date: date) -> dict:
        sym_list = "','".join(symbols)
        sql = f"""
            SELECT timestamp, symbol, last_price, bid, ask, bid_qty, ask_qty, volume, oi
            FROM trading.ticks
            WHERE symbol IN ('{sym_list}')
              AND toDate(timestamp) >= '{start_date}'
              AND toDate(timestamp) <= '{end_date}'
            ORDER BY timestamp ASC
        """

        try:
            rows = await self._clickhouse.query(sql)
        except Exception as e:
            logger.error("backtest_data_fetch_failed", error=str(e))
            rows = []

        logger.info("backtest_started", symbols=symbols, start=str(start_date), end=str(end_date), ticks=len(rows))

        self._cash = self._starting_capital
        self._positions = {}
        self._fills = []
        self._equity_curve = []

        for row in rows:
            ts = row["timestamp"]
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            if ts.tzinfo is None:
                ts = IST.localize(ts)

            self.current_time = ts
            symbol = row["symbol"]
            self._current_prices[symbol] = float(row["last_price"])

            tick_event = self._make_tick_event(row, ts)
            signal = await self._strategy.on_tick(tick_event)

            if signal:
                if hasattr(signal, "timestamp") and signal.timestamp and signal.timestamp > self.current_time:
                    raise LookaheadError(f"Signal timestamp {signal.timestamp} > current_time {self.current_time}")

                fill = self._fill_model.simulate_fill(signal, self.current_time, row)
                if fill:
                    await self._strategy.on_fill(fill)
                    self._apply_fill(fill)
                    self._fills.append({
                        "timestamp": fill.timestamp,
                        "symbol": fill.symbol,
                        "side": fill.side,
                        "qty": fill.qty,
                        "fill_price": fill.fill_price,
                        "expected_price": fill.expected_price,
                        "slippage_bps": fill.slippage_bps,
                        "commission_inr": fill.commission_inr,
                        "strategy_id": fill.order_id,
                    })

            if self._last_equity_snap is None or (ts - self._last_equity_snap).total_seconds() >= 60:
                equity = self._cash + self._mark_to_market()
                self._equity_curve.append({"timestamp": ts, "equity": equity})
                self._last_equity_snap = ts

        final_equity = self._cash + self._mark_to_market()
        logger.info("backtest_complete", fills=len(self._fills), final_equity=round(final_equity, 2))
        return {"fills": self._fills, "equity_curve": self._equity_curve, "final_equity": final_equity}

    def _make_tick_event(self, row: dict, ts: datetime):
        from core.events import TickEvent
        return TickEvent(
            timestamp=ts,
            source_module="backtest",
            symbol=row["symbol"],
            exchange="NSE",
            last_price=float(row["last_price"]),
            bid=float(row.get("bid", row["last_price"])),
            ask=float(row.get("ask", row["last_price"])),
            bid_qty=int(row.get("bid_qty", 0)),
            ask_qty=int(row.get("ask_qty", 0)),
            volume=int(row.get("volume", 0)),
            oi=int(row.get("oi", 0)),
        )

    def _apply_fill(self, fill) -> None:
        symbol = fill.symbol
        qty = fill.qty if fill.side == "BUY" else -fill.qty
        self._positions[symbol] = self._positions.get(symbol, 0) + qty
        cost = fill.qty * fill.fill_price + fill.commission_inr
        if fill.side == "BUY":
            self._cash -= cost
        else:
            self._cash += fill.qty * fill.fill_price - fill.commission_inr

    def _mark_to_market(self) -> float:
        return sum(qty * self._current_prices.get(sym, 0) for sym, qty in self._positions.items())
