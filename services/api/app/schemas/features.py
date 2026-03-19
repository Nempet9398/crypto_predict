from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class FeatureRow(BaseModel):
    ts: datetime
    close: float
    returns_1bar: Optional[float] = None  # DB 컬럼명: returns_1bar (not returns_1h)
    sma_20: Optional[float] = None
    ema_20: Optional[float] = None
    ema_50: Optional[float] = None
    ema_200: Optional[float] = None
    volatility_10: Optional[float] = None
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
