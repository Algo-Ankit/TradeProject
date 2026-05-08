import asyncio
from datetime import datetime, date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import pytz

IST = pytz.timezone("Asia/Kolkata")


# ──────────────────────────────────────────────────────────────────────────────
class TestTickValidator:
    def setup_method(self):
        from data.quality.validator import TickValidator
        self.validator = TickValidator(stale_threshold_seconds=5, max_price_move_pct=5.0)

    def _make_tick(self, **kwargs):
        base = {
            "timestamp": datetime.now(IST),
            "symbol": "RELIANCE",
            "exchange": "NSE",
            "last_price": 2500.0,
            "bid": 2499.5,
            "ask": 2500.5,
            "bid_qty": 500,
            "ask_qty": 300,
            "volume": 100000,
            "oi": 0,
        }
        base.update(kwargs)
        return base

    def test_accepts_valid_tick(self):
        tick = self._make_tick()
        valid, reason = self.validator.validate(tick)
        assert valid is True
        assert reason == ""

    def test_rejects_crossed_spread(self):
        tick = self._make_tick(bid=2501.0, ask=2499.0)
        valid, reason = self.validator.validate(tick)
        assert valid is False
        assert "crossed_spread" in reason

    def test_rejects_stale_tick(self):
        old_ts = datetime.now(IST) - timedelta(seconds=60)
        tick = self._make_tick(timestamp=old_ts)
        valid, reason = self.validator.validate(tick)
        assert valid is False
        assert "stale" in reason

    def test_rejects_zero_price(self):
        tick = self._make_tick(last_price=0, bid=0, ask=0)
        valid, reason = self.validator.validate(tick)
        assert valid is False

    def test_rejects_extreme_price_move(self):
        tick1 = self._make_tick(last_price=1000.0)
        self.validator.validate(tick1)
        tick2 = self._make_tick(last_price=1120.0)
        valid, reason = self.validator.validate(tick2)
        assert valid is False
        assert "extreme_move" in reason

    def test_rejects_zero_depth(self):
        tick = self._make_tick(bid_qty=0, ask_qty=0)
        valid, reason = self.validator.validate(tick)
        assert valid is False

    def test_stats_tracking(self):
        tick = self._make_tick(bid=2501.0, ask=2499.0)
        self.validator.validate(tick)
        stats = self.validator.get_stats()
        assert "RELIANCE" in stats
        assert stats["RELIANCE"]["dropped"] >= 1


# ──────────────────────────────────────────────────────────────────────────────
class TestEventBus:
    @pytest.mark.asyncio
    async def test_publish_subscribe(self):
        from core.events import EventBus, TickEvent
        bus = EventBus()
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe(TickEvent, handler)
        await bus.start()

        tick = TickEvent(
            timestamp=datetime.now(IST),
            source_module="test",
            symbol="RELIANCE",
            exchange="NSE",
            last_price=2500.0,
            bid=2499.5,
            ask=2500.5,
            bid_qty=500,
            ask_qty=300,
            volume=100000,
            oi=0,
        )
        await bus.publish(tick)
        await asyncio.sleep(0.1)
        await bus.stop()

        assert len(received) == 1
        assert received[0].symbol == "RELIANCE"

    @pytest.mark.asyncio
    async def test_multiple_handlers(self):
        from core.events import EventBus, RiskEvent
        bus = EventBus()
        counts = [0, 0]

        async def h1(e): counts[0] += 1
        async def h2(e): counts[1] += 1

        bus.subscribe(RiskEvent, h1)
        bus.subscribe(RiskEvent, h2)
        await bus.start()

        event = RiskEvent(timestamp=datetime.now(IST), source_module="test", event_type="test", severity="INFO", message="test")
        await bus.publish(event)
        await asyncio.sleep(0.1)
        await bus.stop()

        assert counts[0] == 1
        assert counts[1] == 1


# ──────────────────────────────────────────────────────────────────────────────
class TestMarketClock:
    def test_market_open_during_hours(self):
        from core.clock import MarketClock
        clock = MarketClock(holidays=[])
        with patch("core.clock.datetime") as mock_dt:
            trading_day = date(2024, 11, 5)
            fake_now = IST.localize(datetime(2024, 11, 5, 10, 30, 0))
            mock_dt.now.return_value = fake_now
            assert clock.is_market_open() is True

    def test_market_closed_weekend(self):
        from core.clock import MarketClock
        clock = MarketClock(holidays=[])
        with patch("core.clock.datetime") as mock_dt:
            saturday = IST.localize(datetime(2024, 11, 2, 11, 0, 0))
            mock_dt.now.return_value = saturday
            assert clock.is_market_open() is False

    def test_market_closed_before_open(self):
        from core.clock import MarketClock
        clock = MarketClock(holidays=[])
        with patch("core.clock.datetime") as mock_dt:
            fake_now = IST.localize(datetime(2024, 11, 5, 8, 0, 0))
            mock_dt.now.return_value = fake_now
            assert clock.is_market_open() is False

    def test_market_closed_after_close(self):
        from core.clock import MarketClock
        clock = MarketClock(holidays=[])
        with patch("core.clock.datetime") as mock_dt:
            fake_now = IST.localize(datetime(2024, 11, 5, 16, 0, 0))
            mock_dt.now.return_value = fake_now
            assert clock.is_market_open() is False

    def test_pre_open_window(self):
        from core.clock import MarketClock
        clock = MarketClock(holidays=[])
        with patch("core.clock.datetime") as mock_dt:
            fake_now = IST.localize(datetime(2024, 11, 5, 9, 10, 0))
            mock_dt.now.return_value = fake_now
            assert clock.is_pre_open() is True

    def test_holiday_market_closed(self):
        from core.clock import MarketClock
        holiday = "2024-10-02"
        clock = MarketClock(holidays=[holiday])
        with patch("core.clock.datetime") as mock_dt:
            fake_now = IST.localize(datetime(2024, 10, 2, 11, 0, 0))
            mock_dt.now.return_value = fake_now
            assert clock.is_market_open() is False


# ──────────────────────────────────────────────────────────────────────────────
class TestRedisStateManager:
    @pytest.mark.asyncio
    async def test_tick_roundtrip(self):
        from data.storage.redis_state import RedisStateManager
        manager = RedisStateManager()
        mock_redis = AsyncMock()
        mock_redis.setex = AsyncMock()
        mock_redis.get = AsyncMock(return_value='{"bid": 2499.5, "ask": 2500.5, "last_price": 2500.0}')
        mock_redis.ping = AsyncMock()
        manager._redis = mock_redis

        tick = {"bid": 2499.5, "ask": 2500.5, "last_price": 2500.0}
        await manager.update_tick("RELIANCE", tick)
        result = await manager.get_tick("RELIANCE")

        assert result is not None
        assert result["bid"] == "2499.5" or float(result.get("bid", 0)) == 2499.5

    @pytest.mark.asyncio
    async def test_kill_switch(self):
        from data.storage.redis_state import RedisStateManager
        manager = RedisStateManager()
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock()
        mock_redis.get = AsyncMock(return_value="true")
        manager._redis = mock_redis

        await manager.set_kill_switch(True, "test reason")
        active = await manager.is_kill_switch_active()
        assert active is True
