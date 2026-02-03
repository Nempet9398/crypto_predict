from datetime import datetime
from typing import List

from pydantic import BaseModel


class PricePoint(BaseModel):
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class PriceHistoryResponse(BaseModel):
    symbol: str
    timeframe: str
    points: List[PricePoint]


class LatestPriceResponse(BaseModel):
    symbol: str
    timeframe: str
    price: PricePoint
