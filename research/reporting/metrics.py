import math
from dataclasses import dataclass
from typing import List, Optional
import numpy as np
import polars as pl
import structlog

logger = structlog.get_logger()


@dataclass
class PerformanceReport:
    total_return: float
    annualized_return: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    max_drawdown: float
    max_drawdown_duration_days: float
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    turnover_annual: float
    avg_trade_pnl: float
    trades_per_day: float
    total_trades: int
    start_date: str
    end_date: str


class PerformanceMetrics:
    def __init__(self, fills: List[dict], equity_curve: List[dict], starting_capital: float = 1_000_000.0) -> None:
        self._fills = fills
        self._equity_curve = equity_curve
        self._starting_capital = starting_capital

    def compute(self) -> PerformanceReport:
        if not self._equity_curve:
            return self._empty_report()

        equity_df = pl.DataFrame(self._equity_curve)
        equity_arr = equity_df["equity"].to_numpy()

        final_equity = float(equity_arr[-1])
        total_return = (final_equity - self._starting_capital) / self._starting_capital

        ts_col = equity_df["timestamp"]
        start_ts = ts_col[0]
        end_ts = ts_col[-1]
        if hasattr(start_ts, 'date'):
            start_str = str(start_ts.date())
            end_str = str(end_ts.date())
        else:
            start_str = str(start_ts)
            end_str = str(end_ts)

        n_days = max(1, len(equity_arr) / (375 * 60))

        daily_returns = np.diff(equity_arr) / equity_arr[:-1]
        if len(daily_returns) == 0:
            return self._empty_report()

        n_trading_days = n_days
        annualized_return = (1 + total_return) ** (252 / max(n_trading_days, 1)) - 1

        std = float(np.std(daily_returns))
        mean_ret = float(np.mean(daily_returns))
        sharpe = (mean_ret / std * math.sqrt(252 * 375)) if std > 0 else 0.0

        downside = daily_returns[daily_returns < 0]
        down_std = float(np.std(downside)) if len(downside) > 0 else 0.001
        sortino = (mean_ret / down_std * math.sqrt(252 * 375)) if down_std > 0 else 0.0

        running_max = np.maximum.accumulate(equity_arr)
        drawdown = (equity_arr - running_max) / running_max
        max_dd = float(abs(drawdown.min()))
        calmar = annualized_return / max_dd if max_dd > 0 else 0.0

        dd_duration = self._max_drawdown_duration(equity_arr)

        trade_pnls = self._compute_trade_pnls()
        wins = [p for p in trade_pnls if p > 0]
        losses = [p for p in trade_pnls if p < 0]
        win_rate = len(wins) / len(trade_pnls) if trade_pnls else 0.0
        avg_win = float(np.mean(wins)) if wins else 0.0
        avg_loss = float(np.mean(losses)) if losses else 0.0
        profit_factor = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else 0.0
        avg_trade_pnl = float(np.mean(trade_pnls)) if trade_pnls else 0.0
        trades_per_day = len(trade_pnls) / max(n_trading_days, 1)

        total_volume = sum(f.get("qty", 0) * f.get("fill_price", 0) for f in self._fills)
        turnover_annual = (total_volume / self._starting_capital) * (252 / max(n_trading_days, 1))

        return PerformanceReport(
            total_return=round(total_return * 100, 3),
            annualized_return=round(annualized_return * 100, 3),
            sharpe_ratio=round(sharpe, 3),
            sortino_ratio=round(sortino, 3),
            calmar_ratio=round(calmar, 3),
            max_drawdown=round(max_dd * 100, 3),
            max_drawdown_duration_days=round(dd_duration, 1),
            win_rate=round(win_rate * 100, 2),
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            profit_factor=round(profit_factor, 3),
            turnover_annual=round(turnover_annual, 2),
            avg_trade_pnl=round(avg_trade_pnl, 2),
            trades_per_day=round(trades_per_day, 2),
            total_trades=len(trade_pnls),
            start_date=start_str,
            end_date=end_str,
        )

    def _compute_trade_pnls(self) -> List[float]:
        pnls = []
        for fill in self._fills:
            price = fill.get("fill_price", 0)
            expected = fill.get("expected_price", price)
            side = fill.get("side", "BUY")
            qty = fill.get("qty", 0)
            slippage_cost = abs(fill.get("slippage_bps", 0)) * qty * price / 10000
            commission = fill.get("commission_inr", 0)
            if side == "BUY":
                pnl = -(slippage_cost + commission)
            else:
                pnl = -(slippage_cost + commission)
            pnls.append(pnl)
        return pnls

    def _max_drawdown_duration(self, equity_arr: np.ndarray) -> float:
        running_max = np.maximum.accumulate(equity_arr)
        in_drawdown = equity_arr < running_max
        max_duration = 0
        current_duration = 0
        for flag in in_drawdown:
            if flag:
                current_duration += 1
                max_duration = max(max_duration, current_duration)
            else:
                current_duration = 0
        return float(max_duration / (375 * 60))

    def generate_report(self, output_path: str) -> None:
        try:
            import plotly.graph_objects as go
            from plotly.subplots import make_subplots
        except ImportError:
            logger.error("plotly_not_installed")
            return

        metrics = self.compute()
        equity_df = pl.DataFrame(self._equity_curve) if self._equity_curve else pl.DataFrame()

        fig = make_subplots(rows=2, cols=2, subplot_titles=["Equity Curve", "Drawdown", "Trade PnL Distribution", "Metrics"])

        if not equity_df.is_empty():
            equity_arr = equity_df["equity"].to_numpy()
            timestamps = equity_df["timestamp"].to_list()
            fig.add_trace(go.Scatter(x=timestamps, y=equity_arr, name="Equity", line=dict(color="#00ff88")), row=1, col=1)
            running_max = np.maximum.accumulate(equity_arr)
            drawdown = (equity_arr - running_max) / running_max * 100
            fig.add_trace(go.Scatter(x=timestamps, y=drawdown, name="Drawdown %", fill="tozeroy", line=dict(color="#ff4757")), row=1, col=2)

        trade_pnls = self._compute_trade_pnls()
        if trade_pnls:
            fig.add_trace(go.Histogram(x=trade_pnls, name="Trade PnL", marker_color="#00b4d8"), row=2, col=1)

        metrics_text = f"""
        Total Return: {metrics.total_return}%
        Annualized: {metrics.annualized_return}%
        Sharpe: {metrics.sharpe_ratio}
        Sortino: {metrics.sortino_ratio}
        Max DD: {metrics.max_drawdown}%
        Win Rate: {metrics.win_rate}%
        Trades: {metrics.total_trades}
        """
        fig.add_trace(go.Scatter(x=[0], y=[0], mode="text", text=[metrics_text], textposition="middle center"), row=2, col=2)

        fig.update_layout(
            title="Backtest Performance Report",
            paper_bgcolor="#0d1117",
            plot_bgcolor="#161b22",
            font=dict(color="#c9d1d9"),
            height=800,
        )

        import os
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
        fig.write_html(output_path)
        logger.info("report_generated", path=output_path)

    def _empty_report(self) -> PerformanceReport:
        return PerformanceReport(
            total_return=0, annualized_return=0, sharpe_ratio=0, sortino_ratio=0,
            calmar_ratio=0, max_drawdown=0, max_drawdown_duration_days=0,
            win_rate=0, avg_win=0, avg_loss=0, profit_factor=0,
            turnover_annual=0, avg_trade_pnl=0, trades_per_day=0,
            total_trades=0, start_date="", end_date="",
        )
