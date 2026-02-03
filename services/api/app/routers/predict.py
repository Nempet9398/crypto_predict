from fastapi import APIRouter, HTTPException, Request

from app.core.db import fetch_one
from app.core.model import load_model
from app.schemas.predict import PredictRequest, PredictResponse

router = APIRouter(tags=["predict"])

SYMBOL = "ETH/USDT"


def load_latest_features():
    row = fetch_one(
        """
        SELECT returns_1h, sma_5, sma_10, ema_10, volatility_10
        FROM features.eth_features
        WHERE exchange = %s AND symbol = %s
        ORDER BY ts DESC
        LIMIT 1
        """,
        ("binance", SYMBOL),
    )
    return row


@router.post("/predict", response_model=PredictResponse)
def predict(request: Request, payload: PredictRequest):
    model = request.app.state.model
    if model is None:
        model = load_model()
        request.app.state.model = model
    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")

    features = {
        "returns_1h": payload.returns_1h,
        "sma_5": payload.sma_5,
        "sma_10": payload.sma_10,
        "ema_10": payload.ema_10,
        "volatility_10": payload.volatility_10,
    }
    if any(v is None for v in features.values()):
        latest = load_latest_features()
        if latest is None:
            raise HTTPException(status_code=404, detail="No feature data available")
        for key in features:
            if features[key] is None:
                features[key] = float(latest[key])

    X = [[
        features["returns_1h"],
        features["sma_5"],
        features["sma_10"],
        features["ema_10"],
        features["volatility_10"],
    ]]
    pred = float(model.predict(X)[0])
    return PredictResponse(symbol=SYMBOL, predicted_close=pred)
