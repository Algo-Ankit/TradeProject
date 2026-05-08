import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Optional
import numpy as np
import structlog

logger = structlog.get_logger()


@dataclass
class LatencyRecord:
    order_id: str
    signal_generated_ts: float = field(default_factory=time.perf_counter)
    risk_check_done_ts: float = 0.0
    order_sent_ts: float = 0.0
    ack_received_ts: float = 0.0
    filled_ts: float = 0.0

    @property
    def risk_latency_ms(self) -> float:
        return (self.risk_check_done_ts - self.signal_generated_ts) * 1000

    @property
    def order_latency_ms(self) -> float:
        if self.ack_received_ts and self.order_sent_ts:
            return (self.ack_received_ts - self.order_sent_ts) * 1000
        return 0.0

    @property
    def fill_latency_ms(self) -> float:
        if self.filled_ts and self.ack_received_ts:
            return (self.filled_ts - self.ack_received_ts) * 1000
        return 0.0

    @property
    def total_latency_ms(self) -> float:
        end = self.filled_ts or self.ack_received_ts or self.order_sent_ts
        if end:
            return (end - self.signal_generated_ts) * 1000
        return 0.0


class LatencyProfiler:
    def __init__(self, alert_p99_ms: float = 500.0) -> None:
        self._alert_p99_ms = alert_p99_ms
        self._records: Dict[str, LatencyRecord] = {}
        self._completed: deque = deque(maxlen=1000)

    def record_signal(self, order_id: str) -> LatencyRecord:
        record = LatencyRecord(order_id=order_id, signal_generated_ts=time.perf_counter())
        self._records[order_id] = record
        return record

    def record_risk_done(self, order_id: str) -> None:
        if order_id in self._records:
            self._records[order_id].risk_check_done_ts = time.perf_counter()

    def record_order_sent(self, order_id: str) -> None:
        if order_id in self._records:
            self._records[order_id].order_sent_ts = time.perf_counter()

    def record_ack(self, order_id: str) -> None:
        if order_id in self._records:
            self._records[order_id].ack_received_ts = time.perf_counter()

    def record_fill(self, order_id: str) -> None:
        if order_id not in self._records:
            return
        record = self._records.pop(order_id)
        record.filled_ts = time.perf_counter()
        self._completed.append(record)
        self._check_p99_alert(record)

    def get_percentiles(self) -> dict:
        if not self._completed:
            return {}
        records = list(self._completed)
        risk_lat = [r.risk_latency_ms for r in records if r.risk_check_done_ts > 0]
        order_lat = [r.order_latency_ms for r in records if r.order_latency_ms > 0]
        fill_lat = [r.fill_latency_ms for r in records if r.fill_latency_ms > 0]
        total_lat = [r.total_latency_ms for r in records if r.total_latency_ms > 0]

        result = {}
        for name, arr in [("risk", risk_lat), ("order", order_lat), ("fill", fill_lat), ("total", total_lat)]:
            if arr:
                a = np.array(arr)
                result[f"{name}_p50_ms"] = float(np.percentile(a, 50))
                result[f"{name}_p95_ms"] = float(np.percentile(a, 95))
                result[f"{name}_p99_ms"] = float(np.percentile(a, 99))
        return result

    def _check_p99_alert(self, record: LatencyRecord) -> None:
        if record.total_latency_ms > self._alert_p99_ms:
            logger.warning(
                "high_latency_detected",
                order_id=record.order_id,
                total_ms=round(record.total_latency_ms, 2),
                risk_ms=round(record.risk_latency_ms, 2),
                order_ms=round(record.order_latency_ms, 2),
                fill_ms=round(record.fill_latency_ms, 2),
                threshold_ms=self._alert_p99_ms,
            )
