from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class FeatureRow(BaseModel):
    ts: datetime
    close: float
    returns_1h: float
    sma_5: float
    sma_10: float
    ema_10: float
    volatility_10: float
    # Extended indicators
    rsi_14: Optional[float] = None
    macd_line: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_hist: Optional[float] = None
    bb_upper: Optional[float] = None
    bb_middle: Optional[float] = None
    bb_lower: Optional[float] = None
    bb_width: Optional[float] = None
    bb_pct: Optional[float] = None
    atr_14: Optional[float] = None
    atr_pct: Optional[float] = None
    obv: Optional[float] = None
    vwap: Optional[float] = None
    stoch_k: Optional[float] = None
    stoch_d: Optional[float] = None
    vol_regime: Optional[str] = None
    signal_1h: Optional[int] = None
    signal_4h: Optional[int] = None


class LatestFeatureResponse(BaseModel):
    symbol: str
    features: FeatureRow
