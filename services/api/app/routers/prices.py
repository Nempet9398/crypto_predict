from fastapi import APIRouter, HTTPException, Query

from app.core.db import fetch_all, fetch_one
from app.schemas.prices import LatestPriceResponse, PriceHistoryResponse, PricePoint

router = APIRouter(prefix="/prices", tags=["prices"])

SYMBOL = "ETH/USDT"
TIMEFRAME = "1h"


@router.get("/latest", response_model=LatestPriceResponse)
def latest_price():
    row = fetch_one(
        """
        SELECT ts, open, high, low, close, volume
        FROM processed.eth_ohlcv_1h
        WHERE exchange = %s AND symbol = %s
        ORDER BY ts DESC
        LIMIT 1
        """,
        ("binance", SYMBOL),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="No price data available")
    price = PricePoint(**row)
    return LatestPriceResponse(symbol=SYMBOL, timeframe=TIMEFRAME, price=price)


@router.get("/history", response_model=PriceHistoryResponse)
def price_history(limit: int = Query(100, ge=1, le=1000)):
    rows = fetch_all(
        """
        SELECT ts, open, high, low, close, volume
        FROM processed.eth_ohlcv_1h
        WHERE exchange = %s AND symbol = %s
        ORDER BY ts DESC
        LIMIT %s
        """,
        ("binance", SYMBOL, limit),
    )
    points = [PricePoint(**row) for row in reversed(rows)]
    return PriceHistoryResponse(symbol=SYMBOL, timeframe=TIMEFRAME, points=points)
