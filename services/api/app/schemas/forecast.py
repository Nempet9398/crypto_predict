from datetime import datetime
from typing import List

from pydantic import BaseModel


class ForecastPoint(BaseModel):
    ts: datetime
    pred_close: float


class ForecastResponse(BaseModel):
    model: str
    symbol: str
    timeframe: str
    lookback: int
    horizon: int
    points: List[ForecastPoint]
