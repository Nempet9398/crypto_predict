import os
from pathlib import Path

from joblib import load

MODEL_PATH = Path(os.getenv("MODEL_PATH", "/app/model_registry/eth_price_model.joblib"))


def load_model():
    if not MODEL_PATH.exists():
        return None
    return load(MODEL_PATH)
