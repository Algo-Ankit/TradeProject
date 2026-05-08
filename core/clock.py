from datetime import datetime, date, time
from enum import Enum
from typing import List, Optional
import pytz
import structlog

logger = structlog.get_logger()

IST = pytz.timezone("Asia/Kolkata")

NSE_OPEN = time(9, 15)
NSE_CLOSE = time(15, 30)
PRE_OPEN_START = time(9, 0)
PRE_OPEN_END = time(9, 15)


class MarketSession(Enum):
    PRE_OPEN = "pre_open"
    CONTINUOUS = "continuous"
    POST_CLOSE = "post_close"
    HOLIDAY = "holiday"
    WEEKEND = "weekend"


class MarketClock:
    def __init__(self, holidays: Optional[List[str]] = None) -> None:
        self._holidays: set[date] = set()
        if holidays:
            for h in holidays:
                try:
                    self._holidays.add(date.fromisoformat(h))
                except ValueError:
                    logger.warning("invalid_holiday_date", value=h)

    def now(self) -> datetime:
        return datetime.now(IST)

    def today(self) -> date:
        return self.now().date()

    def is_holiday(self, d: Optional[date] = None) -> bool:
        d = d or self.today()
        return d in self._holidays

    def is_weekend(self, d: Optional[date] = None) -> bool:
        d = d or self.today()
        return d.weekday() >= 5

    def is_trading_day(self, d: Optional[date] = None) -> bool:
        d = d or self.today()
        return not self.is_weekend(d) and not self.is_holiday(d)

    def is_market_open(self) -> bool:
        now = self.now()
        if not self.is_trading_day(now.date()):
            return False
        t = now.time()
        return NSE_OPEN <= t < NSE_CLOSE

    def is_pre_open(self) -> bool:
        now = self.now()
        if not self.is_trading_day(now.date()):
            return False
        t = now.time()
        return PRE_OPEN_START <= t < PRE_OPEN_END

    def seconds_to_open(self) -> float:
        now = self.now()
        open_dt = now.replace(hour=9, minute=15, second=0, microsecond=0)
        if now >= open_dt:
            next_day = self._next_trading_day(now.date())
            open_dt = datetime(next_day.year, next_day.month, next_day.day, 9, 15, tzinfo=IST)
        return (open_dt - now).total_seconds()

    def seconds_to_close(self) -> float:
        now = self.now()
        close_dt = now.replace(hour=15, minute=30, second=0, microsecond=0)
        if now >= close_dt:
            return 0.0
        return (close_dt - now).total_seconds()

    def current_session(self) -> MarketSession:
        now = self.now()
        d = now.date()
        if self.is_weekend(d):
            return MarketSession.WEEKEND
        if self.is_holiday(d):
            return MarketSession.HOLIDAY
        t = now.time()
        if PRE_OPEN_START <= t < PRE_OPEN_END:
            return MarketSession.PRE_OPEN
        if NSE_OPEN <= t < NSE_CLOSE:
            return MarketSession.CONTINUOUS
        return MarketSession.POST_CLOSE

    def _next_trading_day(self, from_date: date) -> date:
        from datetime import timedelta
        d = from_date + timedelta(days=1)
        while self.is_weekend(d) or self.is_holiday(d):
            d += timedelta(days=1)
        return d

    def minutes_to_close(self) -> float:
        return self.seconds_to_close() / 60.0
