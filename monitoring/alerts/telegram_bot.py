import asyncio
from datetime import datetime
from typing import Dict, Optional
import pytz
import structlog

logger = structlog.get_logger()
IST = pytz.timezone("Asia/Kolkata")


class AlertSeverity:
    CRITICAL = "🚨"
    WARNING = "⚠️"
    INFO = "ℹ️"


class TelegramAlerter:
    """
    Sends operational alerts to a Telegram chat.
    Does NOT replace structured logging — use for human-visible alerts only.
    Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables.
    """

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._bot = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._init_bot()

    def _init_bot(self) -> None:
        try:
            from telegram import Bot
            self._bot = Bot(token=self._bot_token)
        except ImportError:
            logger.warning("python_telegram_bot_not_installed")
        except Exception as e:
            logger.error("telegram_bot_init_failed", error=str(e))

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._send_loop())
        logger.info("telegram_alerter_started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _send_loop(self) -> None:
        while self._running:
            try:
                message = await asyncio.wait_for(self._queue.get(), timeout=5.0)
                if self._bot:
                    try:
                        await self._bot.send_message(chat_id=self._chat_id, text=message, parse_mode="HTML")
                    except Exception as e:
                        logger.error("telegram_send_failed", error=str(e))
            except asyncio.TimeoutError:
                continue

    def _format(self, emoji: str, title: str, detail: str) -> str:
        ts = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
        return f"{emoji} <b>{title}</b>\n{ts}\n{detail}"

    async def _enqueue(self, message: str) -> None:
        await self._queue.put(message)

    async def send_kill_switch_alert(self, reason: str) -> None:
        msg = self._format(AlertSeverity.CRITICAL, "KILL SWITCH ACTIVATED", reason)
        await self._enqueue(msg)

    async def send_strategy_halted(self, strategy_id: str, reason: str) -> None:
        detail = f"Strategy: {strategy_id}\nReason: {reason}"
        msg = self._format(AlertSeverity.WARNING, "Strategy Halted", detail)
        await self._enqueue(msg)

    async def send_risk_limit_warning(self, limit_name: str, pct_utilized: float) -> None:
        detail = f"Limit: {limit_name}\nUtilized: {pct_utilized:.1f}%"
        msg = self._format(AlertSeverity.WARNING, "Risk Limit Warning", detail)
        await self._enqueue(msg)

    async def send_latency_alert(self, p99_ms: float) -> None:
        detail = f"p99 total latency = {p99_ms:.0f}ms"
        msg = self._format(AlertSeverity.WARNING, "High Latency Alert", detail)
        await self._enqueue(msg)

    async def send_daily_pnl_summary(self, total_pnl: float, strategy_breakdown: Dict[str, float]) -> None:
        breakdown = "\n".join(f"  {k}: ₹{v:,.0f}" for k, v in strategy_breakdown.items())
        detail = f"Total PnL: ₹{total_pnl:,.0f}\n\nBy Strategy:\n{breakdown}"
        msg = self._format(AlertSeverity.INFO, "Daily PnL Summary", detail)
        await self._enqueue(msg)

    async def send_execution_quality_alert(self, avg_slippage_bps: float, threshold_bps: float) -> None:
        detail = f"Avg slippage: {avg_slippage_bps:.1f} bps\nThreshold: {threshold_bps:.1f} bps"
        msg = self._format(AlertSeverity.INFO, "Execution Quality Degraded", detail)
        await self._enqueue(msg)
