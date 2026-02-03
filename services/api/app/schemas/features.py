from datetime import datetime

from pydantic import BaseModel


class FeatureRow(BaseModel):
    ts: datetime
    close: float
    returns_1h: float
    sma_5: float
    sma_10: float
    ema_10: float
    volatility_10: float


class LatestFeatureResponse(BaseModel):
    symbol: str
    features: FeatureRow
