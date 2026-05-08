from datetime import date, timedelta
from typing import Callable, Dict, List
import numpy as np
import structlog

from research.backtester.engine import BacktestEngine
from research.reporting.metrics import PerformanceMetrics

logger = structlog.get_logger()


class WalkForwardRunner:
    def __init__(self, engine: BacktestEngine, strategy_factory: Callable, config: dict) -> None:
        self._engine = engine
        self._strategy_factory = strategy_factory
        self._train_window = int(config.get("train_window_days", 180))
        self._test_window = int(config.get("test_window_days", 30))
        self._step = int(config.get("step_days", 30))

    async def run(self, symbols: List[str], total_start: date, total_end: date) -> dict:
        folds = []
        current = total_start
        while current + timedelta(days=self._train_window + self._test_window) <= total_end:
            train_start = current
            train_end = current + timedelta(days=self._train_window)
            test_start = train_end
            test_end = train_end + timedelta(days=self._test_window)
            folds.append((train_start, train_end, test_start, test_end))
            current += timedelta(days=self._step)

        if not folds:
            logger.warning("walk_forward_no_folds")
            return {}

        fold_results = []
        combined_fills = []
        combined_equity = []

        for n, (train_start, train_end, test_start, test_end) in enumerate(folds, 1):
            logger.info("walk_forward_fold_start", fold=n, train=f"{train_start}→{train_end}", test=f"{test_start}→{test_end}")

            strategy = self._strategy_factory(train_start, train_end)
            self._engine._strategy = strategy

            result = await self._engine.run(symbols, test_start, test_end)
            metrics = PerformanceMetrics(result["fills"], result["equity_curve"], self._engine._starting_capital).compute()

            fold_results.append({
                "fold": n,
                "train_start": str(train_start),
                "train_end": str(train_end),
                "test_start": str(test_start),
                "test_end": str(test_end),
                "sharpe": metrics.sharpe_ratio,
                "total_return_pct": metrics.total_return,
                "max_drawdown_pct": metrics.max_drawdown,
                "trades": metrics.total_trades,
            })
            combined_fills.extend(result["fills"])
            combined_equity.extend(result["equity_curve"])

        combined_metrics = PerformanceMetrics(combined_fills, combined_equity, self._engine._starting_capital).compute()

        self._print_summary(fold_results, combined_metrics)

        return {
            "fold_results": fold_results,
            "combined_oos_metrics": combined_metrics,
            "combined_equity_curve": combined_equity,
        }

    def _print_summary(self, fold_results: List[dict], combined) -> None:
        print("\n" + "=" * 80)
        print("WALK-FORWARD VALIDATION SUMMARY")
        print("=" * 80)
        header = f"{'Fold':>4} | {'Train Period':>22} | {'Test Period':>22} | {'Sharpe':>7} | {'Return%':>8} | {'MaxDD%':>7}"
        print(header)
        print("-" * 80)
        for r in fold_results:
            print(f"{r['fold']:>4} | {r['train_start']}→{r['train_end']} | {r['test_start']}→{r['test_end']} | {r['sharpe']:>7.3f} | {r['total_return_pct']:>8.2f} | {r['max_drawdown_pct']:>7.2f}")
        print("=" * 80)
        print(f"Combined OOS Sharpe:  {combined.sharpe_ratio:.3f}")
        print(f"Combined OOS Return:  {combined.total_return:.2f}%")
        print(f"Combined OOS Max DD:  {combined.max_drawdown:.2f}%")
        print(f"Total OOS Trades:     {combined.total_trades}")
        print("=" * 80 + "\n")
