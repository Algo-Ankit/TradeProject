#!/usr/bin/env python3
"""
Run StatArb backtest and generate HTML report.
Usage: python scripts/run_backtest.py --months 12 --symbols RELIANCE,TCS,HDFCBANK,INFY
"""
import argparse
import asyncio
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    parser = argparse.ArgumentParser(description="Run StatArb backtest")
    parser.add_argument("--months", type=int, default=6, help="Lookback months")
    parser.add_argument("--symbols", default="RELIANCE,TCS,HDFCBANK,INFY,ICICIBANK", help="Comma-separated symbols")
    parser.add_argument("--output", default="reports/backtest.html", help="Output HTML path")
    parser.add_argument("--capital", type=float, default=1_000_000, help="Starting capital in INR")
    args = parser.parse_args()

    from core.logger import configure_logging
    configure_logging("INFO")

    from data.storage.clickhouse_writer import ClickHouseWriter
    from data.storage.redis_state import RedisStateManager
    from core.events import EventBus
    from research.backtester.engine import BacktestEngine
    from research.backtester.fill_model import FillModel
    from research.reporting.metrics import PerformanceMetrics

    writer = ClickHouseWriter(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_PORT", 9000)),
        database=os.getenv("CLICKHOUSE_DB", "trading"),
        user=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", ""),
    )
    redis = RedisStateManager(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", 6379)),
        db=int(os.getenv("REDIS_DB", 0)),
    )

    await writer.connect()
    await redis.connect()

    event_bus = EventBus.get_instance()
    await event_bus.start()

    symbols = [s.strip() for s in args.symbols.split(",")]
    end_date = date.today()
    start_date = end_date - timedelta(days=args.months * 30)

    from strategies.stat_arb.spread_trader import SpreadTrader
    config = {
        "max_position_inr": 500_000,
        "max_open_pairs": 4,
        "entry_zscore": 2.0,
        "exit_zscore": 0.5,
        "stop_zscore": 3.5,
    }
    strategy = SpreadTrader("stat_arb", config, redis, event_bus, writer)
    await strategy.initialize()

    fill_config = {
        "commission_bps": 3,
        "market_impact_eta": 0.1,
        "price_volatility": 0.01,
        "starting_capital": args.capital,
    }
    fill_model = FillModel(fill_config)

    engine_config = {
        "starting_capital": args.capital,
        "commission_bps": 3,
    }
    engine = BacktestEngine(writer, strategy, fill_model, engine_config)

    print(f"\nRunning backtest: {start_date} to {end_date}")
    print(f"Symbols: {symbols}")
    print(f"Starting capital: ₹{args.capital:,.0f}\n")

    results = await engine.run(symbols, start_date, end_date)

    metrics = PerformanceMetrics(results["fills"], results["equity_curve"], args.capital).compute()

    print("\n" + "=" * 60)
    print("BACKTEST RESULTS")
    print("=" * 60)
    print(f"Total Return:       {metrics.total_return:>10.2f}%")
    print(f"Annualized Return:  {metrics.annualized_return:>10.2f}%")
    print(f"Sharpe Ratio:       {metrics.sharpe_ratio:>10.3f}")
    print(f"Sortino Ratio:      {metrics.sortino_ratio:>10.3f}")
    print(f"Calmar Ratio:       {metrics.calmar_ratio:>10.3f}")
    print(f"Max Drawdown:       {metrics.max_drawdown:>10.2f}%")
    print(f"Win Rate:           {metrics.win_rate:>10.2f}%")
    print(f"Total Trades:       {metrics.total_trades:>10d}")
    print(f"Avg Trade PnL:      ₹{metrics.avg_trade_pnl:>9.2f}")
    print(f"Trades/Day:         {metrics.trades_per_day:>10.2f}")
    print("=" * 60)

    os.makedirs(os.path.dirname(args.output) if os.path.dirname(args.output) else ".", exist_ok=True)
    reporter = PerformanceMetrics(results["fills"], results["equity_curve"], args.capital)
    reporter.generate_report(args.output)
    print(f"\nReport saved to: {args.output}")

    await event_bus.stop()


if __name__ == "__main__":
    asyncio.run(main())
