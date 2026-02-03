from fastapi import APIRouter, HTTPException

from app.core.db import fetch_one
from app.schemas.features import FeatureRow, LatestFeatureResponse

router = APIRouter(prefix="/features", tags=["features"])

SYMBOL = "ETH/USDT"


@router.get("/latest", response_model=LatestFeatureResponse)
def latest_features():
    row = fetch_one(
        """
        SELECT ts, close, returns_1h, sma_5, sma_10, ema_10, volatility_10
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
