from pydantic import BaseModel


class PredictRequest(BaseModel):
    returns_1h: float | None = None
    sma_5: float | None = None
    sma_10: float | None = None
    ema_10: float | None = None
    volatility_10: float | None = None


class PredictResponse(BaseModel):
    symbol: str
    predicted_close: float
