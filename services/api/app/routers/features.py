from fastapi import APIRouter, HTTPException

from app.core.db import fetch_one
from app.schemas.features import FeatureRow, LatestFeatureResponse

router = APIRouter(prefix="/features", tags=["features"])

SYMBOL = "ETH/USDT"


@router.get("/latest", response_model=LatestFeatureResponse)
def latest_features():
    row = fetch_one(
        """
        SELECT ts, close, returns_1h, sma_5, sma_10, ema_10, volatility_10,
               rsi_14, macd_line, macd_signal, macd_hist,
               bb_upper, bb_middle, bb_lower, bb_width, bb_pct,
               atr_14, atr_pct, obv, vwap, stoch_k, stoch_d,
               vol_regime, signal_1h, signal_4h
        FROM features.eth_features
        WHERE exchange = %s AND symbol = %s
        ORDER BY ts DESC
        LIMIT 1
        """,
        ("binance", SYMBOL),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="No feature data available")
    return LatestFeatureResponse(symbol=SYMBOL, features=FeatureRow(**row))
