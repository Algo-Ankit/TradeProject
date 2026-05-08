from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Dict, List, Optional

from core.config import get_instruments_config


class InstrumentType(Enum):
    EQUITY = "EQUITY"
    FUTURES = "FUTURES"
    OPTIONS_CALL = "OPTIONS_CALL"
    OPTIONS_PUT = "OPTIONS_PUT"
    INDEX = "INDEX"


class Exchange(Enum):
    NSE = "NSE"
    BSE = "BSE"
    NFO = "NFO"
    BFO = "BFO"


@dataclass
class Instrument:
    symbol: str
    exchange: Exchange
    instrument_type: InstrumentType
    lot_size: int
    tick_size: float
    zerodha_token: Optional[int] = None
    expiry: Optional[date] = None
    strike: Optional[float] = None


class InstrumentRegistry:
    _instance: Optional["InstrumentRegistry"] = None

    def __init__(self) -> None:
        self._instruments: Dict[str, Instrument] = {}
        self._load()

    @classmethod
    def get_instance(cls) -> "InstrumentRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _load(self) -> None:
        cfg = get_instruments_config()
        for entry in cfg.instruments:
            try:
                exchange = Exchange(entry.exchange)
            except ValueError:
                exchange = Exchange.NSE
            try:
                itype = InstrumentType(entry.type)
            except ValueError:
                itype = InstrumentType.EQUITY
            inst = Instrument(
                symbol=entry.symbol,
                exchange=exchange,
                instrument_type=itype,
                lot_size=entry.lot_size,
                tick_size=entry.tick_size,
                zerodha_token=entry.zerodha_token,
            )
            self._instruments[entry.symbol] = inst

    def get_instrument(self, symbol: str) -> Optional[Instrument]:
        return self._instruments.get(symbol)

    def get_all_symbols(self) -> List[str]:
        return list(self._instruments.keys())

    def get_by_token(self, token: int) -> Optional[Instrument]:
        for inst in self._instruments.values():
            if inst.zerodha_token == token:
                return inst
        return None

    def register(self, instrument: Instrument) -> None:
        self._instruments[instrument.symbol] = instrument
